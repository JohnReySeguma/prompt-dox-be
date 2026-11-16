from pydantic import EmailStr, Field, field_validator

from app.schemas.common import CamelModel
from app.schemas.user import UserOut

MIN_PASSWORD = 8


def _check_password(value: str) -> str:
    if len(value) < MIN_PASSWORD:
        raise ValueError(f"Password must be at least {MIN_PASSWORD} characters.")
    if len(value) > 128:
        raise ValueError("Password must be 128 characters or fewer.")
    return value


class RegisterRequest(CamelModel):
    name: str
    email: EmailStr
    password: str

    @field_validator("name")
    @classmethod
    def _valid_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name can't be empty.")
        if len(value) > 80:
            raise ValueError("Name must be 80 characters or fewer.")
        return value

    @field_validator("password")
    @classmethod
    def _valid_password(cls, value: str) -> str:
        return _check_password(value)


class LoginRequest(CamelModel):
    email: EmailStr
    password: str = Field(min_length=1)


class GoogleAuthRequest(CamelModel):
    id_token: str = Field(min_length=1)


class RefreshRequest(CamelModel):
    refresh_token: str = Field(min_length=1)


class ForgotPasswordRequest(CamelModel):
    email: EmailStr


class ResetPasswordRequest(CamelModel):
    token: str = Field(min_length=1)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _valid_password(cls, value: str) -> str:
        return _check_password(value)


class AuthSession(CamelModel):
    user: UserOut
    access_token: str
    refresh_token: str
    expires_in: int
