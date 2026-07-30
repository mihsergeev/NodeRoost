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
# Установка:
#   install -D -m755 ops/backup-offsite.sh /lib65/noderoost/backup-offsite.sh
#   $EDITOR /lib65/noderoost/offsite.env   # вписать реквизиты, chmod 600
#   echo '30 3 * * * root /lib65/noderoost/backup-offsite.sh' > /etc/cron.d/noderoost-offsite
#   chmod 644 /etc/cron.d/noderoost-offsite
set -eu

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH

ENVF="${NODEROOST_OFFSITE_ENV:-/lib65/noderoost/offsite.env}"
[ -r "$ENVF" ] || { echo "нет $ENVF — offsite не настроен, выходим" >&2; exit 0; }
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

# init, если репозитория ещё нет (append-only rest-server допускает init)
restic snapshots >/dev/null 2>&1 || restic init

# прунинг НЕ делаем (append-only; ретеншн — на rest-server)
restic backup --tag noderoost --host noderoost "$SRC"
