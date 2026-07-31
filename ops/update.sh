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

# Хостовые помощники живут ВНЕ каталога панели и сами не обновятся. Ставим их и
# тем, у кого их не было: на установках 0.1.x помощников не существовало, и после
# обновления панель писала «headscale перезапускается», хотя перезапускать его
# было некому — правка DNS ложилась в конфиг и не вступала в силу.
if mkdir -p /lib65/noderoost 2>/dev/null; then
    echo ">> ставлю/обновляю помощники в /lib65/noderoost…"
    for f in hs-apply.sh hs-logs.sh; do
        sed "s|^APP_DIR=.*|APP_DIR=$APP|" "ops/$f" > "/lib65/noderoost/$f"
        chmod 755 "/lib65/noderoost/$f"
    done
    # Сторож ставим и тем, у кого его ещё не было: на установках старше 0.2.2 его
    # просто не существовало, а обновление — единственный момент, когда он там
    # появится.
    sed "s|^APP_ROOT=.*|APP_ROOT=\"\${NODEROOST_APP:-$APP}\"|" \
        ops/panel-watchdog.sh > /lib65/noderoost/panel-watchdog.sh
    chmod 755 /lib65/noderoost/panel-watchdog.sh
    if [ ! -f /etc/cron.d/noderoost-watchdog ]; then
        echo '*/5 * * * * root /lib65/noderoost/panel-watchdog.sh' \
            > /etc/cron.d/noderoost-watchdog
        chmod 644 /etc/cron.d/noderoost-watchdog
    fi
    # systemd-юниты помощников: могли поменяться вместе с релизом, а на старых
    # установках их просто нет — тогда ставим впервые
    for u in noderoost-hs-apply noderoost-hs-logs; do
        for ext in path service; do
            sed "s|/app/noderoost|$APP|g; s|/opt/noderoost|$APP|g" \
                "ops/$u.$ext" > "/etc/systemd/system/$u.$ext"
        done
    done
    systemctl daemon-reload 2>/dev/null || true
    systemctl enable --now noderoost-hs-apply.path noderoost-hs-logs.path \
        >/dev/null 2>&1 || true
fi

echo ">> ГОТОВО. Проверьте: docker compose ps ; версия внизу страницы панели."
