# Security Policy

NodeRoost manages a headscale control server (VPN mesh), so security matters.

## Reporting a vulnerability

Please report security issues privately via GitHub Security Advisories
("Report a vulnerability" on the repository's Security tab) rather than opening a
public issue. We aim to acknowledge reports within a few days.

## Hardening notes

**Whoever can edit `.env` owns the panel.** `NODEROOST_ADMIN_PASSWORD_RESET=1` puts
the admin password back to the one in `.env` and switches the second factor off at
the next start — that is the recovery path for a locked-out administrator, and the
reason `.env` and the host it sits on are the real trust boundary. Keep the file
readable by root only, and treat shell access to that host as equivalent to full
access to the network the panel governs.



- The panel refuses to start with a weak/empty `NODEROOST_JWT_SECRET` or a
  default admin password.
- Login is protected by JWT + optional TOTP 2FA, brute-force rate limiting, and
  an audit log.
- The panel is meant to sit behind an IP allow-list (Caddy) and HTTPS.
- headscale's management API (`/api/v1`, `/swagger`) is blocked on the public
  headscale vhost; only the node-facing endpoints are exposed publicly. It still
  requires its Bearer API key, which is what actually guards it — the edge block
  is defence in depth.

## Network isolation (read this before deploying)

`NODEROOST_CADDY_NETWORK` names the Docker network the reverse proxy uses to
reach NodeRoost. **Give NodeRoost a network of its own and attach the proxy to
it. Do not reuse one shared network for unrelated apps.**

Everything on that network can talk to the panel's frontend container directly,
which means it reaches the panel API *without passing the IP allow-list* — that
list is enforced by the proxy, and a container next to it is already past the
proxy. The same goes for headscale's ports on that network: its metrics are
unauthenticated, and its management API answers there even though the edge
returns 404 for it. So a compromise of any unrelated container on a shared
network turns into direct reach of the panel's login endpoint and of headscale's
internals. We verified this on a real deployment; it is not theoretical.

Keeping the network dedicated means the only things on it are the proxy and
NodeRoost, and the allow-list is again the single front door. As a second layer,
`metrics_listen_addr` in the headscale config binds to `127.0.0.1` so metrics
never leave the container.

Concretely, with caddy-docker-proxy:

    docker network create noderoost-edge

then list that network in the proxy's `CADDY_INGRESS_NETWORKS` **and** attach the
proxy to it (`deploy/caddy-proxy.example.yml` shows both), and set
`NODEROOST_CADDY_NETWORK=noderoost-edge`. The proxy only discovers containers on
the networks it is told about, so missing either half leaves the vhosts with no
upstream (503). Verify afterwards from a container on the old shared network:
the panel's hostname should no longer resolve at all.
