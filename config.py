"""
Central configuration: city coordinates, scrape scope, and browser settings.

Adding a new city only requires a new entry in CITIES -- no scraper code changes.
"""

# Each city needs a representative lat/long (used for geolocation permission
# and as a fallback) plus a human search string used to type into each site's
# delivery-location search box.
CITIES = {
    "gurgaon": {
        "display_name": "Gurgaon",
        "search_query": "Sector 29, Gurgaon",
        "latitude": 28.4595,
        "longitude": 77.0266,
    },
    "mumbai": {
        "display_name": "Mumbai",
        "search_query": "Bandra West, Mumbai",
        "latitude": 19.0596,
        "longitude": 72.8295,
    },
    "bangalore": {
        "display_name": "Bangalore",
        "search_query": "Koramangala, Bangalore",
        "latitude": 12.9352,
        "longitude": 77.6245,
    },
}

DEFAULT_CITY = "gurgaon"

# Categories to scrape per site. Keys are internal labels used for logging.
# For Blinkit, values are substrings matched against real /dc/... hrefs found
# on the live /categories page (Blinkit doesn't expose a stable direct URL
# scheme, so we discover the current href rather than hardcoding one). Any
# label whose substring isn't found on a given run is skipped with a warning
# instead of failing the whole scrape -- category slugs do change over time.
BLINKIT_CATEGORIES = {
    "vegetables_fruits": "vegetables-fruits",
    "atta_rice_dal": "atta-rice-dal",
    "dairy_bread_eggs": "dairy-bread-eggs",
    "bath_body": "bath-body",
}

BIGBASKET_CATEGORIES = {
    "fresh_vegetables": "fruits-vegetables/fresh-vegetables",
    "fresh_fruits": "fruits-vegetables/fresh-fruits",
    "dairy": "bakery-cakes-dairy/dairy",
    "snacks": "snacks-branded-foods/snacks-namkeen",
}

# Cap on how many listing pages to pull per category. None = no cap (full
# pagination). Kept low by default so a demo run stays fast and low-risk
# for bot-detection; raise it for a fuller data pull.
MAX_PAGES_PER_CATEGORY = 3
PRODUCTS_PER_PAGE = 15

# Real installed Chrome is used instead of Playwright's bundled Chromium --
# BigBasket's Akamai bot-protection blocks the bundled Chromium fingerprint
# even with stealth patches, but accepts a genuine Chrome build. See README
# for the reasoning; if "chrome" channel isn't installed on a machine, fall
# back to None (bundled Chromium) and expect BigBasket location-set to be
# unreliable.
BROWSER_CHANNEL = "chrome"

REQUEST_TIMEOUT_MS = 30000
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2
