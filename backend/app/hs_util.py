"""Общие хелперы для роутеров, работающих с headscale."""

from fastapi import HTTPException, status

from app.hs_client import HeadscaleError


def norm_ts(ts: str | None) -> str | None:
    """headscale отдаёт «нулевое» время как 0001-01-01… — нормализуем в None."""
    if not ts or str(ts).startswith("0001-01-01"):
        return None
    return ts


def require_hs(settings) -> None:
    if not settings.headscale_api_key:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "headscale API-ключ не настроен — задайте NODEROOST_HEADSCALE_API_KEY",
        )


async def hs_call(coro):
    """Выполняет вызов headscale, превращая HeadscaleError в 502."""
    try:
        return await coro
    except HeadscaleError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
