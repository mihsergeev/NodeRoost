import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response, status

from app import audit, ca, enroll, settings_store
from app.config import get_settings
from app.deps import CurrentUser, PublicRateLimit, SessionDep
from app.api.nodes import _map_node
from app.hs_client import get_client
from app.hs_util import hs_call, require_hs
from app.schemas import EnrollIn, EnrollOut, EnrollStatusOut

router = APIRouter(prefix="/enroll", tags=["enroll"])
# Скрипт подключения по ссылке — публично, на домене control-сервера: машину как
# раз и подключают откуда угодно, а панель за вайтлистом ей недоступна.
public_router = APIRouter(tags=["enroll-public"], dependencies=[PublicRateLimit])


async def _join_link(session, settings, script: str, os_name: str, expires_at: str):
    """Положить скрипт под случайным токеном и вернуть (ссылка, команда запуска).

    Зачем ссылка, если скрипт и так показан: его вставляют в консоль целиком, а
    консоль выполняет вставленное построчно — падение в середине не останавливает
    остальное, и человек получает каскад вторичных ошибок вместо причины. Одна
    команда исполняется как одно целое. Заодно ключ не попадает ни в историю
    шелла, ни в буфер обмена — в них остаётся только адрес.

    Ссылка живёт столько же, сколько одноразовый ключ внутри неё: она ровно
    настолько же секретна, и переживать его ей незачем.
    """
    token = secrets.token_urlsafe(24)
    await settings_store.save_join_script(session, token, script, os_name, expires_at)
    base = (settings.headscale_server_url or "").rstrip("/")
    url = f"{base}/join/{token}" if base else ""
    if not url:
        return "", ""
    if os_name == "windows":
        # PowerShell ОТ АДМИНИСТРАТОРА: установщик Tailscale ставится на машину.
        cmd = f"irm {url} | iex"
    elif os_name == "android":
        cmd = ""  # там инструкция для человека, а не скрипт
    else:
        cmd = f"curl -fsSL {url} | sudo sh"
    return url, cmd


@public_router.get("/join/{token}")
async def join_script(token: str, session: SessionDep) -> Response:
    """Скрипт подключения по одноразовой ссылке. Секрет здесь — сам токен."""
    script = await settings_store.get_join_script(session, token)
    if script is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Ссылка не найдена или устарела — создайте подключение в панели заново",
        )
    return Response(content=script, media_type="text/plain; charset=utf-8")


@router.post("", response_model=EnrollOut)
async def enroll_node(
    body: EnrollIn, request: Request, user: CurrentUser, session: SessionDep
) -> EnrollOut:
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)

    # Сущность «пользователь» из панели убрана: субъект доступа — сама нода
    # (устройство), доступ выдаётся ей напрямую. headscale обязан привязать ноду
    # к какому-то владельцу, поэтому все ноды живут под одним техническим
    # пользователем default. Ключ БЕЗ тегов → владельцем станет именно он.
    hs_user = await hs_call(client.ensure_user(settings.default_user))
    user_id = str(hs_user.get("id", ""))

    exp = datetime.now(timezone.utc) + timedelta(
        minutes=settings.enroll_key_ttl_minutes
    )
    exp_iso = exp.isoformat().replace("+00:00", "Z")
    key = await hs_call(
        client.create_preauthkey(
            user_id,
            reusable=False,  # одноразовый
            ephemeral=False,
            expiration=exp_iso,
        )
    )
    key_str = key.get("key", "")
    key_id = str(key.get("id", ""))

    version = await settings_store.get_tailscale_version(session, settings)
    # Корень своей CA едет прямо в скрипте: имя внутри сети должно открываться
    # без ругани с первой минуты, а не после отдельного похода с файлом.
    ca_pem = await ca.root_cert(session) if await ca.auto_install(session) else ""
    script = enroll.build_script(
        body.os, settings, key_str, body.name, version=version,
        exit_node=body.exit_node, ca_pem=ca_pem,
    )
    url, cmd = await _join_link(session, settings, script, body.os, exp_iso)
    await audit.record(session, user.username, "node_enroll", body.name)
    return EnrollOut(
        os=body.os,
        hostname=body.name,
        login_server=settings.headscale_server_url,
        script=script,
        key_id=key_id,
        expires_at=exp_iso,
        join_url=url,
        join_cmd=cmd,
    )


@router.get("/status", response_model=EnrollStatusOut)
async def enroll_status(
    key_id: str, hostname: str, _: CurrentUser
) -> EnrollStatusOut:
    """Поллинг: появилась ли нода, зарегистрированная выданным ключом."""
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    nodes = await hs_call(client.get_nodes())

    # ТОЛЬКО точное совпадение по выданному pre-auth-ключу: ключ — единственная
    # привязка, которую контролирует панель.
    #
    # Раньше был запасной матч по имени (givenName/name). Оба поля выбирает САМА
    # нода (`tailscale up --hostname=…`), а вызывающий — «Переподключить» —
    # переносит на найденный id мету и теги, включая флаг «админ». Отсюда прямой
    # захват прав: нода назвалась именем админского устройства, админ нажал
    # «Переподключить» на настоящем (его запись при этом удаляется, имя
    # освобождается), поллинг не нашёл ноду по новому ключу, откатился на имя и
    # вернул ноду атакующего — которой фронт и проставил admin=true.
    for n in nodes:
        pak = n.get("preAuthKey") or {}
        if key_id and str(pak.get("id", "")) != key_id:
            continue
        if not key_id:
            continue
        # Машина, которая уже была в сети, при повторном подключении прилипает к
        # СВОЕЙ старой записи: headscale узнаёт её по machine key и заводить
        # новую не станет. Имя при этом остаётся прежним — админ просил
        # «new-net», а в списке по-прежнему «laptop», и добавление выглядит как
        # будто не сработало. Переименовываем запись под запрошенное имя и
        # говорим, чью запись переиспользовали. Найдена она по НАШЕМУ ключу,
        # выданному минуту назад, так что подменить чужую ноду этим нельзя.
        was = str(n.get("givenName") or "")
        reused = None
        if hostname and was and was != hostname:
            renamed = await hs_call(client.rename_node(str(n.get("id", "")), hostname))
            reused = was
            if renamed:
                n = renamed.get("node") or renamed
        return EnrollStatusOut(connected=True, node=_map_node(n), reused_from=reused)
    return EnrollStatusOut(connected=False)

# --- Сертификат CA: поставить одной командой ---
# Публично и без токена намеренно: сертификат CA и так стоит на каждой ноде, а
# знать по нему нечего — внутренних имён в нём нет (он лишь запрещает публичные
# домены). Ставит его на машину человек с правами администратора, поэтому
# доступность ссылки ничего никому не даёт.


async def _ca_or_404(session) -> str:
    pem = await ca.root_cert(session)
    if not pem:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Центр сертификации ещё не создан — он появится с первым именем, "
            "которому включён сертификат",
        )
    return pem


@public_router.get("/ca/noderoost-ca.crt")
async def ca_file(session: SessionDep) -> Response:
    """Сам сертификат — для телефонов и для `curl --cacert`."""
    pem = await _ca_or_404(session)
    return Response(
        content=pem,
        media_type="application/x-pem-file",
        headers={"Content-Disposition": 'attachment; filename="noderoost-ca.crt"'},
    )


@public_router.get("/ca/install.sh")
async def ca_install_sh(session: SessionDep) -> Response:
    pem = await _ca_or_404(session)
    fp = ca.root_info(pem).get("fingerprint", "")
    return Response(
        content=enroll.ca_install_script("linux", pem, fp),
        media_type="text/plain; charset=utf-8",
    )


@public_router.get("/ca/install.ps1")
async def ca_install_ps1(session: SessionDep) -> Response:
    pem = await _ca_or_404(session)
    fp = ca.root_info(pem).get("fingerprint", "")
    return Response(
        content=enroll.ca_install_script("windows", pem, fp),
        media_type="text/plain; charset=utf-8",
    )
