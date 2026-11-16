from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.core.errors import AppError, demo_restricted, validation_error
from app.models import User
from app.schemas.user import (
    GoogleConnectRequest,
    PlanRequest,
    SettingsOut,
    SettingsPatch,
    UpdateMeRequest,
    UsageOut,
    UserOut,
)
from app.services.google_auth import verify_google_id_token
from app.services.users import build_usage, get_user_by_email, to_settings_out, to_user_out

router = APIRouter(prefix="/me", tags=["account"])


@router.get("", response_model=UserOut)
async def get_me(user: CurrentUser) -> UserOut:
    return to_user_out(user)


@router.patch("", response_model=UserOut)
async def update_me(body: UpdateMeRequest, user: CurrentUser, db: DbSession) -> UserOut:
    if body.email is not None:
        if user.is_demo:
            raise demo_restricted("Demo accounts can't change their email address.")
        email = str(body.email).lower()
        if email != user.email:
            if await get_user_by_email(db, email):
                raise AppError(409, "email_taken", "An account with this email already exists.")
            user.email = email
    if body.name is not None:
        user.name = body.name
    await db.commit()
    return to_user_out(user)


@router.get("/usage", response_model=UsageOut)
async def get_usage(user: CurrentUser, db: DbSession) -> UsageOut:
    return await build_usage(db, user)


@router.get("/settings", response_model=SettingsOut)
async def get_settings(user: CurrentUser) -> SettingsOut:
    return to_settings_out(user)


@router.patch("/settings", response_model=SettingsOut)
async def update_settings(body: SettingsPatch, user: CurrentUser, db: DbSession) -> SettingsOut:
    for field, value in body.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(user, field, value)
    await db.commit()
    return to_settings_out(user)


@router.post("/plan", response_model=UsageOut)
async def switch_plan(body: PlanRequest, user: CurrentUser, db: DbSession) -> UsageOut:
    if user.is_demo and body.plan == "pro":
        raise demo_restricted("The demo workspace stays on the Free plan. Create an account to try Pro.")
    user.plan = body.plan
    await db.commit()
    return await build_usage(db, user)


@router.post("/connections/google", response_model=UserOut)
async def connect_google(body: GoogleConnectRequest, user: CurrentUser, db: DbSession) -> UserOut:
    if user.is_demo:
        raise demo_restricted("Demo accounts can't connect Google. Create an account first.")
    identity = await verify_google_id_token(body.id_token)
    other = await db.scalar(select(User).where(User.google_sub == identity.sub, User.id != user.id))
    if other is not None:
        raise validation_error("This Google account is already linked to another PromptDox account.")
    user.google_sub = identity.sub
    user.google_connected = True
    await db.commit()
    return to_user_out(user)


@router.delete("/connections/google", response_model=UserOut)
async def disconnect_google(user: CurrentUser, db: DbSession) -> UserOut:
    if not user.password_hash:
        raise validation_error("Set a password before disconnecting Google, or you won't be able to sign in.")
    user.google_sub = None
    user.google_connected = False
    await db.commit()
    return to_user_out(user)
