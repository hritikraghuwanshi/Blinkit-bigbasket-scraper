"""
Zepto scraper.

Two things were verified manually before writing this class (see README):
  1. Zepto sits behind AWS WAF Bot Control (`x-amzn-waf-action: challenge` on
     a plain `curl`, 202/empty body). Playwright's bundled Chromium clears
     the JS challenge on its own, no stealth patches needed beyond the usual
     `navigator.webdriver` masking -- confirmed by loading the real page and
     seeing it render with a 200 API response behind it.
  2. Zepto's category *pages* (`zepto.com/cn/...`) are Next.js Server
     Components -- there's no clean listing JSON to intercept there, only an
     RSC stream. Its search endpoint
     (`bff-gateway.zepto.com/user-search-service/api/v3/search`), by
     contrast, returns a rich, clean JSON product grid. So this scraper
     searches representative terms (config.ZEPTO_CATEGORIES) instead of
     browsing real category URLs -- documented in config.py.

Calling the search endpoint with our own crafted `fetch()` (bypassing the
site's own request flow) consistently failed with a generic network error,
even with a valid session -- some client-side request wrapper appears to be
involved, not just cookies. So, like Blinkit, this drives the real search UI
(type + Enter) and reads the response Zepto's own frontend triggers, rather
than issuing hand-built requests.
"""
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

BASE_URL = "https://www.zeptonow.com"
SEARCH_API_PATH = "user-search-service/api/v3/search"


def _slugify(name: str) -> str:
    """Build the same URL-safe slug Zepto's own PDP links (`/pn/<slug>/pvid/<id>`) use."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "-"


class ZeptoScraper(BaseScraper):
    def __init__(self, city: str):
        super().__init__(city)
        self.city_cfg = config.CITIES[city]
        self._playwright = sync_playwright().start()
        self.browser = self._playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self.context = self.browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1400, "height": 1000},
            locale="en-IN",
        )
        self.context.add_init_script(STEALTH_JS)
        self.page = self.context.new_page()

    def set_location(self) -> bool:
        for attempt in range(1, config.RETRY_ATTEMPTS + 1):
            try:
                if self._try_set_location_once():
                    logger.info("Zepto location set to %s", self.city_cfg["display_name"])
                    return True
                logger.warning("set_location attempt %d did not confirm", attempt)
            except Exception as e:
                logger.warning("set_location attempt %d failed: %s", attempt, e)
            time.sleep(config.RETRY_BACKOFF_SECONDS)
        return False

    def _try_set_location_once(self) -> bool:
        self.page.goto(BASE_URL, timeout=config.REQUEST_TIMEOUT_MS, wait_until="networkidle")
        self.page.click("[data-testid='user-address']", timeout=config.REQUEST_TIMEOUT_MS)

        search_box = self.page.get_by_placeholder("Search a new address")
        search_box.wait_for(state="visible", timeout=8000)
        search_box.fill(self.city_cfg["search_query"])

        # Suggestion rows have no stable class (hashed CSS-module names that
        # change per deploy) but live in a container with a real test id --
        # its first row is the first suggestion.
        first_row = self.page.locator("[data-testid='address-search-container'] > div > div").first
        first_row.wait_for(state="visible", timeout=8000)

        with self.page.expect_response(
            lambda r: "user/customer/address/location" in r.url, timeout=8000
        ) as resp_info:
            first_row.click()
        return resp_info.value.status == 200

    def _search_page(self, term: str) -> list[dict]:
        """Drive the real search UI for `term` and return the PRODUCT_GRID items
        from whichever response Zepto's own frontend fires for it."""
        captured = []

        def on_response(resp):
            if SEARCH_API_PATH not in resp.url or "filters" in resp.url:
                return
            if resp.request.method != "POST":
                return
            try:
                captured.append(resp.json())
            except Exception:
                pass

        self.page.on("response", on_response)
        try:
            # A fresh navigation (rather than driving the search box widget)
            # sidesteps re-deriving the exact click/keyboard sequence for the
            # search UI and still fires the same real request.
            self.page.goto(
                f"{BASE_URL}/search?query={term}",
                timeout=config.REQUEST_TIMEOUT_MS,
                wait_until="networkidle",
            )
            self.page.wait_for_timeout(1500)
        finally:
            self.page.remove_listener("response", on_response)

        items = []
        for body in captured:
            for widget in body.get("layout", []):
                if widget.get("widgetId") != "PRODUCT_GRID":
                    continue
                items.extend(widget.get("data", {}).get("resolver", {}).get("data", {}).get("items", []))
        return items

    def _parse_item(self, item: dict, label: str) -> Product | None:
        pr = item.get("productResponse", {})
        product = pr.get("product", {})
        variant = pr.get("productVariant", {})

        name = product.get("name")
        variant_id = variant.get("id")
        if not name or not variant_id:
            return None

        mrp = pr.get("mrp")
        selling_price = pr.get("sellingPrice")
        discount_percent = pr.get("discountPercent") or 0

        return Product(
            source="zepto",
            city=self.city_cfg["display_name"],
            product_name=name,
            brand=product.get("brand") or "",
            # Zepto prices are integer paise (49500 == Rs 495.00).
            selling_price=Product.parse_price(selling_price / 100) if selling_price is not None else None,
            mrp=Product.parse_price(mrp / 100) if mrp is not None else None,
            discount=f"{discount_percent}% OFF" if discount_percent else "",
            availability="Out of Stock" if pr.get("outOfStock") else "In Stock",
            product_url=f"{BASE_URL}/pn/{_slugify(name)}/pvid/{variant_id}",
            category=label,
            subcategory=pr.get("primarySubcategoryName") or label,
            pack_size=variant.get("formattedPacksize", ""),
        )

    def get_listings(self, categories: dict) -> list[Product]:
        all_products: list[Product] = []
        for label, term in categories.items():
            try:
                items = self._search_page(term)
            except Exception as e:
                logger.error("Failed to scrape Zepto category '%s' (term '%s'): %s", label, term, e)
                continue

            if not items:
                logger.warning("No results captured for Zepto category '%s' (term '%s')", label, term)
                continue

            parsed = [p for i in items if (p := self._parse_item(i, label))]
            logger.info("Zepto '%s': %d products", label, len(parsed))
            all_products.extend(parsed)

        return all_products

    def close(self) -> None:
        self.context.close()
        self.browser.close()
        self._playwright.stop()
