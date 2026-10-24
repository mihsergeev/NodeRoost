import time

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from app.config import get_settings
from app.deps import CurrentUser
from app.hs_client import HeadscaleClient
from app.schemas import ConfigOut

router = APIRouter(tags=["system"])

# Проба control-сервера кэшируется: /health открыт без токена (его дёргает
# healthcheck контейнера и любой, кто знает адрес панели), а каждый вызов
# означал новое исходящее соединение к control-серверу и запись из пула БД.
_HS_CACHE_S = 5
_hs_cached: tuple[float, str] = (0.0, "")


@router.get("/health")
async def health(request: Request, response: Response) -> dict[str, str]:
    """Здоровье панели: БД + доступность headscale.

    503 отдаём ТОЛЬКО при недоступной БД — тогда compose-healthcheck перезапустит
    backend. headscale-недоступность лишь показывается в поле ``headscale`` и НЕ
    роняет health (падение control-сервера не должно рестартить панель).
    Версия отдаётся всегда, чтобы не ломать фронт.
    """
    settings = get_settings()
    db = "unknown"
    factory = getattr(request.app.state, "session_factory", None)
    if factory is not None:
        try:
            async with factory() as session:
                await session.execute(text("SELECT 1"))
            db = "ok"
        except Exception:  # noqa: BLE001 — БД недоступна
            db = "down"
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    # headscale-проба с коротким таймаутом, чтобы health не подвисал
    global _hs_cached
    fresh_ts, fresh_val = _hs_cached
    if not settings.headscale_api_key:
        headscale = "unconfigured"
    elif fresh_val and time.monotonic() - fresh_ts < _HS_CACHE_S:
        headscale = fresh_val
    else:
        client = HeadscaleClient(
            settings.headscale_url, settings.headscale_api_key, timeout=3
        )
        try:
            await client.ping()
            headscale = "ok"
            _hs_cached = (time.monotonic(), headscale)
        except Exception as exc:  # noqa: BLE001 — control-сервер недоступен
            # «Не пускает» и «не отвечает» чинятся по-разному: в первом случае
            # headscale жив, а ключ панели истёк или отозван — нужен новый ключ,
            # а не перезапуск. Раньше и то и другое показывалось как «down», и
            # админ шёл поднимать работающий control-сервер.
            msg = str(exc)
            headscale = (
                "unauthorized"
                if "401" in msg or "403" in msg
                else "down"
            )
        _hs_cached = (time.monotonic(), headscale)

    return {
        "status": "ok" if db != "down" else "degraded",
        "db": db,
        "headscale": headscale,
        "version": settings.version,
    }


@router.get("/config", response_model=ConfigOut)
async def config(_: CurrentUser) -> ConfigOut:
    settings = get_settings()
    return ConfigOut(
        panel_ip=settings.panel_ip,
        headscale_server_url=settings.headscale_server_url,
        headscale_configured=bool(settings.headscale_api_key),
    )
