from pymongo import MongoClient
from pymongo.database import Database
import certifi

from config.logging.logger import logger


class MongoManager:
    _client: MongoClient | None = None
    _db: Database | None = None

    def connect(self):
        if self._client is not None:
            return
        from config.config import settings

        self._client = MongoClient(
            settings.MONGO_URI,
            maxPoolSize=settings.MONGO_MAX_POOL_SIZE,
            minPoolSize=settings.MONGO_MIN_POOL_SIZE,
            uuidRepresentation="standard",
            tlsCAFile=certifi.where(),
        )
        self._db = self._client[settings.DB_NAME]

        self._client.admin.command("ping")
        logger.info(f"MongoDB connected: db={settings.DB_NAME}")

        from repository.audit_repo import AuditRepository
        from repository.session_repo import SessionRepository
        from repository.user_repo import UserRepository

        UserRepository.ensure_indexes()
        SessionRepository.ensure_indexes()
        AuditRepository.ensure_indexes()
        logger.info("Indexes ensured: users, sessions, audit_log")

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
