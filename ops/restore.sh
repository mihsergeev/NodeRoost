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
STOPPED=0
REPLACED=0
HS_DATA="$APP/data/headscale"

# Перечень путей, которые заменяем. WAL/SHM у старой базы тоже нужны для отката.
headscale_files() {
    printf '%s\n' lib/db.sqlite lib/db.sqlite-wal lib/db.sqlite-shm \
        config/config.yaml config/extra-records.json
    for key in "$TMP"/headscale/lib/*.key; do
        [ ! -f "$key" ] || printf 'lib/%s\n' "${key##*/}"
    done
}

cleanup() {
    rc=$?
    if [ "$STOPPED" = 1 ]; then
        if [ "$REPLACED" = 1 ] && ! headscale_files | while IFS= read -r name; do
            rm -f "$HS_DATA/$name" || exit 1
            [ ! -e "$TMP/before/$name" ] || cp -a "$TMP/before/$name" "$HS_DATA/$name" || exit 1
        done; then
            echo "откат файлов не завершен, исходные файлы сохранены в $TMP/before" >&2
            exit 1
        fi
        if ! docker compose start headscale backend; then
            echo "не удалось снова запустить сервисы, исходные файлы в $TMP/before" >&2
            exit 1
        fi
    fi
    rm -rf "$TMP"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Распаковываем ДО остановки контейнеров: битый файл не должен ронять живую
# панель. Своё сообщение — вместо «gzip: stdin: not in gzip format».
if ! tar -xzf "$ARCHIVE" --no-same-owner -C "$TMP" 2>/dev/null; then
    echo "не читается как архив бэкапа: $ARCHIVE (повреждён или это не .tar.gz)" >&2
    exit 1
fi

# Архив пишет контейнер панели, а разворачиваем мы его под root и копируем из
# него в каталоги, которые панель потом читает. Обычный cp идёт ПО симлинку в
# источнике: подложенный в архив симлинк на /root/.ssh/id_rsa или /etc/shadow
# скопировал бы содержимое этих файлов туда, откуда их видит контейнер.
# Поэтому: каждый нужный путь обязан быть обычным файлом, и копируем с -P.
check_plain() {
    if [ -L "$1" ] || { [ -e "$1" ] && [ ! -f "$1" ]; }; then
        echo "в архиве подменён путь (симлинк или не файл): ${1#$TMP/}" >&2
        exit 1
    fi
}
for dir in "$TMP/headscale" "$TMP/headscale/lib"; do
    [ ! -L "$dir" ] || { echo "в архиве каталог подменен симлинком: ${dir#$TMP/}" >&2; exit 1; }
done
check_plain "$TMP/panel.json"
check_plain "$TMP/headscale/db.sqlite"
check_plain "$TMP/headscale/config.yaml"
check_plain "$TMP/headscale/extra-records.json"
for _k in "$TMP"/headscale/lib/*.key; do check_plain "$_k"; done

# минимальная комплектность
[ -f "$TMP/panel.json" ] || { echo "в архиве нет panel.json" >&2; exit 1; }
[ -f "$TMP/headscale/db.sqlite" ] || { echo "в архиве нет headscale/db.sqlite" >&2; exit 1; }
# снимок sqlite должен быть настоящей базой
head -c 16 "$TMP/headscale/db.sqlite" | grep -q "SQLite format 3" \
  || { echo "db.sqlite в архиве повреждён" >&2; exit 1; }

# Дамп читается через stdin от имени пользователя backend. На общем томе
# не появляется файл с учетными данными, созданный от root.
docker compose run -T --rm --no-deps backend python -m app.restore_panel --check - < "$TMP/panel.json"

# Не даем хостовому помощнику перезапустить Headscale посреди восстановления.
exec 9>/run/noderoost-hs-apply.lock
flock 9

echo ">> останавливаю backend + headscale (db оставляю для импорта)…"
STOPPED=1
docker compose stop backend headscale
install -d "$TMP/before/lib" "$TMP/before/config"
headscale_files | while IFS= read -r name; do
    [ ! -e "$HS_DATA/$name" ] || cp -a "$HS_DATA/$name" "$TMP/before/$name" || exit 1
done
REPLACED=1

echo ">> восстанавливаю файлы headscale…"
install -d "$APP/data/headscale/lib" "$APP/data/headscale/config"
# сносим WAL/SHM старой базы, иначе headscale смешает их с новым снимком
rm -f "$APP/data/headscale/lib/db.sqlite" \
      "$APP/data/headscale/lib/db.sqlite-wal" \
      "$APP/data/headscale/lib/db.sqlite-shm"
cp -P "$TMP/headscale/db.sqlite" "$APP/data/headscale/lib/db.sqlite"
if [ -f "$TMP/headscale/config.yaml" ]; then
    cp -P "$TMP/headscale/config.yaml" "$HS_DATA/config/config.yaml"
fi
if [ -f "$TMP/headscale/extra-records.json" ]; then
    cp -P "$TMP/headscale/extra-records.json" "$HS_DATA/config/extra-records.json"
fi
# С extra_records_path в конфиге headscale не стартует, если файла нет: архив мог
# быть снят до появления имён внутри сети, а конфиг в нём — уже с путём.
if grep -q '^ *extra_records_path:' "$APP/data/headscale/config/config.yaml" 2>/dev/null \
   && [ ! -f "$APP/data/headscale/config/extra-records.json" ]; then
    echo "   файла имён внутри сети нет — создаю пустой, иначе headscale не поднимется"
    echo '[]' > "$APP/data/headscale/config/extra-records.json"
fi
for k in "$TMP"/headscale/lib/*.key; do
  if [ -f "$k" ]; then cp -P "$k" "$HS_DATA/lib/"; fi
done

echo ">> прогоняю миграции (на случай пустой БД) + импортирую panel.json в Postgres…"
docker compose run -T --rm --no-deps --pull never headscale configtest
docker compose run --rm --no-deps backend alembic upgrade head
docker compose run -T --rm --no-deps backend python -m app.restore_panel - < "$TMP/panel.json"
# После коммита panel.json оставляем восстановленную пару баз. При ошибке
# запуска cleanup повторит start, не возвращая только половину состояния.
REPLACED=0

echo ">> поднимаю стек…"
docker compose up -d
STOPPED=0

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
