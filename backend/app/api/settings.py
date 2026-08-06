import os
import re
import time
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml
from fastapi import APIRouter, HTTPException, Request, status

from app import audit, certs, dnsrecords, settings_store, tsmirror
from app.config import get_settings
from app.deps import CurrentUser, SessionDep
from app.hs_client import get_client
from app.hs_util import hs_call, norm_ts, require_hs
from app.schemas import (
    ApiKeyCreatedOut,
    ApiKeyCreateIn,
    ApiKeyExpireIn,
    ApiKeyOut,
    DerpInfo,
    DnsInfo,
    DnsRecordOut,
    DnsRecordsOut,
    DnsRecordsUpdateIn,
    DnsUpdateIn,
    HsInfoOut,
    NetworkUpdateIn,
    TsCheckOut,
    TsLatestOut,
    TsMirrorFile,
    TsMirrorOut,
    TsVersionIn,
    TsVersionOut,
)

TS_PKG_BASE = "https://pkgs.tailscale.com/stable"
# кэш ответа «последняя версия»: (когда получили, что получили)
_LATEST_TTL = 24 * 3600
_latest_cache: tuple[float, "TsLatestOut"] | None = None

router = APIRouter(tags=["settings"])


def _is_panel_key(prefix: str, panel_key: str) -> bool:
    """headscale маскирует префикс как «<реальный>***». Наш ключ — тот, что
    начинается с реального префикса (работает и для старого формата
    «xxxxxxx.секрет», и для нового «hskey-api-xxxxx-секрет»)."""
    real = prefix.rstrip("*")
    return bool(panel_key and real and panel_key.startswith(real))


def _key_prefix(full: str) -> str:
    """Несекретный префикс ключа — для журнала аудита и показа в UI.

    ВАЖНО: не возвращать ключ целиком ни при каком формате. У нового формата
    headscale «hskey-api-<pfx>-<секрет>» точки НЕТ, и наивный split(".") отдавал
    весь секрет — он утекал в audit_log, в UI «Журнал» и в бэкапы.
    """
    if "." in full:  # старый формат «<pfx>.<секрет>»
        return full.split(".", 1)[0]
    parts = full.split("-")
    if full.startswith("hskey-") and len(parts) >= 4:  # hskey-api-<pfx>-<секрет>
        return "-".join(parts[:3])
    return full[:8]  # неизвестный формат — режем жёстко, секрет наружу не пускаем


def _map_key(k: dict, panel_key: str) -> ApiKeyOut:
    prefix = k.get("prefix", "")
    return ApiKeyOut(
        id=str(k.get("id", "")),
        prefix=prefix,
        expiration=norm_ts(k.get("expiration")),
        created_at=norm_ts(k.get("createdAt")),
        last_seen=norm_ts(k.get("lastSeen")),
        is_panel=_is_panel_key(prefix, panel_key),
    )


@router.get("/apikeys", response_model=list[ApiKeyOut])
async def list_apikeys(_: CurrentUser) -> list[ApiKeyOut]:
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    keys = await hs_call(client.list_apikeys())
    return [_map_key(k, settings.headscale_api_key) for k in keys]


@router.post("/apikeys", response_model=ApiKeyCreatedOut)
async def create_apikey(
    body: ApiKeyCreateIn, request: Request, user: CurrentUser, session: SessionDep
) -> ApiKeyCreatedOut:
    settings = get_settings()
    require_hs(settings)
    client = get_client(settings)
    exp = datetime.now(timezone.utc) + timedelta(days=body.expiration_days)
    exp_iso = exp.isoformat().replace("+00:00", "Z")
    full = await hs_call(client.create_apikey(exp_iso))
    await audit.record(session, user.username, "apikey_create", _key_prefix(full))
    return ApiKeyCreatedOut(
        api_key=full,  # полный ключ показывается ОДИН раз в ответе, нигде не хранится
        prefix=_key_prefix(full),
        expiration=exp_iso,
    )


@router.post("/apikeys/expire", status_code=status.HTTP_204_NO_CONTENT)
async def expire_apikey(
    body: ApiKeyExpireIn, request: Request, user: CurrentUser, session: SessionDep
) -> None:
    settings = get_settings()
    require_hs(settings)
    if _is_panel_key(body.prefix, settings.headscale_api_key):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Нельзя истечь ключ, которым работает сама панель",
        )
    client = get_client(settings)
    await hs_call(client.expire_apikey(body.prefix))
    await audit.record(session, user.username, "apikey_expire", body.prefix)


def _read_hs_config(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001 — конфиг может быть недоступен
        return {}


def _restart_flag_path(config_path: str) -> str:
    # флаг рядом с каталогом конфига: /data/headscale/.restart-request
    return os.path.join(os.path.dirname(os.path.dirname(config_path)), ".restart-request")


def _edit_hs_config(config_path: str, mutate) -> None:
    """Правит config.yaml, сохраняя остальное (комментарии/порядок), делает .bak и
    ставит флаг перезапуска headscale. mutate(data) меняет нужную секцию на месте."""
    from ruamel.yaml import YAML

    y = YAML()
    y.preserve_quotes = True
    with open(config_path, encoding="utf-8") as f:
        data = y.load(f)
    if data is None:
        raise ValueError("config.yaml пуст или не читается")
    mutate(data)
    shutil.copy2(config_path, config_path + ".bak")
    tmp = config_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        y.dump(data, f)
    os.replace(tmp, config_path)
    Path(_restart_flag_path(config_path)).touch()


def _write_dns_config(
    config_path: str,
    magic_dns: bool,
    base_domain: str,
    nameservers: list[str],
    override_local: bool = False,
) -> None:
    def mut(data: dict) -> None:
        dns = data.get("dns")
        if not isinstance(dns, dict):
            dns = {}
            data["dns"] = dns
        dns["magic_dns"] = magic_dns
        dns["base_domain"] = base_domain
        ns = dns.get("nameservers")
        if not isinstance(ns, dict):
            ns = {}
            dns["nameservers"] = ns
        ns["global"] = list(nameservers)
        # headscale НЕ СТАРТУЕТ с пустым списком серверов при override_local_dns:
        # «dns.nameservers.global must be set when dns.override_local_dns is true».
        # Поэтому без списка флаг обязан быть выключен: очистка списка в панели
        # роняла control-сервер целиком, и панель теряла с ним связь.
        #
        # Сам флаг — отдельный выбор администратора. Он подменяет резолвер НА
        # КАЖДОЙ ноде: сервер, который ходил во внутренний DNS компании или в
        # DNS облака, перестаёт его видеть. Раньше он включался сам, стоило
        # вписать резолверы, — вписать их и потерять внутренние имена оказывалось
        # одним действием.
        dns["override_local_dns"] = bool(nameservers) and bool(override_local)

    _edit_hs_config(config_path, mut)


def _write_network_config(config_path: str, v4: str, allocation: str) -> None:
    def mut(data: dict) -> None:
        pref = data.get("prefixes")
        if not isinstance(pref, dict):
            pref = {}
            data["prefixes"] = pref
        if v4:
            pref["v4"] = v4
        pref.pop("v6", None)  # тайлнет только IPv4 — v6 не используется
        if allocation:
            pref["allocation"] = allocation

    _edit_hs_config(config_path, mut)


@router.put("/hs-info/dns", response_model=HsInfoOut)
async def update_dns(
    body: DnsUpdateIn, request: Request, user: CurrentUser, session: SessionDep
) -> HsInfoOut:
    """Правит DNS/MagicDNS в config.yaml и просит хост перезапустить headscale.
    ВНИМАНИЕ: смена base_domain меняет MagicDNS-имена всех нод."""
    settings = get_settings()
    cfg = _read_hs_config(settings.headscale_config_path)
    server_host = (urlparse(str(cfg.get("server_url", ""))).hostname or "").lower()
    bd = body.base_domain
    if bd and server_host and (server_host == bd or server_host.endswith("." + bd)):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Базовый домен не должен совпадать с доменом control-сервера или быть его суффиксом",
        )
    try:
        _write_dns_config(
            settings.headscale_config_path,
            body.magic_dns,
            bd,
            body.nameservers,
            body.override_local_dns,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Не удалось записать config.yaml: {e}",
        ) from e
    await audit.record(session, user.username, "dns_update", bd)
    return await hs_info(user)


def _write_extra_records_path(config_path: str, hs_path: str) -> None:
    """Разовая правка config.yaml: показать headscale наш файл с именами."""

    def mut(data: dict) -> None:
        dns = data.get("dns")
        if not isinstance(dns, dict):
            dns = {}
            data["dns"] = dns
        dns.pop("extra_records_path", None)
        if hasattr(dns, "insert"):  # CommentedMap: ставим ключ ПЕРВЫМ в блоке
            # Новый ключ ruamel дописывает в самый конец блока, а конец блока —
            # это уже после комментария, который относится к следующей секции
            # конфига. Строка остаётся валидной, но читается как чужая: висит
            # под чужим заголовком, и удалить её при правке той секции — дело
            # одного движения.
            dns.insert(0, "extra_records_path", hs_path)
        else:
            dns["extra_records_path"] = hs_path
        # Список прямо в конфиге и файл — два источника одного и того же. Оставить
        # оба значит спорить самим с собой: что победит, по конфигу не прочитать.
        dns.pop("extra_records", None)

    _edit_hs_config(config_path, mut)


async def _hs_nodes() -> list[dict]:
    """Ноды из headscale; он недоступен — пустой список (панель не падает)."""
    settings = get_settings()
    if not settings.headscale_api_key:
        return []
    try:
        return await get_client(settings).get_nodes()
    except Exception:  # noqa: BLE001 — состояние нод тут только для показа
        return []


async def _records_out(
    session, stored: list[dict], nodes: list[dict], cfg: dict, config_path: str
) -> DnsRecordsOut:
    settings = get_settings()
    by_id = {str(n.get("id", "")): n for n in nodes}
    out: list[DnsRecordOut] = []
    for rec in stored:
        node_id = str(rec.get("node_id") or "")
        node = by_id.get(node_id)
        addrs = (
            [str(a) for a in (node.get("ipAddresses") or [])]
            if node
            else ([str(rec.get("ip"))] if rec.get("ip") else [])
        )
        note = ""
        if node_id and node is None:
            note = "нода не найдена" if nodes else "headscale недоступен"
        cert = await certs.get(session, str(rec.get("name") or "")) if rec.get("cert") else None
        out.append(
            DnsRecordOut(
                name=str(rec.get("name") or ""),
                node_id=node_id,
                node_name=str(node.get("givenName") or node.get("name") or "")
                if node
                else "",
                ip=str(rec.get("ip") or ""),
                enabled=bool(rec.get("enabled", True)),
                cert=bool(rec.get("cert", False)),
                cert_status=(cert.status if cert else ("issuing" if rec.get("cert") else "")),
                cert_until=(
                    cert.not_after.date().isoformat() if cert and cert.not_after else ""
                ),
                cert_error=(cert.error if cert else ""),
                addresses=addrs,
                note=note,
            )
        )
    return DnsRecordsOut(
        records=out,
        active=dnsrecords.hs_path_configured(
            cfg, settings.headscale_extra_records_path_in_hs
        ),
        restart_pending=os.path.exists(_restart_flag_path(config_path)),
    )


@router.get("/hs-info/dns-records", response_model=DnsRecordsOut)
async def list_dns_records(_: CurrentUser, session: SessionDep) -> DnsRecordsOut:
    """Имена, которые панель раздаёт внутри меша (снаружи DNS не меняется)."""
    settings = get_settings()
    stored = await settings_store.get_dns_records(session)
    return await _records_out(
        session,
        stored,
        await _hs_nodes(),
        _read_hs_config(settings.headscale_config_path),
        settings.headscale_config_path,
    )


@router.put("/hs-info/dns-records", response_model=DnsRecordsOut)
async def update_dns_records(
    body: DnsRecordsUpdateIn, request: Request, user: CurrentUser, session: SessionDep
) -> DnsRecordsOut:
    """Задать имена внутри меша целиком (список заменяется).

    Записи подхватываются headscale без перезапуска. Перезапуск нужен ровно один
    раз — когда путь к файлу впервые попадает в config.yaml.
    """
    settings = get_settings()
    require_hs(settings)
    nodes = await hs_call(get_client(settings).get_nodes())
    known = {str(n.get("id", "")) for n in nodes}
    cfg = _read_hs_config(settings.headscale_config_path)
    server_host = (urlparse(str(cfg.get("server_url", ""))).hostname or "").lower()
    base_domain = str((cfg.get("dns") or {}).get("base_domain") or "").lower()
    magic = {
        f"{str(n.get('givenName') or n.get('name') or '').lower()}.{base_domain}"
        for n in nodes
        if base_domain
    }
    for r in body.records:
        if r.node_id and r.node_id not in known:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Ноды {r.node_id} больше нет — обновите список",
            )
        if server_host and r.name == server_host:
            # Имя control-сервера, уведённое в меш, отрезает ноды от него
            # НАВСЕГДА: обратно они узнают об исправлении только от него же.
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{r.name} — имя control-сервера. Уведи его внутрь сети, и ноды "
                "потеряют связь с ним без возможности узнать об отмене",
            )
        if r.name in magic:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{r.name} — это MagicDNS-имя ноды, оно и так работает внутри сети",
            )

    stored = [
        {
            "name": r.name,
            "node_id": r.node_id,
            "ip": r.ip,
            "enabled": r.enabled,
            "cert": r.cert,
        }
        for r in body.records
    ]
    # Файл — ПЕРВЫМ, конфиг — вторым: headscale не поднимется, если путь в конфиге
    # есть, а файла нет (os.Stat в конструкторе его следилки за файлом).
    try:
        dnsrecords.write_file(
            settings.headscale_extra_records_path,
            dnsrecords.entries_for(stored, nodes),
        )
    except OSError as e:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Не удалось записать файл имён: {e}",
        ) from e
    if not dnsrecords.hs_path_configured(
        cfg, settings.headscale_extra_records_path_in_hs
    ):
        try:
            _write_extra_records_path(
                settings.headscale_config_path,
                settings.headscale_extra_records_path_in_hs,
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                f"Не удалось записать config.yaml: {e}",
            ) from e
        cfg = _read_hs_config(settings.headscale_config_path)
    await settings_store.set_dns_records(session, stored)
    await audit.record(
        session,
        user.username,
        "dns_records_set",
        "",
        ", ".join(r.name if r.enabled else f"{r.name} (выкл)" for r in body.records)
        or "пусто",
    )
    # сертификаты имён, которых больше нет (или у которых сняли галочку), панели
    # незачем держать: они всё равно не обновятся, а в списке будут врать
    await certs.forget(session, {r.name for r in body.records if r.cert})
    return await _records_out(session, stored, nodes, cfg, settings.headscale_config_path)


@router.put("/hs-info/network", response_model=HsInfoOut)
async def update_network(
    body: NetworkUpdateIn, request: Request, user: CurrentUser, session: SessionDep
) -> HsInfoOut:
    """Меняет диапазоны меша (config.prefixes) и просит хост перезапустить headscale.
    ВНИМАНИЕ: существующие ноды сохраняют старые IP; новый диапазон — для новых нод."""
    settings = get_settings()
    try:
        _write_network_config(
            settings.headscale_config_path,
            body.ipv4_prefix,
            body.allocation,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Не удалось записать config.yaml: {e}",
        ) from e
    await audit.record(session, user.username, "network_update", body.ipv4_prefix)
    return await hs_info(user)


@router.get("/tailscale-version", response_model=TsVersionOut)
async def ts_version(_: CurrentUser, session: SessionDep) -> TsVersionOut:
    """Наша пиновая версия клиента Tailscale (её ставят enroll-скрипты)."""
    settings = get_settings()
    return TsVersionOut(
        current=await settings_store.get_tailscale_version(session, settings),
        env_default=settings.tailscale_version,
    )


@router.put("/tailscale-version", response_model=TsVersionOut)
async def set_ts_version(
    body: TsVersionIn, request: Request, user: CurrentUser, session: SessionDep
) -> TsVersionOut:
    settings = get_settings()
    await settings_store.set_tailscale_version(session, body.version)
    await audit.record(session, user.username, "ts_version_set", body.version)
    return TsVersionOut(current=body.version, env_default=settings.tailscale_version)


@router.get("/tailscale-version/latest", response_model=TsLatestOut)
async def ts_latest(_: CurrentUser) -> TsLatestOut:
    """Последняя стабильная версия из pkgs.tailscale.com.

    Ответ кэшируем на сутки: страницу настроек открывают часто, а версия клиента
    выходит раз в недели. Так панель может спрашивать при каждом открытии, наружу
    при этом сходив не чаще раза в день. Ошибку НЕ кэшируем — иначе разовый сбой
    сети прятал бы подсказку до завтра.
    """
    global _latest_cache
    if _latest_cache and time.time() - _latest_cache[0] < _LATEST_TTL:
        return _latest_cache[1]
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as c:
            r = await c.get(f"{TS_PKG_BASE}/")
        vers = re.findall(r"tailscale_(\d+\.\d+\.\d+)_amd64\.tgz", r.text)
        if not vers:
            return TsLatestOut(note="Не удалось определить версию из pkgs.tailscale.com")
        latest = max(vers, key=lambda v: tuple(int(x) for x in v.split(".")))
        out = TsLatestOut(version=latest)
        _latest_cache = (time.time(), out)
        return out
    except Exception as e:  # noqa: BLE001
        return TsLatestOut(note=f"Ошибка запроса: {e}")


@router.get("/tailscale-version/check", response_model=TsCheckOut)
async def ts_check(version: str, _: CurrentUser) -> TsCheckOut:
    """Проверяет, что версия реально скачивается (amd64-тарбол) — т.е. enroll-скрипт
    с ней сработает."""
    v = version.strip()
    if not re.match(r"^\d+\.\d+\.\d+$", v):
        return TsCheckOut(ok=False, version=v, note="Некорректный формат версии (X.Y.Z)")
    url = f"{TS_PKG_BASE}/tailscale_{v}_amd64.tgz"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(url, headers={"Range": "bytes=0-0"})
        ok = r.status_code in (200, 206)
        return TsCheckOut(
            ok=ok,
            version=v,
            url=url,
            note="" if ok else f"HTTP {r.status_code} — версия не найдена",
        )
    except Exception as e:  # noqa: BLE001
        return TsCheckOut(ok=False, version=v, url=url, note=f"Ошибка запроса: {e}")


@router.get("/tailscale-version/mirror", response_model=TsMirrorOut)
async def ts_mirror_status(_: CurrentUser, session: SessionDep) -> TsMirrorOut:
    """Что из бинарей текущей пиновой версии лежит в нашем мироре (data/tailscale-pkgs)."""
    settings = get_settings()
    v = await settings_store.get_tailscale_version(session, settings)
    return TsMirrorOut(
        version=v,
        files=[TsMirrorFile(**f) for f in tsmirror.list_mirror(settings.data_dir, v)],
    )


@router.post("/tailscale-version/mirror", response_model=TsMirrorOut)
async def ts_mirror_download(user: CurrentUser, session: SessionDep) -> TsMirrorOut:
    """Скачивает бинари текущей пиновой версии (все арх.) с pkgs.tailscale.com в
    наш мирор (с проверкой sha256). Фронт раздаёт их по <hs-домен>/pkgs."""
    settings = get_settings()
    v = await settings_store.get_tailscale_version(session, settings)
    files = await tsmirror.download_mirror(settings.data_dir, v)
    await audit.record(session, user.username, "ts_mirror", v)
    return TsMirrorOut(version=v, files=[TsMirrorFile(**f) for f in files])


@router.get("/hs-info", response_model=HsInfoOut)
async def hs_info(_: CurrentUser) -> HsInfoOut:
    """DNS/MagicDNS и DERP: читаем смонтированный config.yaml. DNS правится через
    PUT /hs-info/dns (перезапуск headscale делает хостовый помощник)."""
    settings = get_settings()
    cfg = _read_hs_config(settings.headscale_config_path)
    dns = cfg.get("dns") or {}
    ns = dns.get("nameservers") or {}
    if isinstance(ns, dict):
        global_ns = ns.get("global") or []
    elif isinstance(ns, list):
        global_ns = ns
    else:
        global_ns = []
    derp = cfg.get("derp") or {}
    derp_server = derp.get("server") or {}
    pref = cfg.get("prefixes") or {}
    return HsInfoOut(
        server_url=str(cfg.get("server_url", "") or ""),
        dns=DnsInfo(
            magic_dns=bool(dns.get("magic_dns", False)),
            base_domain=str(dns.get("base_domain", "") or ""),
            nameservers=[str(x) for x in global_ns],
            search_domains=[str(x) for x in (dns.get("search_domains") or [])],
            override_local_dns=bool(dns.get("override_local_dns", False)),
        ),
        derp=DerpInfo(
            embedded=bool(derp_server.get("enabled", False)),
            urls=[str(x) for x in (derp.get("urls") or [])],
            auto_update=bool(derp.get("auto_update_enabled", False)),
        ),
        ipv4_prefix=str(pref.get("v4", "") or ""),
        allocation=str(pref.get("allocation", "") or ""),
        # флаг снимает хостовый помощник, перезапустив headscale; если он всё ещё
        # здесь — правка записана, но в силу не вступила
        restart_pending=os.path.exists(
            _restart_flag_path(settings.headscale_config_path)
        ),
    )
