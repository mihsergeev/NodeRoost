#!/bin/sh
# По запросу панели (флаг) складывает свежие логи контейнера headscale в файл,
# который читает бэкенд (у него нет доступа к Docker by design). Запускается
# systemd path-юнитом noderoost-hs-logs.path. Живёт в /lib65 (бэкапится).
set -eu

APP_DIR=/app/noderoost
FLAG="$APP_DIR/data/.hslogs-request"
OUT="$APP_DIR/data/_hslogs.txt"

[ -f "$FLAG" ] || exit 0
rm -f "$FLAG"

cd "$APP_DIR"
# Пишем во временный файл ВНЕ каталога, в который пишет контейнер, и только потом
# переносим на место. Раньше промежуточный файл лежал рядом («$OUT.tmp»), а туда
# бэкенд-контейнер имеет запись: подложив по этому пути симлинк, он заставил бы
# root-скрипт записать содержимое куда угодно на хосте — например в /etc/cron.d.
# Содержимое при этом частично подконтрольно: в логи headscale попадают имена нод.
# `mv` безопасен и на приёмнике: он ЗАМЕНЯЕТ путь, а не пишет сквозь симлинк.
TMP=$(mktemp /run/noderoost-hslogs.XXXXXX)
chmod 0644 "$TMP"
docker compose logs --no-color --tail 400 headscale > "$TMP" 2>&1 || true
mv -f "$TMP" "$OUT"
