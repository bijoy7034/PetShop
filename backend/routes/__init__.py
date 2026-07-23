from fastapi import APIRouter

from routes.analytics import router as analytics_router
from routes.auth import router as auth_router
from routes.categories import router as categories_router
from routes.health import router as health_router
from routes.inventory import router as inventory_router
from routes.orders import router as orders_router
from routes.products import router as products_router
from routes.rep_targets import router as rep_targets_router
from routes.sales_achievements import router as sales_achievements_router
from routes.stores import router as stores_router
from routes.subcategories import router as subcategories_router
from routes.users import router as users_router
from routes.visits import router as visits_router

router = APIRouter(prefix="/api")

router.include_router(health_router)
router.include_router(auth_router)
router.include_router(users_router)
router.include_router(categories_router)
router.include_router(subcategories_router)
router.include_router(products_router)
router.include_router(inventory_router)
router.include_router(stores_router)
router.include_router(visits_router)
router.include_router(orders_router)
router.include_router(rep_targets_router)
router.include_router(analytics_router)
router.include_router(sales_achievements_router)
