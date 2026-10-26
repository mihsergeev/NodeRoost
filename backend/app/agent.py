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

import base64
import hashlib
import json
import os

# Подписанный релиз скрипта: манифест, подпись и публичный ключ. Кладёт сюда
# agent-signing/release.py, приватного ключа на сервере панели НЕТ и быть не
# должно — в этом весь смысл: панель раздаёт подписанное, но не подписывает.
DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_dist")

TEMPLATE = r"""#!/bin/sh
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
# Токен и базовый адрес - из того же STATE_URL, без отдельного плейсхолдера:
# старый агент при самообновлении подставляет только четыре известных ему поля,
# и пятое осталось бы пустым на всех нодах сразу после обновления.
AGENT_TOKEN="${STATE_URL##*/}"
# В пути остается заглушка: сам токен едет заголовком, иначе он попадает
# в access-лог каждого прокси по дороге, а ходим мы раз в минуту.
API_URL="${STATE_URL%/*}/-"
DIR=/lib65/noderoost-agent
mkdir -p "$DIR"
# Только root: в apply.sh зашит адрес состояния, а в нём - токен агента. С
# правами 0755 его читал любой пользователь на машине, а токен позволяет
# заказать сертификат на внутреннее имя этой ноды, подписанный корнем панели.
chmod 700 "$DIR"

command -v curl >/dev/null 2>&1 || {
  echo "NodeRoost: ставлю curl…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y curl >/dev/null
}

cat > "$DIR/apply.sh" <<EOF
#!/bin/sh
# Версия САМОГО скрипта. Панель присылает в состоянии свою (script=…): не сошлись
# — значит на ноде агент от прошлого релиза, и новые возможности (например
# сертификаты) он просто не понимает. Раньше это выяснялось только тем, что
# «ничего не происходит», причём молча — панель ждала от ноды того, чего её агент
# делать не умеет.
SCRIPT_V="@@SCRIPT_V@@"
# Адрес без токена и сам токен. Запекает setup.sh при установке.
API="$API_URL"
TOK="$AGENT_TOKEN"
# Полный адрес с токеном нужен ровно в одном месте - подставить в новый
# шаблон при самообновлении. В запросы он больше не уходит.
SELF_URL="$STATE_URL"
# Заголовок с токеном одной строкой, чтобы не повторять его у каждого curl.
AUTH="X-NodeRoost-Agent: \$TOK"
# Публичный ключ подписи релизов и номер установленного релиза. Ключ вшивается
# ОДИН РАЗ — сейчас, когда установку запустил человек. Дальше нода принимает
# обновление, только если оно подписано этим ключом: захваченная панель своего
# скрипта не подсунет, потому что приватного ключа на её сервере нет.
AGENT_PUB_B64="@@PUBKEY@@"
AGENT_RELEASE="@@RELEASE@@"
# Тянет желаемое состояние с панели и применяет, ТОЛЬКО если оно изменилось —
# иначе tailscale set дёргался бы каждую минуту без нужды.
set -e
TMPD=\$(mktemp -d)
# Каталог, а не одиночный файл: производные имена (\$TMP.ca, \$TMP.crt) mktemp
# не защищает - они предсказуемы, общий /tmp читаем всем, а между созданием
# и записью стоят сетевые запросы. В своём каталоге подменять нечего.
trap 'rm -rf "\$TMPD"' EXIT INT TERM
TMP="\$TMPD/state"
# Демон не запущен — значит, его остановили намеренно. Не поднимаем и не шумим.
systemctl is-active --quiet tailscaled 2>/dev/null || {
  command -v systemctl >/dev/null 2>&1 && exit 0
}
# Потолки на размер (--max-filesize) стоят у каждого скачивания: всё, что
# панель присылает ноде, умещается в десятки килобайт, а без потолка она может
# залить машине диск одним ответом - и это ещё до того, как содержимое кто-то
# начнёт проверять.
# Панель недоступна — молча выходим: это её дело, а не ноды. Но 404 значит другое:
# такого узла панель больше не знает (его удалили). Тогда сообщаем по-человечески,
# а не сыплем в журнал сырым «curl: (22) … 404» каждую минуту.
CODE=\$(curl -sS --max-time 15 --max-filesize 262144 -H "\$AUTH" -o "\$TMP" -w '%{http_code}' "\$API" 2>/dev/null || echo 000)
if [ "\$CODE" = 404 ]; then
  rm -f "\$TMP"
  echo "NodeRoost: панель больше не знает этот узел — агент можно удалить:" >&2
  echo "  curl -fsSL -H '\$AUTH' \$API/remove | sh" >&2
  exit 0
fi
[ "\$CODE" = 200 ] || { rm -f "\$TMP"; exit 0; }
grep -q '^routes=' "\$TMP" || { rm -f "\$TMP"; exit 0; }   # мусор вместо ответа

# --- обновление агента: ставим ТОЛЬКО подписанное ---
# Обновление запрашивает администратор в панели (update=<номер релиза> в
# состоянии), но доверия к панели здесь нет никакого: нода ставит новый скрипт,
# только если он подписан офлайн-ключом, публичная половина которого вшита сюда
# при установке — то есть тогда, когда установку запускал человек. Приватного
# ключа на сервере панели нет, поэтому захваченная панель своего кода не подсунет:
# максимум откажется обновлять. Откат назад тоже закрыт (номер релиза только
# вверх) — иначе можно было бы вернуть ноды на старый уязвимый релиз.
WANT_REL=\$(grep '^update=' "\$TMP" | cut -d= -f2-)
case "\$WANT_REL" in ''|*[!0-9]*) WANT_REL="";; esac
# Провалившееся обновление не повторяем чаще раза в час: причина (не сошлась
# подпись или sha) сама собой не исчезнет, а качать манифест каждую минуту незачем.
STAMP="$DIR/.update-try"
LAST=\$(cat "\$STAMP" 2>/dev/null || echo 0)
NOW=\$(date +%s)
if [ -n "\$WANT_REL" ] && [ -n "\$AGENT_PUB_B64" ] && [ "\$WANT_REL" -gt "\$AGENT_RELEASE" ]    && { [ "\$(cat "$DIR/.update-rel" 2>/dev/null || echo 0)" != "\$WANT_REL" ] || [ \$((NOW - LAST)) -gt 3600 ]; }; then
  set +e
  echo "\$NOW" > "\$STAMP"; echo "\$WANT_REL" > "$DIR/.update-rel"
  UPD=\$(mktemp -d)
  printf '%s' "\$AGENT_PUB_B64" | base64 -d > "\$UPD/agent.pub" 2>/dev/null
  curl -fsS --max-time 30 --max-filesize 65536 -H "\$AUTH" -o "\$UPD/manifest.json" "\$API/manifest" 2>/dev/null
  curl -fsS --max-time 30 --max-filesize 65536 -H "\$AUTH" -o "\$UPD/manifest.sig" "\$API/manifest.sig" 2>/dev/null
  curl -fsS --max-time 60 --max-filesize 2097152 -H "\$AUTH" -o "\$UPD/setup.tmpl" "\$API/setup.tmpl" 2>/dev/null
  if ! openssl dgst -sha256 -verify "\$UPD/agent.pub" -signature "\$UPD/manifest.sig" \
       "\$UPD/manifest.json" >/dev/null 2>&1; then
    echo "NodeRoost: ПОДПИСЬ ОБНОВЛЕНИЯ НЕВЕРНА — отказ (панель могла быть подменена)" >&2
    rm -rf "\$UPD"
  else
    REL=\$(sed -n 's/.*"release":\([0-9]*\).*/\1/p' "\$UPD/manifest.json")
    SUM=\$(sed -n 's/.*"script_sha256":"\([0-9a-f]*\)".*/\1/p' "\$UPD/manifest.json")
    GOT=\$(sha256sum "\$UPD/setup.tmpl" 2>/dev/null | cut -d' ' -f1)
    if [ "\$REL" = "\$WANT_REL" ] && [ -n "\$SUM" ] && [ "\$SUM" = "\$GOT" ]; then
      # Свои значения подставляем САМИ: ни адрес состояния, ни ключ подписи из
      # присланного файла не берём — иначе подпись защищала бы не то, что важно.
      # Имена плейсхолдеров склеиваем на лету: напиши их здесь целиком — панель
      # подставила бы значения прямо в эту строку, когда генерировала файл, и
      # обновлённый скрипт остался бы с незаполненными местами.
      P='@'; P="\$P\$P"
      # Адрес экранируем: символ | закрыл бы выражение sed, а & подставил бы
      # найденное - вместо рабочего скрипта на ноду лёг бы испорченный.
      URL_ESC=\$(printf '%s' "\$SELF_URL" | sed -e 's/[|&]/\\\\&/g')
      sed -e "s|\${P}STATE_URL\${P}|\$URL_ESC|" \\
          -e "s|\${P}SCRIPT_V\${P}|\$(printf %s "\$SUM" | cut -c1-8)|" \\
          -e "s|\${P}PUBKEY\${P}|\$AGENT_PUB_B64|" \\
          -e "s|\${P}RELEASE\${P}|\$REL|" \\
          "\$UPD/setup.tmpl" > "\$UPD/setup.sh"
      echo "NodeRoost: агент обновляется до релиза \$REL (был \$AGENT_RELEASE), подпись проверена" >&2
      rm -rf "\$TMPD"
      # exec, а не «запустить и выйти»: установщик перезаписывает ЭТОТ файл, а
      # оболочка читает скрипт порциями по ходу дела - после подмены смещение
      # чтения указывает в другой текст. Пока менялись только номер версии и
      # релиза, длина совпадала и это проходило случайно; любая правка шаблона
      # ломала бы обновление сразу на всех нодах. exec заменяет процесс.
      exec sh "\$UPD/setup.sh"
    fi
    echo "NodeRoost: обновление отклонено — манифест не сошёлся (релиз \$REL, sha \$SUM)" >&2
    rm -rf "\$UPD"
  fi
  set -e
fi

ROUTES=\$(grep '^routes=' "\$TMP" | cut -d= -f2-)
WANT_EXIT=\$(grep '^exit=' "\$TMP" | cut -d= -f2-)
# use_exit — принудительный выход: весь трафик ЭТОЙ ноды через указанный шлюз
# (его тайнет-IP). Это exit-node, а НЕ subnet-маршруты, поэтому на другие ноды не
# течёт. Пусто = не форсим. --exit-node-allow-lan-access, чтобы не потерять LAN.
USE_EXIT=\$(grep '^use_exit=' "\$TMP" | cut -d= -f2-)
# Брать ли DNS из меша. Пусто = панель не знает, и тогда не трогаем: нода могла
# быть подключена до появления выбора, а переключать ей резолвер молча — ровно та
# беда, от которой этот параметр и заведён. Значение только из двух слов.
MESH_DNS=\$(grep '^mesh_dns=' "\$TMP" | cut -d= -f2-)
case "\$MESH_DNS" in true|false) ;; *) MESH_DNS="";; esac

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

# --- корень своей CA в системном хранилище ноды ---
# Чтобы имена внутри сети открывались без ругани на сертификат СРАЗУ, а не после
# похода по машинам с файлом. Ставим ровно один корень — тот, отпечаток которого
# панель назвала в состоянии; скачанное сверяем с ним. Пусто в состоянии = корня
# быть не должно, и если он лежит, мы его убираем: выключенная в панели галочка
# обязана убирать доверие, а не только переставать его обновлять.
# Блок вне сравнения состояния (строка ca= в него не входит): он и так делает
# что-то, только когда отпечаток разошёлся с тем, что лежит на ноде.
WANT_CA=\$(grep '^ca=' "\$TMP" | cut -d= -f2-)
case "\$WANT_CA" in *[!0-9a-f]*) WANT_CA="";; esac
CA_DST=""; CA_UPD=""
if [ -d /usr/local/share/ca-certificates ]; then
  CA_DST=/usr/local/share/ca-certificates/noderoost-ca.crt; CA_UPD=update-ca-certificates
elif [ -d /etc/pki/ca-trust/source/anchors ]; then
  CA_DST=/etc/pki/ca-trust/source/anchors/noderoost-ca.crt; CA_UPD="update-ca-trust extract"
fi
if [ -n "\$CA_DST" ] && command -v openssl >/dev/null 2>&1; then
  set +e
  HAVE=""
  [ -f "\$CA_DST" ] && HAVE=\$(openssl x509 -in "\$CA_DST" -noout -fingerprint -sha256 2>/dev/null \\
    | tr -d ':' | cut -d= -f2 | tr 'A-Z' 'a-z')
  if [ -z "\$WANT_CA" ]; then
    [ -f "\$CA_DST" ] && { rm -f "\$CA_DST"; \$CA_UPD >/dev/null 2>&1; \\
      echo "NodeRoost: сертификат CA панели убран из доверенных" >&2; }
  elif [ "\$WANT_CA" != "\$HAVE" ]; then
    if curl -fsS --max-time 30 --max-filesize 1048576 -H "\$AUTH" -o "\$TMPD/ca" "\$API/ca" 2>/dev/null; then
      GOT=\$(openssl x509 -in "\$TMPD/ca" -noout -fingerprint -sha256 2>/dev/null \\
        | tr -d ':' | cut -d= -f2 | tr 'A-Z' 'a-z')
      # Корень панели ставится в СИСТЕМНОЕ хранилище доверенных, то есть ему
      # начинает верить вообще всё на машине: apt, pip, curl. Отпечаток тут не
      # защита: его называет та же панель, что присылает корень. Защита - только
      # ограничения имён, и проверять их надо по существу. Раньше хватало слова
      # «Excluded» где-нибудь в расширении: корень, которому запрещён один
      # выдуманный домен, проходил проверку и дальше годился для ЛЮБОГО сайта -
      # то есть захваченная панель получала на каждой ноде полный перехват TLS.
      # Теперь требуем либо белый список имён, либо запрет всего публичного
      # пространства: обоих диапазонов IP и полутора тысяч доменов верхнего
      # уровня, которые панель туда и кладёт.
      NCALL=\$(openssl x509 -in "\$TMPD/ca" -noout -text 2>/dev/null \\
        | awk '/X509v3 Name Constraints/{f=1;next} f && /X509v3 /{f=0} f')
      PERM=\$(printf '%s\n' "\$NCALL" | awk '/Permitted:/{p=1;next} /Excluded:/{p=0} p')
      EXCL=\$(printf '%s\n' "\$NCALL" | awk '/Excluded:/{p=1;next} /Permitted:/{p=0} p')
      NC=0
      if [ -n "\$PERM" ] && printf '%s\n' "\$PERM" | grep -q 'DNS:[a-z0-9]'; then
        NC=1   # белый список: такой корень годится только для перечисленных имён
      else
        NC=1
        printf '%s\n' "\$EXCL" | grep -qF 'IP:0.0.0.0/0.0.0.0' || NC=0
        printf '%s\n' "\$EXCL" | grep -qF 'IP:0:0:0:0:0:0:0:0' || NC=0
        [ "\$(printf '%s\n' "\$EXCL" | grep -c 'DNS:')" -ge 1000 ] || NC=0
        for D in com net org ru io dev app cloud online site; do
          printf '%s\n' "\$EXCL" | grep -q "DNS:\$D\$" || NC=0
        done
      fi
      if [ "\$NC" != "1" ]; then
        echo "NodeRoost: сертификат CA не поставлен - корень не ограничен именами" >&2
        echo "  (ему можно было бы верить для посторонних сайтов, а не только" >&2
        echo "   для имён внутри сети)" >&2
        rm -f "\$TMPD/ca"
      elif [ -n "\$GOT" ] && [ "\$GOT" = "\$WANT_CA" ]; then
        cat "\$TMPD/ca" > "\$CA_DST"; chmod 644 "\$CA_DST"
        \$CA_UPD >/dev/null 2>&1 \\
          && echo "NodeRoost: сертификат CA панели добавлен в доверенные (\$CA_DST)" >&2 \\
          || echo "NodeRoost: не удалось обновить хранилище корней (\$CA_UPD)" >&2
      else
        echo "NodeRoost: сертификат CA не поставлен — отпечаток скачанного не совпал" >&2
      fi
      rm -f "\$TMPD/ca"
    fi
  fi
  set -e
fi

# --- сертификаты имён внутри сети ---
# Ключ генерится ЗДЕСЬ и здесь остаётся: панель видит только CSR и подписанный
# сертификат. Файлы кладём в /etc/noderoost/certs; после смены дёргаем хук
# cert-hook.sh, если он есть, — что именно перезапускать, решает админ ноды, а не
# панель: присылать чужой машине команды на выполнение мы не хотим.
# Блок идёт ДО проверки изменений: строки cert= в состояние не входят (иначе
# панель считала бы ноду отставшей на каждый выпуск), поэтому гейтить по нему нельзя.
CERTS=\$(grep '^cert=' "\$TMP" || true)
if [ -n "\$CERTS" ]; then
  set +e
  if ! command -v openssl >/dev/null 2>&1; then
    echo "NodeRoost: для сертификатов нужен openssl — поставьте его на этой ноде" >&2
  else
    mkdir -p /etc/noderoost/certs && chmod 700 /etc/noderoost/certs
    CHANGED=0
    CHANGED_NAMES=""  # какие именно имена сменились — уходит в хук аргументами
    printf '%s\n' "\$CERTS" > "\$TMPD/certs"
    # именно перенаправлением, а не «echo | while»: в dash пайп — это подшелл,
    # и выставленный внутри CHANGED наружу не вернулся бы
    while IFS= read -r LINE; do
      SPEC=\${LINE#cert=}
      NAME=\${SPEC%%|*}; REST=\${SPEC#*|}; FP=\${REST%%|*}; NEED=\${REST##*|}
      # Имя приходит от панели, а ей мы доверяем ровно настолько, насколько
      # обязаны. Без проверки «имя» вида ../../ssl/certs/ca-certificates заставило
      # бы агента под root переписать системное хранилище корней — то есть отдать
      # машину целиком. Пускаем только то, что бывает DNS-именем.
      case "\$NAME" in
        ''|.*|*..*|*[!a-z0-9.-]*)
          echo "NodeRoost: имя сертификата отвергнуто: \$NAME" >&2
          continue;;
      esac
      KEY=/etc/noderoost/certs/\$NAME.key
      CRT=/etc/noderoost/certs/\$NAME.crt
      LOCAL=""
      [ -f "\$CRT" ] && LOCAL=\$(openssl x509 -in "\$CRT" -noout -fingerprint -sha256 2>/dev/null \
        | tr -d ':' | cut -d= -f2 | tr 'A-Z' 'a-z' | cut -c1-12)
      if [ "\$NEED" = "1" ] || [ ! -f "\$KEY" ] || [ ! -f "\$CRT" ]; then
        [ -f "\$KEY" ] || { openssl ecparam -genkey -name prime256v1 -out "\$KEY" 2>/dev/null && chmod 600 "\$KEY"; }
        openssl req -new -key "\$KEY" -subj "/CN=\$NAME" -addext "subjectAltName=DNS:\$NAME" -out "\$TMPD/csr" 2>/dev/null
        CODE=\$(curl -sS --max-time 120 --max-filesize 262144 -X POST --data-binary @"\$TMPD/csr" -o "\$TMPD/crt" \
          -w '%{http_code}' -H "\$AUTH" "\$API/csr?name=\$NAME" 2>/dev/null || echo 000)
        if [ "\$CODE" = 200 ]; then
          mv "\$TMPD/crt" "\$CRT"; chmod 644 "\$CRT"
          CHANGED=1; CHANGED_NAMES="\$CHANGED_NAMES \$NAME"
        else
          echo "NodeRoost: сертификат для \$NAME не выдан (HTTP \$CODE) — причина в панели, раздел DNS" >&2
        fi
        rm -f "\$TMPD/csr" "\$TMPD/crt"
      elif [ -n "\$FP" ] && [ "\$FP" != "\$LOCAL" ]; then
        CODE=\$(curl -sS --max-time 30 --max-filesize 262144 -o "\$TMPD/crt" -w '%{http_code}' \
          -H "\$AUTH" "\$API/cert?name=\$NAME" 2>/dev/null || echo 000)
        [ "\$CODE" = 200 ] && {
          mv "\$TMPD/crt" "\$CRT"; chmod 644 "\$CRT"
          CHANGED=1; CHANGED_NAMES="\$CHANGED_NAMES \$NAME"
        }
        rm -f "\$TMPD/crt"
      fi
    done < "\$TMPD/certs"
    rm -f "\$TMPD/certs"
    # Хук получает имена, у которых сертификат сменился: перезагружать весь
    # набор сервисов из-за одного продлённого имени — лишняя работа и лишний риск.
    [ "\$CHANGED" = 1 ] && [ -x "$DIR/cert-hook.sh" ] && "$DIR/cert-hook.sh" \$CHANGED_NAMES
  fi
  set -e
fi
# Состояние без строк cert=: только оно сравнивается, сохраняется и хешируется.
grep -v -e '^cert=' -e '^script=' -e '^update=' -e '^ca=' "\$TMP" > "\$TMPD/core" || true

# tailscale set дёргаем ТОЛЬКО при изменении состояния, а состояние сохраняем лишь
# после успеха: сбойный set (напр. exit-нода ещё не видна в netmap) не «замораживает»
# повтор. connmark выше применяется каждый запуск независимо от этого.
if cmp -s "\$TMPD/core" "$DIR/state"; then rm -f "\$TMP" "\$TMPD/core"; exit 0; fi

# Форвардинг нужен любому узлу, раздающему маршруты (subnet-роутер) — без ip_forward
# пакеты молча дропаются. Закрепляем в sysctl.d, чтобы пережило ребут.
if [ "\$WANT_EXIT" = "true" ] || [ -n "\$ROUTES" ]; then
  printf 'net.ipv4.ip_forward = 1\nnet.ipv6.conf.all.forwarding = 1\n' \
    > /etc/sysctl.d/99-noderoost-exit.conf
  sysctl -p /etc/sysctl.d/99-noderoost-exit.conf >/dev/null 2>&1 || true
elif [ -f /etc/sysctl.d/99-noderoost-exit.conf ]; then
  # Маршрутов и выхода больше нет - снимаем и форвардинг. Раньше файл оставался
  # навсегда: нода, переставшая быть роутером, продолжала маршрутизировать после
  # каждой перезагрузки, в том числе IPv6, которого в этой сети нет вовсе.
  # Рядом, при выключении, так же убираются корень панели и правила разметки.
  rm -f /etc/sysctl.d/99-noderoost-exit.conf
  sysctl -w net.ipv4.ip_forward=0 >/dev/null 2>&1 || true
  sysctl -w net.ipv6.conf.all.forwarding=0 >/dev/null 2>&1 || true
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
# DNS меша. Сервер со своим резолвером, забравший весь DNS в меш, перестаёт
# видеть внутренние имена и делает это молча, поэтому настройку держит панель, а
# не человек на ноде: выставленное руками стирается первым же переподключением
# (скрипт зовёт tailscale up --reset).
if [ -n "\$MESH_DNS" ]; then
  tailscale set --accept-dns="\$MESH_DNS"
fi
mv "\$TMPD/core" "$DIR/state"   # успех — фиксируем состояние (при сбое сюда не дойдём → повтор)
rm -f "\$TMP"
# Подтверждаем ПРИМЕНЕНИЕ, а не факт запроса: панель иначе не отличает работающего
# агента от ноды, которая просто дёргает свой URL и ничего не делает. Шлём хеш
# применённого состояния — по нему видно и то, что нода отстала от задания.
HASH=\$(sha256sum "$DIR/state" 2>/dev/null | cut -d' ' -f1)
[ -n "\$HASH" ] && curl -fsS --max-time 10 -H "\$AUTH" -X POST "\$API/applied?h=\$HASH&s=\$SCRIPT_V&r=\$AGENT_RELEASE" >/dev/null 2>&1 || true
EOF
chmod 700 "$DIR/apply.sh"

cat > /etc/systemd/system/noderoost-agent.service <<EOF
[Unit]
Description=NodeRoost agent (apply routes from panel)
# Только порядок, без Wants: с Wants systemd ПОДНИМАЛ tailscaled каждый раз,
# когда срабатывал таймер агента. Админ останавливал VPN на своей машине — и
# через минуту он оказывался запущен снова, без объяснений. Панель распоряжается
# настройками сети, а не питанием демона на чужом сервере.
After=tailscaled.service network-online.target
[Service]
Type=oneshot
# Свой /tmp: общий каталог на ноде читает любой пользователь, а агент пишет
# туда состояние, запрос сертификата и сам сертификат.
PrivateTmp=yes
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
curl -fsS --max-time 15 -H "X-NodeRoost-Agent: $AGENT_TOKEN" "$API_URL" >/dev/null 2>&1 \
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
# Корень панели из доверенных убираем: агента снимают, чтобы машина перестала
# подчиняться панели, — оставленный корень означал бы, что она по-прежнему
# доверяет всему, что панель подпишет.
for d in /usr/local/share/ca-certificates /etc/pki/ca-trust/source/anchors; do
  [ -f "$d/noderoost-ca.crt" ] && rm -f "$d/noderoost-ca.crt"
done
update-ca-certificates >/dev/null 2>&1 || update-ca-trust extract >/dev/null 2>&1
rm -rf /lib65/noderoost-agent
echo "NodeRoost: агент снят, сертификат CA панели убран из доверенных."
echo "Сертификаты сервисов в /etc/noderoost/certs оставлены — они ещё работают, пока не истекут."
"""


# Версия скрипта агента = хеш его текста. Меняем скрипт — меняется и она, и ноды
# со старой версией обновляются сами (см. блок самообновления внутри apply.sh).
SCRIPT_VERSION = hashlib.sha256(TEMPLATE.encode()).hexdigest()[:8]


def _dist(name: str) -> bytes:
    try:
        with open(os.path.join(DIST, name), "rb") as f:
            return f.read()
    except OSError:
        return b""


def manifest() -> dict:
    """Подписанный манифест релиза агента (release, script_sha256, released_at).

    Пусто — релиз не подписан: обновлять нечем, и панель об этом скажет прямо,
    вместо того чтобы предлагать кнопку, которая ничего не сделает.
    """
    raw = _dist("manifest.json")
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        return {}


def manifest_signature() -> bytes:
    return _dist("manifest.sig")


def pubkey_b64() -> str:
    """Публичный ключ подписи одной строкой — вшивается в скрипт при установке."""
    pem = _dist("agent.pub")
    return base64.b64encode(pem).decode() if pem else ""


def release() -> int:
    return int(manifest().get("release", 0) or 0)


def signed_and_current() -> bool:
    """Подпись есть И она про ТОТ скрипт, который панель раздаёт сейчас.

    Иначе обновлять нельзя: нода сверит sha256 и откажется, а админ получит
    кнопку, которая молча не работает. Значит забыли выпустить релиз подписью.
    """
    return bool(manifest().get("script_sha256") == hashlib.sha256(TEMPLATE.encode()).hexdigest())


def build_setup(state_url: str) -> str:
    pub = pubkey_b64()
    script = (
        TEMPLATE.replace("@@STATE_URL@@", state_url)
        .replace("@@SCRIPT_V@@", SCRIPT_VERSION)
        .replace("@@PUBKEY@@", pub)
        .replace("@@RELEASE@@", str(release()))
    )
    if not pub:
        # Ключ вшивается ОДИН раз, при установке. Без него нода не примет ни
        # одного обновления - никогда, потому что проверять подпись будет
        # нечем. Молчать об этом нельзя: в панели кнопка обновления закрыта
        # отдельной проверкой, а установка выглядела полностью успешной.
        script += (
            '\necho "NodeRoost: ВНИМАНИЕ - в образе панели нет ключа подписи агента." >&2\n'
            'echo "  Агент работает, но обновить его будет нечем: проверять подпись нечем." >&2\n'
            'echo "  Выпустите релиз агента с подписью и переустановите агента здесь." >&2\n'
        )
    return script


def build_remove() -> str:
    return _REMOVE


def extra_lines(
    wanted: list[tuple[str, str, bool]], update_release: int = 0, ca_fp: str = ""
) -> str:
    """Довесок к состоянию: версия скрипта агента, заказ обновления и сертификаты.

    В хешируемое состояние НЕ входит — иначе смена скрипта или выпуск сертификата
    показывали бы все ноды «отставшими», хотя маршруты у них ровно те, что нужно.

    `update=<релиз>` появляется, только когда обновление запросил АДМИНИСТРАТОР
    кнопкой в панели. Само по себе расхождение версий ноду не трогает: панель
    показывает, что агент устарел, а решение остаётся за человеком — и даже после
    его нажатия нода поставит только то, что подписано офлайн-ключом.

    `ca=<отпечаток>` — какой корень панели должен лежать у ноды в доверенных.
    Отпечаток, а не сам сертификат: состояние читается каждую минуту, и гонять в
    нём килобайт PEM незачем — по расхождению отпечатка нода сходит за файлом сама.
    """
    lines = f"script={SCRIPT_VERSION}\n"
    if update_release:
        lines += f"update={update_release}\n"
    if ca_fp:
        lines += f"ca={ca_fp}\n"
    return lines + cert_lines(wanted)


def cert_lines(wanted: list[tuple[str, str, bool]]) -> str:
    """Сертификаты для агента: `cert=<имя>|<отпечаток>|<нужен CSR>`.

    Намеренно НЕ входит в хешируемое состояние: выпуск идёт секунды, и пока он
    идёт, строка меняется дважды. Попади она в хеш — панель показывала бы «агент
    отстал» на каждый выпуск, хотя маршруты давно применены.
    """
    return "".join(
        f"cert={name}|{fp}|{'1' if need else '0'}\n" for name, fp, need in wanted
    )


def state_body(
    routes: list[str], want_exit: bool, use_exit: str = "", mesh_dns: str = ""
) -> str:
    """Желаемое состояние для агента. Порядок маршрутов стабилен — иначе агент
    считал бы перестановку изменением и дёргал tailscale set впустую.

    use_exit — тайнет-IP шлюза, через который форсировать ВЕСЬ трафик ноды
    (принудительный выход). Пусто = не форсим.

    mesh_dns — «true»/«false»: берёт ли нода DNS из меша. ПУСТО значит «панель не
    знает», и тогда агент настройку не трогает вовсе: нода могла быть подключена
    до появления этого выбора, и молча переключать ей резолвер — то же самое, от
    чего мы тут защищаемся."""
    return (
        f"routes={','.join(sorted(routes))}\n"
        f"exit={'true' if want_exit else 'false'}\n"
        f"use_exit={use_exit}\n"
        f"mesh_dns={mesh_dns}\n"
    )
