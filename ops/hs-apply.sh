#!/bin/sh
# Перезапускает контейнер headscale, когда панель просит применить изменения
# config.yaml (напр. DNS/MagicDNS). Запускается systemd path-юнитом
# noderoost-hs-apply.path при появлении флага. Живёт в /lib65 (бэкапится),
# НЕ в /usr (исключён из restic-бэкапа).
set -eu

APP_DIR="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd || echo /opt/noderoost)"
FLAG="$APP_DIR/data/headscale/.restart-request"

[ -f "$FLAG" ] || exit 0
rm -f "$FLAG"

cd "$APP_DIR"
# docker compose сам подхватывает ./.env (COMPOSE_FILE=compose.yml:compose.caddy.yml)
docker compose restart headscale
