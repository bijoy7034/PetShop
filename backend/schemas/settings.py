from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ThemeColors(BaseModel):
    """Frontend theme accents. Six named slots covering the common
    dark-theme palette used by the app shell + dashboard tiles."""
    primary: str | None = None
    accent: str | None = None
    background: str | None = None
    surface: str | None = None
    text: str | None = None
    danger: str | None = None


class ClientSettings(BaseModel):
    """Singleton doc — the developer console reads/writes it, every
    other user just reads it to render branding, contact info, and
    theme."""
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default="app_settings", alias="_id")
    company_name: str | None = None
    address: str | None = None
    logo_url: str | None = None
    favicon_url: str | None = None
    gst_number: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    website: str | None = None
    support_email: EmailStr | None = None
    theme_colors: ThemeColors | None = None
    currency: str = "INR"
    currency_symbol: str = "₹"
    # Free-form key/value bag for values the developer console adds
    # later without a schema migration.
    extras: dict = {}
    updated_at: datetime | None = None
    updated_by_id: str | None = None
    updated_by_name: str | None = None


class ClientSettingsUpdate(BaseModel):
    company_name: str | None = None
    address: str | None = None
    logo_url: str | None = None
    favicon_url: str | None = None
    gst_number: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    website: str | None = None
    support_email: EmailStr | None = None
    theme_colors: ThemeColors | None = None
    currency: str | None = None
    currency_symbol: str | None = None
    extras: dict | None = None
