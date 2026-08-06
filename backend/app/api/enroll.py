from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request

from app import audit, ca, enroll, settings_store
from app.config import get_settings
from app.deps import CurrentUser, SessionDep
from app.api.nodes import _map_node
from app.hs_client import get_client
from app.hs_util import hs_call, require_hs
from app.schemas import EnrollIn, EnrollOut, EnrollStatusOut

router = APIRouter(prefix="/enroll", tags=["enroll"])


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
    await audit.record(session, user.username, "node_enroll", body.name)
    return EnrollOut(
        os=body.os,
        hostname=body.name,
        login_server=settings.headscale_server_url,
        script=script,
        key_id=key_id,
        expires_at=exp_iso,
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
