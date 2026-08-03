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
