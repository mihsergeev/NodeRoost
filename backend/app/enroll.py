"""Генерация самодостаточных скриптов подключения ноды к тайлнету.

Скрипт ставит ОФИЦИАЛЬНЫЙ клиент Tailscale пиновой версии и выполняет
`tailscale up --login-server=<hs> --authkey=<one-time>`. Нода ходит только в
control-сервер headscale, в панель — нет.

Скрипт часто вставляют прямо в терминал, поэтому ПЕРВОЙ строкой каждый шаблон
гасит запись истории шелла — иначе одноразовый ключ осел бы в ~/.bash_history
(или в ConsoleHost_history.txt у PowerShell). Именно первой: bash пишет в историю
каждую строку по мере выполнения, так что всё, что стоит выше, туда попадёт.
HISTFILE НЕ трогаем — это ломало бы сохранение истории всей сессии пользователя.

Плейсхолдеры подставляются через .replace() (а не format/f-string), чтобы не
конфликтовать с шелловскими ${...} и PowerShell-$.
"""

from app.config import Settings

# --- Linux (POSIX sh, статический тарбол нужной версии + systemd) ---
_LINUX_TEMPLATE = r"""#!/usr/bin/env sh
if [ -n "$BASH_VERSION" ]; then set +o history; fi
# ^ ПЕРВОЙ строкой, до всего остального: при вставке в терминал bash пишет в
# историю каждую строку по мере выполнения, поэтому всё, что стоит выше, туда
# попадёт. Так одноразовый ключ ниже в историю не уходит.
# Проверка на bash обязательна: `set` — специальный встроенный оператор, и в dash
# (/bin/sh в Debian/Ubuntu) неизвестная опция +o history роняет весь скрипт молча —
# `|| true` не спасает. Без проверки `sh join.sh` умирал бы на первой же строке.
# NodeRoost — подключение этой машины к тайлнету. Запускать под root:
#   curl ... | sudo sh   ИЛИ   sudo sh nodroost-join.sh
set -e
VER="@@VER@@"
LOGIN="@@LOGIN@@"
KEY="@@KEY@@"
NAME="@@NAME@@"

ARCH=$(uname -m)
case "$ARCH" in
  x86_64|amd64) A=amd64 ;;
  aarch64|arm64) A=arm64 ;;
  armv7l|armv7) A=arm ;;
  *) A=amd64 ;;
esac

if ! command -v tailscale >/dev/null 2>&1; then
  echo "NodeRoost: ставлю Tailscale $VER ($A)…"
  TMP=$(mktemp -d)
  # сначала наш мирор, при неудаче — официальный pkgs.tailscale.com
  curl -fsSL "@@PKGBASE@@/tailscale_${VER}_${A}.tgz" -o "$TMP/ts.tgz" \
    || curl -fsSL "https://pkgs.tailscale.com/stable/tailscale_${VER}_${A}.tgz" -o "$TMP/ts.tgz"
  tar -xzf "$TMP/ts.tgz" -C "$TMP"
  D="$TMP/tailscale_${VER}_${A}"
  install -m755 "$D/tailscale" /usr/bin/tailscale
  install -m755 "$D/tailscaled" /usr/sbin/tailscaled
  if command -v systemctl >/dev/null 2>&1; then
    install -m644 "$D/systemd/tailscaled.service" /etc/systemd/system/tailscaled.service
    [ -f "$D/systemd/tailscaled.defaults" ] && install -m644 "$D/systemd/tailscaled.defaults" /etc/default/tailscaled || true
    mkdir -p /var/lib/tailscale /run/tailscale
    systemctl daemon-reload
    systemctl enable --now tailscaled
    sleep 2
  else
    echo "NodeRoost: systemd не найден — запустите tailscaled вручную." >&2
  fi
fi
@@EXITSETUP@@
@@CAROOT@@
# --reset обязателен: если на машине уже стояли какие-то настройки Tailscale
# (например --exit-node-allow-lan-access от прошлого выхода через шлюз),
# `tailscale up` отказывается их менять — «requires mentioning all non-default
# flags» — и подключение падает. Мы задаём состояние ноды целиком, поэтому
# сбрасываем прежнее.
# Машина могла быть привязана к ДРУГОМУ control-серверу (переезд с другой панели
# или из чужого тайлнета). Tailscale в этом случае отказывается менять сервер и
# просит --force-reauth — раньше скрипт просто падал с его английской строкой.
# Пробуем как обычно, а на этом отказе переспрашиваем с force-reauth, сказав почему.
if ! tailscale up --reset --login-server="$LOGIN" --authkey="$KEY" --hostname="$NAME" @@EXTRA@@ 2>/tmp/nr-up.err; then
    if grep -q "force-reauth" /tmp/nr-up.err; then
        echo "NodeRoost: машина была подключена к другому control-серверу — переключаю."
        tailscale up --reset --force-reauth --login-server="$LOGIN" --authkey="$KEY" --hostname="$NAME" @@EXTRA@@
    else
        cat /tmp/nr-up.err >&2
        rm -f /tmp/nr-up.err
        exit 1
    fi
fi
rm -f /tmp/nr-up.err
# Докладываем то, ЧТО ЕСТЬ, а не то, что просили. На машине, уже подключённой к
# этой сети, `tailscale up` возвращает 0 при любом ключе — ключ ей не нужен, и
# скрипт с просроченным ключом бодро писал «нода подключена», хотя в панели не
# появлялось ничего.
#
# Спрашиваем DNSName, а не HostName: первое выдаёт control-сервер (это и есть имя
# записи в панели), второе — то, как машина назвала себя сама, и оно совпадёт с
# запрошенным всегда, даже когда никакой новой ноды не завелось.
# Читаем не сразу: сведения о себе на ноде обновляются через секунду-другую
# после регистрации, и первый же ответ показывал прежние имя и адрес.
REAL=""; ADDR=""
i=0
while [ $i -lt 10 ]; do
    REAL="$(tailscale status --json 2>/dev/null | sed -n 's/.*"DNSName" *: *"\([^".]*\)\..*/\1/p' | head -1)"
    ADDR="$(tailscale ip -4 2>/dev/null | head -1)"
    [ "$REAL" = "$NAME" ] && break
    i=$((i + 1))
    sleep 1
done
if [ -z "$REAL" ]; then
    echo "NodeRoost: подключение не подтвердилось — проверьте 'tailscale status'." >&2
    exit 1
fi
if [ "$REAL" != "$NAME" ]; then
    echo "NodeRoost: машина уже была в этой сети как \"$REAL\" ($ADDR) — использована её запись."
    echo "В панели она останется под своим именем, новая нода \"$NAME\" не появится."
else
    echo "NodeRoost: нода \"$REAL\" подключена ($ADDR)."
fi
# возвращаем запись истории (если скрипт упал раньше — просто откройте новый шелл)
set -o history 2>/dev/null || true
"""

# Блок для exit-ноды (Linux): включает IP-форвардинг и делает его ПОСТОЯННЫМ
# (иначе после ребута exit перестаёт роутить). Скрипт и так под root.
_EXIT_SETUP_LINUX = (
    "\n# NodeRoost: exit-нода — включаю IP-форвардинг и закрепляю его\n"
    "printf 'net.ipv4.ip_forward = 1\\nnet.ipv6.conf.all.forwarding = 1\\n'"
    " > /etc/sysctl.d/99-noderoost-exit.conf\n"
    "sysctl -p /etc/sysctl.d/99-noderoost-exit.conf >/dev/null 2>&1 || true"
)

# --- Windows (PowerShell от администратора, MSI нужной версии) ---
_WINDOWS_TEMPLATE = r"""# NodeRoost — подключение этой машины к тайлнету.
# Запускать в PowerShell ОТ АДМИНИСТРАТОРА.
$ErrorActionPreference = 'Stop'
# Одноразовый ключ не должен попасть в историю PSReadLine (ConsoleHost_history.txt)
try { Set-PSReadLineOption -HistorySaveStyle SaveNothing } catch {}
$ver   = '@@VER@@'
$login = '@@LOGIN@@'
$key   = '@@KEY@@'
$name  = '@@NAME@@'

$arch = switch ($env:PROCESSOR_ARCHITECTURE) {
  'AMD64' { 'amd64' }
  'ARM64' { 'arm64' }
  'x86'   { 'x86' }
  default { 'amd64' }
}
$msi = Join-Path $env:TEMP "tailscale-setup-$ver-$arch.msi"
Write-Host "NodeRoost: скачиваю Tailscale $ver ($arch)…"
# сначала наш мирор, при неудаче — официальный pkgs.tailscale.com
try { Invoke-WebRequest "@@PKGBASE@@/tailscale-setup-$ver-$arch.msi" -OutFile $msi }
catch { Invoke-WebRequest "https://pkgs.tailscale.com/stable/tailscale-setup-$ver-$arch.msi" -OutFile $msi }
Start-Process msiexec.exe -ArgumentList '/i', "`"$msi`"", '/quiet', '/norestart' -Wait
$ts = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
@@CAROOT@@
# --reset: см. пояснение в linux-шаблоне — без него `tailscale up` не меняет
# настройки на машине, где уже был задан любой неявный флаг.
# Машина могла быть привязана к ДРУГОМУ control-серверу (см. пояснение в
# linux-шаблоне): Tailscale просит --force-reauth, и без него подключение падало.
$err = & $ts up --reset --login-server=$login --authkey=$key --hostname=$name @@EXTRA@@ 2>&1
if ($LASTEXITCODE -ne 0) {
  if ("$err" -match "force-reauth") {
    Write-Host "NodeRoost: машина была подключена к другому control-серверу — переключаю."
    & $ts up --reset --force-reauth --login-server=$login --authkey=$key --hostname=$name @@EXTRA@@
  } else {
    Write-Error "$err"
  }
}
# Докладываем то, ЧТО ЕСТЬ (см. пояснение в linux-шаблоне): на машине, уже
# подключённой к этой сети, `tailscale up` не ругается ни на какой ключ, включая
# просроченный, и отчёт «нода подключена» оказывался неправдой.
# Читаем не сразу: сведения о себе обновляются через секунду-другую после
# регистрации, и первый же ответ показывал прежние имя и адрес.
$real = $null; $addr = $null
for ($i = 0; $i -lt 10; $i++) {
  try {
    $st = & $ts status --json | ConvertFrom-Json
    $real = ($st.Self.DNSName -split '\.')[0]
    $addr = @($st.Self.TailscaleIPs)[0]
  } catch {}
  if ($real -eq $name) { break }
  Start-Sleep -Seconds 1
}
if (-not $real) {
  Write-Error "NodeRoost: подключение не подтвердилось — проверьте 'tailscale status'."
} elseif ($real -ne $name) {
  Write-Host "NodeRoost: машина уже была в этой сети как `"$real`" ($addr) — использована её запись."
  Write-Host "В панели она останется под своим именем, новая нода `"$name`" не появится."
} else {
  Write-Host "NodeRoost: нода `"$real`" подключена ($addr)."
}
"""

# --- macOS (Терминал, CLI-клиент через Homebrew) ---
# У Mac-версии в App Store нельзя задать свой control-сервер, поэтому ставим
# CLI-вариант через brew (он умеет --login-server). Версия — из brew (не пиновая:
# у brew нет простого пиннинга), @@VER@@/@@PKGBASE@@ здесь не используются.
_MACOS_TEMPLATE = r"""#!/bin/sh
if [ -n "$BASH_VERSION" ]; then set +o history; fi
# ^ первой строкой, чтобы ключ ниже не попал в историю. В zsh (по умолчанию в
# macOS) такой опции нет — там строка ничего не делает, и ключ в историю попадёт.
# Не хотите этого — сохраните скрипт в файл и запустите: sh join.sh
# Проверка на bash обязательна: в dash `set +o history` — ошибка специального
# встроенного оператора, а она роняет весь скрипт молча, невзирая на `|| true`.
# NodeRoost — подключение этого Mac к тайлнету. Запускать в Терминале.
# Нужен Homebrew (https://brew.sh). Ставит CLI-клиент Tailscale (без меню-бара).
set -e
LOGIN="@@LOGIN@@"
KEY="@@KEY@@"
NAME="@@NAME@@"

if ! command -v tailscale >/dev/null 2>&1; then
  if ! command -v brew >/dev/null 2>&1; then
    echo "NodeRoost: нужен Homebrew — поставьте с https://brew.sh и повторите." >&2
    exit 1
  fi
  echo "NodeRoost: ставлю Tailscale через Homebrew…"
  brew install tailscale
  sudo brew services start tailscale
  sleep 2
fi
@@CAROOT@@

# --reset обязателен: если на машине уже стояли какие-то настройки Tailscale
# (например --exit-node-allow-lan-access от прошлого выхода через шлюз),
# `tailscale up` отказывается их менять — «requires mentioning all non-default
# flags» — и подключение падает. Мы задаём состояние ноды целиком, поэтому
# сбрасываем прежнее.
# Машина могла быть привязана к ДРУГОМУ control-серверу (переезд с другой панели
# или из чужого тайлнета). Tailscale в этом случае отказывается менять сервер и
# просит --force-reauth — раньше скрипт просто падал с его английской строкой.
# Пробуем как обычно, а на этом отказе переспрашиваем с force-reauth, сказав почему.
if ! tailscale up --reset --login-server="$LOGIN" --authkey="$KEY" --hostname="$NAME" @@EXTRA@@ 2>/tmp/nr-up.err; then
    if grep -q "force-reauth" /tmp/nr-up.err; then
        echo "NodeRoost: машина была подключена к другому control-серверу — переключаю."
        tailscale up --reset --force-reauth --login-server="$LOGIN" --authkey="$KEY" --hostname="$NAME" @@EXTRA@@
    else
        cat /tmp/nr-up.err >&2
        rm -f /tmp/nr-up.err
        exit 1
    fi
fi
rm -f /tmp/nr-up.err
# Докладываем то, ЧТО ЕСТЬ, а не то, что просили. На машине, уже подключённой к
# этой сети, `tailscale up` возвращает 0 при любом ключе — ключ ей не нужен, и
# скрипт с просроченным ключом бодро писал «нода подключена», хотя в панели не
# появлялось ничего.
#
# Спрашиваем DNSName, а не HostName: первое выдаёт control-сервер (это и есть имя
# записи в панели), второе — то, как машина назвала себя сама, и оно совпадёт с
# запрошенным всегда, даже когда никакой новой ноды не завелось.
# Читаем не сразу: сведения о себе на ноде обновляются через секунду-другую
# после регистрации, и первый же ответ показывал прежние имя и адрес.
REAL=""; ADDR=""
i=0
while [ $i -lt 10 ]; do
    REAL="$(tailscale status --json 2>/dev/null | sed -n 's/.*"DNSName" *: *"\([^".]*\)\..*/\1/p' | head -1)"
    ADDR="$(tailscale ip -4 2>/dev/null | head -1)"
    [ "$REAL" = "$NAME" ] && break
    i=$((i + 1))
    sleep 1
done
if [ -z "$REAL" ]; then
    echo "NodeRoost: подключение не подтвердилось — проверьте 'tailscale status'." >&2
    exit 1
fi
if [ "$REAL" != "$NAME" ]; then
    echo "NodeRoost: машина уже была в этой сети как \"$REAL\" ($ADDR) — использована её запись."
    echo "В панели она останется под своим именем, новая нода \"$NAME\" не появится."
else
    echo "NodeRoost: нода \"$REAL\" подключена ($ADDR)."
fi
"""

# --- Android (скрипта/CLI нет — только приложение из Google Play, вручную) ---
_ANDROID_INSTRUCTIONS = r"""NodeRoost — подключение Android к тайлнету.
У Tailscale на Android нет скрипта/CLI, только приложение — сделайте вручную:

1. Установите «Tailscale» из Google Play и откройте.
2. На экране входа нажмите «⋮» (три точки вверху) →
   «Use an alternate server» / «Использовать другой сервер».
3. Адрес сервера входа:
      @@LOGIN@@
4. Нажмите вход. Если приложение попросит ключ авторизации — введите
   одноразовый ключ:
      @@KEY@@
   (если ключ не спрашивают — вход интерактивный, подтвердите на открывшейся
   странице; ключ действует ограниченное время.)
5. Разрешите VPN-профиль. Устройство «@@NAME@@» появится в панели.
@@CAROOT@@"""

# --- корень своей CA прямо в скрипте подключения ---
# Зачем инлайном, а не скачиванием: скрипт подключения намеренно не ходит в
# панель (нода знает только control-сервер), и заводить ей туда дорогу ради
# одного файла — менять свойство системы ради удобства. PEM не секрет.
# Зачем вообще при подключении, если то же делает агент: агента на ноутбуке и
# телефоне не будет никогда, а на сервере он появится минутой позже — «сразу»
# значит сразу.
_CA_LINUX = """
# NodeRoost: корень панели в доверенные — чтобы имена внутри сети открывались
# без ругани на сертификат. Убрать: rm .../noderoost-ca.crt + update-ca-*
cat > /tmp/noderoost-ca.crt <<'NRCAEOF'
@@CAPEM@@NRCAEOF
if [ -d /usr/local/share/ca-certificates ]; then
  install -m644 /tmp/noderoost-ca.crt /usr/local/share/ca-certificates/noderoost-ca.crt
  update-ca-certificates >/dev/null 2>&1 && echo "NodeRoost: корень панели добавлен в доверенные."
elif [ -d /etc/pki/ca-trust/source/anchors ]; then
  install -m644 /tmp/noderoost-ca.crt /etc/pki/ca-trust/source/anchors/noderoost-ca.crt
  update-ca-trust extract >/dev/null 2>&1 && echo "NodeRoost: корень панели добавлен в доверенные."
else
  echo "NodeRoost: не нашёл хранилище корней — поставьте /tmp/noderoost-ca.crt сами." >&2
fi
rm -f /tmp/noderoost-ca.crt
"""

_CA_WINDOWS = """
# NodeRoost: корень панели в доверенные (LocalMachine\\Root) — чтобы имена внутри
# сети открывались без ругани на сертификат.
$caPem = @'
@@CAPEM@@'@
$caFile = Join-Path $env:TEMP 'noderoost-ca.crt'
Set-Content -Path $caFile -Value $caPem -Encoding ascii
try {
  Import-Certificate -FilePath $caFile -CertStoreLocation Cert:\\LocalMachine\\Root | Out-Null
  Write-Host "NodeRoost: корень панели добавлен в доверенные."
} catch {
  & certutil -addstore -f Root $caFile | Out-Null
  Write-Host "NodeRoost: корень панели добавлен в доверенные (certutil)."
}
Remove-Item $caFile -ErrorAction SilentlyContinue
"""

# macOS: системная связка ключей требует прав, поэтому sudo (пароль спросят).
_CA_MACOS = """
# NodeRoost: корень панели в доверенные — чтобы имена внутри сети открывались
# без ругани на сертификат. Понадобится пароль: связка ключей системная.
cat > /tmp/noderoost-ca.crt <<'NRCAEOF'
@@CAPEM@@NRCAEOF
sudo security add-trusted-cert -d -r trustRoot \\
  -k /Library/Keychains/System.keychain /tmp/noderoost-ca.crt \\
  && echo "NodeRoost: корень панели добавлен в доверенные."
rm -f /tmp/noderoost-ca.crt
"""

_CA_ANDROID = """
6. Отдельно поставьте корневой сертификат панели, иначе внутренние имена будут
   открываться с предупреждением: скачайте его в панели (раздел DNS → «скачать
   корень»), перекиньте на телефон и поставьте через Настройки → Безопасность →
   Шифрование → Установить сертификат → Сертификат ЦС. Сверьте отпечаток с тем,
   что показывает панель. Учтите: приложения (не браузер) пользовательским
   корням по умолчанию не доверяют — это ограничение Android, не панели.
"""

_CA_BLOCKS = {
    "linux": _CA_LINUX,
    "windows": _CA_WINDOWS,
    "macos": _CA_MACOS,
    "android": _CA_ANDROID,
}


def ca_block(os_name: str, ca_pem: str) -> str:
    """Кусок скрипта, ставящий корень панели в доверенные (пусто — если корня нет)."""
    if not ca_pem:
        return ""
    tpl = _CA_BLOCKS.get(os_name, _CA_LINUX)
    if not ca_pem.endswith("\n"):
        ca_pem += "\n"
    return tpl.replace("@@CAPEM@@", ca_pem)


OSES = ("linux", "windows", "macos", "android")

_TEMPLATES = {
    "windows": _WINDOWS_TEMPLATE,
    "macos": _MACOS_TEMPLATE,
    "android": _ANDROID_INSTRUCTIONS,
    "linux": _LINUX_TEMPLATE,
}


def _fill(
    tpl: str,
    *,
    version: str,
    server_url: str,
    key: str,
    hostname: str,
    extra: str = "",
    pkgbase: str = "",
    exit_setup: str = "",
    caroot: str = "",
) -> str:
    return (
        tpl.replace("@@VER@@", version)
        .replace("@@LOGIN@@", server_url)
        .replace("@@KEY@@", key)
        .replace("@@NAME@@", hostname)
        .replace("@@EXITSETUP@@", exit_setup)
        .replace("@@CAROOT@@", caroot)
        .replace("@@EXTRA@@", extra)
        .replace("@@PKGBASE@@", pkgbase)
    )


def build_script(
    os_name: str,
    settings: Settings,
    key: str,
    hostname: str,
    version: str | None = None,
    force_reauth: bool = False,
    exit_node: bool = False,
    ca_pem: str = "",
) -> str:
    """version=None → пиновая из настроек. force_reauth — для переподключения
    (переоформить регистрацию под новым ключом → новый IP из текущего диапазона).
    exit_node — нода анонсирует exit (--advertise-exit-node) + на Linux включаем и
    ЗАКРЕПЛЯЕМ ip_forward (иначе после ребута роутинг отвалится). Одобрить exit-
    маршрут всё равно нужно в панели («Маршруты»). Бинарь — с нашего мирора
    (server_url + /pkgs), при неудаче — с офиц. ca_pem — корень своей CA панели:
    он ставится в доверенные прямо здесь, чтобы имена внутри сети открывались
    без ругани с первой минуты, а не после похода по машинам с файлом."""
    server_url = settings.headscale_server_url or "https://hs.example.com"
    pkgbase = server_url.rstrip("/") + "/pkgs"
    tpl = _TEMPLATES.get(os_name, _LINUX_TEMPLATE)
    # --accept-routes по умолчанию: без него направления («кто → куда через ноду»
    # и полный туннель) молча не действуют на источнике — он игнорирует маршрут,
    # который панель раздаёт и одобряет, и трафик идёт напрямую. В headscale 0.29
    # netmap/маршруты фильтруются по ACL, поэтому нода получает ТОЛЬКО маршруты
    # тех направлений, где она источник, — лишнего не подхватит.
    flags = ["--accept-routes"]
    if force_reauth:
        flags.append("--force-reauth")
    if exit_node:
        flags.append("--advertise-exit-node")
    return _fill(
        tpl,
        version=version or settings.tailscale_version,
        server_url=server_url,
        key=key,
        hostname=hostname,
        extra=" ".join(flags),
        pkgbase=pkgbase,
        exit_setup=_EXIT_SETUP_LINUX if (exit_node and os_name == "linux") else "",
        caroot=ca_block(os_name, ca_pem),
    )
