# Why NodeRoost

[Русская версия](why.ru.md) · [Install guide](install.md) · [README](../README.md)

headscale already works. It gives you your own Tailscale coordination server, and
everything below can be done with its CLI and a hand-written `policy.hujson`.
NodeRoost does not replace it and does not carry your traffic. It takes over the
part that is otherwise done by hand.

## What the panel takes over

| Task | With NodeRoost | Without the panel |
| --- | --- | --- |
| Granting access | a *who → where → port* rule, by clicking | edit `policy.hujson` by hand |
| Isolating personal devices | devices never see each other | yours to get right in the ACL |
| A subnet behind a node | a button on the server's card | `approve-routes` by node id in the CLI |
| Traffic to one destination | name the domain — the panel tracks it | pick the CIDR, watch for the IP changing |
| Applying a route on the node | the agent applies it | SSH into every machine |
| Internet egress | every device gets its own list of gateways | the exit node is open to anyone the ACL allows |
| Who is online | history and Telegram alerts | `nodes list` — this moment only |
| Onboarding a machine | a key and a ready command for the OS | make a key in the CLI, dictate the command |
| Where a server sits | a country flag from its public address | remember it yourself or go look it up |
| Backups | a scheduled snapshot, verified | copy the sqlite file yourself |
| A mistake in the rules | rollback to the last working version | edit the file and apply it again |

## One network across any ISP and any NAT

Machines find each other wherever they sit — cloud, office, home, behind someone
else's NAT. No public address and no port forwarding needed, and you decide who
talks to whom.

**Two LANs, no public addresses.** The office and the warehouse start seeing each
other's networks. Put one node on each side, it advertises its subnet, you approve
it — then rules decide who may cross. Neither side needs a static address from its
ISP.

**A work machine behind someone else's NAT.** Carrier-grade NAT, a router you don't
own, nobody to ask for a port forward. Put a node on that machine and it shows up on
the network. You open access to your laptop only, on one port only — no TeamViewer,
no reverse tunnel.

**Admin panels and databases with no open ports.** The database, the IPMI board, the
cluster API and the router's web UI live on tailnet addresses. Nothing is published,
no bastion host needed, and you grant a port rather than the whole machine.

**A contractor gets exactly what they need.** Access is granted to a port, not to the
whole machine: give them 5432 and they see the database and nothing else — no SSH, no
admin panel. It can go to a role — a group of servers: add another one to it and it
shows up for them on its own. Job done — delete their device, and the access is gone
everywhere at once.

**Internet through a chosen server.** Mark a server as a gateway and the traffic of
whoever you allowed leaves for the internet from its address. People switch between
gateways themselves in the Tailscale client, but only ever see the ones you gave
them.

**A server's whole traffic through another server.** The server reaches the internet
through a node you pick, so its requests arrive from that node's address — from the
country a partner expects, say. One tick on the server's card turns it on, another
turns it off. Normally a server vanishes when its traffic is tunnelled: replies to
incoming connections leave through the tunnel and never arrive, so SSH drops and the
site stops answering. Not here — a reply goes back the way the request came, and the
server stays reachable at its own address.

**A service that allows one address only.** The partner allows one IP only, and you
need to reach them from a laptop. In the panel you say: for this address, go through
`edge-1` — the server whose IP is on their list. The requests arrive from `edge-1`,
while the rest of the laptop's traffic still goes direct.

**Home NAS, cameras, Home Assistant.** No public IP, no dynamic DNS, no port
forwarding on the router. Put a node on the home server and reach it from anywhere —
your phone, your laptop, your work machine — exactly as you would from the sofa.

**Server to server.** The machines sit with different providers, in different data
centres, with no network in common: the runner writes to the database, the backup job
ships to storage, the app talks to the next data centre. That used to mean a port open
to the world and an IP allowlist on a firewall that broke on every migration. Now the
machines see each other directly and the access is one rule in the panel.

**Not everything goes into the tunnel.** A normal VPN swallows all your traffic. Here
the laptop reaches the internet directly and fast, and only what you listed goes
through the network: office subnets, specific services, individual addresses.

**The ISP changed — nothing broke.** You moved to another data centre, the provider
handed out a new address, the machine went from the office to someone's home. On the
tailnet it keeps its address and its name, so rules, scripts and bookmarks need no
editing.

**Instead of mailing out VPN configs.** No `.ovpn` attachments. A person gets a
single-use key and one command for their OS — a minute later they are on the network.
Revoking means deleting a device, not reissuing configs for everyone else.

## What makes something a server and something else a device

headscale knows nothing about this — to it every machine is just a node. The split
into servers and devices is ours; it lives in the panel's database. The difference is
one thing: **access can be opened to a server, never to a device.** So servers reach
each other too, while devices cannot see each other at all.

### A server — access can be opened to it

- This is what access gets opened to: a database, a hypervisor, the office router, a
  build machine.
- A server is a source as well: the runner reaches the database, the backup job
  reaches storage, the app reaches the next data centre.
- Servers can be grouped into roles, so you grant the role instead of every machine
  one by one.
- A server can hand out its local network or work as a gateway out to the internet.
- The agent goes on servers — it picks up the routes you set in the panel.

### A device — access is never opened to it

- A laptop, a phone, a work computer — anything with a person sitting at it.
- A device is given access to servers. Giving access to the device itself is not
  possible — that rule cannot be written.
- Devices don't see each other: a colleague's laptop won't appear in the list, and
  typing its address by hand won't work either.
- This is also where you allow exit gateways — which ones the person can switch on.

**The type is derived automatically.** Roles, an approved subnet or gateway mode mean
a server. None of that means a device. You can also set it by hand on the node's
card: server, device, or back to automatic.

**Only what an administrator confirmed is taken into account.** A node can announce
"I route 192.168.0.0/24". If that counted, one command on someone else's laptop would
be enough to make it a "server" — joining "all servers", no longer hidden from other
devices, inheriting everything granted to servers. So only the roles you assigned and
the subnets you approved are used.

## The panel runs the network without traffic passing through it

```
            ┌───────── public ──────────┐      ┌──── allow-listed ────┐
 nodes ───► │ hs.example.com            │      │ panel.example.com    │
            │ headscale, node endpoints │      │ SPA + panel API      │
            │ only (/api/v1 → 404)      │      └──────────┬───────────┘
            └──────────┬────────────────┘                 │
                       │    internal docker network       │
                       └─────────► headscale API ◄────────┘
```

- **Nothing flows through the panel.** It hands out rules and configuration; the
  packets travel between machines on their own over WireGuard. While the panel is
  unavailable, established connections keep working.
- **The agent exists because headscale has no way back.** A control server cannot
  push anything to a node. So the node fetches its own configuration by token,
  applies it and reports what it applied.
- **The policy is rebuilt as a whole.** Rules, roles, gateways and directions turn
  into one HuJSON document. The panel validates and sends it, and if headscale
  refuses it, puts the last working version back.
- **The panel does not mirror the network state.** Who is where and with which
  address stays in headscale. Its own database holds only its own things: grants,
  roles, directions, history.

## Safe by default

Everything below works from the first start — nothing to switch on or configure
separately.

- **Two-factor sign-in and an audit log.** A password plus TOTP, brute-force limits,
  and a record of who changed what. With a default admin password the panel will not
  start at all.
- **Personal devices are protected on their own.** Laptops and phones reach servers
  but never each other. That lives in how the policy is built, not in a checkbox
  somebody can forget to tick.
- **Access is revoked in one move.** Delete a device and it vanishes from every rule
  at once — no reissued configs, no lists to edit.
- **A bad policy will not lock you out.** The panel validates the HuJSON it built,
  applies it, and restores the last working version if headscale refuses it.
- **A hardened perimeter ships with it.** The repository carries a ready
  reverse-proxy example: TLS, an address allowlist, a dedicated network for the panel
  and headscale's management API closed from outside. It deploys as is.
- **Outbound requests are kept in check.** The address the panel sends alerts to is
  validated on save: nothing goes to the internal network or over plain HTTP.
- **The build can be verified.** Dependencies pinned with hashes, base images pinned
  by digest, tests and a dependency audit on every commit. The code is BSD-3
  licensed — everything claimed here can be read.

Deployment notes that matter — network isolation, what is exposed publicly — are in
[SECURITY.md](../SECURITY.md). Read it before putting this on the internet.

## What you need

| | |
| --- | --- |
| headscale | 0.29+, policy mode `database` |
| host | Docker with Compose, 1 vCPU / 1 GB |
| domains | one for the panel, one for the control server |
| proxy | any TLS-terminating one; a `caddy-docker-proxy` example is included |
| images | `ghcr.io/mihsergeev/noderoost-backend` and `-frontend` |

The [install guide](install.md) covers the one-command install, your own domain, the
address allowlist, the firewall, and what to do when something is wrong.
