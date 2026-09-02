"""Shared product record produced by every scraper, regardless of source site."""
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Product:
    source: str  # "blinkit" or "bigbasket"
    city: str
    product_name: str
    brand: str
    selling_price: Optional[float]
    mrp: Optional[float]
    discount: str
    availability: str
    product_url: str
    category: str
    subcategory: str
    pack_size: str

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def parse_price(raw) -> Optional[float]:
        """Turn '₹44', '44.0', 44, or None into a float, or None if unparseable."""
        if raw is None:
            return None
        if isinstance(raw, (int, float)):
            return float(raw)
        cleaned = str(raw).replace("₹", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
