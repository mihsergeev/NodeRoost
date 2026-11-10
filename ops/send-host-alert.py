#!/usr/bin/env python3
"""Deliver host-side watchdog and offsite alerts without trusting heartbeat URLs."""

import base64
import http.client
import json
import socket
import ssl
import sys
from pathlib import Path
from urllib.parse import quote, unquote

# The installer copies the same policy used by the panel beside this helper.
if (Path(__file__).parent / "urlguard.py").is_file():
    from urlguard import checked_addresses, literal_address, parse_outbound_url, telegram_url
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from app.urlguard import checked_addresses, literal_address, parse_outbound_url, telegram_url


def _post(url: str, payload: dict) -> bool:
    parsed, host, port = parse_outbound_url(url)
    literal = literal_address(host)
    addresses = (literal,) if literal else checked_addresses(
        socket.getaddrinfo(host, port, type=socket.SOCK_STREAM),
    )
    context = ssl.create_default_context()
    conn = http.client.HTTPConnection(host, port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if parsed.username or parsed.password:
        credentials = f"{unquote(parsed.username or '')}:{unquote(parsed.password or '')}"
        headers["Authorization"] = "Basic " + base64.b64encode(credentials.encode()).decode("ascii")
    for address in addresses:
        try:
            with socket.create_connection((address, port), timeout=4) as sock:
                conn.sock = context.wrap_socket(sock, server_hostname=host.rstrip("."))
            break
        except OSError:
            continue
    else:
        return False
    try:
        path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        if parsed.query:
            path += "?" + quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
        # http.client derives the Host header from the original hostname.
        conn.request("POST", path, json.dumps(payload).encode("ascii"), headers)
        with conn.getresponse() as response:
            return 200 <= response.status < 300
    finally:
        conn.close()


def notify(heartbeat: Path, message: str) -> bool:
    values = {}
    try:
        with heartbeat.open(encoding="utf-8") as stream:
            content = stream.read(65537)
        if len(content) > 65536:
            return False
        for line in content.split("\n"):
            key, separator, value = line.partition("=")
            if separator:
                values.setdefault(key, value)
    except (OSError, UnicodeError):
        return False
    requests = []
    token, chat = values.get("tg_token"), values.get("tg_chat")
    if token and chat:
        try:
            url = telegram_url(values.get("tg_api"), token, "sendMessage")
        except ValueError:
            pass
        else:
            requests.append((url, {
                "chat_id": chat, "text": message, "disable_web_page_preview": True,
            }))
    if hook := values.get("webhook"):
        requests.append((hook, {"text": message}))
    sent = False
    for url, payload in requests:
        try:
            sent = _post(url, payload) or sent
        except (OSError, ValueError, http.client.HTTPException):
            # Credentials in the heartbeat and endpoint URLs stay out of cron output.
            continue
    return sent


if __name__ == "__main__":
    sys.exit(0 if notify(Path(sys.argv[1]), sys.stdin.read()) else 1)
