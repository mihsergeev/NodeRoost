from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NODEROOST_", env_file=".env")

    app_name: str = "NodeRoost"
    # Версия панели. Двигается вместе с pyproject.toml, frontend/package.json
    # и NODEROOST_VERSION в .env.example (последний задаёт теги образов) —
    # иначе панель показывает не ту версию, что установлена.
    version: str = "0.9.1"
    debug: bool = False

    db_url: str = "sqlite+aiosqlite:///./data/panel.db"
    data_dir: str = "./data"

    # --- Интеграция с headscale (control-сервер Tailscale) ---
    # Панель ходит в управляющий REST-API headscale по ВНУТРЕННЕЙ docker-сети;
    # наружу этот API не торчит (публичен только узловой контур на hs-домене).
    headscale_url: str = "http://headscale:8080"
    headscale_api_key: str = ""
    headscale_timeout: int = 10
    # путь к config.yaml headscale (смонтирован в бэкенд через ./data) —
    # для read-only показа DNS/DERP (у headscale нет API для них)
    headscale_config_path: str = "/data/headscale/config/config.yaml"
    # Имена, раздаваемые внутри меша (headscale dns.extra_records_path).
    # Файл лежит в каталоге КОНФИГА, а не в lib, как можно было бы ждать: lib
    # смонтирован бэкенду только на чтение (там база нод и приватные ключи), а
    # в каталог конфига панель и так пишет config.yaml.
    headscale_extra_records_path: str = "/data/headscale/config/extra-records.json"
    # тот же файл глазами headscale — это значение уходит в config.yaml
    headscale_extra_records_path_in_hs: str = "/etc/headscale/extra-records.json"
    # За сколько дней до конца сертификата просить у ноды новый CSR
    cert_renew_days: int = 30
    # БД headscale — читаем ТОЛЬКО на чтение и только ради host_info (ОС/версия
    # клиента), которого нет в REST API. Это не публичный контракт headscale,
    # поэтому всё best-effort: недоступна/сменилась схема → блок просто не покажем.
    headscale_db_path: str = "/data/headscale/lib/db.sqlite"
    # Публичный адрес control-сервера (server_url в headscale) — для подсказок в UI
    # (команда `tailscale up --login-server=…`). Пусто = не показывать.
    headscale_server_url: str = ""

    # Enroll (добавление нод): имя дефолтного пользователя-владельца новых нод,
    # пиновая версия официального клиента Tailscale в скриптах подключения,
    # срок жизни одноразового ключа подключения (минуты).
    default_user: str = "default"
    tailscale_version: str = "1.98.8"
    enroll_key_ttl_minutes: int = 60

    # Внешний IP панели — для информации/скриптов
    panel_ip: str = ""

    # Алерты (Telegram / вебхук): нода упала/вернулась, ключ скоро истекает
    alert_telegram_token: str = ""
    alert_telegram_chat: str = ""
    alert_webhook: str = ""
    # Сколько подряд пропущенных наблюдений считать нодой «упавшей» (антидребезг)
    node_down_misses: int = 2

    # Сбор метрик: интервал опроса headscale (сек, 0 = выключить), хранение (дни)
    metrics_interval: int = 60
    metrics_retention_days: int = 90
    # За сколько дней до истечения ключа ноды слать предупреждение (0 = выключить)
    key_expiry_warn_days: int = 7
    # Через сколько дней удалять отработавшие pre-auth-ключи enroll-флоу
    # (просроченные или использованные одноразовые). Каждое подключение ноды
    # создаёт такой ключ, и без подчистки их список растёт без предела.
    # 0 = не подчищать.
    preauth_retention_days: int = 7
    # Через сколько минут молчания агента на ноде считать, что он умер, и слать
    # алерт (0 = выключить). Агент опрашивает панель раз в минуту, так что запас
    # тут щедрый: один-два пропуска из-за сетевой икоты будить не должны.
    agent_silent_minutes: int = 10

    # Автобэкап (значения по умолчанию; редактируются в UI через settings_store)
    backup_interval_hours: int = 24
    backup_keep: int = 7

    # Публичный URL панели (для heartbeat/watchdog — какая именно панель молчит)
    panel_url: str = ""

    jwt_secret: str = "dev-insecure-change-me"
    jwt_ttl_minutes: int = 12 * 60

    # Начальная учётка админа (сидируется при первом старте; далее пароль
    # меняется в UI и из .env НЕ пересинхронизируется)
    admin_user: str = "admin"
    admin_password: str = "admin"

    # Аварийный сброс (break-glass): при NODEROOST_ADMIN_PASSWORD_RESET=1 старт
    # сбрасывает пароль админа на admin_password и отключает 2FA. Убрать флаг
    # из .env после входа. Нужно, если потерян пароль И 2FA.
    admin_password_reset: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
