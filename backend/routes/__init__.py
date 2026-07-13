from fastapi import APIRouter

from routes.attendance import router as attendance_router
from routes.auth import router as auth_router
from routes.categories import router as categories_router
from routes.health import router as health_router
from routes.orders import router as orders_router
from routes.products import router as products_router
from routes.stores import router as stores_router
from routes.subcategories import router as subcategories_router
from routes.users import router as users_router

router = APIRouter(prefix="/api")

router.include_router(health_router)
router.include_router(auth_router)
router.include_router(users_router)
router.include_router(categories_router)
router.include_router(subcategories_router)
router.include_router(products_router)
router.include_router(stores_router)
router.include_router(attendance_router)
router.include_router(orders_router)
