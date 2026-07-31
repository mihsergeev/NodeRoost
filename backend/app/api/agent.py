"""API агента ноды: панель задаёт желаемое состояние (маршруты, exit), нода
забирает его по токену и применяет сама.

Публичные роуты (без сессии) отдают только то, что нода и так о себе знает —
свои маршруты. Токен 128-битный, неизвестный → 404.
"""

import secrets

from fastapi import APIRouter, HTTPException, Response, status

from app import agent, audit, routing, settings_store
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
async def agent_applied(token: str, h: str, session: SessionDep) -> Response:
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
    await settings_store.mark_agent_applied(session, token, (h or "")[:64])
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
    return Response(content=body, media_type="text/plain")
