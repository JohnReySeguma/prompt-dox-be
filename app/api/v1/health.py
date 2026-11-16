from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import ping_db
from app.schemas.system import HealthOut

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut, responses={503: {"model": HealthOut}})
async def health() -> JSONResponse:
    database_ok = await ping_db()
    body = HealthOut(
        status="ok" if database_ok else "degraded",
        database="ok" if database_ok else "error",
        version=settings.app_version,
    )
    return JSONResponse(status_code=200 if database_ok else 503, content=body.model_dump(by_alias=True))
