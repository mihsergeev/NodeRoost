#!/usr/bin/env bash
# Установка NodeRoost на чистый сервер: Docker, панель, headscale и Caddy
# с автоматическим TLS. Скрипт идемпотентный — повторный запуск не ломает
# существующую установку (секреты и данные не перегенерируются).
#
#   curl -fsSL https://raw.githubusercontent.com/mihsergeev/NodeRoost/main/ops/install.sh \
#     | sudo bash -s -- --panel-domain panel.example.com --hs-domain hs.example.com
#
# Полный список параметров: --help.
set -euo pipefail

REPO_URL="https://github.com/mihsergeev/NodeRoost.git"
RAW_URL="https://raw.githubusercontent.com/mihsergeev/NodeRoost/main"
DIR="/opt/noderoost"
PANEL_DOMAIN=""
HS_DOMAIN=""
ALLOW_IPS="0.0.0.0/0 ::/0"
ACME_EMAIL=""
VERSION=""
BUILD=0
UFW=0
PUBLIC_IP=""

say()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[33m  ! \033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31mОшибка:\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Установка NodeRoost.

Обязательное:
  --panel-domain ДОМЕН   имя, по которому вы будете открывать панель
  --hs-domain ДОМЕН      имя control-сервера; его запоминают ноды, менять потом дорого

Необязательное:
  --allow-ips "СПИСОК"   с каких адресов пускать в панель, через пробел
                         (по умолчанию отовсюду: "0.0.0.0/0 ::/0")
  --acme-email ПОЧТА     Let's Encrypt пришлёт на неё предупреждение об истечении
  --dir ПУТЬ             куда ставить (по умолчанию /opt/noderoost)
  --version ТЕГ          версия образов (по умолчанию последний релиз)
  --build                собрать образы из исходников вместо готовых
  --ufw                  настроить фаервол: закрыть всё, кроме SSH, 80, 443, 3478/udp
  --public-ip АДРЕС      внешний IP сервера для встроенного DERP
                         (по умолчанию определяется автоматически)

Пример:
  install.sh --panel-domain panel.example.com --hs-domain hs.example.com \
             --allow-ips "203.0.113.10 198.51.100.0/24" --ufw
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --panel-domain) PANEL_DOMAIN="${2:-}"; shift 2 ;;
        --hs-domain)    HS_DOMAIN="${2:-}"; shift 2 ;;
        --allow-ips)    ALLOW_IPS="${2:-}"; shift 2 ;;
        --acme-email)   ACME_EMAIL="${2:-}"; shift 2 ;;
        --dir)          DIR="${2:-}"; shift 2 ;;
        --version)      VERSION="${2:-}"; shift 2 ;;
        --public-ip)    PUBLIC_IP="${2:-}"; shift 2 ;;
        --build)        BUILD=1; shift ;;
        --ufw)          UFW=1; shift ;;
        -h|--help)      usage; exit 0 ;;
        *)              die "неизвестный параметр: $1 (--help)" ;;
    esac
done

[ "$(id -u)" -eq 0 ] || die "нужен root: запустите через sudo"
[ -n "$PANEL_DOMAIN" ] || { usage; die "не задан --panel-domain"; }
[ -n "$HS_DOMAIN" ] || { usage; die "не задан --hs-domain"; }
[ "$PANEL_DOMAIN" != "$HS_DOMAIN" ] || die "домены панели и control-сервера должны различаться"

# ── 1. Docker ─────────────────────────────────────────────────────────────
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    ok "Docker уже установлен"
else
    say "Ставлю Docker"
    if command -v curl >/dev/null 2>&1; then :; else apt-get update -qq && apt-get install -y -qq curl; fi
    # Официальный установщик знает не про каждый свежий релиз дистрибутива —
    # тогда откатываемся на пакеты самого дистрибутива.
    if ! curl -fsSL https://get.docker.com | sh >/tmp/nr-docker.log 2>&1; then
        warn "get.docker.com не отработал, ставлю docker.io из репозитория дистрибутива"
        apt-get update -qq
        apt-get install -y -qq docker.io docker-compose-v2 \
            || die "не удалось поставить Docker, смотрите /tmp/nr-docker.log"
    fi
    systemctl enable --now docker >/dev/null 2>&1 || true
    docker compose version >/dev/null 2>&1 || die "docker compose недоступен после установки"
    ok "Docker готов"
fi

# ── 2. Исходники ──────────────────────────────────────────────────────────
if [ -d "$DIR/.git" ]; then
    say "Обновляю $DIR"
    git -C "$DIR" fetch --depth 1 origin main -q && git -C "$DIR" reset --hard origin/main -q
else
    say "Скачиваю NodeRoost в $DIR"
    command -v git >/dev/null 2>&1 || { apt-get update -qq; apt-get install -y -qq git; }
    git clone --depth 1 "$REPO_URL" "$DIR" -q
fi
cd "$DIR"

if [ -z "$VERSION" ]; then
    # Последний релиз; без сети/без релизов — версия из CHANGELOG, иначе сборка.
    VERSION="$(curl -fsSL https://api.github.com/repos/mihsergeev/NodeRoost/releases/latest 2>/dev/null \
        | sed -n 's/.*"tag_name": *"v\{0,1\}\([^"]*\)".*/\1/p' | head -1 || true)"
    [ -n "$VERSION" ] || { VERSION="0.0.0-local"; BUILD=1; warn "релиз не найден — собираю из исходников"; }
fi

# ── 3. Конфигурация ───────────────────────────────────────────────────────
mkdir -p data/postgres data/headscale/config data/headscale/lib data/headscale/run \
         data/caddy/data data/caddy/config data/tailscale-pkgs data/backups
chmod 700 data/backups

if [ -z "$PUBLIC_IP" ]; then
    PUBLIC_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
    [ -n "$PUBLIC_IP" ] || PUBLIC_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.*src \([0-9.]*\).*/\1/p')"
fi
[ -n "$PUBLIC_IP" ] || die "не удалось определить внешний IP, задайте --public-ip"

if [ -f .env ]; then
    ok ".env уже есть — секреты и пароли не трогаю"
    # Домены и список адресов обновляем: ради них скрипт и запускают повторно.
    sed -i "s|^NODEROOST_DOMAIN=.*|NODEROOST_DOMAIN=$PANEL_DOMAIN|;
            s|^NODEROOST_HS_DOMAIN=.*|NODEROOST_HS_DOMAIN=$HS_DOMAIN|;
            s|^NODEROOST_ALLOW_IPS=.*|NODEROOST_ALLOW_IPS=$ALLOW_IPS|;
            s|^NODEROOST_VERSION=.*|NODEROOST_VERSION=$VERSION|" .env
else
    say "Генерирую .env (пароли — случайные)"
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
NODEROOST_ACME_EMAIL=$ACME_EMAIL

NODEROOST_VERSION=$VERSION
NODEROOST_PANEL_IP=$PUBLIC_IP
NODEROOST_PANEL_URL=https://$PANEL_DOMAIN
NODEROOST_HEADSCALE_API_KEY=
COMPOSE_FILE=compose.yml:compose.tls.yml
EOF
    chmod 600 .env
    ok ".env создан"
fi

if [ ! -f data/headscale/config/config.yaml ]; then
    say "Готовлю конфиг headscale"
    sed -e "s|^server_url:.*|server_url: https://$HS_DOMAIN|" \
        -e "s|^\( *ipv4: *\)[0-9.]*|\1$PUBLIC_IP|" \
        deploy/headscale/config.example.yaml > data/headscale/config/config.yaml
    ok "server_url = https://$HS_DOMAIN, DERP на $PUBLIC_IP"
fi

# ── 4. Запуск ─────────────────────────────────────────────────────────────
say "Поднимаю контейнеры"
COMPOSE=(docker compose --env-file .env)
if [ "$BUILD" = 1 ]; then
    "${COMPOSE[@]}" build backend frontend
fi
# Бэкенд без ключа headscale не стартует — поднимаем остальное, ключ создаём ниже.
"${COMPOSE[@]}" up -d db headscale frontend caddy

say "Жду headscale"
for i in $(seq 1 60); do
    if "${COMPOSE[@]}" exec -T headscale headscale users list >/dev/null 2>&1; then break; fi
    sleep 2
    [ "$i" = 60 ] && die "headscale не поднялся, смотрите: docker compose logs headscale"
done
ok "headscale отвечает"

if ! grep -q '^NODEROOST_HEADSCALE_API_KEY=.\+' .env; then
    say "Создаю API-ключ headscale для панели"
    KEY="$("${COMPOSE[@]}" exec -T headscale headscale apikeys create --expiration 3650d 2>/dev/null | tail -1 | tr -d '\r')"
    [ -n "$KEY" ] || die "не удалось создать API-ключ"
    sed -i "s|^NODEROOST_HEADSCALE_API_KEY=.*|NODEROOST_HEADSCALE_API_KEY=$KEY|" .env
    ok "ключ записан в .env"
fi

"${COMPOSE[@]}" up -d
say "Жду панель"
for i in $(seq 1 60); do
    if "${COMPOSE[@]}" exec -T backend python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')" >/dev/null 2>&1; then break; fi
    sleep 2
    [ "$i" = 60 ] && die "панель не поднялась, смотрите: docker compose logs backend"
done
ok "панель отвечает"

# ── 5. Фаервол (по желанию) ───────────────────────────────────────────────
if [ "$UFW" = 1 ]; then
    say "Настраиваю ufw"
    command -v ufw >/dev/null 2>&1 || { apt-get update -qq; apt-get install -y -qq ufw; }
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
    ok "открыты SSH, 80, 443 и 3478/udp (STUN для встроенного DERP)"
fi

ADMIN_USER="$(grep '^NODEROOST_ADMIN_USER=' .env | cut -d= -f2-)"
ADMIN_PASS="$(grep '^NODEROOST_ADMIN_PASSWORD=' .env | cut -d= -f2-)"
cat <<EOF

  NodeRoost установлен.

  Панель:          https://$PANEL_DOMAIN
  Control-сервер:  https://$HS_DOMAIN
  Логин:           $ADMIN_USER
  Пароль:          $ADMIN_PASS

  Пароль лежит в $DIR/.env — смените его в панели и включите второй фактор.
  Сертификаты Let's Encrypt выдаются при первом обращении: если страница не
  открылась сразу, подождите полминуты и обновите.

  Дальше: «Добавить сервер» в панели — она выдаст команду для подключения ноды.

EOF
