"""Validates scraped Product records before they're exported."""
import logging
from models.product import Product

logger = logging.getLogger(__name__)


def is_valid(product: Product) -> bool:
    """A record is kept only if it has a name, a URL, and at least one usable price."""
    if not product.product_name or not product.product_name.strip():
        logger.warning("Dropping record: empty product_name (url=%s)", product.product_url)
        return False

    if not product.product_url or not product.product_url.strip():
        logger.warning("Dropping record: empty product_url (name=%s)", product.product_name)
        return False

    if product.selling_price is None and product.mrp is None:
        logger.warning("Dropping record: no usable price (name=%s)", product.product_name)
        return False

    if product.selling_price is not None and product.selling_price < 0:
        logger.warning("Dropping record: negative selling_price (name=%s)", product.product_name)
        return False

    return True


def validate_batch(products: list[Product]) -> list[Product]:
    valid = [p for p in products if is_valid(p)]
    dropped = len(products) - len(valid)
    if dropped:
        logger.info("Validation dropped %d of %d records", dropped, len(products))
    return valid
