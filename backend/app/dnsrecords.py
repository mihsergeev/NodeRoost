"""Имена внутри сети: DNS-записи, которые headscale раздаёт нодам меша.

Зачем. Сервис живёт на публичном имени, но закрыт вайтлистом или вовсе не имеет
открытых наружу портов. Внутри меша он доступен по своему адресу — не хватает
только имени: браузер спрашивает DNS, получает публичный адрес и идёт через
интернет, где его и разворачивают.

Что делает панель. Раздаёт нодам запись «имя → адрес в меше». **Публичный DNS
она не трогает**: снаружи имя ведёт туда же, куда вело, и кто не в меше — заходит
как раньше. Меняется только то, что видят машины сети.

Как это устроено у headscale (`dns.extra_records_path`): панель пишет JSON-файл,
headscale следит за ним и перечитывает при изменении — **без перезапуска**.
Перезапуск нужен ровно один раз, чтобы прописать путь в config.yaml.

Две вещи, на которых легко обжечься, — обе учтены здесь:

* **Файл должен существовать до старта headscale.** С `extra_records_path` на
  несуществующий файл он не поднимается вовсе (os.Stat в конструкторе следилки).
  Поэтому файл пишется ПЕРВЫМ, и только потом путь попадает в конфиг.
* **Адрес нельзя запоминать.** Нода после переподключения — уже другая запись у
  headscale и, вообще говоря, другой адрес. Панель хранит намерение («это имя —
  на эту ноду»), а адрес подставляет текущий, каждый раз заново.
"""

import ipaddress
import json
import logging
import os
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app import settings_store
from app.config import Settings

log = logging.getLogger("noderoost.dnsrecords")


def entries_for(stored: list[dict], nodes: list[dict]) -> list[dict]:
    """Готовые записи для headscale из того, что задал админ, и текущих нод.

    Запись, привязанная к ноде, берёт ВСЕ её адреса в меше (обычно один IPv4).
    Ноды нет — записи нет: имя, ведущее на адрес удалённой машины, рано или
    поздно приведёт на чужую (адреса переиспользуются).
    """
    by_id = {str(n.get("id", "")): n for n in nodes}
    out: list[dict] = []
    for rec in stored:
        name = str(rec.get("name") or "").strip().lower()
        if not name:
            continue
        # Выключенное имя остаётся в списке, но нодам не раздаётся: внутри сети
        # оно снова ведёт туда же, куда снаружи. Так «внутрь» и «как обычно»
        # переключаются, не теряя саму запись. Старые записи поля не знают —
        # отсутствие читаем как «включено».
        if not rec.get("enabled", True):
            continue
        node_id = str(rec.get("node_id") or "")
        if node_id:
            node = by_id.get(node_id)
            if node is None:
                continue
            addrs = [str(a) for a in (node.get("ipAddresses") or [])]
        else:
            addrs = [str(rec.get("ip") or "")]
        for a in addrs:
            try:
                ip = ipaddress.ip_address(a)
            except ValueError:
                continue
            out.append(
                {
                    "name": name,
                    "type": "A" if ip.version == 4 else "AAAA",
                    "value": str(ip),
                }
            )
    return out


def read_file(path: str) -> list[dict]:
    """Что сейчас раздаёт headscale. Нет файла или он битый — пустой список."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def write_file(path: str, entries: list[dict]) -> bool:
    """Записать файл целиком. True — если содержимое изменилось.

    Пишем через временный файл и os.replace: headscale читает файл по событию
    изменения и иначе поймал бы его недописанным. Подмену по rename он переживает
    — теряет слежку, находит файл заново и перечитывает (см. Run() в его
    extrarecords.go), а вот пустой JSON в ответ на полузаписанный файл снял бы
    все имена разом.
    """
    data = json.dumps(entries, ensure_ascii=False, indent=2) + "\n"
    try:
        if Path(path).read_text(encoding="utf-8") == data:
            return False  # нечего менять — не дёргаем следилку headscale зря
    except OSError:
        pass
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return True


def hs_path_configured(cfg: dict, hs_path: str) -> bool:
    """Прописан ли в config.yaml путь к НАШЕМУ файлу (иначе записи не раздаются)."""
    dns = cfg.get("dns") if isinstance(cfg, dict) else None
    if not isinstance(dns, dict):
        return False
    return str(dns.get("extra_records_path") or "") == hs_path


async def sync(session: AsyncSession, settings: Settings, nodes: list[dict]) -> bool:
    """Привести файл в соответствие с записями панели и текущими нодами.

    Зовётся из коллектора раз в цикл: нода переподключилась и получила другой
    адрес, ноду удалили — имя должно перестать врать само, без похода в UI.
    """
    stored = await settings_store.get_dns_records(session)
    path = settings.headscale_extra_records_path
    if not stored and not os.path.exists(path):
        return False  # фичей не пользовались — не сорим файлом в каталоге конфига
    return write_file(path, entries_for(stored, nodes))

# These are local to each Tailscale client, not recursive upstreams.
MESH_RESOLVERS = {
    ipaddress.ip_address("100.100.100.100"),
    ipaddress.ip_address("fd7a:115c:a1e0::53"),
}


def _is_mesh_resolver(value: str) -> bool:
    try:
        addr = ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError:
        return False
    return (getattr(addr, "ipv4_mapped", None) or addr) in MESH_RESOLVERS


def is_recursive_split_route(upstreams: list[str]) -> bool:
    return isinstance(upstreams, list) and bool(upstreams) and all(
        _is_mesh_resolver(value) for value in upstreams
    )


def routes_for_split(
    records: list[dict], base_domain: str = "", upstreams: list[str] | None = None
) -> dict[str, list[str]]:
    """Маршруты extra records, которые клиент должен обрабатывать через MagicDNS.

    Для публичного имени маршрут охватывает само имя и его поддомены, но не
    соседние имена. Неизвестные поддомены идут к заданным администратором
    резолверам. Для изолированных зон и при отсутствии upstream пустой список
    означает локальный авторитетный ответ (NXDOMAIN для неизвестного имени).
    Quad100 нельзя использовать как upstream: запрос вернется к тому же клиенту.
    """
    from app import ca  # локально: ca тянет список TLD с диска

    public = set(ca.public_tlds())
    base = (base_domain or "").strip().strip(".").lower()
    resolvers = [value for value in upstreams or [] if not _is_mesh_resolver(value)]
    out: dict[str, list[str]] = {}
    for rec in records or []:
        if not rec.get("enabled", True):
            continue
        name = str(rec.get("name") or "").strip().strip(".").lower()
        if not name or name == base or (base and name.endswith("." + base)):
            continue
        if ca.tld_of(name) in public:
            out[name] = list(resolvers)
        else:
            out[zone_of(name)] = []
    return dict(sorted(out.items()))


def merge_split_routes(
    existing: dict[str, list[str]],
    previous: dict[str, list[str]],
    desired: dict[str, list[str]],
    *,
    keep_unused_private: bool = False,
) -> dict[str, list[str]]:
    """Обновить маршруты панели и убрать старые петли, сохранив ручные маршруты."""
    from app import ca

    public = set(ca.public_tlds())
    result = dict(existing)
    for name, values in existing.items():
        legacy = is_recursive_split_route(values)
        managed = legacy or (name in previous and values == previous[name])
        if not managed:
            continue
        if name in desired:
            result[name] = desired[name]
        elif keep_unused_private and name != "." and ca.tld_of(name) not in public:
            result[name] = []
        else:
            del result[name]
    for name, values in desired.items():
        result.setdefault(name, values)
    return result


def zone_of(name: str) -> str:
    """Домен имени: nas.mesh -> mesh, loki.lab -> lab."""
    name = (name or "").strip().strip(".").lower()
    return name.split(".", 1)[1] if "." in name else name
