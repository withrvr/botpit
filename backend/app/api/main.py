from fastapi import APIRouter

from app.api.routes import (
    admin,
    games,
    health,
    items,
    login,
    private,
    roles,
    rooms,
    users,
    utils,
)
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(items.router)
api_router.include_router(health.router)
api_router.include_router(roles.router)
api_router.include_router(admin.router)
api_router.include_router(admin.me_router)
api_router.include_router(rooms.router)
api_router.include_router(games.router)


if settings.FASTAPI_ENV == "development":
    api_router.include_router(private.router)
