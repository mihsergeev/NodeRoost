"""Клиент управляющего REST-API headscale (`/api/v1`).

headscale отдаёт REST, автоматически сгенерённый из его gRPC-сервиса
(gRPC-gateway): JSON, аутентификация — заголовок ``Authorization: Bearer
<api_key>``. Панель ходит сюда по ВНУТРЕННЕЙ docker-сети
(``http://headscale:8080``); наружу этот API не выставляется.

На этапе 1 — минимальный набор (проверка доступности + чтение
пользователей/нод). CRUD (создание/удаление нод, pre-auth-ключи, маршруты,
политика) добавляется по мере прохождения этапов.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from app.config import Settings


class HeadscaleError(Exception):
    """Ошибка обращения к headscale (сеть, авторизация, HTTP-статус)."""


class HeadscaleClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self._base and self._key)

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        return headers

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self._base}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.request(
                    method, url, headers=self._headers(), **kwargs
                )
        except httpx.HTTPError as exc:  # сеть/таймаут/DNS
            # У таймаутов httpx текст пустой, и сообщение обрывалось на
            # двоеточии: «headscale недоступен: » — ни что случилось, ни как
            # долго ждали. Зависший control-сервер выглядит именно так, и это
            # самый частый его отказ: не упал, а перестал отвечать.
            reason = str(exc) or (
                f"не ответил за {self._timeout:g} с"
                if isinstance(exc, httpx.TimeoutException)
                else type(exc).__name__
            )
            raise HeadscaleError(f"headscale недоступен: {reason}") from exc
        if resp.status_code >= 400:
            # headscale отдаёт ошибки как {"code":.., "message":"...", ...} —
            # достаём message (для понятных сообщений валидации политики)
            msg = resp.text[:400]
            try:
                j = resp.json()
                if isinstance(j, dict) and j.get("message"):
                    msg = j["message"]
            except ValueError:
                pass
            raise HeadscaleError(f"headscale {resp.status_code}: {msg}")
        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError as exc:
            raise HeadscaleError(f"headscale вернул не-JSON: {exc}") from exc

    async def _get(self, path: str) -> dict:
        return await self._request("GET", path)

    async def _post(self, path: str, json: dict | None = None) -> dict:
        return await self._request("POST", path, json=json)

    async def _delete(self, path: str) -> dict:
        return await self._request("DELETE", path)

    async def ping(self) -> bool:
        """Лёгкая проверка доступности + валидности API-ключа.

        ``/api/v1/apikey`` требует авторизации и возвращает список ключей —
        подходит как health-проба (не мутирует состояние).
        """
        await self._get("/api/v1/apikey")
        return True

    async def get_users(self) -> list[dict]:
        data = await self._get("/api/v1/user")
        return data.get("users", [])

    async def get_nodes(self) -> list[dict]:
        data = await self._get("/api/v1/node")
        return data.get("nodes", [])

    async def get_node(self, node_id: str) -> dict | None:
        data = await self._get(f"/api/v1/node/{node_id}")
        return data.get("node")

    async def delete_node(self, node_id: str) -> None:
        await self._delete(f"/api/v1/node/{node_id}")

    async def expire_node(self, node_id: str) -> dict | None:
        """Истечь ключ ноды — она перестанет подключаться до перерегистрации."""
        data = await self._post(f"/api/v1/node/{node_id}/expire")
        return data.get("node")

    async def rename_node(self, node_id: str, new_name: str) -> dict | None:
        """Сменить givenName (имя ноды в тайлнете). Имя уходит в путь URL."""
        data = await self._post(f"/api/v1/node/{node_id}/rename/{quote(new_name, safe='')}")
        return data.get("node")

    async def set_node_tags(self, node_id: str, tags: list[str]) -> dict | None:
        data = await self._post(f"/api/v1/node/{node_id}/tags", json={"tags": tags})
        return data.get("node")


    async def approve_routes(self, node_id: str, routes: list[str]) -> dict | None:
        """Задаёт ПОЛНЫЙ список одобренных маршрутов ноды (не аддитивно).

        Активными (subnetRoutes) становятся одобренные, которые нода реально
        анонсирует (availableRoutes). Exit-node = одобренные 0.0.0.0/0 и ::/0.
        """
        data = await self._post(
            f"/api/v1/node/{node_id}/approve_routes", json={"routes": routes}
        )
        return data.get("node")

    # --- пользователи ---

    async def create_user(self, name: str) -> dict:
        data = await self._post("/api/v1/user", json={"name": name})
        return data.get("user", {})

    async def ensure_user(self, name: str) -> dict:
        """Возвращает пользователя по имени, создавая его при отсутствии."""
        for u in await self.get_users():
            if u.get("name") == name:
                return u
        return await self.create_user(name)



    # --- pre-auth-ключи ---

    async def create_preauthkey(
        self,
        user_id: str,
        *,
        reusable: bool = False,
        ephemeral: bool = False,
        expiration: str | None = None,
        acl_tags: list[str] | None = None,
    ) -> dict:
        body: dict = {
            "user": str(user_id),  # uint64 строкой
            "reusable": reusable,
            "ephemeral": ephemeral,
            "aclTags": acl_tags or [],
        }
        if expiration:
            body["expiration"] = expiration
        data = await self._post("/api/v1/preauthkey", json=body)
        return data.get("preAuthKey", {})

    async def list_preauthkeys(self) -> list[dict]:
        """Все pre-auth-ключи. В 0.29 запрос без параметров — список приходит
        сразу по всем пользователям."""
        data = await self._get("/api/v1/preauthkey")
        return data.get("preAuthKeys", [])

    async def delete_preauthkey(self, key_id: str) -> None:
        """Удалить ключ насовсем. id уходит query-параметром: у DELETE-биндинга
        тела нет, и gRPC-gateway раскладывает поля запроса в query."""
        await self._request(
            "DELETE", "/api/v1/preauthkey", params={"id": str(key_id)}
        )



    # --- API-ключи headscale ---

    async def list_apikeys(self) -> list[dict]:
        data = await self._get("/api/v1/apikey")
        return data.get("apiKeys", [])

    async def create_apikey(self, expiration: str | None = None) -> str:
        body = {"expiration": expiration} if expiration else {}
        data = await self._post("/api/v1/apikey", json=body)
        return data.get("apiKey", "")

    async def expire_apikey(self, prefix: str) -> None:
        await self._post("/api/v1/apikey/expire", json={"prefix": prefix})

    async def delete_apikey(self, prefix: str) -> None:
        await self._delete(f"/api/v1/apikey/{prefix}")

    # --- ACL-политика (только в policy.mode: database) ---

    async def get_policy(self) -> dict:
        """{policy, updatedAt}. В database-режиме без заданной политики
        headscale отдаёт 500 «acl policy not found» — обрабатывает вызывающий."""
        return await self._get("/api/v1/policy")

    async def set_policy(self, policy: str) -> dict:
        return await self._request("PUT", "/api/v1/policy", json={"policy": policy})


def get_client(settings: Settings) -> HeadscaleClient:
    return HeadscaleClient(
        settings.headscale_url,
        settings.headscale_api_key,
        settings.headscale_timeout,
    )
