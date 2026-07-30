#!/bin/bash
# Хостовый сторож NodeRoost (dead-man's-switch, вне docker).
#
# Панель не может сама сообщить о собственной смерти по своему же каналу, поэтому
# нужен наблюдатель ВНЕ её процесса. Этот скрипт читает «пульс», который панель
# пишет раз в минуту в <установка>/data/heartbeat (backend/app/heartbeat.py),
# и НЕЗАВИСИМО шлёт в Telegram/webhook, если:
#   • пульс протух (>MAX_AGE) — контейнер/БД мертвы или зависли;
#   • alerts_ok=0 — self-тест канала алертов панели не прошёл.
# Креды (токен/чат/зеркало) берёт из самого пульса — достучится даже когда панель
# и БД уже не отвечают. Алертит только на переходе (без спама).
#
# УСТАНОВКА (на хосте панели, от root):
#   install -D -m755 ops/panel-watchdog.sh /lib65/noderoost/panel-watchdog.sh
#   echo '*/5 * * * * root /lib65/noderoost/panel-watchdog.sh' > /etc/cron.d/noderoost-watchdog
#   chmod 644 /etc/cron.d/noderoost-watchdog
# (скрипт — в /lib65, т.к. /usr исключён из бэкапа; cron-конфиг в /etc.)

set -u
# Корень приложения: по умолчанию — каталог, из которого запущен скрипт
# (ops/ внутри установки). Так он работает и в /opt/noderoost, и в любом
# другом каталоге, куда поставили панель.
APP_ROOT="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd || echo /opt/noderoost)"
HB="${NODEROOST_HEARTBEAT:-$APP_ROOT/data/heartbeat}"
STATE=/lib65/noderoost/watchdog.state
MAX_AGE="${NODEROOST_HB_MAX_AGE:-600}"
STRIKES="${NODEROOST_WD_STRIKES:-2}"          # проверок ПОДРЯД с проблемой до тревоги
NOW=$(date +%s)

val(){ grep -m1 "^$1=" "$HB" 2>/dev/null | cut -d= -f2-; }

send(){
  local msg="$1" token chat api hook
  token=$(val tg_token); chat=$(val tg_chat); api=$(val tg_api); hook=$(val webhook)
  [ -z "$api" ] && api="https://api.telegram.org"
  if [ -n "$token" ] && [ -n "$chat" ]; then
    curl -s --max-time 15 "$api/bot$token/sendMessage" \
      --data-urlencode "chat_id=$chat" --data-urlencode "text=$msg" \
      --data-urlencode "disable_web_page_preview=true" >/dev/null 2>&1
  fi
  [ -n "$hook" ] && curl -s --max-time 15 -H 'Content-Type: application/json' \
    --data "{\"text\": \"$(printf '%s' "$msg" | sed 's/\\/\\\\/g; s/"/\\"/g')\"}" "$hook" >/dev/null 2>&1
}

problem=""
if [ ! -f "$HB" ]; then
  problem="файл пульса отсутствует — панель не пишет heartbeat (не запущена?)"
else
  ts=$(val ts); ok=$(val alerts_ok); age=$(( NOW - ${ts:-0} ))
  if [ "${ts:-0}" -eq 0 ] || [ "$age" -gt "$MAX_AGE" ]; then
    problem="панель молчит ${age}с (>${MAX_AGE}с) — контейнер/БД мертвы или зависли"
  elif [ "$ok" = "0" ]; then
    problem="панель жива, но канал алертов сломан (Telegram недоступен/токен невалиден)"
  fi
fi

panel=$(val panel); [ -z "$panel" ] && panel="$(hostname)"
tag="🚑 NodeRoost watchdog [$panel]"

# Дебаунс: тревогу шлём после STRIKES проверок ПОДРЯД с проблемой — чтобы рестарт
# панели при деплое / разовый блип не будили сторожа. Состояние: строка1 = слали
# ли уже (ok|problem), строка2 = стрик.
alerted=$(sed -n 1p "$STATE" 2>/dev/null); [ -z "$alerted" ] && alerted=ok
strikes=$(sed -n 2p "$STATE" 2>/dev/null); case "$strikes" in ''|*[!0-9]*) strikes=0;; esac

if [ -n "$problem" ]; then
  strikes=$((strikes + 1))
  if [ "$alerted" != "problem" ] && [ "$strikes" -ge "$STRIKES" ]; then
    send "$tag: $problem"
    alerted=problem
  fi
else
  strikes=0
  if [ "$alerted" = "problem" ]; then
    send "✅ NodeRoost watchdog [$panel]: панель снова в норме."
    alerted=ok
  fi
fi
mkdir -p "$(dirname "$STATE")"
printf '%s\n%s\n' "$alerted" "$strikes" > "$STATE"
