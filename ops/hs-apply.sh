#!/bin/sh
# Перезапускает контейнер headscale, когда панель просит применить изменения
# config.yaml (напр. DNS/MagicDNS). Запускается systemd path-юнитом
# noderoost-hs-apply.path при появлении флага. Живёт в /lib65 (бэкапится),
# НЕ в /usr (исключён из restic-бэкапа).
set -eu

# Каталог установки подставляет ops/install.sh. Этот скрипт лежит в /lib65, то
# есть вне каталога панели, и «от себя» путь не вычислить - получится /lib65.
# Ставите руками - поправьте строку, иначе флаг не найдётся и DNS в панели будет
# молча не применяться.
APP_DIR="${NODEROOST_APP:-/opt/noderoost}"
if [ ! -f "$APP_DIR/compose.yml" ]; then
    _near="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd || true)"
    if [ -n "${_near:-}" ] && [ -f "$_near/compose.yml" ]; then
        APP_DIR="$_near"
    else
        echo "hs-apply: не найден каталог установки ($APP_DIR) - поправьте APP_DIR" >&2
        exit 1
    fi
fi
FLAG="$APP_DIR/data/headscale/.restart-request"
STAMP="/run/noderoost-hs-apply.stamp"
MIN_GAP="${NODEROOST_HS_APPLY_GAP:-20}"

[ -f "$FLAG" ] || exit 0
rm -f "$FLAG"

# Флаг ставит контейнер панели, а перезапуск делаем мы на хосте. Без паузы между
# перезапусками контейнер мог крутить control-сервер в цикле: ставим флаг -
# рестарт - ставим снова. Ждём, пока с прошлого раза пройдёт хотя бы MIN_GAP.
NOW=$(date +%s)
LAST=$(cat "$STAMP" 2>/dev/null || echo 0)
case "$LAST" in ''|*[!0-9]*) LAST=0 ;; esac
if [ $((NOW - LAST)) -lt "$MIN_GAP" ]; then
    echo "hs-apply: прошлый перезапуск был $((NOW - LAST))с назад - пропускаю" >&2
    exit 0
fi
echo "$NOW" > "$STAMP" 2>/dev/null || true

cd "$APP_DIR"
# docker compose сам подхватывает ./.env (COMPOSE_FILE=compose.yml:compose.caddy.yml)
docker compose restart headscale
