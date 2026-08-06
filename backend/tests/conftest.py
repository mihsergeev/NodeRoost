import os
import tempfile

import pytest_asyncio

# Тестовое окружение задаём ДО импорта приложения (config кэшируется).
os.environ["NODEROOST_DEBUG"] = "1"
_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.close(_db_fd)
os.environ["NODEROOST_DB_URL"] = f"sqlite+aiosqlite:///{_db_path}"
os.environ["NODEROOST_JWT_SECRET"] = "x" * 40
os.environ["NODEROOST_ADMIN_USER"] = "admin"
os.environ["NODEROOST_ADMIN_PASSWORD"] = "test-password-123"
os.environ["NODEROOST_HEADSCALE_API_KEY"] = ""

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app import models  # noqa: E402,F401 — регистрирует таблицы
from app.bootstrap import ensure_admin  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.main import create_app  # noqa: E402

ADMIN_PASSWORD = "test-password-123"


@pytest_asyncio.fixture
async def client():
    get_settings.cache_clear()
    settings = get_settings()
    app = create_app()
    engine = app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_admin(app.state.session_factory, settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await engine.dispose()


@pytest_asyncio.fixture
async def session(tmp_path):
    """Отдельная БД-сессия (файловый sqlite) для юнит-тестов логики над БД."""
    from app.db import create_engine_and_factory

    engine, factory = create_engine_and_factory(
        f"sqlite+aiosqlite:///{tmp_path.as_posix()}/rec.db"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(tmp_path):
    """Фабрика сессий — для кода, который сам открывает и закрывает сессии
    (коллектор делает это по нескольку раз за проход)."""
    from app.db import create_engine_and_factory

    engine, factory = create_engine_and_factory(
        f"sqlite+aiosqlite:///{tmp_path.as_posix()}/col.db"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()
