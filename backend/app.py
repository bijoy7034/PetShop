from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from config.config import settings
from config.db import mongo_manager
from config.logging.logger import logger
from middleware.csrf import CSRFMiddleware
from middleware.request_id import RequestIDTrackingMiddleware
from routes import router
from scripts.seed_admin import seed_first_admin
from utils.validation import validation_exception_handler


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Pet Shop Backend")
    mongo_manager.connect()
    seed_first_admin()
    yield
    mongo_manager.close()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Pet Shop Inventory Backend",
    version="0.1.0",
    description="Auth, RBAC, and user management for the pet shop inventory system.",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Accept",
        "X-Request-ID",
        settings.CSRF_HEADER_NAME,
    ],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(CSRFMiddleware)
app.add_middleware(RequestIDTrackingMiddleware)

app.add_exception_handler(RequestValidationError, validation_exception_handler)

app.include_router(router)
