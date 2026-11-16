"""PromptDox API entry point."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.core.config import settings
from app.core.database import engine, init_db
from app.core.errors import register_exception_handlers
from app.core.tasks import cancel_all, spawn
from app.services.indexing import requeue_stuck_documents
from app.services.maintenance import maintenance_loop
from app.services.seed import seed_on_startup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("promptdox")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    await init_db()
    await requeue_stuck_documents()
    spawn(seed_on_startup(), name="seed-demo-template")
    spawn(maintenance_loop(), name="maintenance")
    logger.info("PromptDox API %s started", settings.app_version)
    yield
    await cancel_all()
    await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Backend for PromptDox: upload documents and chat with them, with page-level citations.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
register_exception_handlers(app)
app.include_router(api_router)
