"""Генерация HuJSON-политики headscale из визуальных правил панели.

Правило = Источник → Назначение : Порт(ы). Источник/назначение — селектор:
- any  → «*» (кто угодно / куда угодно)
- node → конкретная нода (по id панель резолвит её tailnet-IPv4)
(сущности «пользователь» в панели нет: субъект доступа — сама нода/устройство)
- tag  → роль (тег headscale «tag:<роль>») — группа нод с этим тегом

Всё, что не разрешено явным правилом, режется (родной default-deny Tailscale,
как только в политике есть непустой acls). Пустой список правил = запрещено всё.
Для каждого использованного тега эмитим tagOwners (иначе headscale не валидирует
политику); владелец — переданный tag_owner (обычно дефолтный пользователь).
"""

import ipaddress
import json


def _ipv4(node: dict) -> str | None:
    for ip in node.get("ipAddresses", []) or []:
        if ":" not in ip:  # IPv4 (у IPv6 есть двоеточия)
            return ip
    return None


def _node_tags(node: dict) -> list[str]:
    """Теги ноды. Импортируем лениво, чтобы не тянуть nodekind в генератор."""
    from app.nodekind import node_tags

    return node_tags(node)


def _tag(value: str) -> str:
    v = value.strip()
    return v if v.startswith("tag:") else f"tag:{v}"


# Диапазон адресов меша. Tailscale требует, чтобы тайлнет лежал внутри CGNAT
# 100.64.0.0/10, поэтому проверять достаточно его.
MESH_RANGE = ipaddress.ip_network("100.64.0.0/10")

# Порт правила видимости шлюза выхода (9/discard — заведомо никем не слушается).
# Смысл правила — только показать шлюз в netmap источника, а не дать к нему доступ.
VIS_PORT = "9"

# Владелец всех тегов политики — заведомо ПУСТАЯ группа: теги ставит только панель
# (админским API), и ни одна нода не может присвоить их себе сама. См. пояснение
# в generate_policy, где формируется tagOwners.
TAG_OWNER_GROUP = "group:noderoost-tags"


def touches_mesh(dst: str) -> bool:
    """Адрес или сеть, попадающая в диапазон меша — в ЛЮБОЙ записи, включая /32.

    Для МАРШРУТА (что нода анонсирует) и для НАПРАВЛЕНИЯ («ходить туда-то через
    ноду») это всегда ошибка: ноды меша достижимы напрямую, а маршрут внутрь
    тайлнета означает перехват трафика к соседу. Здесь поблажки для одиночного
    адреса нет и быть не может — именно точечный перехват и опаснее всего.
    """
    try:
        net = ipaddress.ip_network(dst, strict=False)
    except ValueError:
        return False
    return net.version == 4 and net.overlaps(MESH_RANGE)


def covers_mesh(dst: str, devices: set[str] | None = None) -> bool:
    """Назначение ПРАВИЛА ДОСТУПА, которое пропускать нельзя. Два случая.

    1. Сеть содержит адрес устройства — в любой записи. Сравнение строк тут не
       годится: «100.64.0.3» и «100.64.0.3/32» — разные строки и один адрес.
       Ровно так инвариант изоляции однажды и обошли: голый адрес отбрасывался,
       а тот же адрес с «/32» проезжал мимо обоих фильтров.
    2. Широкая сеть, задевающая меш («0.0.0.0/0» и подобные) — открывает разом
       всё, не совпадая буквально ни с одним адресом.

    Одиночный адрес СЕРВЕРА при этом законная цель, поэтому исключение по длине
    префикса оставлено только для второго случая. Нечисловые селекторы (tag:*,
    autogroup:internet) сюда не попадают — они не разбираются как сеть;
    autogroup:internet остаётся законным выходом наружу, он не включает тайлнет.
    """
    try:
        net = ipaddress.ip_network(dst, strict=False)
    except ValueError:
        return False
    if net.version != 4:
        return False
    if any(ipaddress.ip_address(d) in net for d in (devices or set()) if "/" not in d):
        return True
    return net.prefixlen < 32 and net.overlaps(MESH_RANGE)


def _resolve(sel: dict, by_id: dict[str, str], server_ips: list[str]) -> list[str]:
    """Селектор → список адресов политики (обычно один; servers — все серверы)."""
    kind = sel.get("kind")
    if kind == "any":
        return ["*"]
    if kind == "tag":
        v = (sel.get("value") or "").strip()
        return [_tag(v)] if v else []
    if kind == "servers":
        return list(server_ips)  # все ноды-серверы (по IP)
    if kind == "internet":
        # выход в интернет через exit-node (autogroup:internet = публичный интернет,
        # без тайлнета/приватных сетей). Проверено: headscale 0.29 принимает.
        return ["autogroup:internet"]
    if kind == "cidr":
        # конкретный IP или подсеть (напр. 8.8.8.8, 10.0.0.0/8) — «сайт» пинится
        # в IP на стороне панели (резолвер) и хранится тоже как cidr.
        # IPv6 отбрасываем: тайлнет v4-only, такие правила — мёртвый груз
        # (старые могли сохраниться до того, как резолвер стал v4-only).
        v = (sel.get("value") or "").strip()
        if not v:
            return []
        try:
            if ipaddress.ip_network(v, strict=False).version != 4:
                return []
        except ValueError:
            return []
        return [v]
    if kind == "node":
        ip = by_id.get(str(sel.get("value") or ""))  # None если нода удалена
        return [ip] if ip else []
    return []


def generate_policy(
    rules: list[dict],
    nodes: list[dict],
    tag_owner: str = "default",
    server_ips: list[str] | None = None,
    device_ips: list[str] | None = None,
    extra_tags: list[str] | None = None,
    exit_via: list[dict] | None = None,
) -> str:
    """rules — список {src, dst, ports}; nodes — сырые ноды headscale;
    server_ips — IP всех нод-серверов (для селектора servers = «все серверы»);
    device_ips — IP всех устройств (они НИКОГДА не могут быть назначением).

    exit_via — правила выхода в интернет через РАЗРЕШЁННЫЕ exit-ноды: список
    {"src": ip_устройства, "via": [теги шлюзов]}. Эмитятся как grants с полем
    `via` (headscale 0.29): устройство видит в трее и может выходить в интернет
    ТОЛЬКО через ноды с этими тегами. Так «юзеру A можно через ноды 1,2, юзеру B
    только через 1» — headscale фильтрует и видимость, и сам выход по via."""
    server_ips = server_ips or []
    devices = set(device_ips or [])
    by_id: dict[str, str] = {}
    device_tags: set[str] = set()
    for n in nodes:
        ip = _ipv4(n)
        if ip:
            by_id[str(n.get("id", ""))] = ip
        if ip in devices:
            # тег, висящий на устройстве, нельзя пускать в назначение: headscale
            # развернёт его и в это устройство — снова взаимная видимость
            device_tags.update(_node_tags(n))
    acls: list[dict] = []
    used_tags: set[str] = set()
    for r in rules:
        srcs = _resolve(r.get("src") or {}, by_id, server_ips)
        dsts = _resolve(r.get("dst") or {}, by_id, server_ips)
        # ИНВАРИАНТ: устройство не бывает назначением — иначе устройства увидят
        # друг друга в netmap (в Tailscale грант делает стороны взаимно видимыми).
        # Фильтруем здесь, а не в UI: так правило не протащить ни через API, ни
        # через «cidr» с tailnet-IP устройства, ни выбором ноды-устройства целью.
        # «*» как назначение тоже опасен (охватывает и устройства) → это «все серверы».
        dsts = [
            d
            for d in dsts
            if d not in devices and d not in device_tags and not covers_mesh(d, devices)
        ]
        if "*" in dsts:
            dsts = [d for d in dsts if d != "*"] + list(server_ips)
        # Нода сама себе доступ не выдаёт: ACL не фильтрует трафик ноды к
        # собственному адресу, так что правило инертно и остаётся мусором в
        # политике. Проверка здесь, а не только в модалке: через API и через
        # выдачу внутри карточки ноды такое правило раньше проходило.
        #
        # РОЛЬ на саму себя при этом НЕ трогаем: «tag:web → tag:web» означает
        # «серверы этой роли видят друг друга» — законное правило, которое просто
        # ждёт второго участника. Отбросить его значило бы решить за админа.
        if (
            (r.get("src") or {}).get("kind") == "node"
            and (r.get("dst") or {}).get("kind") == "node"
            and srcs == dsts
        ):
            continue
        if not srcs or not dsts:
            continue  # нода удалена / пустой селектор / цель-устройство — пропускаем
        ports = (r.get("ports") or "*").strip() or "*"
        acls.append(
            {"action": "accept", "src": srcs, "dst": [f"{d}:{ports}" for d in dsts]}
        )
        for s in (*srcs, *dsts):
            if s.startswith("tag:"):
                used_tags.add(s)
    # Теги, которые ЕЩЁ нигде не использованы, тоже надо объявить: headscale
    # отказывается навешивать на ноду тег, отсутствующий в tagOwners политики.
    # Без этого роль невозможно создать в принципе — её не назначить, пока она не
    # в правиле, и не добавить в правило, пока она не назначена.
    for tg in extra_tags or []:
        v = (tg or "").strip()
        if v:
            used_tags.add(_tag(v))
    # grants выхода через разрешённые exit-ноды (via). Каждый via-тег тоже надо
    # объявить в tagOwners, иначе headscale отвергнет политику.
    grants: list[dict] = []
    for g in exit_via or []:
        src_ip = str(g.get("src") or "")
        # ИНВАРИАНТ: шлюзом выхода не может быть устройство. Иначе тег устройства
        # попал бы в назначение (правило видимости ниже) — и устройства увидели бы
        # друг друга, в обход изоляции. Держим в движке: через API ноду можно
        # пометить шлюзом, минуя UI, где галка есть только у серверов.
        vias = [_tag(v) for v in (g.get("via") or []) if v and _tag(v) not in device_tags]
        if not src_ip or not vias:
            continue
        grants.append(
            {"src": [src_ip], "dst": ["autogroup:internet"], "via": vias, "ip": ["*"]}
        )
        # Источник ДОЛЖЕН видеть шлюз в netmap: иначе `tailscale set --exit-node`
        # падает с «no node found in netmap», а в трее шлюз не появляется. Грант
        # `autogroup:internet via tag` видимость пира сам по себе НЕ даёт (это
        # всплыло вживую: не-админ-нода не видела свой шлюз).
        #
        # Порт здесь ЗАВЕДОМО глухой (9/discard), а не «*»: правило нужно ровно для
        # того, чтобы шлюз появился в netmap и поднялся туннель, — сам выход в
        # интернет разрешает грант выше. С «*» разрешение «выходить в интернет»
        # тихо давало бы ещё и полный доступ к сервисам шлюза (SSH, БД), причём
        # невидимый в «Доступах»: правило синтетическое и в acl_rules не хранится.
        acls.append(
            {"action": "accept", "src": [src_ip], "dst": [f"{v}:{VIS_PORT}" for v in vias]}
        )
        used_tags.update(vias)
    policy: dict = {}
    if used_tags:
        # Владелец тегов — ПУСТАЯ группа, а не пользователь, которому принадлежат
        # ноды. В модели Tailscale tagOwners = «кто вправе навесить этот тег», и
        # пока владельцем был `<default_user>@`, любая нода этого пользователя (то
        # есть ЛЮБАЯ нода тайлнета) могла присвоить себе тег сама, через
        # `tailscale up --advertise-tags`: взять чужую роль с её доступами или
        # выдать себя за шлюз выхода (tag:xgw-*) и принимать чужой интернет-трафик.
        # Группа без участников закрывает это: навесить тег не может НИКТО из нод.
        # Панели это не мешает — она ставит теги админским API (forced tags),
        # который принадлежность к владельцу не проверяет (проверено на 0.29.2).
        policy["groups"] = {TAG_OWNER_GROUP: []}
        policy["tagOwners"] = {t: [TAG_OWNER_GROUP] for t in sorted(used_tags)}
    policy["acls"] = acls
    if grants:
        policy["grants"] = grants
    return json.dumps(policy, indent=2, ensure_ascii=False)
