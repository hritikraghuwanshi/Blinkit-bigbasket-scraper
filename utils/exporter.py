"""Exports a list of Product records to CSV or JSON."""
import csv
import json
import logging
from models.product import Product

logger = logging.getLogger(__name__)

FIELDNAMES = [
    "source", "city", "product_name", "brand", "selling_price", "mrp",
    "discount", "availability", "product_url", "category", "subcategory",
    "pack_size",
]


def export_csv(products: list[Product], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for p in products:
            writer.writerow(p.to_dict())
    logger.info("Wrote %d records to %s", len(products), path)


def export_json(products: list[Product], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([p.to_dict() for p in products], f, indent=2, ensure_ascii=False)
    logger.info("Wrote %d records to %s", len(products), path)


def export(products: list[Product], path: str, fmt: str) -> None:
    if fmt == "csv":
        export_csv(products, path)
    elif fmt == "json":
        export_json(products, path)
    else:
        raise ValueError(f"Unsupported export format: {fmt}")
