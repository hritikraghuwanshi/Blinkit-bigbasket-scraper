"""
Blinkit scraper.

Blinkit renders through client-side JS and sits behind bot-detection that
blocks a plain HTTP client, so this uses a real (headless) browser. Masking
a few obvious automation fingerprints (navigator.webdriver, missing plugins)
is enough to get past it with the bundled Chromium -- verified manually
before writing this class.

Rather than scraping rendered DOM cards, this intercepts the JSON responses
Blinkit's own frontend requests for its product grid (`/v1/layout/listing_widgets`).
That's more efficient (no HTML parsing) and more robust to frontend markup
changes than CSS-selector scraping.
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

BASE_URL = "https://blinkit.com"


def _slugify(name: str) -> str:
    """Build the same URL-safe slug Blinkit's own PDP links use in place of the product name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "-"


class BlinkitScraper(BaseScraper):
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
                self.page.goto(BASE_URL, timeout=config.REQUEST_TIMEOUT_MS, wait_until="domcontentloaded")
                self.page.wait_for_timeout(2000)

                box = self.page.get_by_placeholder("search delivery location")
                box.click()
                box.type(self.city_cfg["search_query"], delay=80)
                self.page.wait_for_timeout(2000)

                rows = self.page.locator("[class*='LocationSearchList__LocationListContainer']")
                if rows.count() == 0:
                    logger.warning("No location suggestions found (attempt %d)", attempt)
                    continue
                rows.first.click()
                self.page.wait_for_timeout(2500)

                # Blinkit stores the formal city name ("Gurugram", not
                # "Gurgaon") in this cookie, so comparing against the
                # configured display name would never match. A non-empty
                # locality cookie is enough -- we only ever searched for
                # the configured city, so any value confirms success.
                confirmed = any(
                    c["value"]
                    for c in self.context.cookies()
                    if "locality" in c["name"].lower()
                )
                if confirmed:
                    logger.info("Blinkit location set to %s", self.city_cfg["display_name"])
                    return True
                logger.warning("Location click didn't confirm (attempt %d)", attempt)
            except Exception as e:
                logger.warning("set_location attempt %d failed: %s", attempt, e)
                time.sleep(config.RETRY_BACKOFF_SECONDS)
        return False

    def _find_category_hrefs(self, categories: dict) -> dict:
        """Map each requested category label to a real /dc/ href discovered on the live site."""
        self.page.goto(f"{BASE_URL}/categories", timeout=config.REQUEST_TIMEOUT_MS, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2000)
        hrefs = self.page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
        dc_hrefs = [h for h in hrefs if h and "/dc/" in h]

        resolved = {}
        for label, slug in categories.items():
            match = next((h for h in dc_hrefs if slug in h), None)
            if match:
                resolved[label] = match
            else:
                logger.warning("Category '%s' (slug '%s') not found on /categories page, skipping", label, slug)
        return resolved

    def _next_page_body(self, template_body: dict, offset: int) -> dict:
        body = dict(template_body)
        body["offset"] = str(offset)
        body["limit"] = str(config.PRODUCTS_PER_PAGE)
        # Echoed metadata from the previous response, not needed to fetch
        # the next page.
        body.pop("total_pagination_items", None)
        body.pop("total_entities_processed", None)
        return body

    def _parse_snippet(self, snippet: dict, label: str, subcategory: str) -> Product | None:
        data = snippet.get("data", {})
        product_id = data.get("identity", {}).get("id")
        name = data.get("name", {}).get("text")
        if not product_id or not name:
            return None  # not a product card (banner/filter widget)

        mrp = Product.parse_price(data.get("mrp", {}).get("text"))
        selling_price = Product.parse_price(data.get("normal_price", {}).get("text"))
        discount = data.get("offer_tag", {}).get("title", {}).get("text", "").replace("\n", " ").strip()
        inventory = data.get("inventory", 0)

        return Product(
            source="blinkit",
            city=self.city_cfg["display_name"],
            product_name=name,
            brand=data.get("brand", "") or "",
            selling_price=selling_price,
            mrp=mrp,
            discount=discount,
            availability="In Stock" if inventory and inventory > 0 else "Out of Stock",
            product_url=f"{BASE_URL}/prn/{_slugify(name)}/prid/{product_id}",
            category=label,
            subcategory=subcategory,
            pack_size=data.get("variant", {}).get("text", ""),
        )

    def _scrape_category(self, label: str, href: str) -> list[Product]:
        products: list[Product] = []
        captured_bodies = []  # list of (parsed_request_body, response_json)

        def on_response(resp):
            if "/v1/layout/listing_widgets" not in resp.url:
                return
            if resp.request.method != "POST" or "json" not in resp.headers.get("content-type", ""):
                return
            try:
                req_body = json.loads(resp.request.post_data or "{}")
                captured_bodies.append((req_body, resp.json()))
            except Exception:
                pass

        self.page.on("response", on_response)
        try:
            self.page.goto(f"{BASE_URL}{href}", timeout=config.REQUEST_TIMEOUT_MS, wait_until="domcontentloaded")
            self.page.wait_for_timeout(3000)
        finally:
            self.page.remove_listener("response", on_response)

        if not captured_bodies:
            logger.warning("No listing API captured for category '%s'", label)
            return products

        # The real endpoint is POST with pagination fields in a JSON body
        # (not URL query params, which turned out to be vestigial/unused
        # by the server) and requires lat/lon headers -- discovered by
        # inspecting an actual captured request, not guessed. The initial
        # page load already triggers 1-2 of these calls itself (the site's
        # own virtualized list prefetches ahead); use those real pages
        # before issuing any of our own.
        template_body = captured_bodies[0][0]
        subcategory = href.strip("/").split("/")[-2] if "/" in href.strip("/") else label
        page_count = 0
        offset = 0

        for req_body, resp_json in captured_bodies:
            if config.MAX_PAGES_PER_CATEGORY is not None and page_count >= config.MAX_PAGES_PER_CATEGORY:
                break
            snippets = resp_json.get("response", {}).get("snippets", [])
            page_products = [p for s in snippets if (p := self._parse_snippet(s, label, subcategory))]
            if not page_products:
                continue
            products.extend(page_products)
            page_count += 1
            offset = max(offset, int(req_body.get("offset", 0)) + config.PRODUCTS_PER_PAGE)

        while config.MAX_PAGES_PER_CATEGORY is None or page_count < config.MAX_PAGES_PER_CATEGORY:
            body = self._next_page_body(template_body, offset)
            try:
                result = self.page.evaluate(
                    """async ({url, body, lat, lon}) => {
                        const res = await fetch(url, {
                            method: 'POST',
                            credentials: 'include',
                            headers: {
                                'content-type': 'application/json',
                                'lat': String(lat),
                                'lon': String(lon),
                                'access_token': 'null',
                            },
                            body: JSON.stringify(body),
                        });
                        if (!res.ok) return { __status: res.status };
                        return await res.json();
                    }""",
                    {
                        "url": f"{BASE_URL}/v1/layout/listing_widgets",
                        "body": body,
                        "lat": self.city_cfg["latitude"],
                        "lon": self.city_cfg["longitude"],
                    },
                )
            except Exception as e:
                logger.warning("Listing page fetch error for %s: %s", label, e)
                break
            if result.get("__status"):
                logger.warning("Listing page fetch failed (%d) for %s", result["__status"], label)
                break

            snippets = result.get("response", {}).get("snippets", [])
            if not snippets:
                break
            page_products = [p for s in snippets if (p := self._parse_snippet(s, label, subcategory))]
            if not page_products:
                break

            products.extend(page_products)
            offset += config.PRODUCTS_PER_PAGE
            page_count += 1

        logger.info("Blinkit '%s': %d products from %d page(s)", label, len(products), page_count)
        return products

    def get_listings(self, categories: dict) -> list[Product]:
        resolved = self._find_category_hrefs(categories)
        all_products: list[Product] = []
        for label, href in resolved.items():
            try:
                all_products.extend(self._scrape_category(label, href))
            except Exception as e:
                logger.error("Failed to scrape Blinkit category '%s': %s", label, e)
        return all_products

    def close(self) -> None:
        self.context.close()
        self.browser.close()
        self._playwright.stop()
