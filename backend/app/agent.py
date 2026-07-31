"""Агент ноды: маршруты и exit задаются в панели, нода применяет их сама.

Зачем: headscale умеет только ОДОБРЯТЬ маршруты. Заставить ноду их анонсировать
(`tailscale set --advertise-routes=…`) он не может — это настройка клиента, и
канала «панель → нода» у headscale нет. Поэтому канал делаем сами, тем же
способом: на ноде systemd-таймер раз в минуту тянет с панели желаемое состояние
по токену и применяет его.

Формат состояния намеренно `key=value`, а не JSON: агент — POSIX sh, и разбор
JSON в нём получается хрупким (sed по кавычкам ломается на первом же пробеле).
"""

from __future__ import annotations

_SETUP = r"""#!/bin/sh
# Подавления истории здесь НЕТ намеренно. Скрипт доставляется через `curl … | sh`,
# то есть исполняется неинтерактивным шеллом, у которого истории нет вовсе, а токен
# в историю попадает из URL, который админ набирает руками — телом скрипта на это
# не повлиять. При этом `set +o history` тут был активно вреден: `set` — специальный
# встроенный оператор, и в dash (/bin/sh в Debian/Ubuntu) неизвестная опция роняет
# ВЕСЬ скрипт, причём молча, и `|| true` от этого не спасает.
# NodeRoost — агент ноды. Раз в минуту спрашивает панель, какие маршруты
# анонсировать и быть ли exit-нодой, и применяет это через tailscale set.
# Запускать под root ОДИН раз. Дальше всё управляется галками в панели.
set -e
STATE_URL="@@STATE_URL@@"
DIR=/lib65/noderoost-agent
mkdir -p "$DIR"

command -v curl >/dev/null 2>&1 || {
  echo "NodeRoost: ставлю curl…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y curl >/dev/null
}

cat > "$DIR/apply.sh" <<EOF
#!/bin/sh
# Тянет желаемое состояние с панели и применяет, ТОЛЬКО если оно изменилось —
# иначе tailscale set дёргался бы каждую минуту без нужды.
set -e
TMP=\$(mktemp)
# Панель недоступна — молча выходим: это её дело, а не ноды. Но 404 значит другое:
# такого узла панель больше не знает (его удалили). Тогда сообщаем по-человечески,
# а не сыплем в журнал сырым «curl: (22) … 404» каждую минуту.
CODE=\$(curl -sS --max-time 15 -o "\$TMP" -w '%{http_code}' "$STATE_URL" 2>/dev/null || echo 000)
if [ "\$CODE" = 404 ]; then
  rm -f "\$TMP"
  echo "NodeRoost: панель больше не знает этот узел — агент можно удалить:" >&2
  echo "  curl -fsSL $STATE_URL/remove | sh" >&2
  exit 0
fi
[ "\$CODE" = 200 ] || { rm -f "\$TMP"; exit 0; }
grep -q '^routes=' "\$TMP" || { rm -f "\$TMP"; exit 0; }   # мусор вместо ответа

ROUTES=\$(grep '^routes=' "\$TMP" | cut -d= -f2-)
WANT_EXIT=\$(grep '^exit=' "\$TMP" | cut -d= -f2-)
# use_exit — принудительный выход: весь трафик ЭТОЙ ноды через указанный шлюз
# (его тайнет-IP). Это exit-node, а НЕ subnet-маршруты, поэтому на другие ноды не
# течёт. Пусто = не форсим. --exit-node-allow-lan-access, чтобы не потерять LAN.
USE_EXIT=\$(grep '^use_exit=' "\$TMP" | cut -d= -f2-)

# --- сохранение публичного inbound при принудительном выходе (connmark) ---
# При --exit-node дефолтный маршрут уходит в туннель, и ОТВЕТЫ на входящие
# соединения тоже → сервер становится недоступен по своему внешнему IP. Помечаем
# входящие соединения conntrack'ом и заворачиваем их ответы в main-таблицу (прямой
# выход через eth0), мимо exit; весь ИСХОДЯЩИЙ трафик при этом идёт через шлюз.
# Правила iptables/ip rule НЕ персистентны — ставим их КАЖДЫЙ запуск (идемпотентно),
# чтобы пережить ребут. Выполняем ДО cmp, поэтому не гейтим по изменению состояния.
# Весь блок best-effort (set +e): нет iptables — применение exit-node не должно
# падать, просто сервер останется недоступен извне (о чём и так предупреждаем).
set +e
MARK=0x1
WAN=\$(ip -4 route show table main default 2>/dev/null | awk '{for(i=1;i<=NF;i++)if(\$i=="dev")print \$(i+1)}' | head -1)
while ip rule del fwmark \$MARK/\$MARK table main priority 5200 2>/dev/null; do :; done
if [ -n "\$USE_EXIT" ] && [ -n "\$WAN" ]; then
  iptables -t mangle -C PREROUTING -i "\$WAN" -m conntrack --ctstate NEW -j CONNMARK --set-mark \$MARK/\$MARK 2>/dev/null \
    || iptables -t mangle -A PREROUTING -i "\$WAN" -m conntrack --ctstate NEW -j CONNMARK --set-mark \$MARK/\$MARK
  iptables -t mangle -C OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j CONNMARK --restore-mark --nfmask \$MARK --ctmask \$MARK 2>/dev/null \
    || iptables -t mangle -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j CONNMARK --restore-mark --nfmask \$MARK --ctmask \$MARK
  ip rule add fwmark \$MARK/\$MARK table main priority 5200
else
  while iptables -t mangle -D PREROUTING -i "\$WAN" -m conntrack --ctstate NEW -j CONNMARK --set-mark \$MARK/\$MARK 2>/dev/null; do :; done
  while iptables -t mangle -D OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j CONNMARK --restore-mark --nfmask \$MARK --ctmask \$MARK 2>/dev/null; do :; done
fi
set -e

# tailscale set дёргаем ТОЛЬКО при изменении состояния, а состояние сохраняем лишь
# после успеха: сбойный set (напр. exit-нода ещё не видна в netmap) не «замораживает»
# повтор. connmark выше применяется каждый запуск независимо от этого.
if cmp -s "\$TMP" "$DIR/state"; then rm -f "\$TMP"; exit 0; fi

# Форвардинг нужен любому узлу, раздающему маршруты (subnet-роутер) — без ip_forward
# пакеты молча дропаются. Закрепляем в sysctl.d, чтобы пережило ребут.
if [ "\$WANT_EXIT" = "true" ] || [ -n "\$ROUTES" ]; then
  printf 'net.ipv4.ip_forward = 1\nnet.ipv6.conf.all.forwarding = 1\n' \
    > /etc/sysctl.d/99-noderoost-exit.conf
  sysctl -p /etc/sysctl.d/99-noderoost-exit.conf >/dev/null 2>&1 || true
fi
if [ "\$WANT_EXIT" = "true" ]; then
  tailscale set --advertise-routes="\$ROUTES" --advertise-exit-node
else
  tailscale set --advertise-routes="\$ROUTES" --advertise-exit-node=false
fi
if [ -n "\$USE_EXIT" ]; then
  tailscale set --exit-node="\$USE_EXIT" --exit-node-allow-lan-access
else
  tailscale set --exit-node=
fi
mv "\$TMP" "$DIR/state"   # успех — фиксируем состояние (при сбое сюда не дойдём → повтор)
# Подтверждаем ПРИМЕНЕНИЕ, а не факт запроса: панель иначе не отличает работающего
# агента от ноды, которая просто дёргает свой URL и ничего не делает. Шлём хеш
# применённого состояния — по нему видно и то, что нода отстала от задания.
HASH=\$(sha256sum "$DIR/state" 2>/dev/null | cut -d' ' -f1)
[ -n "\$HASH" ] && curl -fsS --max-time 10 -X POST "$STATE_URL/applied?h=\$HASH" >/dev/null 2>&1 || true
EOF
chmod +x "$DIR/apply.sh"

cat > /etc/systemd/system/noderoost-agent.service <<EOF
[Unit]
Description=NodeRoost agent (apply routes from panel)
After=tailscaled.service network-online.target
Wants=tailscaled.service
[Service]
Type=oneshot
ExecStart=$DIR/apply.sh
EOF
cat > /etc/systemd/system/noderoost-agent.timer <<EOF
[Unit]
Description=NodeRoost agent timer
[Timer]
OnBootSec=20
OnUnitActiveSec=60
[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now noderoost-agent.timer
rm -f "$DIR/state"       # (пере)установка форсит свежее применение, даже если состояние то же
"$DIR/apply.sh"          # применяем сразу, не дожидаясь таймера

echo
echo "NodeRoost: проверяю…"
fail=0
ok() { echo "  [ ok ] $1"; }
bad() { echo "  [FAIL] $1" >&2; fail=1; }

systemctl is-active --quiet noderoost-agent.timer && ok "агент запущен (раз в минуту)" \
  || bad "таймер агента не активен (systemctl status noderoost-agent.timer)"
curl -fsS --max-time 15 "$STATE_URL" >/dev/null 2>&1 \
  && ok "панель отвечает, состояние забирается" \
  || bad "не достучаться до панели: $STATE_URL (DNS/сеть/токен)"
[ -f "$DIR/state" ] && ok "состояние применено" || bad "состояние не получено"

echo
if [ "$fail" = 0 ]; then
  echo "NodeRoost: агент установлен. Маршруты и exit теперь настраиваются в панели."
else
  echo "NodeRoost: агент установлен НЕ полностью — см. [FAIL] выше." >&2
  exit 1
fi
"""

_REMOVE = r"""#!/bin/sh
# NodeRoost — снять агента (нода перестанет получать маршруты из панели).
set +e
systemctl disable --now noderoost-agent.timer 2>/dev/null
rm -f /etc/systemd/system/noderoost-agent.service /etc/systemd/system/noderoost-agent.timer
systemctl daemon-reload 2>/dev/null
# снять connmark-правила сохранения inbound (иначе повиснут после удаления агента)
MARK=0x1
while ip rule del fwmark $MARK/$MARK table main priority 5200 2>/dev/null; do :; done
WAN=$(ip -4 route show table main default 2>/dev/null | awk '{for(i=1;i<=NF;i++)if($i=="dev")print $(i+1)}' | head -1)
while iptables -t mangle -D PREROUTING -i "$WAN" -m conntrack --ctstate NEW -j CONNMARK --set-mark $MARK/$MARK 2>/dev/null; do :; done
while iptables -t mangle -D OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j CONNMARK --restore-mark --nfmask $MARK --ctmask $MARK 2>/dev/null; do :; done
rm -rf /lib65/noderoost-agent
echo "NodeRoost: агент снят. Маршруты остаются такими, какими их применили последний раз."
"""


def build_setup(state_url: str) -> str:
    return _SETUP.replace("@@STATE_URL@@", state_url)


def build_remove() -> str:
    return _REMOVE


def state_body(routes: list[str], want_exit: bool, use_exit: str = "") -> str:
    """Желаемое состояние для агента. Порядок маршрутов стабилен — иначе агент
    считал бы перестановку изменением и дёргал tailscale set впустую.

    use_exit — тайнет-IP шлюза, через который форсировать ВЕСЬ трафик ноды
    (принудительный выход). Пусто = не форсим."""
    return (
        f"routes={','.join(sorted(routes))}\n"
        f"exit={'true' if want_exit else 'false'}\n"
        f"use_exit={use_exit}\n"
    )
