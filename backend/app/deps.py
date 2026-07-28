from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import ratelimit
from app.clientip import client_ip
from app.config import get_settings
from app.db import get_session
from app.models import User
from app.security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
    session: SessionDep,
) -> User:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Недействительный или отсутствующий токен"
    )
    if credentials is None:
        raise unauthorized
    payload = decode_access_token(credentials.credentials, get_settings().jwt_secret)
    if payload is None:
        raise unauthorized
    user = await session.scalar(select(User).where(User.username == payload["sub"]))
    if user is None:
        raise unauthorized
    # токен, выпущенный до смены пароля (иная version), больше не действителен
    if payload.get("ver", 0) != user.token_version:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def public_rate_limit(request: Request) -> None:
    """Ограничитель для роутов, доступных БЕЗ сессии (по токену в URL).

    Такой роут открыт всему интернету и на каждый запрос лезет в БД за
    настройками — в том числе чтобы понять, что токен неизвестен. Без лимита
    это дешёвый способ нагружать панель, а токены утекают легко: они видны в
    URL, а значит в истории шелла, в логах прокси и на любом скриншоте.
    """
    if ratelimit.too_many(client_ip(request)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много запросов"
        )


PublicRateLimit = Depends(public_rate_limit)
