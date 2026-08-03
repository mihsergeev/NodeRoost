# Задача: управление DNS-записями меша (`extra_records`) в NodeRoost

Документ-задание для отдельной сессии. Всё, что ниже, проверено на реальном
окружении 01.08.2026: конфиг headscale v0.29.2, код NodeRoost, боевой хост меша.

## Зачем это нужно

Панель Amnezia Control (`acontrol.msergeev.ru`, хост `kz-se-advamnz-admin`)
закрыта IP-вайтлистом на caddy. Хост входит в меш как `100.100.0.1`, рабочая
машина — `ms-work` = `100.100.0.2`.

Проверено вживую: запрос **по меш-адресу работает** — `curl` на `100.100.0.1`
с именем `acontrol.msergeev.ru` отдаёт `HTTP 200` и валидный сертификат,
правила tailscale вход разрешают (`-A ts-input -i tailscale0 -j ACCEPT`).

Не хватает ровно одного — **DNS**: имя резолвится в публичный адрес, поэтому
браузер идёт через интернет и caddy видит публичный IP, а не меш-адрес.

Лечится записью «`acontrol.msergeev.ru` → `100.100.0.1`», раздаваемой узлам
меша. Сейчас это можно сделать только правкой hosts на каждой машине —
хочется из панели, централизованно.

Побочная выгода: когда меш-доступ настроен, вайтлист панели можно сузить до
`100.64.0.0/10` и убрать публичные адреса совсем — панель станет недоступна из
интернета в принципе.

## Что умеет headscale (v0.29.2, сверено с config-example.yaml)

Два способа задать статические записи:

```yaml
dns:
  # 1) список прямо в конфиге — ТРЕБУЕТ ПЕРЕЗАПУСКА headscale
  extra_records:
    - name: "grafana.myvpn.example.com"
      type: "A"
      value: "100.64.0.3"

  # 2) внешний JSON-файл — headscale перечитывает его ПРИ КАЖДОМ ИЗМЕНЕНИИ
  extra_records_path: /var/lib/headscale/extra-records.json
```

Поддерживаются только типы **A и AAAA** (ограничение стороны tailscale).

### Выбирать надо второй способ — `extra_records_path`

Причина: файл подхватывается **без перезапуска**. Перезапуск headscale — это
разрыв control-соединения для всех узлов; ради добавления DNS-записи платить
такую цену не нужно. Плюс не придётся трогать `config.yaml` на каждое
изменение и ставить флаг рестарта.

Формат файла — тот же массив объектов:

```json
[
  { "name": "acontrol.msergeev.ru", "type": "A", "value": "100.100.0.1" }
]
```

`config.yaml` правится **один раз** — чтобы прописать `extra_records_path`.
Дальше панель пишет только JSON.

## Где это делать в коде

### Бэкенд

`backend/app/api/settings.py` — там уже есть весь нужный инструментарий:

| Что | Где | Замечание |
|---|---|---|
| Чтение конфига | `_read_hs_config(path)` | возвращает `{}` при ошибке |
| Правка конфига | `_edit_hs_config(path, mutate)` | ruamel.yaml (сохраняет комментарии), делает `.bak`, ставит флаг рестарта |
| Флаг рестарта | `_restart_flag_path(config_path)` | `/data/headscale/.restart-request` |
| Текущая DNS-ручка | `PUT /hs-info/dns` → `_write_dns_config(...)` | образец стиля |

Путь к конфигу: `settings.headscale_config_path` = `/data/headscale/config/config.yaml`
(`backend/app/config.py:27`).

**Важно про пути.** В compose тома смонтированы так:

```
headscale:  ./data/headscale/config  -> /etc/headscale
            ./data/headscale/lib     -> /var/lib/headscale
backend:    ./data                   -> /data
```

Значит один и тот же файл виден по разным путям:

* бэкенд пишет в **`/data/headscale/lib/extra-records.json`**
* в `config.yaml` для headscale указывается **`/var/lib/headscale/extra-records.json`**

Перепутать легко — headscale молча не увидит записи.

### Что реализовать

1. **Настройка пути к файлу** — добавить в `app/config.py` рядом с
   `headscale_config_path`:
   ```python
   headscale_extra_records_path: str = "/data/headscale/lib/extra-records.json"
   # тот же файл глазами headscale (для записи в config.yaml)
   headscale_extra_records_path_in_hs: str = "/var/lib/headscale/extra-records.json"
   ```

2. **Схемы** (`backend/app/schemas.py`, рядом с `DnsInfo`/`DnsUpdateIn`):
   ```python
   class DnsRecord(BaseModel):
       name: str          # FQDN
       type: str = "A"    # только A|AAAA
       value: str         # IP-адрес

   class DnsRecordsUpdateIn(RequestModel):
       records: list[DnsRecord] = Field(default_factory=list, max_length=200)
   ```
   Валидация (в стиле существующего `_valid_domain`):
   * `name` — по регулярке домена, приводить к нижнему регистру;
   * `type` — только `A`/`AAAA`, иначе понятная ошибка;
   * `value` — `ipaddress.ip_address()`; для `A` обязателен IPv4, для `AAAA` — IPv6
     (несоответствие типа и адреса headscale просто проигнорирует);
   * дубли по `name` — отклонять, иначе непонятно, какая запись победит.

3. **Ручки**:
   * `GET  /hs-info/dns-records` — читает JSON-файл (нет файла → пустой список);
   * `PUT  /hs-info/dns-records` — пишет файл **атомарно** (`.tmp` + `os.replace`,
     как в `_edit_hs_config`), иначе headscale может прочитать пустой файл в
     момент записи.

4. **Однократная настройка `config.yaml`**: при первой записи проверить, что
   `dns.extra_records_path` уже указывает на наш файл; если нет — прописать через
   `_edit_hs_config` (это единственный случай, когда нужен флаг рестарта).
   Одновременно **очистить `dns.extra_records`**, если он непустой: при обоих
   заданных источниках поведение неочевидно, а список из конфига будет спорить с
   файлом.

5. **Фронтенд** — `frontend/src/SettingsPage.tsx`, в раздел DNS рядом с MagicDNS:
   таблица «имя → тип → адрес» с добавлением/удалением строк. Функции API — в
   `frontend/src/api.ts` по образцу существующих DNS-вызовов.
   **Не забыть** прогнать `node scripts/check-i18n.mjs` — в CI есть проверка, что
   каждая русская строка переведена на EN.

## Грабли (собраны из этого же кода и опыта)

* **Не ломать headscale пустыми значениями.** В `_write_dns_config` уже есть
  прецедент: headscale не стартует с `override_local_dns: true` и пустым списком
  серверов — панель роняла control-сервер и теряла с ним связь. Аналогично здесь:
  писать `extra_records_path` только вместе с реально существующим файлом.
* **Атомарная запись обязательна** — headscale читает файл по событию изменения и
  может поймать его недописанным.
* **MagicDNS не нужен для внешних имён.** Записи вида `acontrol.msergeev.ru`
  работают независимо от `base_domain`; MagicDNS отвечает за короткие имена узлов.
* **Записи раздаются только узлам меша.** Кто не в меше — резолвит имя публично,
  как и раньше. Это ровно то, что нужно.
* **Проверка результата.** На узле меша: `nslookup acontrol.msergeev.ru` должен
  вернуть `100.100.0.1`. Если вернул публичный адрес — либо на ноде включён свой
  резолвер (см. `override_local_dns`), либо headscale не подхватил файл (проверить
  путь глазами контейнера, см. таблицу томов выше).

## Как проверить, что задача решена

1. В панели добавить запись `acontrol.msergeev.ru → 100.100.0.1`.
2. На машине в меше: `nslookup acontrol.msergeev.ru` → `100.100.0.1`.
3. Открыть `https://acontrol.msergeev.ru` — должна открыться панель Amnezia
   Control **без предупреждений о сертификате** (имя то же, сертификат валиден).
4. Убедиться, что headscale **не перезапускался** (узлы не отваливались):
   `docker logs headscale --tail 20` без строк о старте.

Готовый вайтлист на стороне Amnezia Control уже настроен: `100.100.0.2`
(ms-work) в списке разрешённых, так что шаг 3 должен пройти сразу.
