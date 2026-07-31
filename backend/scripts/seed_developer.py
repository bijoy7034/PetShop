from config.config import settings
from config.logging.logger import logger
from enums.user import Role
from repository.user_repo import UserRepository
from utils.auth import hash_password


def seed_first_developer():
    """Same shape as seed_first_admin, but for the developer role.
    Skipped in production (once the account is provisioned, leave the
    env vars blank so a rogue restart can't create additional
    developers)."""
    email = (settings.DEVELOPER_EMAIL or "").strip().lower()
    if not email:
        logger.info("Seed developer skipped: DEVELOPER_EMAIL not set")
        return

    if UserRepository.by_email(email):
        logger.info(f"Seed developer skipped: {email} already exists")
        return

    password = settings.DEVELOPER_PASSWORD or ""
    if len(password) < 10:
        logger.warning(
            "Seed developer skipped: DEVELOPER_PASSWORD must be at least 10 characters"
        )
        return

    name = (settings.DEVELOPER_NAME or "").strip() or "Developer"
    UserRepository.insert(
        email=email,
        name=name,
        role=Role.DEVELOPER.value,
        password_hash=hash_password(password),
        must_change_password=True,
    )
    logger.info(
        f"Seeded first developer: {email} (must change password on first login)"
    )
