# NodeRoost

A self-hosted web panel for [headscale](https://github.com/juanfont/headscale) —
the open-source implementation of the Tailscale coordination server.

Your own Tailscale control server, with a real admin panel. Add a machine, grant
access, send traffic through a chosen node — no editing `policy.hujson`, no SSH
into nodes. The panel does not replace headscale and does not proxy traffic: it
takes over the part that is otherwise done by hand.

[Русская версия](README.ru.md) · [Why and what for](docs/why.md) · [Changelog](CHANGELOG.md) · [noderoost.ru](https://noderoost.ru)

![Servers](docs/screenshots/servers.png)

<table>
<tr>
<td width="50%"><a href="docs/screenshots/access.png"><img src="docs/screenshots/access.png" alt="Access"></a><br><sub>Access — who reaches which server, on which port</sub></td>
<td width="50%"><a href="docs/screenshots/routing.png"><img src="docs/screenshots/routing.png" alt="Routing"></a><br><sub>Routing — who goes where, through which node</sub></td>
</tr>
<tr>
<td width="50%"><a href="docs/screenshots/node.png"><img src="docs/screenshots/node.png" alt="Server detail"></a><br><sub>Server detail — roles, subnets, exit gateway, agent</sub></td>
<td width="50%"><a href="docs/screenshots/login.png"><img src="docs/screenshots/login.png" alt="Sign-in"></a><br><sub>Sign-in - password and optional 2FA</sub></td>
</tr>
<tr>
<td width="50%"><a href="docs/screenshots/dns.png"><img src="docs/screenshots/dns.png" alt="DNS and certificates"></a><br><sub>DNS - internal names and certificates</sub></td>
<td width="50%"><a href="docs/screenshots/settings.png"><img src="docs/screenshots/settings.png" alt="Settings"></a><br><sub>Settings - network and client binary mirror</sub></td>
</tr>
</table>

<sub>Screenshots show the running React interface with demonstration data.
Public endpoints use documentation ranges (RFC 5737); this is not a live tailnet.</sub>

---

## Why a panel when headscale already works

The panel does not replace headscale and does not carry your traffic. It takes over
the part that is otherwise done by hand: granting access becomes a *who → where → on
which port* rule instead of an edit to `policy.hujson`; a subnet behind a node is a
button instead of `approve-routes` by node id; a route is applied by an agent instead
of an SSH session on every machine.

What people build with it — two office LANs seeing each other without a public address
anywhere, a work machine reachable from behind someone else's NAT, a database open to
a contractor on one port and nothing else, a laptop leaving for the internet through a
server whose address a partner allows. **[Reasons, goals and the full set of
scenarios → docs/why.md](docs/why.md)**

## Use cases

The diagrams use example names and subnets. Select a diagram to open it at full size.

<table>
<tr>
<td width="50%"><strong>Two LANs, no public addresses</strong><br><a href="docs/diagrams/en/lan-to-lan.svg"><img src="docs/diagrams/en/lan-to-lan.svg" alt="Office and warehouse networks connected through two nodes" width="420"></a><p>Connect an office and a warehouse through one node on each side. Approve their subnets and decide who may reach them with access rules.</p></td>
<td width="50%"><strong>A work machine behind someone else's NAT</strong><br><a href="docs/diagrams/en/behind-nat.svg"><img src="docs/diagrams/en/behind-nat.svg" alt="A laptop reaching a work machine behind NAT" width="420"></a><p>Reach a work computer without changing its router or forwarding a port. Grant access to your laptop on the port you need.</p></td>
</tr>
<tr>
<td width="50%"><strong>Admin panels and databases with no open ports</strong><br><a href="docs/diagrams/en/private-services.svg"><img src="docs/diagrams/en/private-services.svg" alt="A device reaching private database and cluster ports while internet access is blocked" width="420"></a><p>Keep databases, IPMI and cluster APIs on network addresses. Open only the required ports to approved sources.</p></td>
<td width="50%"><strong>A contractor gets exactly what they need</strong><br><a href="docs/diagrams/en/contractor-access.svg"><img src="docs/diagrams/en/contractor-access.svg" alt="A contractor reaching only the database ports of a server role" width="420"></a><p>Grant a database port to a contractor, including all servers in a role. Delete their device when the work is done.</p></td>
</tr>
<tr>
<td width="50%"><strong>Internet through a chosen server</strong><br><a href="docs/diagrams/en/device-exit.svg"><img src="docs/diagrams/en/device-exit.svg" alt="A device reaching the internet through an allowed exit gateway" width="420"></a><p>Allow specific exit gateways. People choose among those gateways in their Tailscale client.</p></td>
<td width="50%"><strong>A server's whole traffic through another server</strong><br><a href="docs/diagrams/en/server-exit.svg"><img src="docs/diagrams/en/server-exit.svg" alt="A server using an exit gateway while keeping its incoming access path" width="420"></a><p>Send a server's outgoing traffic through a chosen node. Replies to incoming connections keep their original path.</p></td>
</tr>
<tr>
<td width="50%"><strong>A service that allows one address only</strong><br><a href="docs/diagrams/en/ip-allowlist.svg"><img src="docs/diagrams/en/ip-allowlist.svg" alt="A laptop reaching an IP allowlisted service through a selected gateway" width="420"></a><p>Reach a partner's service through a server whose IP is allowed. Other destinations still use the laptop's direct connection.</p></td>
<td width="50%"><strong>Home NAS, cameras, Home Assistant</strong><br><a href="docs/diagrams/en/home-network.svg"><img src="docs/diagrams/en/home-network.svg" alt="A phone reaching services in a home network" width="420"></a><p>Reach home services from your phone or laptop without a public address, dynamic DNS or port forwarding.</p></td>
</tr>
<tr>
<td width="50%"><strong>Server to server</strong><br><a href="docs/diagrams/en/server-to-server.svg"><img src="docs/diagrams/en/server-to-server.svg" alt="Servers at different providers connected by an access rule" width="420"></a><p>Let a runner reach a database, or a backup job reach storage at another provider. Access follows a rule instead of a public IP allowlist.</p></td>
<td width="50%"><strong>Not everything goes into the tunnel</strong><br><a href="docs/diagrams/en/split-tunnel.svg"><img src="docs/diagrams/en/split-tunnel.svg" alt="Selected destinations using the tunnel while other traffic goes directly to the internet" width="420"></a><p>Route only the subnets, domains or addresses you choose through the network. Keep other internet traffic direct.</p></td>
</tr>
<tr>
<td width="50%"><strong>The ISP changed - nothing broke</strong><br><a href="docs/diagrams/en/provider-change.svg"><img src="docs/diagrams/en/provider-change.svg" alt="A node keeping its network identity after its public address changes" width="420"></a><p>A node keeps its network address and name when its provider or location changes. Rules and bookmarks keep working.</p></td>
<td width="50%"><strong>Instead of mailing out VPN configs</strong><br><a href="docs/diagrams/en/enrollment.svg"><img src="docs/diagrams/en/enrollment.svg" alt="A new device joining the network with a single-use key" width="420"></a><p>Connect a machine with a single-use key and a command for its OS. Revoke access by removing the device.</p></td>
</tr>
<tr>
<td width="50%"><strong>SSH closed to the internet</strong><br><a href="docs/diagrams/en/private-ssh.svg"><img src="docs/diagrams/en/private-ssh.svg" alt="SSH reachable through the private network while the public port is closed" width="420"></a><p>Close SSH on the public interface and connect through the network address, with access controlled by a rule.</p></td>
<td width="50%"><strong>A way in when the usual one is shut</strong><br><a href="docs/diagrams/en/recovery-access.svg"><img src="docs/diagrams/en/recovery-access.svg" alt="An alternate network path to a machine whose public access path is unavailable" width="420"></a><p>A node with outbound connectivity can provide another way in when its public address or usual VPN path is unavailable.</p></td>
</tr>
<tr>
<td colspan="2"><strong>Hardware that takes no client</strong><br><a href="docs/diagrams/en/subnet-router.svg"><img src="docs/diagrams/en/subnet-router.svg" alt="A subnet router providing access to cameras, IPMI and other equipment without a client" width="420"></a><p>Put a node beside cameras, printers, IPMI or an old NAS and approve its subnet. Grant access to that equipment by destination and port.</p></td>
</tr>
</table>

## What it does

**Servers and devices.** Nodes split into servers (things you connect *to*) and
personal devices. Devices are isolated from one another and can never be a rule's
target — enforced in the policy engine, not in the UI, because a rule can also
arrive through the API.

The grouped list stays beside the selected node's details, so you can move
between machines without leaving the overview. Search and online/offline filters
work across groups. On a phone, selecting a node opens its details with a back
button to the list.

**Grants, not an ACL file.** A rule is *who → where → on which port*. Grant it by
clicking, to several nodes at once, or from inside a node's own page. The HuJSON is
generated, pushed, and rolled back if headscale refuses it.

**Roles.** A role is a group of servers (a headscale tag). Grant access to the
role; adding a server to it later needs no rule changes.

![A node's page](docs/screenshots/node.png)

**Routing.** Directions — *these nodes reach that address through this node*. Give a
hostname and the panel resolves it, keeps the route in sync when the site moves, and
refuses addresses that would hijack traffic inside the mesh.

**Names inside the network.** A service behind an address allowlist — or with no
ports open to the world at all — is reached by the name it already has: the panel
hands the nodes a *name → address on the network* record. Public DNS is left alone,
so from outside the name leads where it always did. The address is not remembered
but taken from the node, so a node reconnecting does not break the record.

**Certificates for those names.** Each name has a "certificate" tick: the panel
signs it with its own root while the key is generated on the node itself and never
leaves it — only a request to sign goes up. No domain, no DNS record, no open port
and no internet: the name can be anything, `nas.mesh` included. The panel hands the
root out to the nodes itself, and that root can only sign invented internal domains:
every real one is forbidden to it, so the panel cannot impersonate someone else's
site to your machines — and a new project needs no reissuing. Renewal takes care of
itself.

**Internet egress through gateways.** Mark a server as an exit gateway and pick
which devices may use it. In the Tailscale tray each user sees only the exits you
allowed — the choice is theirs, the set is yours. A node's whole outbound traffic can
also be forced through a gateway while the node stays reachable on its public IP.

**An agent on the node.** headscale can approve routes but has no channel to *tell* a
node what to advertise, so NodeRoost ships a small POSIX-sh agent: a systemd timer
pulls the desired state, applies it with `tailscale set`, and reports the hash of
what it actually applied.

**Where a server sits.** A country flag next to the node name, resolved from the
node's public address **offline** against a table in the repository: no need to
send every address in your fleet to a geo service for the sake of a flag, and no
internet required. The node's name plays no part in it.

**Backups, monitoring, alerts.** Consistent snapshots of the headscale database and
panel settings with a self-test and a rehearsed restore; uptime history; Telegram or
webhook alerts for a downed server or an expiring key; and an independent host
watchdog for when the panel itself goes quiet.

## Security

The panel runs a VPN network, so the guarantees live in code and are covered by
tests:

- devices never reach each other, and a device is never a rule's destination —
  not by picking it from a list, not by typing its tailnet address by hand;
- a node cannot promote itself: its class and its tags follow only what the
  administrator approved, never what the node announces about itself;
- tags are owned by an empty group, so no node can apply one to itself;
- sign-in is JWT + optional TOTP with attempt limits and an audit log; the panel
  refuses to start with a weak secret or the default password;
- releases build from a hash-verified dependency lock, with base images pinned by
  digest.

Deployment notes that matter — network isolation, what is exposed publicly — are in
[SECURITY.md](SECURITY.md). Read it before putting this on the internet.

## Requirements

- Docker with Compose
- a reverse proxy terminating TLS (the compose overlay ships
  [caddy-docker-proxy](https://github.com/lucaslorentz/caddy-docker-proxy) labels;
  give NodeRoost a network of its own — see SECURITY.md)
- two DNS names: one for the panel, one public one for the control server

## Quick start

**Full guide:** [docs/install.md](docs/install.md) — one-command install, your own
domain, the address allowlist, the firewall and what to do when something is wrong.

```bash
git clone https://github.com/mihsergeev/NodeRoost.git
cd NodeRoost
cp .env.example .env
```

Fill in `.env` — at minimum a random `NODEROOST_JWT_SECRET` (`openssl rand -hex 32`),
a strong `NODEROOST_ADMIN_PASSWORD`, `NODEROOST_DB_PASSWORD`, your two domains and
the addresses allowed to reach the panel. Then put the headscale config in place:

```bash
mkdir -p data/headscale/config
cp deploy/headscale/config.example.yaml data/headscale/config/config.yaml
$EDITOR data/headscale/config/config.yaml   # server_url, base_domain, prefixes
docker compose up -d
```

The backend needs a headscale API key, which can only be created once headscale is
running:

```bash
docker compose exec headscale headscale apikeys create --expiration 3650d
# put it in .env as NODEROOST_HEADSCALE_API_KEY, then:
docker compose up -d backend
```

Sign in with the admin credentials from `.env` and add your first node — the panel
hands you a one-time key and a ready-made command for Linux, Windows, macOS or
Android.

## Architecture

![Control through NodeRoost and headscale, with traffic flowing between nodes](docs/diagrams/en/architecture.svg)

Settings go through the panel. User traffic travels between nodes over WireGuard.
DERP relays it when a direct connection cannot be established.

```
            ┌───────── public ──────────┐      ┌──── allow-listed ────┐
 nodes ───► │ hs.example.com            │      │ panel.example.com    │
            │ headscale, node endpoints │      │ SPA + panel API      │
            │ only (/api/v1 → 404)      │      └──────────┬───────────┘
            └──────────┬────────────────┘                 │
                       │    internal docker network       │
                       └─────────► headscale API ◄────────┘
```

The panel reaches headscale's management API only over the internal network; on the
public vhost `/api/v1` and `/swagger` return 404, leaving the node-facing endpoints.
Panel state lives in Postgres, headscale keeps its own SQLite.

## Development

```bash
cd backend  && python -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pytest
cd frontend && npm install && npm run dev
```

The country flag next to a node is resolved from its public address **offline** — none of your fleet's addresses leave the host. The table (`backend/app/data/geoip.csv.gz`, DB-IP IP-to-Country Lite, CC BY 4.0) ships in the repository; refresh it every few months:

```bash
python ops/build-geoip.py
```

## License

BSD-3-Clause — see [LICENSE](LICENSE).
