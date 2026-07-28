from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from app.config import get_settings
from app.deps import CurrentUser
from app.hs_client import HeadscaleClient
from app.schemas import ConfigOut

router = APIRouter(tags=["system"])


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
    if not settings.headscale_api_key:
        headscale = "unconfigured"
    else:
        client = HeadscaleClient(
            settings.headscale_url, settings.headscale_api_key, timeout=3
        )
        try:
            await client.ping()
            headscale = "ok"
        except Exception:  # noqa: BLE001 — control-сервер недоступен
            headscale = "down"

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
