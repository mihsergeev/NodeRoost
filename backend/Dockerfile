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

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
