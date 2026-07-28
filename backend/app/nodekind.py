"""Тип ноды (server/device): авто-определение + ручное переопределение из node_meta.

Вынесено отдельно, чтобы и api/nodes, и генерация политики (policy_apply) считали
тип одинаково, без циклических импортов.
"""

EXIT_ROUTES = ("0.0.0.0/0", "::/0")


def node_tags(node: dict) -> list[str]:
    """Теги ноды из ответа headscale.

    В 0.29 поле одно — `tags`; до этого их было два, `forcedTags` и `validTags`.
    Мы читали только старые, поэтому после апгрейда теги навешивались, но панель
    их не видела — а следующее сохранение затирало пустым списком. Имя поля
    держим в одном месте, чтобы следующая такая смена чинилась однажды.
    """
    if isinstance(node.get("tags"), list):
        return list(node["tags"])
    forced = node.get("forcedTags") or []
    valid = node.get("validTags") or []
    return list(dict.fromkeys([*valid, *forced]))


def editable_tags(node: dict) -> list[str]:
    """Теги, которые панель вправе переписать.

    В 0.29 различия нет — все теги приходят одним списком. В старой схеме
    редактируемыми были только forcedTags: validTags могли достаться ноде от
    ключа подключения, и затирать их панель не должна.
    """
    if isinstance(node.get("tags"), list):
        return list(node["tags"])
    return list(node.get("forcedTags") or [])


def guess_kind(node: dict) -> str:
    """Авто-определение типа: только по тому, что решил АДМИН.

    Тип ноды — не косметика: от него зависят изоляция устройств (устройство
    никогда не бывает назначением), состав «всех серверов» в правилах и право
    быть шлюзом выхода. Поэтому судим исключительно по признакам, которые
    подтвердил админ: теги (их ставит панель) и ОДОБРЕННЫЕ маршруты.

    Раньше сюда входил и `availableRoutes` — то, что нода АНОНСИРУЕТ О СЕБЕ САМА,
    без чьего-либо согласия. Достаточно было одной команды на взломанном ноутбуке
    (`tailscale set --advertise-routes=192.168.0.0/24`), чтобы он через минуту
    стал «сервером»: попал в server_ips, выпал из device_ips (то есть перестал
    отсекаться фильтром изоляции и стал законной целью правил «все серверы»/«*»)
    и получил доступы, выданные всем серверам. Классификация не может опираться
    на данные, которые контролирует классифицируемый.
    """
    approved = node.get("approvedRoutes", []) or []
    approved_nonexit = [r for r in approved if r not in EXIT_ROUTES]
    is_exit = "0.0.0.0/0" in approved
    if node_tags(node) or is_exit or approved_nonexit:
        return "server"
    return "device"


def effective_kind(node: dict, meta: dict | None) -> str:
    """Ручной тип из node_meta, иначе авто-определение."""
    entry = (meta or {}).get(str(node.get("id", ""))) or {}
    k = entry.get("kind", "")
    return k if k in ("server", "device") else guess_kind(node)
