# база пинуется по дайджесту: плавающий тег переносит сборку на другой образ
# незаметно для нас — и то, что проверено, перестаёт быть тем, что выкачено
FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6

WORKDIR /srv

COPY pyproject.toml requirements.lock ./
# Зависимости — строго из lock-файла и только с совпавшими хешами: без него дерево
# зависимостей резолвилось на прод-хосте в момент сборки, то есть выкатывалось
# что-то, чего никто не проверял (и что мог подменить кто угодно на пути к PyPI).
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY app ./app
RUN pip install --no-cache-dir --no-deps .

COPY alembic.ini ./
COPY alembic ./alembic

# Не root: любая дыра в приложении иначе сразу даёт нулевой uid со всеми
# возможностями по умолчанию и правом писать в каталог, который потом читает
# помощник на хосте. Каталог данных монтируется снаружи, поэтому его владельца
# выставляем здесь же - на уже существующих установках файлы могли быть от root.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin noderoost
# Байткод складываем на этапе сборки, пока есть root: иначе первый импорт от
# 10001 упирается в недоступный для записи /srv, __pycache__ не создается, и
# каждый старт заново компилирует весь пакет.
RUN python -m compileall -q /srv/app
USER 10001

EXPOSE 8000
# Проверка записи в /data до миграций: без нее панель поднимается как обычно, а
# непонятные отказы приходят потом по одному - бэкап, DNS, зеркало пакетов.
CMD ["sh", "-c", "if [ ! -w /data ]; then echo \"FATAL: /data is not writable for uid $(id -u). Run 'sudo ops/data-owner.sh' on the host.\" >&2; exit 1; fi; alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
