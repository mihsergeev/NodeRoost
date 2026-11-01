#!/usr/bin/env python3
"""Serve the actual built frontend with an isolated, deterministic demo API.

Build frontend/dist first, then run: python docs/preview-demo.py --port 8765
Open http://127.0.0.1:8765 and sign in with admin / demo. The explicit test
session marker is demo-session. Mutations affect memory only and reset on restart.
For a 2560 x 1800 full-page capture, open /capture: the same live frontend
uses a 1280 x 900 iframe viewport, rendered by the browser at CSS scale 2.
No backend, remote request, command execution, DNS lookup or agent is involved.
Fixtures contain only reserved example names and documentation IP ranges.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import mimetypes
from pathlib import Path
import re
import threading
from urllib.parse import parse_qs, unquote, urlsplit


HERE = Path(__file__).resolve().parent
STAMP = "2026-11-01T09:00:00Z"
SESSION = "demo-session"
MAX_BODY = 256 * 1024
META_FIELDS = {
    "description", "kind", "admin", "muted", "hidden", "exit_gateway",
    "exit_via", "force_exit", "group", "subgroup",
}


class DemoError(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail


class DemoState:
    def __init__(self, fixture: dict):
        self.responses = copy.deepcopy(fixture["responses"])
        self.nodes = []
        for row in fixture["nodes"]:
            node = copy.deepcopy(fixture["node_defaults"])
            node.update(copy.deepcopy(row))
            node["hostname"] = node["hostname"] or node["name"]
            self.nodes.append(node)
        self.agents = {}
        for node in self.nodes:
            agent = copy.deepcopy(fixture["agent_defaults"])
            agent.update(copy.deepcopy(fixture["agents"].get(node["id"], {})))
            self.agents[node["id"]] = agent
        self.lock = threading.RLock()
        self.direction_seq = 2

    def node(self, node_id: str) -> dict:
        for node in self.nodes:
            if node["id"] == node_id:
                return node
        raise DemoError(404, "Demo node not found")

    def outdated(self) -> dict:
        ids = [n["id"] for n in self.nodes if n["kind"] == "server"
               and not n["hidden"] and not self.agents[n["id"]]["script_current"]]
        return {"count": len(ids), "nodes": ids, "signed": True}

    @staticmethod
    def resolve(host: str) -> list[str]:
        try:
            return [str(ipaddress.ip_network(host, strict=False)) if "/" in host
                    else str(ipaddress.ip_address(host))]
        except ValueError:
            return ["203.0.113.42"]

    def enroll(self, body: dict, origin: str, name: str | None = None) -> dict:
        hostname = name or str(body.get("name", "demo-new-node"))
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", hostname):
            raise DemoError(422, "Use a lowercase demo hostname with letters, digits and hyphens")
        return {
            "os": body.get("os", "linux"), "hostname": hostname,
            "login_server": "https://control.example.test", "key_id": "demo-enroll",
            "expires_at": "2026-11-01T09:30:00Z",
            "script": "# Local demo only. No network connection or installation is performed.\n"
                      "echo 'Preview enrollment for " + hostname + "'\n",
            "join_cmd": "echo 'Local demo: enrollment preview only'",
            "join_url": origin + "/demo-enrollment.txt",
        }

    def api(self, method: str, path: str, query: dict, body: dict, origin: str):
        """Return (status, JSON response), with all state confined to this object."""
        r = self.responses
        if method == "POST" and path == "/api/auth/login":
            if body.get("username") != "admin" or body.get("password") != "demo":
                raise DemoError(401, "Local demo credentials: admin / demo")
            return 200, {"access_token": SESSION, "token_type": "bearer"}
        if method == "POST" and path == "/api/auth/logout":
            return 204, None
        if method == "GET":
            if path == "/api/nodes":
                return 200, self.nodes
            if path == "/api/agent/outdated":
                return 200, self.outdated()
            if path == "/api/enroll/status":
                return 200, {"connected": False, "node": None, "reused_from": None}
            if path == "/api/tailscale-version/check":
                return 200, {"ok": True, "version": query.get("version", ["1.90.9"])[0],
                             "url": origin + "/demo-package.txt", "note": "Local fixture; no remote check"}
            if path == "/api/metrics/history":
                base = datetime(2026, 11, 1, 9, tzinfo=timezone.utc)
                points = [{"ts": (base - timedelta(minutes=30 * (48 - i))).isoformat(),
                           "online": [9, 9, 8, 9, 9, 10, 9, 9][i % 8], "total": 11}
                          for i in range(49)]
                return 200, {"interval_seconds": 1800, "points": points}
            if path == "/api/logs/summary":
                nodes = [n for n in self.nodes if not n["hidden"]]
                hs = r["/api/hs-info"]
                return 200, {"panel_version": "0.25.2", "headscale_url": hs["server_url"],
                             "headscale_ok": True, "magic_dns": hs["dns"]["magic_dns"],
                             "base_domain": hs["dns"]["base_domain"], "nameservers": hs["dns"]["nameservers"],
                             "derp_embedded": True, "nodes_total": len(nodes),
                             "servers": sum(n["kind"] == "server" for n in nodes),
                             "devices": sum(n["kind"] == "device" for n in nodes),
                             "online": sum(n["online"] for n in nodes),
                             "last_backup": r["/api/backup/list"][0]["filename"],
                             "last_backup_at": r["/api/backup/list"][0]["created"]}
            if path == "/api/policy":
                return 200, {"policy": r["/api/policy/rules"]["generated"], "updated_at": STAMP, "exists": True}
            match = re.fullmatch(r"/api/nodes/(\d+)", path)
            if match:
                return 200, self.node(match[1])
            match = re.fullmatch(r"/api/agent/(\d+)", path)
            if match:
                self.node(match[1])
                return 200, self.agents[match[1]]
            if path in r:
                return 200, r[path]

        match = re.fullmatch(r"/api/nodes/(\d+)(?:/([a-z-]+))?", path)
        if match:
            node = self.node(match[1])
            action = match[2]
            if method == "DELETE" and action is None:
                self.nodes.remove(node)
                return 204, None
            if method == "POST":
                if action == "meta":
                    node.update({k: v for k, v in body.items() if k in META_FIELDS and v is not None})
                    node["kind"] = node["kind"] or "server"
                elif action == "rename":
                    name = str(body.get("name", ""))
                    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", name):
                        raise DemoError(422, "Invalid demo node name")
                    node["name"] = name
                elif action == "tags":
                    node["forced_tags"] = node["tags"] = body.get("tags", [])
                elif action == "routes":
                    node["approved_routes"] = body.get("routes", [])
                    node["subnet_routes"] = [x for x in node["approved_routes"] if x not in ("0.0.0.0/0", "::/0")]
                    node["is_exit_node"] = "0.0.0.0/0" in node["approved_routes"]
                elif action == "expire":
                    node.update({"key_expired": True, "online": False, "expiry": STAMP})
                elif action == "exit-clients":
                    for device in self.nodes:
                        ids = [i for i in device["exit_via"] if i != node["id"]]
                        if device["id"] in body.get("devices", []):
                            ids.append(node["id"])
                        device["exit_via"] = ids
                elif action == "reconnect":
                    return 200, self.enroll(body, origin, node["name"])
                else:
                    raise DemoError(404, "Unknown demo node action")
                return 200, node

        match = re.fullmatch(r"/api/agent/(\d+)(?:/(reissue|update))?", path)
        if match:
            self.node(match[1])
            agent = self.agents[match[1]]
            if method == "PUT" and match[2] is None:
                agent.update({"routes": body.get("routes", []), "exit_node": body.get("exit_node", False)})
                return 200, agent
            if method == "POST" and match[2] in ("reissue", "update"):
                agent["script_current"] = True
                return 200, agent
        if method == "POST" and path == "/api/agent/update-outdated":
            for agent in self.agents.values():
                agent["script_current"] = True
            return 200, self.outdated()
        if method == "POST" and path == "/api/enroll":
            return 200, self.enroll(body, origin)
        if method == "POST" and path == "/api/policy/resolve-host":
            host = str(body.get("host", "example.com"))
            return 200, {"host": host, "ips": self.resolve(host), "note": "Local demo resolution"}
        if method == "PUT" and path == "/api/policy/rules":
            rules = body.get("rules", [])
            r[path] = {"rules": rules, "generated": json.dumps({"demo_rules": rules}, indent=2)}
            return 200, r[path]
        if method == "POST" and path == "/api/routing/refresh":
            return 200, r["/api/routing"]
        if method == "POST" and path == "/api/routing":
            self.node(str(body.get("via", "")))
            self.direction_seq += 1
            row = {"id": f"demo-{self.direction_seq}", "src_kind": body.get("src_kind", "node"),
                   "src": body.get("src", []), "full": False, "dst": str(body.get("dst", "example.com")),
                   "via": body["via"], "ports": body.get("ports", "*"),
                   "resolved_at": STAMP, "error": "", "active": True, "via_agent": True}
            row["ips"] = self.resolve(row["dst"])
            r[path]["directions"].append(row)
            return 200, row
        if method == "DELETE" and path.startswith("/api/routing/"):
            rows = r["/api/routing"]["directions"]
            rows[:] = [x for x in rows if x["id"] != path.rsplit("/", 1)[1]]
            return 204, None
        if method == "PUT" and path in ("/api/hs-info/dns", "/api/hs-info/network"):
            info = r["/api/hs-info"]
            target = info["dns"] if path.endswith("/dns") else info
            keys = ("magic_dns", "base_domain", "nameservers", "override_local_dns") if path.endswith("/dns") else ("ipv4_prefix", "allocation")
            target.update({k: body[k] for k in keys if k in body})
            return 200, info
        if method == "PUT" and path == "/api/hs-info/dns-records":
            rows = []
            for row in body.get("records", []):
                node = self.node(row["node_id"]) if row.get("node_id") else None
                rows.append({**row, "node_name": node["name"] if node else "", "cert_status": "ok" if row.get("cert") else "",
                             "cert_until": "2027-01-30T00:00:00Z" if row.get("cert") else "", "cert_error": "", "note": "",
                             "addresses": (node["ip_addresses"] if node else [row.get("ip", "")]) if row.get("enabled") else []})
            r[path]["records"] = rows
            return 200, r[path]
        if method == "PUT" and path == "/api/ca":
            ca = r["/api/hs-info/dns-records"]["ca"]
            ca["auto"] = bool(body.get("auto", True))
            return 200, ca
        if method == "PUT" and path in ("/api/tailscale-version", "/api/backup/config", "/api/alerts"):
            if path == "/api/tailscale-version":
                r[path]["current"] = body.get("version", r[path]["current"])
            elif path == "/api/backup/config":
                r[path].update({k: body[k] for k in ("interval_hours", "keep") if k in body})
            else:
                r[path].update({k: body[k] for k in ("telegram_token", "telegram_chat", "telegram_api", "webhook") if k in body})
                r[path]["enabled"] = bool(body.get("webhook") or body.get("telegram_token"))
            return 200, r[path]
        if method == "POST" and path == "/api/tailscale-version/mirror":
            return 200, r[path]
        if method == "POST" and path == "/api/alerts/test":
            return 200, {"sent": False, "errors": ["Local demo: no notification was sent"]}
        if method == "POST" and path == "/api/auth/devices/days":
            r["/api/auth/devices"]["trust_days"] = body.get("days", 30)
            return 200, r["/api/auth/devices"]
        if (method == "DELETE" and re.fullmatch(r"/api/auth/devices/\d+", path)) or (method == "POST" and path == "/api/auth/devices/forget-all"):
            r["/api/auth/devices"]["devices"] = []
            return 204, None
        if path in ("/api/auth/password", "/api/auth/2fa/setup", "/api/auth/2fa/enable", "/api/auth/2fa/disable",
                    "/api/apikeys", "/api/apikeys/expire", "/api/backup/run", "/api/policy", "/api/ca") or path.startswith("/api/backup/file/"):
            raise DemoError(409, "Local demo: this operation is read-only; no real credentials, files or services are changed")
        raise DemoError(404, "Unknown demo API endpoint")


class DemoServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, dist: Path, fixture: dict):
        self.dist = dist
        self.state = DemoState(fixture)
        super().__init__(("127.0.0.1", port), DemoHandler)


class DemoHandler(BaseHTTPRequestHandler):
    server: DemoServer
    server_version = "NodeRoostLocalDemo/1"

    def send_payload(self, status: int, content: bytes = b"", content_type: str = "application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; frame-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def send_json(self, status: int, body):
        self.send_payload(status, b"" if status == 204 else json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def handle_request(self):
        origin = f"http://127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
            raise DemoError(400, "Use the printed 127.0.0.1 URL")
        if self.headers.get("Origin", origin) != origin:
            raise DemoError(403, "Only the local demo origin is allowed")
        split = urlsplit(self.path)
        path = unquote(split.path)
        if path.startswith("/api/") or path == "/api":
            public = path in ("/api/health", "/api/auth/login-options", "/api/auth/login")
            if not public and self.headers.get("Authorization") != f"Bearer {SESSION}":
                raise DemoError(401, "Sign in to the local demo with admin / demo")
            body = {}
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_BODY:
                raise DemoError(413, "Demo request is too large")
            if length:
                try:
                    body = json.loads(self.rfile.read(length))
                except (ValueError, UnicodeDecodeError):
                    raise DemoError(400, "Expected a JSON object") from None
                if not isinstance(body, dict):
                    raise DemoError(400, "Expected a JSON object")
            with self.server.state.lock:
                status, result = self.server.state.api(self.command, path, parse_qs(split.query), body, origin)
                self.send_json(status, result)
            return
        if self.command not in ("GET", "HEAD"):
            raise DemoError(405, "Only API requests may change demo memory")
        if path == "/capture":
            self.send_payload(200, b"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="robots" content="noindex, nofollow, noarchive">
  <title>NodeRoost screenshot preview</title>
  <style>
    html, body { margin: 0; padding: 0; width: 2560px; height: 1800px; }
    iframe { position: absolute; inset: 0 auto auto 0; border: 0;
      width: 1280px; height: 900px; transform: scale(2); transform-origin: 0 0; }
  </style>
</head>
<body><iframe src="/" title="NodeRoost screenshot preview"></iframe></body>
</html>
""", "text/html; charset=utf-8")
            return
        if path in ("/demo-enrollment.txt", "/demo-package.txt"):
            self.send_payload(200, b"Local demo only. No installation or download is performed.\n", "text/plain; charset=utf-8")
            return
        target = (self.server.dist / path.lstrip("/")).resolve()
        if not target.is_relative_to(self.server.dist):
            raise DemoError(404, "File not found")
        if not target.is_file():
            if "." in Path(path).name:
                raise DemoError(404, "File not found")
            target = self.server.dist / "index.html"
        mime = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}.get(target.suffix)
        self.send_payload(200, target.read_bytes(), mime or mimetypes.guess_type(target.name)[0] or "application/octet-stream")

    def dispatch(self):
        try:
            self.handle_request()
        except DemoError as exc:
            self.send_json(exc.status, {"detail": exc.detail})
        except (KeyError, TypeError, ValueError) as exc:
            self.send_json(422, {"detail": "Invalid demo request: " + type(exc).__name__})
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = dispatch

    def log_message(self, fmt, *args):
        # Do not log bodies or headers: even a preview should not echo passwords.
        print(f"{self.address_string()} {fmt % args}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8765, help="Loopback HTTP port (default: 8765)")
    parser.add_argument("--dist", type=Path, default=HERE.parent / "frontend" / "dist", help="Existing frontend build directory")
    parser.add_argument("--fixtures", type=Path, default=HERE / "demo-fixtures.json", help="Neutral JSON fixtures")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    dist = args.dist.resolve()
    if not (dist / "index.html").is_file():
        parser.error(f"Frontend build not found in {dist}; run npm run build in frontend first")
    fixture = json.loads(args.fixtures.read_text(encoding="utf-8"))
    server = DemoServer(args.port, dist, fixture)
    print(f"NodeRoost local demo: http://127.0.0.1:{args.port}")
    print("Sign in: admin / demo. All changes are temporary and reset on restart.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
