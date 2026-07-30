#!/usr/bin/env bash
# Установка NodeRoost на чистый сервер: Docker, панель, headscale и Caddy
# с автоматическим TLS. Скрипт идемпотентный — повторный запуск не ломает
# существующую установку (секреты и данные не перегенерируются).
#
#   curl -fsSL https://raw.githubusercontent.com/mihsergeev/NodeRoost/main/ops/install.sh \
#     | sudo bash -s -- --panel-domain panel.example.com --hs-domain hs.example.com
#
# Полный список параметров: --help.
# ВАЖНО про stdin: скрипт запускают как `curl … | sudo bash`, то есть bash читает
# САМ СЕБЯ со стандартного ввода. Любая команда внутри, которая тоже читает stdin
# (docker compose, apt-get, git), сожрёт остаток скрипта — и он оборвётся на
# середине без единого сообщения. Поэтому таким командам явно даём </dev/null.
set -euo pipefail

REPO_URL="https://github.com/mihsergeev/NodeRoost.git"
RAW_URL="https://raw.githubusercontent.com/mihsergeev/NodeRoost/main"
DIR="/opt/noderoost"
PANEL_DOMAIN=""
HS_DOMAIN=""
ALLOW_IPS="0.0.0.0/0 ::/0"
VERSION=""
BUILD=0
UFW=0
PUBLIC_IP=""

say()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[33m  ! \033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Install NodeRoost.

Required:
  --panel-domain DOMAIN  the name you will open the panel at
  --hs-domain DOMAIN     the control server's name; nodes remember it, changing it later is costly

Optional:
  --allow-ips "LIST"     space-separated addresses allowed into the panel
                         (default: everyone, "0.0.0.0/0 ::/0")
  --dir PATH             where to install (default /opt/noderoost)
  --version TAG          image version (default: latest release)
  --build                build images from source instead of pulling
  --ufw                  configure the firewall: allow only SSH, 80, 443, 3478/udp
  --public-ip ADDRESS    public address for the embedded DERP
                         (detected automatically by default)

Example:
  install.sh --panel-domain panel.example.com --hs-domain hs.example.com \
             --allow-ips "203.0.113.10 198.51.100.0/24" --ufw
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --panel-domain) PANEL_DOMAIN="${2:-}"; shift 2 ;;
        --hs-domain)    HS_DOMAIN="${2:-}"; shift 2 ;;
        --allow-ips)    ALLOW_IPS="${2:-}"; shift 2 ;;
        --dir)          DIR="${2:-}"; shift 2 ;;
        --version)      VERSION="${2:-}"; shift 2 ;;
        --public-ip)    PUBLIC_IP="${2:-}"; shift 2 ;;
        --build)        BUILD=1; shift ;;
        --ufw)          UFW=1; shift ;;
        -h|--help)      usage; exit 0 ;;
        *)              die "unknown option: $1 (try --help)" ;;
    esac
done

[ "$(id -u)" -eq 0 ] || die "root required: run with sudo"
[ -n "$PANEL_DOMAIN" ] || { usage; die "--panel-domain is missing"; }
[ -n "$HS_DOMAIN" ] || { usage; die "--hs-domain is missing"; }
[ "$PANEL_DOMAIN" != "$HS_DOMAIN" ] || die "the panel and the control server need different domains"

# ── 1. Docker ─────────────────────────────────────────────────────────────
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    ok "Docker already installed"
else
    say "Installing Docker"
    if command -v curl >/dev/null 2>&1; then :; else apt-get update -qq </dev/null && apt-get install -y -qq curl </dev/null; fi
    # Официальный установщик знает не про каждый свежий релиз дистрибутива —
    # тогда откатываемся на пакеты самого дистрибутива.
    if ! curl -fsSL https://get.docker.com | sh >/tmp/nr-docker.log 2>&1 </dev/null; then
        warn "get.docker.com failed, falling back to the distro packages"
        apt-get update -qq </dev/null
        apt-get install -y -qq docker.io docker-compose-v2 </dev/null \
            || die "could not install Docker, see /tmp/nr-docker.log"
    fi
    systemctl enable --now docker >/dev/null 2>&1 || true
    docker compose version >/dev/null 2>&1 || die "docker compose is missing after install"
    ok "Docker ready"
fi

# ── 2. Исходники ──────────────────────────────────────────────────────────
if [ -d "$DIR/.git" ]; then
    say "Updating $DIR"
    git -C "$DIR" fetch --depth 1 origin main -q </dev/null && git -C "$DIR" reset --hard origin/main -q </dev/null
else
    say "Fetching NodeRoost into $DIR"
    command -v git >/dev/null 2>&1 || { apt-get update -qq </dev/null; apt-get install -y -qq git </dev/null; }
    git clone --depth 1 "$REPO_URL" "$DIR" -q </dev/null
fi
cd "$DIR"

if [ -z "$VERSION" ]; then
    # Версия берётся из .env.example склонированного репозитория: там она и есть
    # источник правды для тегов образов. На GitHub Releases не ориентируемся —
    # выпуск помечается тегом, объект Release при этом не создаётся, и запрос
    # к releases/latest молча возвращал пусто, отправляя установку в долгую
    # сборку из исходников на однопроцессорной машине.
    VERSION="$(sed -n 's/^NODEROOST_VERSION=\(.*\)/\1/p' .env.example | head -1)"
    [ -n "$VERSION" ] || { VERSION="0.0.0-local"; BUILD=1; warn "no version found — building from source"; }
fi

# ── 3. Конфигурация ───────────────────────────────────────────────────────
mkdir -p data/postgres data/headscale/config data/headscale/lib data/headscale/run \
         data/caddy/data data/caddy/config data/tailscale-pkgs data/backups
chmod 700 data/backups

if [ -z "$PUBLIC_IP" ]; then
    PUBLIC_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
    [ -n "$PUBLIC_IP" ] || PUBLIC_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.*src \([0-9.]*\).*/\1/p')"
fi
[ -n "$PUBLIC_IP" ] || die "could not detect the public IP, pass --public-ip"

if [ -f .env ]; then
    ok ".env already exists — secrets left untouched"
    # Домены и список адресов обновляем: ради них скрипт и запускают повторно.
    sed -i "s|^NODEROOST_DOMAIN=.*|NODEROOST_DOMAIN=$PANEL_DOMAIN|;
            s|^NODEROOST_HS_DOMAIN=.*|NODEROOST_HS_DOMAIN=$HS_DOMAIN|;
            s|^NODEROOST_ALLOW_IPS=.*|NODEROOST_ALLOW_IPS=$ALLOW_IPS|;
            s|^NODEROOST_VERSION=.*|NODEROOST_VERSION=$VERSION|" .env
else
    say "Writing .env (random passwords)"
    ADMIN_PASSWORD="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-20)"
    cat > .env <<EOF
# Создан ops/install.sh $(date -u +%Y-%m-%dT%H:%M:%SZ). Пароли ниже — случайные.
NODEROOST_ADMIN_USER=admin
NODEROOST_ADMIN_PASSWORD=$ADMIN_PASSWORD
NODEROOST_JWT_SECRET=$(openssl rand -hex 32)
NODEROOST_DB_PASSWORD=$(openssl rand -hex 16)
NODEROOST_JWT_TTL_MINUTES=720
NODEROOST_ADMIN_PASSWORD_RESET=0

# Домены. HS_DOMAIN ноды запоминают у себя: сменить его после подключения нод
# = переводить каждую ноду вручную.
NODEROOST_DOMAIN=$PANEL_DOMAIN
NODEROOST_HS_DOMAIN=$HS_DOMAIN
NODEROOST_DOMAIN_ALIASES=
NODEROOST_HS_DOMAIN_ALIASES=

# С каких адресов пускать в ПАНЕЛЬ. Control-сервер это не ограничивает —
# ноды подключаются откуда угодно.
NODEROOST_ALLOW_IPS=$ALLOW_IPS

NODEROOST_VERSION=$VERSION
NODEROOST_PANEL_IP=$PUBLIC_IP
NODEROOST_PANEL_URL=https://$PANEL_DOMAIN
NODEROOST_HEADSCALE_API_KEY=
COMPOSE_FILE=compose.yml:compose.tls.yml
EOF
    chmod 600 .env
    ok ".env written"
fi

if [ ! -f data/headscale/config/config.yaml ]; then
    say "Preparing the headscale config"
    sed -e "s|^server_url:.*|server_url: https://$HS_DOMAIN|" \
        -e "s|^\( *ipv4: *\)[0-9.]*|\1$PUBLIC_IP|" \
        deploy/headscale/config.example.yaml > data/headscale/config/config.yaml
    ok "server_url = https://$HS_DOMAIN, DERP on $PUBLIC_IP"
fi

# ── 4. Запуск ─────────────────────────────────────────────────────────────
say "Starting the containers"
COMPOSE=(docker compose --env-file .env)
if [ "$BUILD" = 1 ]; then
    "${COMPOSE[@]}" build backend frontend </dev/null
fi
# Бэкенд без ключа headscale не стартует — поднимаем остальное, ключ создаём ниже.
"${COMPOSE[@]}" up -d db headscale frontend caddy </dev/null

say "Waiting for headscale"
for i in $(seq 1 60); do
    if "${COMPOSE[@]}" exec -T headscale headscale users list >/dev/null 2>&1 </dev/null; then break; fi
    sleep 2
    [ "$i" = 60 ] && die "headscale did not come up, see: docker compose logs headscale"
done
ok "headscale is answering"

if ! grep -q '^NODEROOST_HEADSCALE_API_KEY=.\+' .env; then
    say "Creating a headscale API key for the panel"
    KEY="$("${COMPOSE[@]}" exec -T headscale headscale apikeys create --expiration 3650d 2>/dev/null </dev/null | tail -1 | tr -d '\r')"
    [ -n "$KEY" ] || die "could not create the API key"
    sed -i "s|^NODEROOST_HEADSCALE_API_KEY=.*|NODEROOST_HEADSCALE_API_KEY=$KEY|" .env
    ok "key written to .env"
fi

"${COMPOSE[@]}" up -d </dev/null
say "Waiting for the panel"
for i in $(seq 1 60); do
    if "${COMPOSE[@]}" exec -T backend python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')" >/dev/null 2>&1 </dev/null; then break; fi
    sleep 2
    [ "$i" = 60 ] && die "the panel did not come up, see: docker compose logs backend"
done
ok "the panel is answering"

# ── 5. Хостовые помощники ─────────────────────────────────────────────────
# У бэкенда нет доступа к Docker (сознательно), поэтому перезапуск headscale
# после правки его config.yaml и снятие логов делает хост: systemd видит флаг
# от панели и запускает скрипт. Без этого «Настройки → DNS» и «Логи headscale»
# в панели не работают.
say "Installing the host helpers (DNS apply, headscale logs, watchdog)"
install -d /lib65/noderoost
for f in hs-apply.sh hs-logs.sh; do
    sed "s|^APP_DIR=.*|APP_DIR=$DIR|" "ops/$f" > "/lib65/noderoost/$f"
    chmod 755 "/lib65/noderoost/$f"
done
# Сторож панели: единственный, кто заметит смерть самой панели — она не может
# сообщить о ней своим же каналом. Путь к установке вписываем: скрипт лежит вне её.
sed "s|^APP_ROOT=.*|APP_ROOT=\"\${NODEROOST_APP:-$DIR}\"|" \
    ops/panel-watchdog.sh > /lib65/noderoost/panel-watchdog.sh
chmod 755 /lib65/noderoost/panel-watchdog.sh
echo '*/5 * * * * root /lib65/noderoost/panel-watchdog.sh' \
    > /etc/cron.d/noderoost-watchdog
chmod 644 /etc/cron.d/noderoost-watchdog
for u in noderoost-hs-apply noderoost-hs-logs; do
    for ext in path service; do
        sed "s|/app/noderoost|$DIR|g" "ops/$u.$ext" > "/etc/systemd/system/$u.$ext"
    done
done
systemctl daemon-reload
systemctl enable --now noderoost-hs-apply.path noderoost-hs-logs.path >/dev/null 2>&1 || true
ok "helpers in /lib65/noderoost, systemd is watching for flags, watchdog every 5 min"

# ── 6. Фаервол (по желанию) ───────────────────────────────────────────────
if [ "$UFW" = 1 ]; then
    say "Configuring ufw"
    command -v ufw >/dev/null 2>&1 || { apt-get update -qq </dev/null; apt-get install -y -qq ufw </dev/null; }
    # Существующие правила НЕ сбрасываем: на сервере может быть уже что-то нужное.
    ufw default deny incoming >/dev/null
    ufw default allow outgoing >/dev/null
    ufw allow OpenSSH >/dev/null 2>&1 || ufw allow 22/tcp >/dev/null
    # Порты, опубликованные Docker, идут через FORWARD — нужен именно route allow,
    # обычного allow мало.
    ufw route allow 80/tcp  >/dev/null
    ufw route allow 443/tcp >/dev/null
    ufw route allow 3478/udp >/dev/null
    ufw --force enable >/dev/null
    ok "allowed SSH, 80, 443 and 3478/udp (STUN for the embedded DERP)"
fi

ADMIN_USER="$(grep '^NODEROOST_ADMIN_USER=' .env | cut -d= -f2-)"
ADMIN_PASS="$(grep '^NODEROOST_ADMIN_PASSWORD=' .env | cut -d= -f2-)"
cat <<EOF

  NodeRoost is installed.

  Panel:           https://$PANEL_DOMAIN
  Control server:  https://$HS_DOMAIN
  Login:           $ADMIN_USER
  Password:        $ADMIN_PASS

  The password also sits in $DIR/.env — change it in the panel and turn on the
  second factor. Let's Encrypt issues the certificates on the first request: if
  the page does not open straight away, wait half a minute and reload.

  Next: "Add server" in the panel hands you the command to join a node.

EOF
