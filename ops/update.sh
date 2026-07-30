#!/bin/bash
# Обновление NodeRoost до свежего релиза.
#
# Просто `git pull && docker compose up -d` НЕ обновляет панель: теги образов
# берутся из NODEROOST_VERSION в .env, а .env — локальный файл, который git не
# трогает. Команды отрабатывают без единой ошибки, и панель остаётся на прежней
# сборке. Этот скрипт переносит версию из .env.example (её двигает релиз) в .env
# и поднимает стек.
#
#   sudo ops/update.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."
APP="$(pwd)"
[ -f compose.yml ] && [ -f .env ] || { echo "Это не каталог установки NodeRoost: $APP"; exit 1; }

# Локальные правки в рабочем дереве молча затирать нельзя — они могут быть
# осмысленными (свой Caddyfile, патч). Останавливаемся и говорим прямо.
if [ -d .git ] && ! git diff --quiet HEAD 2>/dev/null; then
    echo "В $APP есть незакоммиченные изменения — обновление остановлено."
    echo "Посмотрите 'git status', сохраните или отмените их и запустите снова."
    exit 1
fi

echo ">> забираю свежий код…"
[ -d .git ] && git pull --ff-only

NEW="$(sed -n 's/^NODEROOST_VERSION=\(.*\)/\1/p' .env.example | head -1)"
CUR="$(sed -n 's/^NODEROOST_VERSION=\(.*\)/\1/p' .env | head -1)"
if [ -n "$NEW" ] && [ "$NEW" != "$CUR" ]; then
    sed -i "s|^NODEROOST_VERSION=.*|NODEROOST_VERSION=$NEW|" .env
    echo ">> версия: $CUR → $NEW"
else
    echo ">> версия та же ($CUR) — обновляю на месте"
fi

# Готовые образы есть только у релизов. Если их нет (свой форк, промежуточный
# коммит) — собираем локально, чтобы обновление не упиралось в реестр.
echo ">> тяну образы…"
if ! docker compose pull --quiet </dev/null 2>/dev/null; then
    echo "   образов в реестре нет — собираю на месте"
    docker compose build </dev/null
fi

echo ">> поднимаю стек…"
docker compose up -d </dev/null

# Хостовые помощники живут ВНЕ каталога панели и сами не обновятся.
if [ -d /lib65/noderoost ]; then
    echo ">> обновляю помощники в /lib65/noderoost…"
    for f in hs-apply.sh hs-logs.sh; do
        [ -f "/lib65/noderoost/$f" ] || continue
        sed "s|^APP_DIR=.*|APP_DIR=$APP|" "ops/$f" > "/lib65/noderoost/$f"
        chmod 755 "/lib65/noderoost/$f"
    done
    if [ -f /lib65/noderoost/panel-watchdog.sh ]; then
        sed "s|^APP_ROOT=.*|APP_ROOT=\"\${NODEROOST_APP:-$APP}\"|" \
            ops/panel-watchdog.sh > /lib65/noderoost/panel-watchdog.sh
        chmod 755 /lib65/noderoost/panel-watchdog.sh
    fi
fi

echo ">> ГОТОВО. Проверьте: docker compose ps ; версия внизу страницы панели."
