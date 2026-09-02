# Blinkit / BigBasket / Zepto Product Listing Scraper

Scrapes product listings from Blinkit, BigBasket, and Zepto for a given city
(Gurgaon by default), extracts the required fields, validates them, and exports
to CSV or JSON.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium       # bundled browser, used for Blinkit
playwright install chrome         # real Chrome channel, used for BigBasket (see below)
```

Both scrapers use Playwright, so a Chromium/Chrome binary must be installed via
`playwright install` before running.

## Usage

```bash
python main.py --site blinkit --city gurgaon --format csv
python main.py --site bigbasket --city gurgaon --format json
python main.py --site zepto --city gurgaon --format json
python main.py --site all --city gurgaon --format csv
```

`--site` accepts `blinkit`, `bigbasket`, `zepto`, or `all` (default). `--format` accepts
`csv` or `json` (default `csv`). `--output` overrides the output file path
(default: `<site>_<city>.<format>`). `--city` accepts any key in `config.CITIES`.

Sample output from a real run is included: `sample_output.csv`, `sample_output.json`.

## Adding a new city

Add an entry to `CITIES` in `config.py` with a display name, a search string for
that site's location picker, and lat/long coordinates. No scraper code changes
are needed — this is what makes the solution scalable across cities.

## Architecture

```
main.py                 CLI entrypoint, orchestrates scrapers + export
config.py                City list, category list, scrape limits
models/product.py        Shared Product record both scrapers produce
scrapers/
  base_scraper.py         Common interface (set_location, get_listings, close)
  blinkit_scraper.py       Blinkit implementation
  bigbasket_scraper.py     BigBasket implementation
  zepto_scraper.py         Zepto implementation
utils/
  validator.py             Drops records missing required fields
  exporter.py               CSV/JSON writer
```

Both scrapers intercept the JSON API responses each site's own frontend uses for
its product grid, rather than parsing rendered HTML/DOM. This is faster and more
robust to frontend markup changes than CSS-selector scraping.

## Why a browser at all, and why two different setups

Both sites require a delivery location to be set before they'll return real
listings, and that's a JS-driven UI interaction on both, so this uses Playwright
rather than a plain HTTP client. Two site-specific things were confirmed by
testing against the live sites before writing this code, not assumed:

- **Blinkit** sits behind bot-protection that returns 403 on any non-browser
  request (plain `curl` included). Playwright's bundled Chromium works, but only
  after masking a few obvious automation fingerprints (`navigator.webdriver`,
  missing `plugins`) that would otherwise get it blocked too.
- **BigBasket** is the opposite: plain HTTP requests are allowed (its product
  data is server-rendered JSON, no browser needed to *read* it), but its
  bot-protection blocks Playwright's *bundled* Chromium specifically, even with
  the same stealth patches, while a genuine installed Chrome build
  (`channel="chrome"`) is accepted. So BigBasket uses `channel="chrome"` for the
  location-set step, then reuses that session's cookies for fast plain requests
  to fetch category pages.

Both site-specific behaviors were verified by hand (checking actual HTTP status
codes and response bodies) rather than guessed, since getting this wrong would
have meant building on a false assumption.

**Zepto** sits behind AWS WAF Bot Control (`x-amzn-waf-action: challenge` on a
plain `curl`, 202 with an empty body). Unlike Blinkit and BigBasket, Playwright's
*bundled* Chromium with the same stealth patches clears this challenge on its
own -- no special browser channel needed. Its category pages, though, are
Next.js Server Components with no clean listing JSON to intercept (only an RSC
stream), so this scraper searches representative terms
(`config.ZEPTO_CATEGORIES`) against its search API instead of browsing real
category URLs -- that endpoint returns a clean, rich product grid. Calling that
endpoint with a hand-crafted `fetch()` consistently failed even with a valid
session cookie, so the scraper drives the real search UI/URL and reads the
response the site's own frontend triggers, the same approach used for Blinkit.

## Known limitations

- **Category coverage**: each site scrapes a small, configurable set of
  categories (`BLINKIT_CATEGORIES` / `BIGBASKET_CATEGORIES` / `ZEPTO_CATEGORIES`
  in `config.py`), not the full catalog. The assignment doesn't specify
  exhaustive coverage, and scraping every category on every run would be slow
  and increase bot-detection risk for little added value in a demo. Add more
  entries to scrape more.
- **Pagination cap**: `MAX_PAGES_PER_CATEGORY` in `config.py` (default 3) caps
  how many pages are pulled per category for Blinkit and BigBasket, for the
  same reason. Set to `None` for full pagination. Zepto doesn't use this --
  its search results load via client-side infinite scroll that didn't
  reliably trigger further pages in headless testing, so each Zepto category
  term returns whatever a single search response holds (~30 products in
  testing), not a capped multi-page pull like the other two sites.
- **Blinkit category discovery**: category slugs are matched against the live
  `/categories` page rather than hardcoded, since Blinkit doesn't expose a
  stable direct URL scheme. If a configured slug no longer matches any current
  category, it's skipped with a logged warning rather than failing the run.
- **BigBasket location-set retries**: BigBasket's popup timing and click
  handling in headless mode needed specific workarounds (see comments in
  `bigbasket_scraper.py`). If a future frontend change breaks the flow again,
  `set_location()` retries `RETRY_ATTEMPTS` times and falls back to
  BigBasket's default (non-hyperlocal) catalog rather than crashing — logged
  clearly, not silent.
- **Brand field**: genuinely empty for some products on both sites (e.g. plain
  produce like a single banana) — this is real source data, not a parsing gap;
  branded items (packaged snacks, dairy, etc.) do carry a brand value.

## Error handling

Network calls retry up to `RETRY_ATTEMPTS` times with a backoff delay. A
category that fails entirely is skipped (logged) rather than aborting the whole
run. Records missing a name, URL, or any usable price are dropped during
validation rather than exported. Nothing fails silently -- every skip/drop is
logged.
