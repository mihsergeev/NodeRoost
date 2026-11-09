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
IN_PROGRESS="$APP_DIR/data/headscale/.restart-in-progress"
STAMP="/run/noderoost-hs-apply.stamp"
MIN_GAP="${NODEROOST_HS_APPLY_GAP:-20}"
CONFIG="$APP_DIR/data/headscale/config/config.yaml"

exec 9>/run/noderoost-hs-apply.lock
flock -n 9 || exit 0
[ -f "$FLAG" ] || [ -f "$IN_PROGRESS" ] || exit 0

# Systemd повторит обработку, пока есть запрос. Пауза ограничивает частоту
# попыток даже при невалидном конфиге или отказе Docker.
NOW=$(date +%s)
LAST=$(cat "$STAMP" 2>/dev/null || echo 0)
case "$LAST" in ''|*[!0-9]*) LAST=0 ;; esac
WAIT=$((MIN_GAP - NOW + LAST))
[ "$WAIT" -le 0 ] || sleep "$WAIT"
date +%s > "$STAMP"

cd "$APP_DIR"
# Переименование забирает только текущий запрос. Правка, поступившая во время
# применения, создает новый флаг. Не удаляем ни один флаг при отказе.
if [ -f "$FLAG" ]; then
    mv -fT "$FLAG" "$IN_PROGRESS"
fi
REVISION=$(sha256sum "$CONFIG")
docker compose run --rm --no-deps --pull never headscale configtest
[ "$REVISION" = "$(sha256sum "$CONFIG")" ] || exit 0

# docker compose сам подхватывает ./.env (COMPOSE_FILE=compose.yml:compose.caddy.yml)
docker compose restart headscale
for _ in $(seq 1 12); do
    if timeout 5 docker compose exec -T headscale headscale health >/dev/null 2>&1; then
        rm -f "$IN_PROGRESS"
        exit 0
    fi
    sleep 2
done
echo "hs-apply: headscale не прошел проверку после перезапуска" >&2
exit 1
