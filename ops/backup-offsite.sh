#!/bin/sh
# NodeRoost: отправка бэкап-архивов панели в offsite restic-репозиторий
# (rest-server, append-only). Сами архивы делает панель (data/backups/*.tar.gz,
# каждый — консистентный снимок headscale + panel.json); этот скрипт лишь
# складывает их в restic, чтобы пережить потерю всего сервера.
#
# Реквизиты держим ВНЕ репо, в /lib65/noderoost/offsite.env (chmod 600):
#   export RESTIC_REPOSITORY="rest:https://USER:PASS@backup.example.com/noderoost/"
#   export RESTIC_PASSWORD="<пароль шифрования репозитория>"
# (rest-server append-only: init и backup можно, forget/prune — нельзя; ретеншн
#  настраивается на стороне rest-server.)
#
# Установка: создайте offsite.env с реквизитами и запустите ops/install.sh
# или ops/update.sh - скрипт и cron поставятся сами:
#   install -d -m700 /lib65/noderoost
#   $EDITOR /lib65/noderoost/offsite.env && chmod 600 /lib65/noderoost/offsite.env
#   sudo ops/update.sh
# Вручную:
#   ops/install-alert-helper.sh
#   install -D -m755 ops/backup-offsite.sh /lib65/noderoost/backup-offsite.sh
#   echo '30 3 * * * root /lib65/noderoost/backup-offsite.sh' > /etc/cron.d/noderoost-offsite
#   chmod 644 /etc/cron.d/noderoost-offsite
set -eu

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH

ENVF="${NODEROOST_OFFSITE_ENV:-/lib65/noderoost/offsite.env}"
[ -r "$ENVF" ] || { echo "нет $ENVF — offsite не настроен, выходим" >&2; exit 0; }
# Файл содержит пароль репозитория бэкапов и ключи хранилища, а мы его
# ИСПОЛНЯЕМ (точка-пробел): доступный на запись кому-то кроме root, он
# превращается в чужой код от root. Инструкция про chmod 600 была, проверки не
# было - проверяем сами.
if [ -n "$(find "$ENVF" -maxdepth 0 -perm /go+w 2>/dev/null)" ]; then
    echo "$ENVF доступен на запись группе или всем - исправьте: chmod 600 $ENVF" >&2
    exit 1
fi
_owner=$(stat -c '%u' "$ENVF" 2>/dev/null || echo 0)
[ "$_owner" = "0" ] || { echo "$ENVF принадлежит не root (uid $_owner) - исправьте: chown root $ENVF" >&2; exit 1; }
# shellcheck disable=SC1090
. "$ENVF"
: "${RESTIC_REPOSITORY:?offsite.env: не задан RESTIC_REPOSITORY}"
: "${RESTIC_PASSWORD:?offsite.env: не задан RESTIC_PASSWORD}"
export RESTIC_REPOSITORY RESTIC_PASSWORD

# Корень установки. Этот скрипт ставится в /lib65/noderoost — то есть вне каталога
# панели, и вычислить путь «от себя» нельзя: получится /lib65. Установщик
# подставляет сюда реальный каталог; если панель переехала, поправьте строку.
APP_ROOT="${NODEROOST_APP:-/opt/noderoost}"
# Запуск прямо из ops/, без установки: берём соседний каталог, если он похож на
# установку панели.
if [ ! -f "$APP_ROOT/compose.yml" ]; then
    _near="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd || true)"
    [ -n "${_near:-}" ] && [ -f "$_near/compose.yml" ] && APP_ROOT="$_near"
fi
SRC="${NODEROOST_BACKUPS_DIR:-$APP_ROOT/data/backups}"
[ -d "$SRC" ] || { echo "нет каталога бэкапов: $SRC" >&2; exit 0; }

# Отказ выгрузки - самый тихий из отказов: архивы продолжают копиться на
# сервере, панель показывает свежий бекап, и все выглядит целым - до того
# дня, когда сервера не станет. Канал алертов берем из пульса панели - тот
# же, что у сторожа (ops/panel-watchdog.sh), настраивать отдельно не надо.
HB="${NODEROOST_HEARTBEAT:-$APP_ROOT/data/heartbeat}"
STATE="${NODEROOST_OFFSITE_STATE:-/lib65/noderoost/offsite.state}"

val(){ grep -m1 "^$1=" "$HB" 2>/dev/null | cut -d= -f2-; }
notify(){
    _helper=/lib65/scripts/noderoost/send-host-alert.py
    [ ! -f "$(dirname "$0")/send-host-alert.py" ] || _helper="$(dirname "$0")/send-host-alert.py"
    printf '%s' "$1" | timeout 30s python3 -Es "$_helper" "$HB"
}

_panel=$(val panel); [ -n "$_panel" ] || _panel="$(hostname)"

# Алерт только на переходе: иначе недоступное хранилище прислало бы по
# сообщению в сутки, и их перестали бы читать.
fail(){
    echo "$1" >&2
    if [ "$(cat "$STATE" 2>/dev/null)" != "fail" ] &&
        notify "NodeRoost offsite [$_panel]: $2"; then
        mkdir -p "$(dirname "$STATE")" 2>/dev/null || true
        printf 'fail\n' > "$STATE" 2>/dev/null || true
    fi
    exit 1
}

# restic ставится отдельно; без него скрипт свалился бы невнятной ошибкой sh
command -v restic >/dev/null 2>&1 || fail "не найден restic - поставьте его (apt install restic)" "не найден restic"

LOG=$(mktemp 2>/dev/null) || LOG="/tmp/noderoost-offsite.$$"
trap 'rm -f "$LOG"' EXIT INT TERM

# init, если репозитория ещё нет (append-only rest-server допускает init)
if ! restic snapshots >/dev/null 2>&1; then
    restic init >"$LOG" 2>&1 ||
        fail "не удалось создать репозиторий: $(tail -n 3 "$LOG" | tr '\n' ' ')" "не удалось создать offsite-репозиторий"
fi

# прунинг НЕ делаем (append-only; ретеншн — на rest-server)
if restic backup --tag noderoost --host noderoost "$SRC" >"$LOG" 2>&1; then
    if [ "$(cat "$STATE" 2>/dev/null)" = "fail" ]; then
        notify "NodeRoost offsite [$_panel]: выгрузка бекапов снова проходит" || true
    fi
    mkdir -p "$(dirname "$STATE")" 2>/dev/null || true
    printf 'ok\n' > "$STATE" 2>/dev/null || true
else
    fail "выгрузка бекапов не прошла: $(tail -n 3 "$LOG" | tr '\n' ' ')" "выгрузка бекапов не прошла"
fi
