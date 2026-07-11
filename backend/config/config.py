import json

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from config.logging.logger import logger


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("CORS_ALLOW_ORIGINS", mode="before")
    @classmethod
    def _parse_origins(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("["):
                return json.loads(v)
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

    # Runtime
    MODE: str = "development"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    # MongoDB
    MONGO_URI: str = "mongodb://localhost:27017"
    DB_NAME: str = "petshop"
    MONGO_MAX_POOL_SIZE: int = 50
    MONGO_MIN_POOL_SIZE: int = 5

    # CORS
    CORS_ALLOW_ORIGINS: list[str] = ["http://localhost:5173"]

    # JWT
    JWT_SECRET: str = "change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_AUDIENCE: str | None = None

    ACCESS_TOKEN_TTL_MINUTES: int = 15
    REFRESH_TOKEN_TTL_DAYS: int = 14

    # Cookies
    COOKIE_SECURE: bool = True
    COOKIE_SAMESITE: str = "lax"
    COOKIE_DOMAIN: str | None = None
    ACCESS_COOKIE_NAME: str = "petshop_access"
    REFRESH_COOKIE_NAME: str = "petshop_refresh"
    CSRF_COOKIE_NAME: str = "petshop_csrf"
    CSRF_HEADER_NAME: str = "X-CSRF-Token"

    # Collections
    USERS_COLL: str = "users"
    SESSIONS_COLL: str = "sessions"
    AUDIT_COLL: str = "audit_log"

    # First-boot admin bootstrap. Empty → skipped.
    ADMIN_EMAIL: str = ""
    ADMIN_PASSWORD: str = ""
    ADMIN_NAME: str = ""


settings = Settings()
logger.info(f"Loaded settings: MODE={settings.MODE}, DB_NAME={settings.DB_NAME}")
