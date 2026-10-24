#!/bin/sh
# Выставляет владельца каталогов, в которые пишет бэкенд панели.
#
#   sudo ops/data-owner.sh
#
# Бэкенд в контейнере работает от uid 10001 (см. backend/Dockerfile), а каталоги
# под ./data на установках до 0.20 созданы от root. После обновления панель
# молча теряет право писать: бэкапы не создаются, правка DNS не ложится в
# config.yaml, зеркало пакетов не качается. Скрипт вызывается из install.sh и
# update.sh, но его можно запустить и руками.
#
# Рекурсивно по всему ./data ходить НЕЛЬЗЯ: там же data/postgres, data/caddy и
# data/headscale/lib - у этих служб свои uid внутри их образов, и смена
# владельца их ломает (postgres просто не стартует). Поэтому берем ровно те
# пути, куда пишет бэкенд.
set -eu

cd "$(dirname "$0")/.."
[ -f compose.yml ] || { echo "Это не каталог установки NodeRoost: $(pwd)" >&2; exit 1; }
[ -d data ] || { echo "Каталога ./data нет - сначала установка (ops/install.sh)" >&2; exit 1; }

UID_APP=10001

if [ "$(id -u)" != "0" ]; then
    echo "Нужен root: sudo ops/data-owner.sh" >&2
    exit 1
fi

# сам каталог данных и файлы прямо в нем (heartbeat, флаги выгрузки логов)
chown "$UID_APP:$UID_APP" data
find data -maxdepth 1 -type f -exec chown "$UID_APP:$UID_APP" {} +

# каталоги целиком
for d in data/backups data/tailscale-pkgs data/headscale/config; do
    [ -d "$d" ] || continue
    chown -R "$UID_APP:$UID_APP" "$d"
done

# родительский каталог headscale: в него ложится флаг .restart-request, но
# lib/ и run/ внутри принадлежат самому headscale - рекурсию не включаем
[ -d data/headscale ] && chown "$UID_APP:$UID_APP" data/headscale

# бэкапы содержат приватные ключи узла и дамп базы - только владельцу
chmod 700 data/backups 2>/dev/null || true

echo "владелец ./data и каталогов панели: $UID_APP (postgres, caddy и headscale/lib не тронуты)"
