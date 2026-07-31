import ipaddress
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
    r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)

# Имя роли (тега). Двоеточие и пробел исключены намеренно: значение попадает в
# HuJSON-политику, где двоеточие отделяет цель от порта.
_ROLE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")


class RequestModel(BaseModel):
    """База для тел запросов: неизвестное поле — ошибка, а не тишина.

    По умолчанию pydantic лишние поля молча выбрасывает. Из-за этого опечатка в
    имени поля выглядела как успех: запрос отвечал 200, а настройка не менялась
    (так, «node_offline_minutes» в алертах не существует вовсе, но принимался).
    Тот, кто ходит в API скриптом, узнавал об этом в лучшем случае случайно.
    """

    model_config = ConfigDict(extra="forbid")


class LoginRequest(RequestModel):
    username: str
    password: str
    otp: str | None = None


class TwoFAStatusOut(BaseModel):
    enabled: bool


class TwoFASetupOut(BaseModel):
    secret: str
    otpauth_uri: str


class TwoFAVerifyRequest(RequestModel):
    otp: str = Field(min_length=1, max_length=16)


class PasswordChangeRequest(RequestModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str


class ConfigOut(BaseModel):
    """Небезопасная публичная конфигурация для фронта (только после входа)."""

    panel_ip: str = ""
    # Публичный адрес control-сервера headscale (для подсказок «tailscale up …»)
    headscale_server_url: str = ""
    # Настроен ли API-ключ headscale (без него операции с нодами недоступны)
    headscale_configured: bool = False


# --- headscale: ноды ---
# Сущности «пользователь headscale» в панели больше нет: субъект доступа — сама
# нода (устройство), доступ выдаётся ей напрямую. Все ноды заводятся под одним
# техническим владельцем (settings.default_user), наружу он не показывается.


class NodeOut(BaseModel):
    id: str
    name: str  # givenName — имя ноды в тайлнете
    hostname: str  # исходное имя хоста (name)
    ip_addresses: list[str] = []
    online: bool = False
    last_seen: str | None = None
    expiry: str | None = None
    key_expired: bool = False
    forced_tags: list[str] = []  # редактируемые через API теги
    tags: list[str] = []  # эффективные теги для отображения (valid ∪ forced)
    created_at: str | None = None
    # то, что сообщил о себе клиент Tailscale (см. app/hostinfo.py). Best-effort:
    # REST API headscale этих данных не отдаёт, поэтому могут быть пустыми.
    client_version: str = ""  # версия клиента Tailscale (видно, кто отстал)
    os: str = ""  # «Ubuntu 24.04»
    arch: str = ""  # x86_64 / aarch64
    container: bool = False
    endpoint: str = ""  # публичный адрес:порт, с которого нода видна
    # ISO-код страны по адресу из endpoint (app/geoip.py, офлайн-таблица).
    # Пусто, если адреса нет, он приватный или таблица не собрана.
    country: str = ""
    direct_ok: bool = False  # UDP работает → возможен прямой P2P, а не через DERP
    # маршруты
    available_routes: list[str] = []  # что нода анонсирует
    approved_routes: list[str] = []  # что одобрено админом
    subnet_routes: list[str] = []  # активные (одобрено ∩ анонсируется), без exit
    is_exit_node: bool = False  # одобрен как exit-node (0.0.0.0/0 в approved)
    advertises_exit_node: bool = False  # анонсирует exit (0.0.0.0/0 в available)
    description: str = ""  # произвольная заметка панели (не из headscale)
    kind: str = "server"  # server | device — классификация панели (не из headscale)
    admin: bool = False  # админ-устройство: полный доступ ко всем серверам
    muted: bool = False  # алерты по этой ноде не шлём (наблюдение продолжается)
    exit_gateway: bool = False  # сервер — шлюз выхода в интернет (per-device via)
    exit_via: list[str] = []  # у устройства: id серверов-шлюзов, через кот. можно выходить
    force_exit: str = ""  # id шлюза: весь трафик этой ноды принудительно через него
    group: str = ""  # группировка списков: организация…
    subgroup: str = ""  # …и проект внутри неё


# Имя ноды = ОДНА DNS-метка: именно так его проверяет headscale, и именно из
# него собирается имя в MagicDNS. Точки, подчёркивания и хвостовой дефис он
# отвергает — раньше панель их пропускала, и пользователь получал 502 с
# английскими потрохами вместо внятного отказа.
#
# Регистр приводим к нижнему: DNS его не различает, а headscale — различает.
# Нода, названная «WEB-FRA» рядом с «web-fra», проходила проверку уникальности
# и отбирала у соседа имя: оба имени резолвились в ЧУЖОЙ адрес.
_NODE_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def normalize_node_name(v: str) -> str:
    name = str(v or "").strip().lower()
    if not _NODE_LABEL.match(name):
        raise ValueError(
            "Имя ноды — латинские буквы, цифры и дефис (до 63 символов), "
            "без точек, подчёркиваний и дефиса по краям"
        )
    return name


_PORTS_RE = re.compile(r"^(\*|\d{1,5}(-\d{1,5})?(,\d{1,5}(-\d{1,5})?)*)$")


def validate_ports(v: str) -> str:
    """Порты правила: «*», номера и диапазоны через запятую.

    Одной регулярки мало — она пропускала «0», «70000» и «22-10». Такое значение
    уходит в HuJSON, headscale отвергает политику ЦЕЛИКОМ, и админ получает
    четыре строки его парсера вместо «порт вне диапазона». Хуже того: политику
    после этого перестают принимать и последующие пуши, пока плохое правило
    лежит в панели.
    """
    v = str(v or "").strip()
    if not _PORTS_RE.match(v):
        raise ValueError("Порты: «*», номера и диапазоны через запятую (напр. 22,80,8000-8080)")
    if v == "*":
        return v
    for part in v.split(","):
        bounds = [int(x) for x in part.split("-")]
        for n in bounds:
            if not 1 <= n <= 65535:
                raise ValueError(f"Порт {n} вне диапазона 1–65535")
        if len(bounds) == 2 and bounds[0] > bounds[1]:
            raise ValueError(f"Диапазон «{part}» задом наперёд: начало больше конца")
    return v


class NodeRenameIn(RequestModel):
    name: str = Field(min_length=1, max_length=63)

    _norm_name = field_validator("name")(normalize_node_name)


class NodeTagsIn(RequestModel):
    tags: list[str] = Field(default_factory=list, max_length=64)


class NodeMetaIn(RequestModel):
    # заметка панели о ноде: произвольное описание и тип (сервер/устройство).
    # kind="" — сбросить в авто-определение.
    # None = поле НЕ передано и не должно меняться. Иначе частичный вызов (напр.
    # перетаскивание карточки в другую группу шлёт только группу) молча сбрасывал
    # бы «не слать алерты», «шлюз выхода» и принудительный туннель.
    description: str | None = Field(default=None, max_length=500)
    kind: Literal["", "server", "device"] | None = None
    admin: bool | None = None  # админ-устройство (полный доступ ко всем серверам)
    muted: bool | None = None  # не слать алерты по этой ноде
    exit_gateway: bool | None = None  # сервер — шлюз выхода
    exit_via: list[str] | None = Field(default=None, max_length=64)
    # id шлюза, через который принудительно гнать весь трафик этой ноды (exit-node)
    force_exit: str | None = Field(default=None, max_length=32, pattern=r"^\d*$")
    # группировка в списках: группа → подгруппа (напр. организация → проект).
    # Свободный текст, пусто = «без группы».
    group: str = Field(default="", max_length=63)
    subgroup: str = Field(default="", max_length=63)


class NodeRoutesIn(RequestModel):
    # полный список одобряемых маршрутов (CIDR); exit-node = 0.0.0.0/0 + ::/0
    routes: list[str] = Field(default_factory=list, max_length=256)

    @field_validator("routes")
    @classmethod
    def _check_routes(cls, v: list[str]) -> list[str]:
        """Одобряемые маршруты приходят из АНОНСА САМОЙ НОДЫ: в модалке они просто
        показаны галочками («нода анонсирует — одобрить»). Значит содержимое
        выбирает потенциально скомпрометированная нода, а одобрение раздаёт
        маршрут всем остальным (все подключаются с --accept-routes).

        Поэтому здесь тот же набор проверок, что у AgentIn.routes: без него нода
        могла анонсировать 100.64.0.0/10 или /32 админского устройства, это
        выглядело бы в UI обычной строкой, а один клик админа сделал бы её
        subnet-роутером для адресов соседей — то есть точкой перехвата их трафика.

        Разница с AgentIn одна: exit-маршруты («0.0.0.0/0», «::/0») здесь законны —
        этот эндпоинт их и одобряет, когда нода помечена шлюзом выхода.
        """
        from app.aclgen import touches_mesh
        from app.nodekind import EXIT_ROUTES

        out: list[str] = []
        for raw in v:
            r = (raw or "").strip()
            if not r:
                continue
            if r in EXIT_ROUTES:  # exit-нода одобряется именно тут
                out.append(r)
                continue
            if any(c.isspace() for c in r):
                raise ValueError(f"в маршруте не должно быть пробелов: {r!r}")
            try:
                net = ipaddress.ip_network(r, strict=False)
            except ValueError:
                raise ValueError(f"неверный маршрут: {r!r}")
            if net.version != 4:
                raise ValueError(f"IPv6 не используется в этой сети: {r!r}")
            if touches_mesh(r):
                raise ValueError(
                    f"{r!r} — адрес внутри самого меша; одобрив такой маршрут, вы "
                    "разрешили бы этой ноде перехватывать трафик к соседям"
                )
            out.append(str(net))
        return out


class ExitClientsIn(RequestModel):
    # серверная сторона выбора выхода: id устройств, которым разрешён выход через
    # этот шлюз (обратная проекция device.exit_via)
    devices: list[str] = Field(default_factory=list, max_length=512)


# --- enroll (добавление ноды) ---

class EnrollIn(RequestModel):
    # то же имя, что станет именем ноды в сети, — правила те же
    name: str = Field(min_length=1, max_length=63)

    _norm_name = field_validator("name")(normalize_node_name)
    os: Literal["linux", "windows", "macos", "android"] = "linux"
    # exit-нода: скрипт анонсирует exit + на Linux закрепляет ip_forward.
    exit_node: bool = False


class EnrollOut(BaseModel):
    os: str
    hostname: str
    login_server: str
    script: str
    key_id: str
    expires_at: str


class ReconnectIn(RequestModel):
    os: Literal["linux", "windows", "macos", "android"] = "linux"


# --- версия клиента Tailscale ---

class TsVersionOut(BaseModel):
    current: str = ""  # эффективная пиновая версия (из БД, иначе env)
    env_default: str = ""


class TsVersionIn(RequestModel):
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$", max_length=20)


class TsCheckOut(BaseModel):
    ok: bool = False
    version: str = ""
    url: str = ""
    note: str = ""


class TsLatestOut(BaseModel):
    version: str = ""
    note: str = ""


class TsMirrorFile(BaseModel):
    name: str = ""
    ok: bool = False
    size: int = 0
    note: str = ""


class TsMirrorOut(BaseModel):
    version: str = ""
    files: list[TsMirrorFile] = []


class EnrollStatusOut(BaseModel):
    connected: bool = False
    node: NodeOut | None = None
    # Имя, под которым эта машина уже стояла в сети: запись переиспользована.
    reused_from: str | None = None


# --- ACL-политика ---

class PolicyOut(BaseModel):
    policy: str = ""
    updated_at: str | None = None
    exists: bool = True  # False = политика ещё не задана (отдан дефолтный шаблон)


class PolicyIn(RequestModel):
    policy: str = Field(max_length=1_000_000)


# --- визуальный конструктор ACL ---

class AclSelector(RequestModel):
    # servers = все ноды-серверы (разворачивается в их IP); internet = выход в
    # интернет через exit-node (autogroup:internet); cidr = конкретный IP/подсеть.
    kind: Literal["any", "node", "tag", "servers", "internet", "cidr"] = "any"
    value: str = ""  # id ноды / имя роли / IP-CIDR; пусто для any/servers/internet

    @model_validator(mode="after")
    def _check_cidr(self) -> "AclSelector":
        if self.kind == "cidr":
            v = (self.value or "").strip()
            try:
                net = ipaddress.ip_network(v, strict=False)
            except ValueError:
                raise ValueError(f"неверный IP/подсеть: {v!r}")
            # тайлнет v4-only — IPv6-правило работать не будет
            if net.version != 4:
                raise ValueError(f"IPv6 не используется в этой сети: {v!r}")
            # Любой адрес меша здесь запрещён — и «0.0.0.0/0», открывающий разом
            # всё, и «100.64.0.3/32», который есть адрес конкретного устройства.
            # Второе особенно коварно: голый адрес фильтр изоляции отбрасывал, а
            # тот же адрес с «/32» — уже другая строка, и он проезжал насквозь.
            # Ноды меша адресуются селекторами «нода»/«роль»/«все серверы», поле
            # IP-подсети — для внешних адресов.
            from app.aclgen import touches_mesh

            if touches_mesh(v):
                raise ValueError(
                    f"{v!r} — адрес внутри меша. Ноды выбираются селектором «нода» "
                    "или «роль», здесь указываются внешние адреса"
                )
        if self.kind == "tag":
            from app import exitvia

            v = (self.value or "").strip()
            name = v[4:] if v.startswith("tag:") else v
            # Набор символов: значение уходит прямо в HuJSON-политику, где
            # двоеточие разделяет цель и порт. Тег с пробелом или двоеточием либо
            # меняет смысл правила, либо (чаще) заставляет headscale отвергнуть
            # политику ЦЕЛИКОМ — а поскольку правило уже сохранено, отвергаться
            # начнут и все последующие пуши: ACL замрёт на последней удачной версии.
            if not name or not _ROLE_NAME_RE.match(name):
                raise ValueError(
                    f"недопустимое имя роли {name!r}: латиница, цифры, дефис, точка, _"
                )
            # Служебные теги шлюзов выхода — не роли: правило на них выдавало бы
            # доступ к шлюзу (или от его имени) в обход галки «Шлюз выхода».
            if exitvia.is_service_tag(f"tag:{name}"):
                raise ValueError(
                    f"{name!r} — служебный тег шлюза выхода, он не роль: "
                    "доступ к шлюзу выдаётся обычными правилами на саму ноду"
                )
        return self


class AclRule(RequestModel):
    src: AclSelector
    dst: AclSelector
    # порт(ы): «*», «22», «5430», «80,443», «1000-2000»
    ports: str = Field(default="*", max_length=200)

    _ports = field_validator("ports")(validate_ports)


class AclRulesIn(RequestModel):
    rules: list[AclRule] = Field(default_factory=list, max_length=500)


class AgentIn(RequestModel):
    # что нода должна анонсировать; применяет агент на самой ноде
    routes: list[str] = Field(default_factory=list, max_length=256)
    exit_node: bool = False

    @field_validator("routes")
    @classmethod
    def _check_routes(cls, v: list[str]) -> list[str]:
        """Маршруты уходят на ноду и применяются там `tailscale set`, поэтому
        проверяем их здесь, а не полагаемся на поле ввода в UI.

        Отдельно про «0.0.0.0/0»: он валиден как маршрут, но означает exit-ноду.
        Пропустив его сюда, мы дали бы сделать ноду exit-нодой МИМО галки, у
        которой написано, чем это грозит — весь трафик клиента, а не выбранные
        адреса. Для этого есть exit_node.
        """
        from app.aclgen import touches_mesh

        out: list[str] = []
        for raw in v:
            r = (raw or "").strip()
            if not r:
                continue
            # перевод строки разорвал бы формат key=value, который парсит агент
            if any(c.isspace() for c in r):
                raise ValueError(f"в маршруте не должно быть пробелов: {r!r}")
            try:
                net = ipaddress.ip_network(r, strict=False)
            except ValueError:
                raise ValueError(f"неверный маршрут: {r!r}")
            if net.version != 4:
                raise ValueError(f"IPv6 не используется в этой сети: {r!r}")
            if net.prefixlen == 0:
                raise ValueError(
                    "0.0.0.0/0 — это exit-нода, а не маршрут: включается отдельной "
                    "галкой «Exit-node», потому что уводит ВЕСЬ трафик клиента"
                )
            if touches_mesh(r):
                raise ValueError(
                    f"{r!r} — адрес внутри самого меша; такой маршрут перехватывал "
                    "бы трафик к соседней ноде"
                )
            out.append(str(net))  # нормализуем: 1.2.3.4 → 1.2.3.4/32
        return out


class AgentOut(BaseModel):
    routes: list[str] = []
    exit_node: bool = False
    token: str = ""
    installed: bool = False  # агент недавно забирал состояние
    last_poll: str | None = None
    # подтверждение ПРИМЕНЕНИЯ (сам запрос состояния ничего не доказывает: ноде
    # достаточно дёргать свой URL, чтобы выглядеть живой, ничего не применяя)
    last_applied: str | None = None
    applied_hash: str = ""
    applied_current: bool = False  # применённое совпадает с текущим заданием
    setup_oneline: str = ""
    remove_oneline: str = ""


class ResolveHostIn(RequestModel):
    host: str = Field(min_length=1, max_length=253)


class ResolveHostOut(BaseModel):
    host: str = ""
    ips: list[str] = []  # A/AAAA-адреса (или сам host, если это уже IP/CIDR)
    note: str = ""


class AclRulesOut(BaseModel):
    rules: list[AclRule] = []
    generated: str = ""  # сгенерированный HuJSON (read-only превью)


# --- API-ключи headscale + инфо DNS/DERP ---

class ApiKeyOut(BaseModel):
    id: str
    prefix: str
    expiration: str | None = None
    created_at: str | None = None
    last_seen: str | None = None
    is_panel: bool = False  # ключ, которым ходит сама панель (не истекать!)


class ApiKeyCreateIn(RequestModel):
    expiration_days: int = Field(default=90, ge=1, le=3650)


class ApiKeyCreatedOut(BaseModel):
    api_key: str  # полный ключ, показывается ОДИН раз
    prefix: str
    expiration: str | None = None


class ApiKeyExpireIn(RequestModel):
    prefix: str = Field(min_length=1, max_length=64)


class DnsInfo(BaseModel):
    magic_dns: bool = False
    base_domain: str = ""
    nameservers: list[str] = []
    search_domains: list[str] = []
    override_local_dns: bool = False


class DerpInfo(BaseModel):
    embedded: bool = False
    urls: list[str] = []
    auto_update: bool = False


class HsInfoOut(BaseModel):
    server_url: str = ""
    dns: DnsInfo = DnsInfo()
    derp: DerpInfo = DerpInfo()
    ipv4_prefix: str = ""
    allocation: str = ""
    # Правка записана, но headscale ещё не перезапущен: флаг перезапуска лежит на
    # месте. Перезапускает его хостовый помощник, и если его не поставили (или он
    # сломан), правка так и не вступит в силу — молчать об этом нельзя.
    restart_pending: bool = False


class NetworkUpdateIn(RequestModel):
    # диапазон меша (headscale config.prefixes.v4) — обязательно внутри CGNAT
    # 100.64.0.0/10 (Tailscale). Тайлнет только IPv4 (v6 не используется).
    ipv4_prefix: str = Field(max_length=64)
    allocation: Literal["sequential", "random"] = "sequential"

    @field_validator("ipv4_prefix")
    @classmethod
    def _v4(cls, v: str) -> str:
        try:
            net = ipaddress.ip_network(v.strip(), strict=False)
        except ValueError as e:
            raise ValueError("Некорректный IPv4-диапазон (пример: 100.64.0.0/10)") from e
        if net.version != 4:
            raise ValueError("Нужен именно IPv4-диапазон")
        if not net.subnet_of(ipaddress.ip_network("100.64.0.0/10")):
            raise ValueError(
                "IPv4-диапазон должен быть внутри 100.64.0.0/10 (Tailscale CGNAT)"
            )
        return str(net)


class AuditEntryOut(BaseModel):
    ts: str | None = None
    username: str = ""
    action: str = ""
    target: str = ""
    detail: str = ""


class HeadscaleLogsOut(BaseModel):
    available: bool = False
    text: str = ""
    note: str = ""


class SummaryOut(BaseModel):
    panel_version: str = ""
    headscale_url: str = ""
    headscale_ok: bool = False
    magic_dns: bool = False
    base_domain: str = ""
    nameservers: list[str] = []
    derp_embedded: bool = False
    nodes_total: int = 0
    servers: int = 0
    devices: int = 0
    online: int = 0
    last_backup: str = ""
    last_backup_at: str | None = None


class DnsUpdateIn(RequestModel):
    magic_dns: bool = False
    base_domain: str = Field(default="", max_length=253)
    nameservers: list[str] = Field(default_factory=list, max_length=16)
    # Подменять ли резолвер на самих нодах. Выключено: сервер может ходить во
    # внутренний DNS, и отбирать его молча нельзя (см. _write_dns_config).
    override_local_dns: bool = False

    @field_validator("base_domain")
    @classmethod
    def _valid_domain(cls, v: str) -> str:
        v = v.strip().lower()
        if v and not _DOMAIN_RE.match(v):
            raise ValueError("Некорректный базовый домен (пример: noderoost.internal)")
        return v

    @field_validator("nameservers")
    @classmethod
    def _valid_ns(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for x in v:
            x = x.strip()
            if not x:
                continue
            try:
                ipaddress.ip_address(x)
            except ValueError as e:
                raise ValueError(f"Некорректный IP DNS-сервера: {x}") from e
            if x not in out:
                out.append(x)
        return out

    @model_validator(mode="after")
    def _magic_needs_domain(self) -> "DnsUpdateIn":
        if self.magic_dns and not self.base_domain:
            raise ValueError("Для MagicDNS нужен базовый домен")
        return self


# --- метрики + алерты ---

class HistoryPoint(BaseModel):
    ts: str
    online: int
    total: int


class MetricsHistory(BaseModel):
    interval_seconds: int
    points: list[HistoryPoint] = []


class AlertConfigIn(RequestModel):
    telegram_token: str = ""
    telegram_chat: str = ""
    telegram_api: str = ""
    webhook: str = ""


class AlertConfigOut(BaseModel):
    telegram_token: str = ""
    telegram_chat: str = ""
    telegram_api: str = ""
    webhook: str = ""
    enabled: bool = False


class AlertTestResult(BaseModel):
    sent: bool
    errors: list[str] = []


# --- бэкапы ---

class BackupFileInfo(BaseModel):
    filename: str
    size: int
    created: str


class BackupRunResult(BaseModel):
    filename: str
    size: int
    problems: list[str] = []  # пусто = self-тест пройден


class BackupConfig(RequestModel):
    interval_hours: int = Field(default=24, ge=0, le=24 * 30)
    keep: int = Field(default=7, ge=1, le=365)


class DirectionIn(RequestModel):
    """Направление: кто → куда → через какую ноду."""

    # кто ходит: конкретные ноды (src) либо группа целиком. Группа хранится
    # ГРУППОЙ, а не снимком списка, — новая нода подхватится сама.
    src_kind: Literal["node", "devices", "servers"] = "node"
    src: list[str] = Field(default_factory=list, max_length=200)
    # full УБРАН: полный туннель через subnet-маршруты оказался небезопасным (любая
    # нода с accept-routes и правом в интернет забирала широкие маршруты и текла в
    # туннель). «Весь трафик сервера через другой» делается exit-нодой (см. «Шлюз
    # выхода»). Поле оставлено для совместимости входа, но игнорируется.
    full: bool = False
    dst: str = Field(default="", max_length=253)  # домен, IP или подсеть
    via: str = Field(min_length=1, max_length=32, pattern=r"^\d+$")  # id ноды-выхода
    # Тот же формат, что у AclRule.ports, и по той же причине: значение попадает
    # прямо в HuJSON-политику. Непроходной порт headscale отвергает целиком —
    # а поскольку направление уже сохранено, отвергаться начнут ВСЕ последующие
    # пуши политики, и ACL замрёт на последней удачной версии.
    ports: str = Field(default="*", max_length=200)

    _ports = field_validator("ports")(validate_ports)

    @model_validator(mode="after")
    def _check_src(self) -> "DirectionIn":
        if self.src_kind == "node":
            ids = [i.strip() for i in self.src if i.strip()]
            if not ids:
                raise ValueError("не выбрано ни одного источника")
            if not all(i.isdigit() for i in ids):
                raise ValueError("id ноды должен быть числом")
            self.src = ids
        else:
            self.src = []  # группа раскрывается при сборке политики
        if not self.dst.strip():
            raise ValueError("укажите адрес назначения")
        return self


class DirectionOut(BaseModel):
    id: str = ""
    src_kind: str = "node"
    src: list[str] = []
    full: bool = False
    dst: str = ""
    via: str = ""
    ports: str = "*"
    ips: list[str] = []  # во что резолвится dst сейчас
    resolved_at: str | None = None
    error: str = ""  # текст ошибки резолва (адреса при этом остаются прежними)
    active: bool = False  # маршрут реально раздаётся нодой-выходом
    via_agent: bool = False  # на ноде-выходе стоит агент (без него не применится)


class DirectionsOut(BaseModel):
    directions: list[DirectionOut] = []
