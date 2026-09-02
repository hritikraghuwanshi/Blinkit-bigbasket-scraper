"""
BigBasket scraper.

Two things were verified manually before writing this class (see README):
  1. BigBasket's product data is server-rendered as a `__NEXT_DATA__` JSON
     blob, so a plain HTTP GET (no browser) is enough to read listings --
     far cheaper than DOM scraping.
  2. BigBasket's bot-protection blocks headless *Chromium* even with
     stealth patches, but accepts a genuine installed Chrome build
     (Playwright's `channel="chrome"`). So the location-picker UI, which
     does need a real browser, is driven with that channel.

Design: use the browser only to set the delivery location (cookies), then
reuse those cookies via the browser context's own request API for fast,
JS-free category page fetches -- one browser session, no separate HTTP
client to keep in sync.
"""
import json
import logging
import re
import time

from playwright.sync_api import sync_playwright

import config
from models.product import Product
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
window.chrome = { runtime: {} };
"""

BASE_URL = "https://www.bigbasket.com"
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class BigBasketScraper(BaseScraper):
    def __init__(self, city: str):
        super().__init__(city)
        self.city_cfg = config.CITIES[city]
        self._playwright = sync_playwright().start()
        self.browser = self._playwright.chromium.launch(
            headless=True,
            channel=config.BROWSER_CHANNEL,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.context = self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="en-IN",
            geolocation={
                "latitude": self.city_cfg["latitude"],
                "longitude": self.city_cfg["longitude"],
            },
            permissions=["geolocation"],
        )
        self.context.add_init_script(STEALTH_JS)
        self.page = self.context.new_page()

    def set_location(self) -> bool:
        for attempt in range(1, config.RETRY_ATTEMPTS + 1):
            try:
                if self._try_set_location_once():
                    logger.info("BigBasket location set to %s", self.city_cfg["display_name"])
                    return True
                logger.warning("set_location attempt %d did not confirm", attempt)
            except Exception as e:
                logger.warning("set_location attempt %d failed: %s", attempt, e)
            time.sleep(config.RETRY_BACKOFF_SECONDS)
        logger.error(
            "Could not set BigBasket location after %d attempts; "
            "falling back to default (non-hyperlocal) catalog",
            config.RETRY_ATTEMPTS,
        )
        return False

    def _try_set_location_once(self) -> bool:
        self.page.goto(BASE_URL, timeout=config.REQUEST_TIMEOUT_MS, wait_until="domcontentloaded")

        # BigBasket shows a "Why choose Bigbasket?" promo popup ~2-3s after
        # load that intercepts clicks if still open. Wait for it (bounded)
        # and dismiss it, rather than guessing a fixed delay -- if it never
        # appears within the window we just move on.
        try:
            self.page.get_by_text("Why choose Bigbasket", exact=False).wait_for(
                state="visible", timeout=5000
            )
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(500)
        except Exception:
            pass

        # BigBasket renders a second, normally-offscreen mobile header that
        # `get_by_text("Select Location")` can resolve to instead of the
        # visible desktop one (confirmed by inspecting devtools: it reports
        # "<header class='sm:hidden'> subtree intercepts pointer events").
        # A semantic locator + force-click lands on the wrong element there,
        # so this clicks the known, fixed position of the visible "Select
        # Location" control instead -- reliable only because viewport size
        # is pinned above; if that ever changes, this coordinate must too.
        self.page.mouse.click(920, 41)

        search_box = self.page.get_by_placeholder("Search for area or street name").first
        search_box.wait_for(state="visible", timeout=8000)
        # A forced click doesn't reliably call .focus() on this input (the
        # keyboard.type() below then goes nowhere and no suggestions load),
        # so focus it explicitly via JS rather than relying on click focus.
        search_box.evaluate("el => el.focus()")
        self.page.wait_for_timeout(200)
        self.page.keyboard.type(self.city_cfg["search_query"], delay=80)

        suggestions = self.page.locator("li").filter(has_text=self.city_cfg["display_name"])
        suggestions.first.wait_for(state="visible", timeout=8000)
        # The modal's own backdrop div physically sits above the suggestion
        # list in this headless render. click(force=True) only skips
        # Playwright's actionability *check* -- the simulated mouse event
        # still really hits whatever's on top (the backdrop), silently
        # closing the modal instead of selecting anything. Dispatching the
        # click directly on the DOM element sidesteps hit-testing entirely.
        suggestions.first.evaluate("el => el.click()")
        self.page.wait_for_timeout(2000)

        # _bb_addressinfo is base64 and stores the formal name ("Gurugram",
        # not "Gurgaon"), so matching city_cfg display_name against it
        # directly would never work. A non-empty pincode cookie is a
        # reliable, simpler signal that some real address got set -- and
        # since we only ever searched for the configured city, it's the
        # right one.
        pin_cookie = next(
            (c for c in self.context.cookies() if c["name"] == "_bb_pin_code"), None
        )
        if pin_cookie and pin_cookie["value"]:
            logger.info("BigBasket pincode set to %s", pin_cookie["value"])
            return True
        return False

    def _fetch_next_data(self, url: str) -> dict | None:
        for attempt in range(1, config.RETRY_ATTEMPTS + 1):
            try:
                resp = self.context.request.get(url, timeout=config.REQUEST_TIMEOUT_MS)
                if resp.status != 200:
                    logger.warning("GET %s -> %d (attempt %d)", url, resp.status, attempt)
                    # Honor the server's own Retry-After on a 429 rather
                    # than guessing a delay.
                    retry_after = resp.headers.get("retry-after")
                    delay = float(retry_after) if retry_after else config.RETRY_BACKOFF_SECONDS * attempt
                    time.sleep(delay)
                    continue
                match = NEXT_DATA_RE.search(resp.text())
                if not match:
                    logger.warning("No __NEXT_DATA__ found at %s (attempt %d)", url, attempt)
                    time.sleep(config.RETRY_BACKOFF_SECONDS)
                    continue
                return json.loads(match.group(1))
            except Exception as e:
                logger.warning("Fetch failed for %s (attempt %d): %s", url, attempt, e)
                time.sleep(config.RETRY_BACKOFF_SECONDS * attempt)
        return None

    def _parse_product(self, raw: dict, label: str, subcategory: str) -> Product | None:
        name = raw.get("desc")
        url_path = raw.get("absolute_url")
        if not name or not url_path:
            return None

        pricing = raw.get("pricing", {}).get("discount", {})
        prim_price = pricing.get("prim_price", {})
        availability = raw.get("availability", {})
        brand = raw.get("brand", {}) or {}

        return Product(
            source="bigbasket",
            city=self.city_cfg["display_name"],
            product_name=name,
            brand=brand.get("name", ""),
            selling_price=Product.parse_price(prim_price.get("sp")),
            mrp=Product.parse_price(pricing.get("mrp")),
            discount=pricing.get("d_text", "") or "",
            availability="Out of Stock" if availability.get("not_for_sale") else "In Stock",
            product_url=f"{BASE_URL}{url_path}",
            category=label,
            subcategory=subcategory,
            pack_size=raw.get("pack_desc") or raw.get("w", ""),
        )

    def get_listings(self, categories: dict) -> list[Product]:
        all_products: list[Product] = []
        for label, path in categories.items():
            url = f"{BASE_URL}/pc/{path}/?nc=nb"
            data = self._fetch_next_data(url)
            if not data:
                logger.error("Skipping BigBasket category '%s': could not fetch/parse", label)
                continue

            try:
                tabs = data["props"]["pageProps"]["SSRData"]["tabs"]
                raw_products = tabs[0]["product_info"]["products"] if tabs else []
            except (KeyError, IndexError):
                logger.warning("Unexpected page shape for BigBasket category '%s'", label)
                continue

            subcategory = path.split("/")[-1]
            parsed = [p for r in raw_products if (p := self._parse_product(r, label, subcategory))]
            logger.info("BigBasket '%s': %d products", label, len(parsed))
            all_products.extend(parsed)

        return all_products

    def close(self) -> None:
        self.context.close()
        self.browser.close()
        self._playwright.stop()
