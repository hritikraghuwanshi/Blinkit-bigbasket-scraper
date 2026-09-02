"""Common interface every site-specific scraper implements."""
from abc import ABC, abstractmethod
from models.product import Product


class BaseScraper(ABC):
    def __init__(self, city: str):
        self.city = city

    @abstractmethod
    def set_location(self) -> bool:
        """Set the delivery location for self.city. Returns True on confirmed success."""
        raise NotImplementedError

    @abstractmethod
    def get_listings(self, categories: dict) -> list[Product]:
        """Fetch and parse product listings for the given {label: category_slug} map."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Release any browser/network resources."""
        raise NotImplementedError

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
