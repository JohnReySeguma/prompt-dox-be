from typing import Literal

from app.schemas.common import CamelModel


class HealthOut(CamelModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "error"]
    version: str
