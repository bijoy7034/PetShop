from pydantic import BaseModel, EmailStr, Field

from schemas.user import User


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10)


class TokenResponse(BaseModel):
    token_type: str = "cookie"
    expires_in_seconds: int
    user: User


class MeResponse(BaseModel):
    user: User
