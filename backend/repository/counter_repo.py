from pymongo import ReturnDocument

from config.config import settings
from config.db import get_db


def _fmt(prefix, seq, width=4):
    return f"{prefix}-{seq:0{width}d}"


class CounterRepository:
    @staticmethod
    def _coll():
        return get_db()[settings.COUNTERS_COLL]

    @staticmethod
    def next_seq(name):
        doc = CounterRepository._coll().find_one_and_update(
            {"_id": name},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(doc["seq"])

    @staticmethod
    def next_code(name, prefix, width=4):
        return _fmt(prefix, CounterRepository.next_seq(name), width)


COUNTERS = {
    "stores": {"prefix": "STR", "width": 4},
    "products": {"prefix": "PRD", "width": 4},
    "orders": {"prefix": "ORD", "width": 4},
}


def next_store_code():
    c = COUNTERS["stores"]
    return CounterRepository.next_code("stores", c["prefix"], c["width"])


def next_product_code():
    c = COUNTERS["products"]
    return CounterRepository.next_code("products", c["prefix"], c["width"])


def next_order_code():
    c = COUNTERS["orders"]
    return CounterRepository.next_code("orders", c["prefix"], c["width"])


def variant_code(product_code, variant_seq, width=2):
    """Format the per-product variant code. The variant seq is stored on
    the product document (`next_variant_seq`) and bumped atomically by
    ProductRepository.add_variant."""
    if not product_code:
        return None
    return f"{product_code}-V{variant_seq:0{width}d}"
