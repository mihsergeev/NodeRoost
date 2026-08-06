import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.api import (
    agent as agent_api,
    alerts as alerts_api,
    auth,
    backup as backup_api,
    enroll,
    health,
    logs as logs_api,
    metrics,
    nodes,
    policy,
    routing as routing_api,
    settings as settings_api,
)
from app.autobackup import backup_loop
from app.bootstrap import ensure_admin
from app.collector import collector_loop
from app.config import get_settings
from app.db import create_engine_and_factory
from app.heartbeat import heartbeat_loop
from app.routing import routing_loop


_WEAK_SECRETS = {"", "changeme", "dev-insecure-change-me"}
_WEAK_PASSWORDS = {"", "changeme", "admin"}


def _enforce_secrets(settings) -> None:
    """Отказываемся стартовать в проде с дефолтными/слабыми секретами."""
    if settings.debug:
        return
    if settings.jwt_secret in _WEAK_SECRETS or len(settings.jwt_secret) < 32:
        raise RuntimeError(
            "NODEROOST_JWT_SECRET не задан или слишком слабый — задайте случайный "
            "секрет (openssl rand -hex 32). Панель не запущена в целях безопасности."
        )
    if settings.admin_password in _WEAK_PASSWORDS:
        raise RuntimeError(
            "NODEROOST_ADMIN_PASSWORD не задан или дефолтный — задайте надёжный "
            "пароль. Панель не запущена в целях безопасности."
        )


def create_app() -> FastAPI:
    settings = get_settings()
    engine, session_factory = create_engine_and_factory(settings.db_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _enforce_secrets(settings)
        await ensure_admin(session_factory, settings)
        hb_task = asyncio.create_task(heartbeat_loop(session_factory, settings))
        collector_task = asyncio.create_task(collector_loop(session_factory, settings))
        backup_task = asyncio.create_task(backup_loop(session_factory, settings))
        routing_task = asyncio.create_task(routing_loop(session_factory, settings))
        yield
        hb_task.cancel()
        collector_task.cancel()
        backup_task.cancel()
        routing_task.cancel()
        await engine.dispose()

    # доки/схему API отдаём только в debug — в проде не раскрываем поверхность API
    docs_url = "/api/docs" if settings.debug else None
    openapi_url = "/api/openapi.json" if settings.debug else None
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        docs_url=docs_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )
    app.state.engine = engine
    app.state.session_factory = session_factory

    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(nodes.router, prefix="/api")
    app.include_router(enroll.router, prefix="/api")
    app.include_router(enroll.public_router)
    app.include_router(policy.router, prefix="/api")
    app.include_router(settings_api.router, prefix="/api")
    app.include_router(metrics.router, prefix="/api")
    app.include_router(alerts_api.router, prefix="/api")
    app.include_router(backup_api.router, prefix="/api")
    app.include_router(logs_api.router, prefix="/api")
    app.include_router(agent_api.router, prefix="/api")
    app.include_router(agent_api.public_router)
    app.include_router(routing_api.router, prefix="/api")
    return app


app = create_app()
