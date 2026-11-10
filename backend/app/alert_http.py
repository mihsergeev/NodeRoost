"""HTTPS to a checked numeric address with the original HTTP and TLS identity."""

import asyncio
from base64 import b64encode
from urllib.parse import unquote

import httpx

from app.urlguard import parse_outbound_url, resolve_outbound


async def alert_request(
    method: str, raw_url: str, *, json: dict | None = None, read_body: bool = False,
) -> httpx.Response:
    parsed, host, port = parse_outbound_url(raw_url)
    # Use the same IDNA hostname for validation, SNI and the Host header.
    authority = f"[{host}]" if ":" in host else host
    if parsed.port is not None:
        authority += f":{port}"
    url = httpx.URL(parsed._replace(netloc=authority).geturl())
    headers = {"Host": url.netloc.decode("ascii"), "Accept-Encoding": "identity"}
    if parsed.username or parsed.password:
        credentials = f"{unquote(parsed.username or '')}:{unquote(parsed.password or '')}"
        headers["Authorization"] = "Basic " + b64encode(credentials.encode()).decode("ascii")
    extensions = {
        "sni_hostname": host.rstrip("."),
        "timeout": {"connect": 4, "read": 8, "write": 8, "pool": 4},
    }
    addresses = await resolve_outbound(host, port)
    # Use the transport directly. It has no proxy or redirect handling and does
    # not log the request URL, which can contain a Telegram token or webhook key.
    async with asyncio.timeout(15), httpx.AsyncHTTPTransport() as transport:
        for index, address in enumerate(addresses):
            request = httpx.Request(
                method, url.copy_with(host=address), json=json,
                headers=headers, extensions=extensions,
            )
            try:
                response = await transport.handle_async_request(request)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                # Only try another IP if the HTTP request has not been sent.
                if index == len(addresses) - 1:
                    raise
                continue
            try:
                content = bytearray()
                if read_body and response.headers.get("content-encoding", "identity") == "identity":
                    async for chunk in response.aiter_raw():
                        content.extend(chunk[:8192 - len(content)])
                        if len(content) == 8192:
                            break
                return httpx.Response(
                    response.status_code, content=bytes(content), request=httpx.Request(method, url),
                )
            finally:
                await response.aclose()
