import certifi
from pymongo import MongoClient
from pymongo.database import Database

from config.logging.logger import logger


def _needs_tls(uri):
    """Atlas (mongodb+srv://) and explicit tls=true URIs need a CA bundle;
    plain mongodb://... running locally without TLS must NOT get tlsCAFile —
    passing it forces the handshake and the server rejects with
    UNEXPECTED_EOF_WHILE_READING."""
    u = (uri or "").lower()
    return u.startswith("mongodb+srv://") or "tls=true" in u or "ssl=true" in u


class MongoManager:
    _client: MongoClient | None = None
    _db: Database | None = None

    def connect(self):
        if self._client is not None:
            return
        from config.config import settings

        kwargs = {
            "maxPoolSize": settings.MONGO_MAX_POOL_SIZE,
            "minPoolSize": settings.MONGO_MIN_POOL_SIZE,
            "uuidRepresentation": "standard",
            "serverSelectionTimeoutMS": 5000,
        }
        if _needs_tls(settings.MONGO_URI):
            kwargs["tlsCAFile"] = certifi.where()

        self._client = MongoClient(settings.MONGO_URI, **kwargs)
        self._db = self._client[settings.DB_NAME]

        self._client.admin.command("ping")
        logger.info(f"MongoDB connected: db={settings.DB_NAME}")

        from repository.audit_repo import AuditRepository
        from repository.category_repo import CategoryRepository
        from repository.inventory_repo import InventoryRepository
        from repository.order_repo import OrderRepository
        from repository.product_repo import ProductRepository
        from repository.rep_target_repo import RepTargetRepository
        from repository.session_repo import SessionRepository
        from repository.store_repo import StoreRepository
        from repository.subcategory_repo import SubcategoryRepository
        from repository.user_repo import UserRepository
        from repository.visit_repo import VisitRepository

        UserRepository.ensure_indexes()
        SessionRepository.ensure_indexes()
        AuditRepository.ensure_indexes()
        CategoryRepository.ensure_indexes()
        SubcategoryRepository.ensure_indexes()
        ProductRepository.ensure_indexes()
        InventoryRepository.ensure_indexes()
        StoreRepository.ensure_indexes()
        VisitRepository.ensure_indexes()
        OrderRepository.ensure_indexes()
        RepTargetRepository.ensure_indexes()
        logger.info(
            "Indexes ensured: users, sessions, audit_log, categories, "
            "subcategories, products, inventory, stores, visits, orders, rep_targets"
        )

    def ping(self):
        if self._client is None:
            return False
        try:
            self._client.admin.command("ping")
            return True
        except Exception as e:
            logger.error(f"MongoDB ping failed: {e}")
            return False
    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None
            self._db = None
            logger.info("MongoDB closed")

    @property
    def db(self):
        if self._db is None:
            raise RuntimeError("MongoManager not connected. Call connect() first.")
        return self._db


mongo_manager = MongoManager()


def get_db():
    return mongo_manager.db
