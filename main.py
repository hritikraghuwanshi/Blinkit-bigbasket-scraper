"""
CLI entrypoint for the Blinkit / BigBasket / Zepto product listing scraper.

Usage:
    python main.py --site blinkit --city gurgaon --format csv
    python main.py --site bigbasket --city gurgaon --format json
    python main.py --site zepto --city gurgaon --format json
    python main.py --site all --city gurgaon --format csv
"""
import argparse
import logging
import sys

import config
from utils.validator import validate_batch
from utils.exporter import export

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")


def run_blinkit(city: str) -> list:
    from scrapers.blinkit_scraper import BlinkitScraper

    with BlinkitScraper(city) as scraper:
        if not scraper.set_location():
            logger.warning("Blinkit location-set failed; results may not reflect %s", city)
        return scraper.get_listings(config.BLINKIT_CATEGORIES)


def run_bigbasket(city: str) -> list:
    from scrapers.bigbasket_scraper import BigBasketScraper

    with BigBasketScraper(city) as scraper:
        if not scraper.set_location():
            logger.warning("BigBasket location-set failed; falling back to default catalog")
        return scraper.get_listings(config.BIGBASKET_CATEGORIES)


def run_zepto(city: str) -> list:
    from scrapers.zepto_scraper import ZeptoScraper

    with ZeptoScraper(city) as scraper:
        if not scraper.set_location():
            logger.warning("Zepto location-set failed; results may not reflect %s", city)
        return scraper.get_listings(config.ZEPTO_CATEGORIES)


SCRAPERS = {
    "blinkit": run_blinkit,
    "bigbasket": run_bigbasket,
    "zepto": run_zepto,
}


def main():
    parser = argparse.ArgumentParser(description="Blinkit / BigBasket / Zepto product listing scraper")
    parser.add_argument("--site", choices=["blinkit", "bigbasket", "zepto", "all"], default="all")
    parser.add_argument("--city", choices=list(config.CITIES.keys()), default=config.DEFAULT_CITY)
    parser.add_argument("--format", choices=["csv", "json"], default="csv")
    parser.add_argument("--output", default=None, help="Output file path (default: <site>_<city>.<format>)")
    args = parser.parse_args()

    sites = list(SCRAPERS.keys()) if args.site == "all" else [args.site]

    all_products = []
    for site in sites:
        logger.info("Starting %s scrape for %s", site, args.city)
        try:
            products = SCRAPERS[site](args.city)
        except Exception as e:
            logger.error("%s scrape failed entirely: %s", site, e)
            continue
        logger.info("%s: %d raw records scraped", site, len(products))
        all_products.extend(products)

    valid_products = validate_batch(all_products)
    if not valid_products:
        logger.error("No valid products scraped, nothing to export")
        sys.exit(1)

    output_path = args.output or f"{args.site}_{args.city}.{args.format}"
    export(valid_products, output_path, args.format)
    logger.info("Done: %d products exported to %s", len(valid_products), output_path)


if __name__ == "__main__":
    main()
