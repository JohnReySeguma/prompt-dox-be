from fastapi import APIRouter

from app.api.v1 import auth, chat, documents, health, me

api_router = APIRouter(prefix="/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(me.router)
api_router.include_router(documents.router)
api_router.include_router(chat.router)
