from fastapi import APIRouter, BackgroundTasks, Depends, Response, status

from app.api.deps import DbSession
from app.core.ratelimit import limit_auth, limit_demo
from app.schemas.auth import (
    AuthSession,
    ForgotPasswordRequest,
    GoogleAuthRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
)
from app.services import auth as auth_service
from app.services.demo import create_demo_session
from app.services.google_auth import verify_google_id_token

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(limit_auth)])


@router.post("/register", response_model=AuthSession, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: DbSession) -> AuthSession:
    return await auth_service.register(db, body.name, str(body.email), body.password)


@router.post("/login", response_model=AuthSession)
async def login(body: LoginRequest, db: DbSession) -> AuthSession:
    return await auth_service.login(db, str(body.email), body.password)


@router.post("/google", response_model=AuthSession)
async def google(body: GoogleAuthRequest, db: DbSession) -> AuthSession:
    identity = await verify_google_id_token(body.id_token)
    return await auth_service.sign_in_with_google(db, identity)


@router.post("/refresh", response_model=AuthSession)
async def refresh(body: RefreshRequest, db: DbSession) -> AuthSession:
    return await auth_service.refresh_session(db, body.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest, db: DbSession) -> Response:
    await auth_service.logout(db, body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/password/forgot", status_code=status.HTTP_204_NO_CONTENT)
async def forgot_password(body: ForgotPasswordRequest, db: DbSession, background: BackgroundTasks) -> Response:
    await auth_service.forgot_password(db, str(body.email), background)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/password/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(body: ResetPasswordRequest, db: DbSession) -> Response:
    await auth_service.reset_password(db, body.token, body.new_password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/demo", response_model=AuthSession, dependencies=[Depends(limit_demo)])
async def demo(db: DbSession) -> AuthSession:
    return await create_demo_session(db)
