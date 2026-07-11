from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from enums.user import Role, UserStatus


class UserCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    role: Role
    phone: str | None = Field(default=None, max_length=32)
    password: str | None = Field(default=None, min_length=10)


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: Role | None = None
    phone: str | None = Field(default=None, max_length=32)
    status: UserStatus | None = None
    password: str | None = Field(default=None, min_length=10)


class User(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="_id")
    email: EmailStr
    name: str
    role: Role
    phone: str | None = None
    status: UserStatus = UserStatus.ACTIVE
    must_change_password: bool = False
    last_seen_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class StoredUser(User):
    password_hash: str


class UserListResponse(BaseModel):
    items: list[User]
    total: int
    page: int
    page_size: int


class UserCreateResponse(BaseModel):
    user: User
    temporary_password: str | None = None
