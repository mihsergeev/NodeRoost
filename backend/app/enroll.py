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
# --reset обязателен: если на машине уже стояли какие-то настройки Tailscale
# (например --exit-node-allow-lan-access от прошлого выхода через шлюз),
# `tailscale up` отказывается их менять — «requires mentioning all non-default
# flags» — и подключение падает. Мы задаём состояние ноды целиком, поэтому
# сбрасываем прежнее.
tailscale up --reset --login-server="$LOGIN" --authkey="$KEY" --hostname="$NAME" @@EXTRA@@
echo "NodeRoost: нода \"$NAME\" подключена."
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
# --reset: см. пояснение в linux-шаблоне — без него `tailscale up` не меняет
# настройки на машине, где уже был задан любой неявный флаг.
& $ts up --reset --login-server=$login --authkey=$key --hostname=$name @@EXTRA@@
Write-Host "NodeRoost: нода `"$name`" подключена."
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

# --reset обязателен: если на машине уже стояли какие-то настройки Tailscale
# (например --exit-node-allow-lan-access от прошлого выхода через шлюз),
# `tailscale up` отказывается их менять — «requires mentioning all non-default
# flags» — и подключение падает. Мы задаём состояние ноды целиком, поэтому
# сбрасываем прежнее.
tailscale up --reset --login-server="$LOGIN" --authkey="$KEY" --hostname="$NAME" @@EXTRA@@
echo "NodeRoost: нода \"$NAME\" подключена."
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
"""

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
) -> str:
    return (
        tpl.replace("@@VER@@", version)
        .replace("@@LOGIN@@", server_url)
        .replace("@@KEY@@", key)
        .replace("@@NAME@@", hostname)
        .replace("@@EXITSETUP@@", exit_setup)
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
) -> str:
    """version=None → пиновая из настроек. force_reauth — для переподключения
    (переоформить регистрацию под новым ключом → новый IP из текущего диапазона).
    exit_node — нода анонсирует exit (--advertise-exit-node) + на Linux включаем и
    ЗАКРЕПЛЯЕМ ip_forward (иначе после ребута роутинг отвалится). Одобрить exit-
    маршрут всё равно нужно в панели («Маршруты»). Бинарь — с нашего мирора
    (server_url + /pkgs), при неудаче — с офиц."""
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
    )
