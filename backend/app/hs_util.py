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


# Ошибки, которые headscale возвращает на НЕВЕРНЫЙ ВВОД, а не на свою поломку.
# Он отдаёт их как 500 с английским текстом внутри, и панель показывала это
# админу дословно: «502 headscale 500: renaming node: "srv.prod" is not a valid
# DNS label…». Переводим в понятный отказ.
_INPUT_ERRORS = (
    ("name is not unique", status.HTTP_409_CONFLICT, "Имя уже занято другой нодой"),
    (
        "not a valid dns label",
        status.HTTP_400_BAD_REQUEST,
        "Недопустимое имя: латинские буквы, цифры и дефис, без точек и подчёркиваний",
    ),
)


async def hs_call(coro):
    """Выполняет вызов headscale, превращая HeadscaleError в HTTP-ошибку."""
    try:
        return await coro
    except HeadscaleError as exc:
        text = str(exc)
        low = text.lower()
        for needle, code, human in _INPUT_ERRORS:
            if needle in low:
                raise HTTPException(code, human) from exc
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, text) from exc
