#!/bin/sh
# NodeRoost: восстановление из бэкап-архива (tar.gz, что делает панель).
# Кладёт файлы headscale (db.sqlite/config/ключи) на место, импортит panel.json
# в Postgres и перезапускает стек. У бэкенда нет доступа к Docker by design,
# поэтому оркестрация — здесь, на хосте.
#
#   sudo /lib65/noderoost/restore.sh /app/noderoost/data/backups/noderoost-backup-YYYYMMDD-HHMMSS.tar.gz
#
# Второй аргумент — корень приложения (по умолчанию /app/noderoost).
# Для DR на ПУСТОМ сервере сначала подними стек один раз (docker compose up -d),
# чтобы прогнать миграции, затем запусти restore.
set -eu

ARCHIVE="${1:?укажите путь к архиву бэкапа}"
APP="${2:-/app/noderoost}"
[ -f "$ARCHIVE" ] || { echo "нет архива: $ARCHIVE" >&2; exit 1; }
cd "$APP"

TMP=$(mktemp -d)
# В $APP/data/_restore_panel.json лежит дамп панели (учётки, TOTP-секреты,
# настройки алертов с токенами). Чистим его тем же trap'ом: при падении любой из
# команд ниже (set -eu) файл иначе остаётся на диске и уезжает в следующий бэкап.
trap 'rm -rf "$TMP"; rm -f "$APP/data/_restore_panel.json"' EXIT INT TERM
tar -xzf "$ARCHIVE" -C "$TMP"

# минимальная комплектность
[ -f "$TMP/panel.json" ] || { echo "в архиве нет panel.json" >&2; exit 1; }
[ -f "$TMP/headscale/db.sqlite" ] || { echo "в архиве нет headscale/db.sqlite" >&2; exit 1; }
# снимок sqlite должен быть настоящей базой
head -c 16 "$TMP/headscale/db.sqlite" | grep -q "SQLite format 3" \
  || { echo "db.sqlite в архиве повреждён" >&2; exit 1; }

echo ">> останавливаю backend + headscale (db оставляю для импорта)…"
docker compose stop backend headscale

echo ">> восстанавливаю файлы headscale…"
install -d "$APP/data/headscale/lib" "$APP/data/headscale/config"
# сносим WAL/SHM старой базы, иначе headscale смешает их с новым снимком
rm -f "$APP/data/headscale/lib/db.sqlite" \
      "$APP/data/headscale/lib/db.sqlite-wal" \
      "$APP/data/headscale/lib/db.sqlite-shm"
cp "$TMP/headscale/db.sqlite" "$APP/data/headscale/lib/db.sqlite"
[ -f "$TMP/headscale/config.yaml" ] && \
  cp "$TMP/headscale/config.yaml" "$APP/data/headscale/config/config.yaml" || true
for k in "$TMP"/headscale/lib/*.key; do
  [ -f "$k" ] && cp "$k" "$APP/data/headscale/lib/" || true
done

echo ">> прогоняю миграции (на случай пустой БД) + импортирую panel.json в Postgres…"
cp "$TMP/panel.json" "$APP/data/_restore_panel.json"
chmod 600 "$APP/data/_restore_panel.json"   # учётки/TOTP — не под umask 0644
docker compose run --rm --no-deps backend alembic upgrade head
docker compose run --rm --no-deps backend python -m app.restore_panel /data/_restore_panel.json
rm -f "$APP/data/_restore_panel.json"

echo ">> поднимаю стек…"
docker compose up -d

echo ">> ГОТОВО. Проверь: docker compose ps ; вход в панель ; docker compose exec headscale headscale nodes list"
