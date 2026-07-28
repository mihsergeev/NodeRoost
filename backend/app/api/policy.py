import asyncio
import ipaddress
import socket

from fastapi import APIRouter, HTTPException, Request, status

from app import audit, policy_apply, settings_store
from app.config import get_settings
from app.deps import CurrentUser, SessionDep
from app.hs_client import HeadscaleError, get_client
from app.hs_util import hs_call, norm_ts, require_hs
from app.schemas import (
    AclRule,
    AclRulesIn,
    AclRulesOut,
    PolicyIn,
    PolicyOut,
    ResolveHostIn,
    ResolveHostOut,
)

router = APIRouter(prefix="/policy", tags=["policy"])

# Дефолтный шаблон (HuJSON — JSON с комментариями) для инициализации политики.
DEFAULT_POLICY = """// Политика доступа NodeRoost (HuJSON — JSON с комментариями).
// Документация: https://tailscale.com/kb/1018/acls
{
  "acls": [
    // По умолчанию — разрешить всем узлам общаться со всеми.
    { "action": "accept", "src": ["*"], "dst": ["*:*"] }
  ]

  // Примеры (раскомментируйте и настройте):
  // "tagOwners": { "tag:server": ["autogroup:admin"] },
  // "groups":    { "group:dev": ["alice", "bob"] },
  // "hosts":     { "office": "192.168.99.0/24" }
}
"""


@router.get("", response_model=PolicyOut)
async def get_policy(_: CurrentUser) -> PolicyOut:
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    try:
        data = await client.get_policy()
    except HeadscaleError as exc:
        # database-режим без заданной политики → отдаём дефолтный шаблон
        if "not found" in str(exc).lower():
            return PolicyOut(policy=DEFAULT_POLICY, updated_at=None, exists=False)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    return PolicyOut(
        policy=data.get("policy", "") or "",
        updated_at=norm_ts(data.get("updatedAt")),
        exists=True,
    )


@router.put("", response_model=PolicyOut)
async def put_policy(
    body: PolicyIn, request: Request, user: CurrentUser, session: SessionDep
) -> PolicyOut:
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    try:
        data = await client.set_policy(body.policy)
    except HeadscaleError as exc:
        msg = str(exc)
        # 400/422 от headscale = ошибка валидации политики (user error)
        if " 400:" in msg or " 422:" in msg or "policy" in msg.lower():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, msg)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, msg)
    await audit.record(session, user.username, "policy_set", "acl")
    return PolicyOut(
        policy=data.get("policy", "") or "",
        updated_at=norm_ts(data.get("updatedAt")),
        exists=True,
    )


# --- визуальный конструктор ACL (правила → HuJSON) ---


@router.get("/rules", response_model=AclRulesOut)
async def get_rules(_: CurrentUser, session: SessionDep) -> AclRulesOut:
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    rules = await settings_store.get_acl_rules(session)
    nodes = await hs_call(client.get_nodes())
    return AclRulesOut(
        rules=[AclRule(**r) for r in rules],
        generated=await policy_apply.build_policy(session, client, settings, nodes) or "",
    )


@router.put("/rules", response_model=AclRulesOut)
async def put_rules(
    body: AclRulesIn, request: Request, user: CurrentUser, session: SessionDep
) -> AclRulesOut:
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    nodes = await hs_call(client.get_nodes())
    rules_dicts = [r.model_dump() for r in body.rules]
    # Сохраняем, пушим ЕДИНЫМ сборщиком и откатываем при отказе headscale — так
    # запушенное всегда совпадает с хранимым и с кэшем _last_pushed (иначе
    # самоисцеление считает, что всё в порядке, и расхождение живёт до следующей
    # правки). Порядок «сохранить → запушить → откатить» тот же, что у направлений.
    prev = await settings_store.get_acl_rules(session)
    await settings_store.set_acl_rules(session, rules_dicts)
    err = await policy_apply.push_policy(session, client, settings)
    if err:
        await settings_store.set_acl_rules(session, prev)
        await policy_apply.apply_policy(session, client, settings)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err)
    generated = await policy_apply.build_policy(session, client, settings, nodes) or ""
    await audit.record(
        session, user.username, "acl_rules_set", "", f"{len(rules_dicts)} правил"
    )
    return AclRulesOut(rules=body.rules, generated=generated)


@router.post("/resolve-host", response_model=ResolveHostOut)
async def resolve_host(body: ResolveHostIn, _: CurrentUser) -> ResolveHostOut:
    """Резолвит домен «сайта» в IP, чтобы запинить его в правило (ACL умеет только
    IP, не URL). Если ввели уже IP/подсеть — возвращаем как есть. IP — снимок: у
    сайта они могут смениться, тогда правило надо обновить.

    ТОЛЬКО IPv4: тайлнет v4-only, AAAA-адрес в правиле — мёртвый груз, который
    вдобавок выглядит так, будто IPv6 в сети всё-таки есть."""
    host = body.host.strip()
    try:
        net = ipaddress.ip_network(host, strict=False)
        if net.version != 4:
            return ResolveHostOut(host=host, ips=[], note="IPv6 не используется")
        return ResolveHostOut(host=host, ips=[host])
    except ValueError:
        pass
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, host, None, socket.AF_INET)
        ips = sorted({i[4][0] for i in infos})
        return ResolveHostOut(host=host, ips=ips, note="" if ips else "нет адресов")
    except Exception as e:  # noqa: BLE001 — резолв не должен ронять запрос
        return ResolveHostOut(host=host, ips=[], note=str(e)[:100])
