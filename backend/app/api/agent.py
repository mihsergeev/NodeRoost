"""API агента ноды: панель задаёт желаемое состояние (маршруты, exit), нода
забирает его по токену и применяет сама.

Публичные роуты (без сессии) отдают только то, что нода и так о себе знает —
свои маршруты. Токен 128-битный, неизвестный → 404.
"""

import secrets

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from fastapi import APIRouter, HTTPException, Request, Response, status

from app import agent, audit, certs, routing, settings_store
from app.config import get_settings
from app.deps import CurrentUser, PublicRateLimit, SessionDep
from app.schemas import AgentIn, AgentOut

router = APIRouter(prefix="/agent", tags=["agent"])
# публичные роуты — под лимитом на источник (см. deps.public_rate_limit)
public_router = APIRouter(tags=["agent-public"], dependencies=[PublicRateLimit])

# агент ходит раз в минуту; 5 минут тишины — считаем, что его нет
_ALIVE_S = 300


def _state_url(token: str) -> str:
    base = (get_settings().headscale_server_url or "").rstrip("/")
    return f"{base}/agent/{token}" if token else ""


async def _wanted_routes(session, node_id: str, cfg: dict) -> list[str]:
    """Что нода реально должна анонсировать: маршруты, заданные на ней руками,
    ПЛЮС выведенные из направлений («кто → куда через неё»).

    Объединяем при отдаче, а не при записи: так направление и ручной маршрут не
    затирают друг друга, и удаление направления не может унести чужой маршрут.
    """
    derived = routing.routes_by_node(await settings_store.get_routing(session))
    return sorted(set(cfg.get("routes") or []) | set(derived.get(str(node_id), [])))


def _out(cfg: dict, wanted_hash: str = "") -> AgentOut:
    from datetime import datetime, timedelta, timezone

    token = cfg.get("token", "")
    url = _state_url(token)
    installed = False
    raw = cfg.get("last_poll")
    if raw:
        try:
            installed = datetime.now(timezone.utc) - datetime.fromisoformat(raw) < timedelta(
                seconds=_ALIVE_S
            )
        except (ValueError, TypeError):
            installed = False
    return AgentOut(
        routes=cfg.get("routes", []),
        exit_node=bool(cfg.get("exit", False)),
        token=token,
        installed=installed,
        last_poll=raw,
        last_applied=cfg.get("last_applied"),
        applied_hash=str(cfg.get("applied_hash") or ""),
        # применённое совпадает с тем, что панель просит СЕЙЧАС. Если нода
        # опрашивает, но не применяет (или отстала) — здесь будет False
        applied_current=bool(
            wanted_hash and str(cfg.get("applied_hash") or "") == wanted_hash
        ),
        setup_oneline=f"curl -fsSL {url}/setup | sh" if url else "",
        remove_oneline=f"curl -fsSL {url}/remove | sh" if url else "",
        # Агент, поставленный до появления новой возможности, о ней не знает и
        # молча её игнорирует. Пока панель этого не показывала, выглядело это как
        # «включил — ничего не произошло». Свежие агенты обновляются сами.
        script_current=(not installed) or cfg.get("script") == agent.SCRIPT_VERSION,
    )


@router.get("/{node_id}", response_model=AgentOut)
async def get_agent(node_id: str, _: CurrentUser, session: SessionDep) -> AgentOut:
    all_cfg = await settings_store.get_agent_all(session)
    cfg = all_cfg.get(node_id, {})
    if not cfg.get("token"):  # токен выдаём один раз и держим стабильным
        cfg["token"] = secrets.token_urlsafe(16)
        all_cfg[node_id] = cfg
        await settings_store.set_agent_all(session, all_cfg)
    return _out(cfg, await _wanted_hash(session, node_id, cfg))


@router.put("/{node_id}", response_model=AgentOut)
async def put_agent(
    node_id: str, body: AgentIn, user: CurrentUser, session: SessionDep
) -> AgentOut:
    all_cfg = await settings_store.get_agent_all(session)
    cfg = all_cfg.get(node_id, {})
    if not cfg.get("token"):
        cfg["token"] = secrets.token_urlsafe(16)
    cfg["routes"] = [r.strip() for r in body.routes if r.strip()]
    cfg["exit"] = body.exit_node
    all_cfg[node_id] = cfg
    await settings_store.set_agent_all(session, all_cfg)
    # Это меняет то, ЧТО НОДА ОБЪЯВЛЯЕТ ВСЕЙ СЕТИ: подсеть за ней и выход в
    # интернет через неё. Такое обязано попадать в журнал наравне с правилами
    # доступа — раньше единственное изменение, которое не оставляло следа.
    await audit.record(
        session,
        user.username,
        "agent_set",
        node_id,
        f"маршруты: {', '.join(cfg['routes']) or 'нет'}; exit: {'да' if cfg['exit'] else 'нет'}",
    )
    return _out(cfg, await _wanted_hash(session, node_id, cfg))


async def _wanted_hash(session, node_id: str, cfg: dict) -> str:
    """sha256 состояния, которое панель отдаёт ноде СЕЙЧАС, — ровно того текста,
    что агент кладёт в файл и хеширует у себя."""
    import hashlib

    body = agent.state_body(
        await _wanted_routes(session, node_id, cfg),
        bool(cfg.get("exit", False)),
        str(cfg.get("use_exit") or ""),
    )
    return hashlib.sha256(body.encode()).hexdigest()


@public_router.get("/agent/{token}/setup")
async def agent_setup(token: str, session: SessionDep) -> Response:
    if await settings_store.get_agent_by_token(session, token) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown token")
    return Response(
        content=agent.build_setup(_state_url(token)), media_type="text/plain"
    )


@public_router.get("/agent/{token}/remove")
async def agent_remove(token: str, session: SessionDep) -> Response:
    if await settings_store.get_agent_by_token(session, token) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown token")
    return Response(content=agent.build_remove(), media_type="text/plain")


@public_router.post("/agent/{token}/applied")
async def agent_applied(token: str, h: str, session: SessionDep, s: str = "") -> Response:
    """Агент подтверждает, что ПРИМЕНИЛ состояние (h — sha256 применённого файла).

    Отдельно от запроса состояния: сам по себе запрос ничего не доказывает — ноде
    достаточно дёргать свой URL, чтобы панель считала агента живым, ничего при этом
    не применяя. Сверяя хеш с текущим заданием, панель видит и «агент есть, но
    отстал». Токен тот же, что и у состояния: больше прав это не даёт — подделать
    можно лишь отчёт о СВОЁМ применении, а расхождение хеша всё равно вылезет.
    """
    found = await settings_store.get_agent_by_token(session, token)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown token")
    node_id, _ = found
    await settings_store.mark_agent_applied(session, token, (h or "")[:64], (s or "")[:16])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@public_router.get("/agent/{token}")
async def agent_state(token: str, session: SessionDep) -> Response:
    """Желаемое состояние для ноды. Сам факт запроса = агент жив."""
    found = await settings_store.get_agent_by_token(session, token)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown token")
    node_id, cfg = found
    await settings_store.touch_agent_poll(session, token)
    body = agent.state_body(
        await _wanted_routes(session, node_id, cfg),
        bool(cfg.get("exit", False)),
        str(cfg.get("use_exit") or ""),
    )
    body += agent.extra_lines(
        await certs.wanted_for_node(session, get_settings(), node_id)
    )
    return Response(content=body, media_type="text/plain")


async def _node_may_ask(session, token: str, name: str) -> str:
    """Нода вправе просить сертификат только на имя, которое панель ей и назначила.

    Без этой проверки владелец любой ноды выпускал бы сертификаты на чужие имена
    сети — токен агента давал бы куда больше, чем свои маршруты.
    """
    found = await settings_store.get_agent_by_token(session, token)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown token")
    node_id, _ = found
    wanted = {n for n, _, _ in await certs.wanted_for_node(session, get_settings(), node_id)}
    if name not in wanted:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "это имя не назначено данной ноде"
        )
    return node_id


@public_router.post("/agent/{token}/csr")
async def agent_csr(token: str, name: str, request: Request, session: SessionDep) -> Response:
    """Нода прислала CSR — панель проводит ACME-заказ и отвечает сертификатом.

    Ключ остаётся на ноде: сюда приезжает только запрос на подпись.
    """
    node_id = await _node_may_ask(session, token, name)
    pem = (await request.body()).decode("utf-8", "replace")
    try:
        csr_der = x509.load_pem_x509_csr(pem.encode()).public_bytes(Encoding.DER)
    except Exception as e:  # noqa: BLE001 — сюда приходит текст с чужой машины
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"не похоже на CSR: {e}") from e
    try:
        row = await certs.issue(session, get_settings(), name, node_id, csr_der)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    if row.status != "ok":
        # Агент не должен долбиться в ответ на отказ: причина и пауза — в панели
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, row.error or "сертификат не выдан"
        )
    return Response(content=row.cert_pem, media_type="application/x-pem-file")


@public_router.get("/agent/{token}/cert")
async def agent_cert(token: str, name: str, session: SessionDep) -> Response:
    """Забрать уже выданный сертификат (например, если файл на ноде потерялся)."""
    await _node_may_ask(session, token, name)
    row = await certs.get(session, name)
    if row is None or not row.cert_pem:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "сертификата ещё нет")
    return Response(content=row.cert_pem, media_type="application/x-pem-file")


@public_router.get("/.well-known/acme-challenge/{token}")
async def acme_challenge(token: str, session: SessionDep) -> Response:
    """Ответ на проверку Let's Encrypt. Публично и без авторизации — по замыслу:
    сюда приходит не человек, а проверяющий, и токен здесь единственный секрет."""
    answer = await certs.answer_for(session, token)
    if not answer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown token")
    return Response(content=answer, media_type="text/plain")
