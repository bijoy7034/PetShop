from config.config import settings
from config.logging.logger import logger
from enums.user import Role
from repository.user_repo import UserRepository
from utils.auth import hash_password


def seed_first_admin():
    email = (settings.ADMIN_EMAIL or "").strip().lower()
    if not email:
        logger.info("Seed admin skipped: ADMIN_EMAIL not set")
        return

    if UserRepository.by_email(email):
        logger.info(f"Seed admin skipped: {email} already exists")
        return

    password = settings.ADMIN_PASSWORD or ""
    if len(password) < 10:
        logger.warning(
            "Seed admin skipped: ADMIN_PASSWORD must be at least 10 characters"
        )
        return

    name = (settings.ADMIN_NAME or "").strip() or "Owner"
    UserRepository.insert(
        email=email,
        name=name,
        role=Role.ADMIN.value,
        password_hash=hash_password(password),
        must_change_password=True,
    )
    logger.info(f"Seeded first admin: {email} (must change password on first login)")
