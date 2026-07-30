#!/bin/sh
# NodeRoost: восстановление из бэкап-архива (tar.gz, что делает панель).
# Кладёт файлы headscale (db.sqlite/config/ключи) на место, импортит panel.json
# в Postgres и перезапускает стек. У бэкенда нет доступа к Docker by design,
# поэтому оркестрация — здесь, на хосте.
#
#   sudo ops/restore.sh data/backups/noderoost-backup-YYYYMMDD-HHMMSS.tar.gz
#
# Второй аргумент — корень приложения (по умолчанию — каталог установки).
# Для DR на ПУСТОМ сервере сначала подними стек один раз (docker compose up -d),
# чтобы прогнать миграции, затем запусти restore.
set -eu

ARCHIVE="${1:?укажите путь к архиву бэкапа}"
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
APP="${2:-$APP_ROOT}"
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

# ── Ключ панели к восстановленному headscale ────────────────────────────────
# В архиве приехала БАЗА headscale, а вместе с ней и его список API-ключей.
# Ключ, которым ходит панель, лежит в .env — и если восстанавливаемся на другой
# машине, он там свой, новый: в восстановленной базе его нет, и панель молча
# показывает «headscale: down». Проверяем и, если ключ не подходит, выпускаем
# новый прямо здесь.
echo ">> проверяю ключ панели к headscale…"
for _ in $(seq 1 30); do
    docker compose exec -T headscale headscale apikeys list >/dev/null 2>&1 && break
    sleep 2
done
KEY_OK=0
if [ -f "$APP/.env" ]; then
    CUR="$(sed -n 's/^NODEROOST_HEADSCALE_API_KEY=//p' "$APP/.env" | head -1)"
    if [ -n "$CUR" ]; then
        PFX="$(printf '%s' "$CUR" | cut -c1-14)"
        docker compose exec -T headscale headscale apikeys list 2>/dev/null \
            | grep -q "$PFX" && KEY_OK=1
    fi
fi
if [ "$KEY_OK" != "1" ]; then
    NEW_KEY="$(docker compose exec -T headscale headscale apikeys create --expiration 3650d 2>/dev/null | tail -1 | tr -d '\r')"
    if [ -n "$NEW_KEY" ]; then
        sed -i "s|^NODEROOST_HEADSCALE_API_KEY=.*|NODEROOST_HEADSCALE_API_KEY=$NEW_KEY|" "$APP/.env"
        docker compose up -d backend
        echo "   ключ панели перевыпущен под восстановленную базу"
    else
        echo "   ВНИМАНИЕ: не удалось выпустить ключ — панель не увидит headscale"
    fi
fi

# Восстановленный config.yaml несёт server_url ТОЙ машины, с которой снят архив.
# Если восстанавливаемся на другую, ноды продолжат ходить по старому адресу.
SRV="$(sed -n 's/^server_url: *//p' "$APP/data/headscale/config/config.yaml" | head -1)"
HSD="$(sed -n 's/^NODEROOST_HS_DOMAIN=//p' "$APP/.env" | head -1)"
if [ -n "$HSD" ] && [ -n "$SRV" ] && [ "${SRV#*//}" != "$HSD" ]; then
    echo
    echo "   ВНИМАНИЕ: в восстановленном конфиге server_url = $SRV,"
    echo "   а этот сервер обслуживает $HSD. Ноды помнят СТАРЫЙ адрес и придут"
    echo "   именно на него. Либо направьте старое имя на этот сервер, либо"
    echo "   переподключите ноды на новое (в панели: «Переподключить»)."
fi

echo ">> ГОТОВО. Проверь: docker compose ps ; вход в панель ; docker compose exec headscale headscale nodes list"
