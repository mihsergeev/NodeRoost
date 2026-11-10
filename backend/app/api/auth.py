from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import alerts, audit, devices, ratelimit, totp
from app.clientip import client_ip
from app.config import get_settings
from app.deps import CurrentUser, SessionDep
from app.models import User
from app.schemas import (
    LoginOptionsOut,
    LoginRequest,
    PasswordChangeRequest,
    TokenResponse,
    TwoFASetupOut,
    TwoFASetupRequest,
    TwoFAStatusOut,
    TrustDaysIn,
    TrustedDevicesOut,
    TwoFAVerifyRequest,
    UserOut,
)
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

# фиктивный хэш для сравнения при несуществующем юзере — выравнивает время
# ответа, чтобы по задержке нельзя было перечислять логины
_DUMMY_HASH = hash_password("no-such-user-timing-guard")


def _client_key(request: Request) -> str:
    return client_ip(request)


def _stepup_key(request: Request) -> str:
    return f"stepup:{_client_key(request)}"


def _require_attempt(key: str) -> None:
    if ratelimit.is_locked(key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Слишком много неверных попыток — подождите немного.",
        )


def _require_password(request: Request, user: User, password: str) -> None:
    """Подтверждение текущим паролем перед операцией с доступом в панель.

    Один токен не должен быть достаточным условием: его можно угнать, а срок
    жизни у него длинный. Счётчик отдельный от входного, чтобы подбор пароля
    здесь не был бесплатным.
    """
    key = _stepup_key(request)
    _require_attempt(key)
    if not verify_password(password, user.password_hash):
        ratelimit.record_failure(key)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неверный текущий пароль")
    ratelimit.clear(key)


async def _update_auth_user(
    session: AsyncSession, user: User, *, consume_counter: int | None = None, **values,
) -> bool:
    """Меняем ровно то состояние учётных данных, которое проверил запрос."""
    stmt = update(User).where(
        User.id == user.id,
        User.password_hash == user.password_hash,
        User.token_version == user.token_version,
        User.totp_enabled == user.totp_enabled,
        User.totp_secret == user.totp_secret,
    )
    if consume_counter is not None:
        # Условие должно проверяться в самой БД: два запроса могут прочитать
        # одинаковый старый счётчик. Счётчик не может уменьшиться в этой гонке.
        stmt = stmt.where(User.totp_last_counter < consume_counter)
        values["totp_last_counter"] = consume_counter
    result = await session.execute(stmt.values(**values).execution_options(synchronize_session=False))
    return result.rowcount == 1


async def _consume_totp(session: AsyncSession, user: User, otp: str, **values) -> bool:
    counter = totp.matched_counter(user.totp_secret, otp)
    if counter is None or counter <= user.totp_last_counter:
        return False
    return await _update_auth_user(session, user, consume_counter=counter, **values)


async def _consume_stepup_totp(
    session: AsyncSession, request: Request, user: User, otp: str, **values,
) -> None:
    # Угадывание кода ограничено отдельно: верный пароль не сбрасывает этот счётчик.
    key = f"stepup-otp:{_client_key(request)}"
    _require_attempt(key)
    if not await _consume_totp(session, user, otp, **values):
        ratelimit.record_failure(key)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неверный код")
    ratelimit.clear(key)


async def _record_failure(session, settings, key: str, username: str, action: str) -> None:
    """Пишет неудачный вход в журнал; на переходе в блокировку — алерт (брутфорс)."""
    locked_now = ratelimit.record_failure(key)
    await audit.record(session, username, action, key)
    if locked_now:
        await audit.record(
            session, username, "login_lockout", key,
            f"{ratelimit.MAX_FAILURES} неудачных попыток",
        )
        await alerts.security_alert(
            session, settings,
            f"🚨 NodeRoost: {ratelimit.MAX_FAILURES} неудачных попыток входа "
            f"подряд с IP {key} — вход временно заблокирован.",
        )


def _secure_request(request: Request) -> bool:
    """Пришёл ли запрос по https (панель может стоять и за своим прокси).

    Флаг Secure на куке за http браузер молча игнорирует, и устройство тогда не
    запоминается вовсе - поэтому ставим его по факту, а не всегда.
    """
    if urlparse(get_settings().panel_url).scheme == "https":
        return True
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    return (proto or request.url.scheme) == "https"


def _set_device_cookie(
    request: Request, response: Response, secret: str, max_age: int
) -> None:
    response.set_cookie(
        devices.COOKIE_NAME,
        secret,
        max_age=max_age,
        path=devices.COOKIE_PATH,  # кука нужна только на входе
        httponly=True,  # недоступна скриптам на странице
        samesite="lax",
        secure=_secure_request(request),
    )


@router.get("/login-options", response_model=LoginOptionsOut)
async def login_options(session: SessionDep) -> LoginOptionsOut:
    """Срок доверия устройству - чтобы форма входа знала, предлагать ли галку."""
    return LoginOptionsOut(trust_days=await devices.trust_days(session))


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest, request: Request, response: Response, session: SessionDep
) -> TokenResponse:
    settings = get_settings()
    key = _client_key(request)
    if ratelimit.is_locked(key):
        await audit.record(session, body.username, "login_blocked", key, "rate-limited")
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Слишком много попыток входа — подождите несколько минут",
        )
    user = await session.scalar(select(User).where(User.username == body.username))
    # verify_password вызываем всегда (в т.ч. по фиктивному хэшу), чтобы время
    # ответа не зависело от существования логина
    password_ok = verify_password(
        body.password, user.password_hash if user else _DUMMY_HASH
    )
    if user is None or not password_ok:
        await _record_failure(session, settings, key, body.username, "login_fail")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")
    device_token = request.cookies.get(devices.COOKIE_NAME)
    trusted = False
    if user.totp_enabled:
        # Запомненное устройство снимает ВТОРОЙ фактор, а не первый: сюда
        # попадают только с верным паролем, поэтому украденная кука без пароля
        # ничего не даёт.
        trusted = await devices.check(session, user.username, device_token)
        if not trusted:
            # detail — машиночитаемый маркер для фронта (показать поле кода)
            if not body.otp:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "2fa_required")
            # отвергаем неверный код И уже использованный (защита от replay)
            if not await _consume_totp(session, user, body.otp):
                await _record_failure(
                    session, settings, key, body.username, "login_2fa_fail"
                )
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "2fa_invalid")
    # Просьбу запомнить устройство выполняем только после пройденной 2FA: иначе
    # доверие выдавалось бы тем же знанием пароля, которое оно и подстраховывает.
    if user.totp_enabled and not trusted:
        made = None
        if body.remember:
            made = await devices.remember(
                session, user.username, request.headers.get("user-agent", ""), key,
                commit=False,
            )
        # Счётчик и новое устройство фиксируются вместе, до выдачи токена и audit.
        await session.commit()
        if made is not None:
            secret, max_age = made
            _set_device_cookie(request, response, secret, max_age)
            await audit.record(
                session, user.username, "device_trusted", key,
                devices.label_from_agent(request.headers.get("user-agent", "")),
            )
    ratelimit.clear(key)
    token = create_access_token(
        user.username, settings.jwt_secret, settings.jwt_ttl_minutes,
        user.token_version,
    )
    await audit.record(
        session, user.username, "login_ok", key,
        "без кода, устройство запомнено" if trusted else "",
    )
    return TokenResponse(access_token=token)


@router.post("/password", response_model=TokenResponse)
async def change_password(
    body: PasswordChangeRequest, request: Request, user: CurrentUser, session: SessionDep
) -> TokenResponse:
    """Смена пароля из UI: проверяет текущий, инвалидирует все старые токены."""
    _require_password(request, user, body.current_password)
    version = user.token_version + 1
    if not await _update_auth_user(
        session, user, password_hash=hash_password(body.new_password), token_version=version,
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "Учётные данные изменились, повторите запрос")
    # Пароль меняют в том числе когда подозревают, что его узнали. Оставить при
    # этом устройства, которым код 2FA не нужен, - значит оставить дверь, ради
    # закрытия которой всё и затевалось.
    await devices.revoke_all(session, user.username)
    await session.commit()
    await audit.record(session, user.username, "password_change", user.username)
    settings = get_settings()
    await alerts.security_alert(
        session, settings,
        f"🔑 NodeRoost: пароль администратора «{user.username}» изменён "
        f"(IP {_client_key(request)}).",
    )
    token = create_access_token(
        user.username, settings.jwt_secret, settings.jwt_ttl_minutes,
        version,
    )
    return TokenResponse(access_token=token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, user: CurrentUser, session: SessionDep) -> None:
    """Выход завершает сессию на сервере, а не только в браузере.

    Раньше кнопка «Выйти» просто стирала токен из localStorage, и снятая до
    этого копия работала ещё до конца срока жизни (12 часов). Поднимаем версию
    токенов: все ранее выданные перестают проходить проверку.
    """
    await session.execute(
        update(User).where(User.id == user.id, User.token_version == user.token_version)
        .values(token_version=User.token_version + 1)
    )
    await session.commit()
    await audit.record(session, user.username, "logout", user.username, _client_key(request))


@router.get("/devices", response_model=TrustedDevicesOut)
async def trusted_devices(
    request: Request, user: CurrentUser, session: SessionDep
) -> TrustedDevicesOut:
    rows = await devices.listing(session, user.username)
    return TrustedDevicesOut(
        trust_days=await devices.trust_days(session),
        current_id=devices.current_id(rows, request.cookies.get(devices.COOKIE_NAME)),
        devices=rows,
    )


@router.post("/devices/days", response_model=TrustedDevicesOut)
async def set_trust_days(
    body: TrustDaysIn, request: Request, user: CurrentUser, session: SessionDep
) -> TrustedDevicesOut:
    days = await devices.set_trust_days(session, body.days)
    await audit.record(
        session, user.username, "trusted_days", user.username,
        f"{days} дней" if days else "выключено, устройства забыты",
    )
    rows = await devices.listing(session, user.username)
    return TrustedDevicesOut(
        trust_days=days,
        current_id=devices.current_id(rows, request.cookies.get(devices.COOKIE_NAME)),
        devices=rows,
    )


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def forget_device(
    device_id: int, user: CurrentUser, session: SessionDep
) -> None:
    if not await devices.forget(session, user.username, device_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Устройство не найдено")
    await audit.record(session, user.username, "device_forget", str(device_id))


@router.post("/devices/forget-all", status_code=status.HTTP_204_NO_CONTENT)
async def forget_all_devices(user: CurrentUser, session: SessionDep) -> None:
    """Забыть все устройства - то, что нажимают с чужого компьютера или после
    потери ноутбука: со следующего входа код спросят везде."""
    count = await devices.forget_all(session, user.username)
    await audit.record(
        session, user.username, "device_forget_all", user.username, f"{count}"
    )


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user


@router.get("/2fa", response_model=TwoFAStatusOut)
async def twofa_status(user: CurrentUser) -> TwoFAStatusOut:
    return TwoFAStatusOut(enabled=user.totp_enabled)


@router.post("/2fa/setup", response_model=TwoFASetupOut)
async def twofa_setup(
    body: TwoFASetupRequest, request: Request, user: CurrentUser, session: SessionDep
) -> TwoFASetupOut:
    _require_password(request, user, body.password)
    if user.totp_enabled:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "2FA уже включена — сначала отключите её",
        )
    secret = totp.random_secret()
    # Новый ожидающий подтверждения секрет имеет свой счётчик.
    if not await _update_auth_user(session, user, totp_secret=secret, totp_last_counter=0):
        raise HTTPException(status.HTTP_409_CONFLICT, "Настройки 2FA изменились, повторите запрос")
    await session.commit()
    await audit.record(session, user.username, "2fa_setup", user.username)
    return TwoFASetupOut(
        secret=secret,
        otpauth_uri=totp.provisioning_uri(secret, user.username),
    )


@router.post("/2fa/enable", response_model=TwoFAStatusOut)
async def twofa_enable(
    body: TwoFAVerifyRequest, request: Request, user: CurrentUser, session: SessionDep
) -> TwoFAStatusOut:
    _require_password(request, user, body.password)
    if user.totp_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "2FA уже включена")
    if not user.totp_secret:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Сначала запросите настройку (setup)"
        )
    # Код засчитываем ТАК ЖЕ, как при входе: одноразово. Иначе код, введённый
    # при включении, оставался годным ещё полминуты — и подсмотренный через
    # плечо (или снятый вместе с QR на скриншот) пускал в панель.
    await _consume_stepup_totp(session, request, user, body.otp, totp_enabled=True)
    await session.commit()
    await audit.record(session, user.username, "2fa_enable", user.username)
    return TwoFAStatusOut(enabled=True)


@router.post("/2fa/disable", response_model=TwoFAStatusOut)
async def twofa_disable(
    body: TwoFAVerifyRequest, request: Request, user: CurrentUser, session: SessionDep
) -> TwoFAStatusOut:
    _require_password(request, user, body.password)
    if not user.totp_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "2FA не включена")
    await _consume_stepup_totp(
        session, request, user, body.otp, totp_enabled=False, totp_secret="",
    )
    # Без 2FA доверять устройству нечему: записи стали бы тихо ждать включения
    # и пропустили бы вход без кода сразу после него.
    await devices.revoke_all(session, user.username)
    await session.commit()
    await audit.record(session, user.username, "2fa_disable", user.username)
    return TwoFAStatusOut(enabled=False)
