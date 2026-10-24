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
# Пульс пишет контейнер панели, а мы идем от root на хосте: все, что пришло
# из файла, считаем недоверенным. Адрес берем только http(s) и отдаем curl
# через --url: иначе значение с дефиса разбирается как опция (-K читает
# конфиг curl, то есть дает произвольные запросы и запись файлов).
url_or(){ case "$1" in https://*|http://*) printf '%s' "$1";; *) printf '%s' "$2";; esac; }

notify(){
    _msg="$1"
    _token=$(val tg_token); _chat=$(val tg_chat)
    _api=$(url_or "$(val tg_api)" "https://api.telegram.org")
    _hook=$(url_or "$(val webhook)" "")
    # В токене бота бывают только эти символы; остальное - мусор или попытка
    # дописать к адресу лишнее.
    case "$_token" in *[!A-Za-z0-9:_-]*) _token="";; esac
    if [ -n "$_token" ] && [ -n "$_chat" ]; then
        curl -s --max-time 15 --url "$_api/bot$_token/sendMessage" \
            --data-urlencode "chat_id=$_chat" --data-urlencode "text=$_msg" \
            --data-urlencode "disable_web_page_preview=true" >/dev/null 2>&1 || true
    fi
    if [ -n "$_hook" ]; then
        curl -s --max-time 15 -H 'Content-Type: application/json' \
            --data "{\"text\": \"$(printf '%s' "$_msg" | sed 's/\\/\\\\/g; s/"/\\"/g')\"}" \
            --url "$_hook" >/dev/null 2>&1 || true
    fi
}

_panel=$(val panel); [ -n "$_panel" ] || _panel="$(hostname)"

# Алерт только на переходе: иначе недоступное хранилище прислало бы по
# сообщению в сутки, и их перестали бы читать.
fail(){
    echo "$1" >&2
    if [ "$(cat "$STATE" 2>/dev/null)" != "fail" ]; then
        notify "NodeRoost offsite [$_panel]: $1"
        mkdir -p "$(dirname "$STATE")" 2>/dev/null || true
        printf 'fail\n' > "$STATE" 2>/dev/null || true
    fi
    exit 1
}

# restic ставится отдельно; без него скрипт свалился бы невнятной ошибкой sh
command -v restic >/dev/null 2>&1 || fail "не найден restic - поставьте его (apt install restic)"

LOG=$(mktemp 2>/dev/null) || LOG="/tmp/noderoost-offsite.$$"
trap 'rm -f "$LOG"' EXIT INT TERM

# init, если репозитория ещё нет (append-only rest-server допускает init)
if ! restic snapshots >/dev/null 2>&1; then
    restic init >"$LOG" 2>&1 || fail "не удалось создать репозиторий: $(tail -n 3 "$LOG" | tr '\n' ' ')"
fi

# прунинг НЕ делаем (append-only; ретеншн — на rest-server)
if restic backup --tag noderoost --host noderoost "$SRC" >"$LOG" 2>&1; then
    if [ "$(cat "$STATE" 2>/dev/null)" = "fail" ]; then
        notify "NodeRoost offsite [$_panel]: выгрузка бекапов снова проходит"
    fi
    mkdir -p "$(dirname "$STATE")" 2>/dev/null || true
    printf 'ok\n' > "$STATE" 2>/dev/null || true
else
    fail "выгрузка бекапов не прошла: $(tail -n 3 "$LOG" | tr '\n' ' ')"
fi
