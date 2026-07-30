#!/bin/sh
# NodeRoost: синк IP добавленных нод в хостовый фаервол (вне docker).
#
# Закрытый меш: control-сервер headscale (порт 443) доступен НЕ всему миру, а
# только админ-IP и IP нод, которые панель добавила при enroll. Панель ведёт файл
# <data>/node_allow_ips (по строке на IP/CIDR); этот скрипт СВОДИТ фаервол с ним:
#   • добавляет отсутствующие правила (метка noderoost-node);
#   • СНИМАЕТ правила noderoost-node, чьих IP больше нет в файле (авточистка).
# Трогает ТОЛЬКО правила со своей меткой noderoost-node — админские и любые
# другие правила остаются нетронутыми. Порт 80 держите открытым всему миру
# отдельно (Let's Encrypt http-01). Нет ни ufw, ни firewalld — тихо выходит.
#
# Установка (скрипты держим в /lib65 — он в бэкапе, /usr нет):
#   install -D -m755 ops/node-firewall-sync.sh /lib65/noderoost/node-firewall-sync.sh
#   echo '* * * * * root NODEROOST_NODE_IPS=/opt/noderoost/data/node_allow_ips /lib65/noderoost/node-firewall-sync.sh' \
#     > /etc/cron.d/noderoost-node-fw
#   chmod 644 /etc/cron.d/noderoost-node-fw
#
# Идемпотентен.

set -eu

# cron даёт урезанный PATH (/usr/bin:/bin) — ufw/firewall-cmd живут в sbin
PATH=/usr/local/sbin:/usr/sbin:/sbin:$PATH

# Корень установки вычисляем от самого скрипта — панель может стоять в любом
# каталоге (установщик кладёт в /opt/noderoost).
APP_ROOT="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd || echo /opt/noderoost)"
IPS_FILE="${NODEROOST_NODE_IPS:-$APP_ROOT/data/node_allow_ips}"
[ -r "$IPS_FILE" ] || exit 0

# регэксп IPv4/CIDR или IPv6/CIDR — для вырезания source-IP из строки правила
IPRE='([0-9]{1,3}\.){3}[0-9]{1,3}(/[0-9]{1,2})?|([0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}(/[0-9]{1,3})?'

# желаемый набор (валидированный) во временный файл
DESIRED=$(mktemp)
trap 'rm -f "$DESIRED"' EXIT INT TERM
while IFS= read -r ip; do
  ip=$(printf '%s' "$ip" | tr -d '[:space:]')
  [ -n "$ip" ] || continue
  # только IPv4/IPv6/CIDR — никаких шелл-сюрпризов из файла
  printf '%s' "$ip" | grep -Eq '^[0-9a-fA-F.:/]+$' || continue
  echo "$ip" >> "$DESIRED"
done < "$IPS_FILE"

add_ufw() {
  ip="$1"
  # docker-published 443 идёт через FORWARD (ufw-docker) — нужен именно route allow,
  # обычного «ufw allow» (INPUT) для трафика к контейнеру недостаточно.
  # существующие IP наших правил (точная сверка, чтобы 10.0.0.1 ≠ 10.0.0.10)
  existing=$(ufw status 2>/dev/null | grep 'noderoost-node' | grep -oE "$IPRE" | grep -vx '443')
  printf '%s\n' "$existing" | grep -qxF "$ip" && return 0
  ufw route allow proto tcp from "$ip" to any port 443 comment noderoost-node >/dev/null 2>&1 || true
}

prune_ufw() {
  # номера правил noderoost-node, чей source-IP не в DESIRED — удаляем по убыванию
  # (после каждого удаления номера сдвигаются, поэтому сначала большие)
  nums=$(ufw status numbered 2>/dev/null | grep 'noderoost-node' | while IFS= read -r line; do
    ip=$(printf '%s' "$line" | grep -oE "$IPRE" | grep -vx '443' | head -n1)
    [ -n "$ip" ] || continue
    grep -qxF "$ip" "$DESIRED" && continue
    printf '%s\n' "$line" | grep -oE '^\[[ 0-9]+\]' | tr -dc '0-9'
    echo
  done)
  for n in $(printf '%s\n' "$nums" | grep -E '^[0-9]+$' | sort -rn); do
    yes | ufw delete "$n" >/dev/null 2>&1 || true
  done
}

add_firewalld() {
  ip="$1"
  rule="rule family=ipv4 source address=$ip port port=443 protocol=tcp accept"
  firewall-cmd --query-rich-rule="$rule" >/dev/null 2>&1 && return 0
  firewall-cmd --permanent --add-rich-rule="$rule" >/dev/null 2>&1 || true
  CHANGED=1
}

prune_firewalld() {
  firewall-cmd --list-rich-rules 2>/dev/null | while IFS= read -r rule; do
    # только наши правила: source address + port 443 tcp accept
    case "$rule" in
      *"port port=\"443\""*"protocol=\"tcp\""*accept|*"port port=443"*"protocol=tcp"*accept) : ;;
      *) continue ;;
    esac
    ip=$(printf '%s' "$rule" | grep -oE "$IPRE" | grep -vx '443' | head -n1)
    [ -n "$ip" ] || continue
    grep -qxF "$ip" "$DESIRED" && continue
    firewall-cmd --permanent --remove-rich-rule="$rule" >/dev/null 2>&1 || true
    CHANGED=1
  done
}

CHANGED=0
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "^Status: active"; then
  while IFS= read -r ip; do add_ufw "$ip"; done < "$DESIRED"
  prune_ufw
elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  while IFS= read -r ip; do add_firewalld "$ip"; done < "$DESIRED"
  prune_firewalld
  [ "$CHANGED" = "1" ] && firewall-cmd --reload >/dev/null 2>&1 || true
fi
exit 0
