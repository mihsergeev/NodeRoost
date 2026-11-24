"""Кто получает имя внутри сети, но не дойдет до его цели.

Имена внутри сети headscale раздает ВСЕМ нодам, которые берут DNS из меша:
выдать имя одним нодам и не выдать другим он не умеет. Доступ же к цели
определяют правила. Нода без правила до цели получает вместо внешнего адреса
имени адрес в меше, пира с этим адресом в ее карте нет, и пакеты уходят в обычный
шлюз и пропадают. Снаружи это выглядит как «после подключения к мешу сервис
перестал отвечать»: так агент мониторинга на новом сервере перестал доходить до
своей панели, имя которой вело внутрь сети.

Считаем по УЖЕ собранной политике (policy_apply.build_policy): в нее входят и
правила из «Доступов», и синтетические - админ-устройства, направления, шлюзы, -
а второй логики доступа, которая могла бы разойтись с настоящей, нет.
"""

from __future__ import annotations

import ipaddress
import json

from app.aclgen import VIS_PORT, _ipv4
from app.nodekind import node_tags


def _split_dst(entry: str) -> tuple[str, str]:
    """«100.64.0.5:443» -> («100.64.0.5», «443»), «tag:web:*» -> («tag:web», «*»)."""
    host, _, ports = str(entry).rpartition(":")
    return (host, ports) if host else (str(entry), "*")


def _matches(selector: str, ip: str, tags: list[str]) -> bool:
    if selector == "*" or selector == ip or selector in tags:
        return True
    if "/" in selector and ip:
        try:
            return ipaddress.ip_address(ip) in ipaddress.ip_network(selector, strict=False)
        except ValueError:
            return False
    return False


def _reachable(acls: list[dict], src_ip: str, src_tags: list[str], dst_ip: str, dst_tags: list[str]) -> bool:
    """Есть ли правило src -> dst хотя бы на одном порту. Правило только на
    «глухой» порт видимости шлюза (VIS_PORT) доступом к сервисам не считается."""
    for acl in acls:
        if acl.get("action", "accept") != "accept":
            continue
        if not any(_matches(str(s), src_ip, src_tags) for s in acl.get("src") or []):
            continue
        for entry in acl.get("dst") or []:
            host, ports = _split_dst(entry)
            if ports != VIS_PORT and _matches(host, dst_ip, dst_tags):
                return True
    return False


def unreachable(policy: str | None, records: list[dict], nodes: list[dict], meta: dict) -> dict[str, list[dict]]:
    """{имя: [{id, name}]} - ноды, которые получают имя и не дойдут до цели.

    Получает имя нода, у которой DNS из меша не выключен явно (mesh_dns=False):
    по умолчанию tailscale DNS из меша берет. Цель - нода записи или нода с
    адресом из записи; адрес вне меша (сеть за роутером) так не оценить -
    пропускаем. Выключенные имена нодам не раздаются - тоже пропускаем.
    """
    if not policy:
        return {}
    try:
        acls = json.loads(policy).get("acls") or []
    except (ValueError, AttributeError):
        return {}
    info = {}
    for n in nodes:
        nid = str(n.get("id", ""))
        info[nid] = (_ipv4(n) or "", node_tags(n), str(n.get("givenName") or n.get("name") or nid))
    by_ip = {ip: nid for nid, (ip, _, _) in info.items() if ip}
    out: dict[str, list[dict]] = {}
    for rec in records:
        if not rec.get("enabled", True):
            continue
        target = str(rec.get("node_id") or "") or by_ip.get(str(rec.get("ip") or ""), "")
        if target not in info:
            continue
        t_ip, t_tags, _ = info[target]
        if not t_ip:
            continue
        lost = []
        for nid, (ip, tags, name) in info.items():
            if nid == target or not ip:
                continue
            if (meta.get(nid) or {}).get("mesh_dns") is False:
                continue
            if not _reachable(acls, ip, tags, t_ip, t_tags):
                lost.append({"id": nid, "name": name})
        if lost:
            out[str(rec.get("name") or "")] = sorted(lost, key=lambda x: x["name"])
    return out
