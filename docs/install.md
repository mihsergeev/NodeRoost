# Installing NodeRoost

Every step below was carried out on a clean Ubuntu box: the panel was installed,
three machines joined it (two servers and a device), and access rules,
isolation, subnets, an exit gateway, per-destination routing, backups and alerts
were all exercised. This is what was run, not what should work.

[Русская версия](install.ru.md)

---

## What you need

| | |
|---|---|
| A server | Ubuntu or Debian; 1 vCPU and 1 GB of memory is enough. Root (sudo) required. |
| Open ports | **80** and **443** (TCP) for certificates and traffic, **3478** (UDP) for the embedded DERP's STUN, which helps nodes behind NAT connect directly. |
| Two domains | One for the panel, one for the control server. Both must have an A record pointing at this server. |

**No domain?** Use [sslip.io](https://sslip.io): it resolves a name like
`panel.203-0-113-10.sslip.io` to `203.0.113.10` with no registration at all —
that is what this guide was verified on. For a permanent install prefer your own
domain: nodes remember the control server's name, and changing it later is
expensive.

**Why two domains.** headscale serves both the surface nodes connect to (which
must be public) and its management API (which must not be) on the same port. The
panel reaches the API over an internal Docker network, so only the node-facing
surface is exposed.

---

## One command

```bash
curl -fsSL https://raw.githubusercontent.com/mihsergeev/NodeRoost/main/ops/install.sh \
  | sudo bash -s -- \
      --panel-domain panel.example.com \
      --hs-domain hs.example.com
```

It installs Docker, fetches NodeRoost into `/opt/noderoost`, generates the
secrets, brings up the panel, headscale and Caddy, obtains the certificates and
prints your credentials:

```
  NodeRoost is installed.

  Panel:           https://panel.example.com
  Control server:  https://hs.example.com
  Login:           admin
  Password:        npMBKFqLc5aiDVsmuUV8
```

Let's Encrypt issues the certificate on the first request — if the page does not
open straight away, wait half a minute and reload.

The script is **idempotent**: running it again updates the code and the domains
but leaves passwords, the database and issued certificates alone.

### Options

| Option | What it does |
|---|---|
| `--panel-domain DOMAIN` | The panel's name. Required. |
| `--hs-domain DOMAIN` | The control server's name. Required. |
| `--allow-ips "LIST"` | Space-separated addresses allowed into the panel. Open to everyone by default. |
| `--ufw` | Configure the firewall: deny everything except SSH, 80, 443 and 3478/udp. |
| `--dir PATH` | Where to install. `/opt/noderoost` by default. |
| `--version TAG` | Image version. Latest release by default. |
| `--build` | Build the images from source instead of pulling them. |
| `--public-ip ADDRESS` | Public address for the embedded DERP if detection gets it wrong. |

With an address list and the firewall:

```bash
curl -fsSL https://raw.githubusercontent.com/mihsergeev/NodeRoost/main/ops/install.sh \
  | sudo bash -s -- \
      --panel-domain panel.example.com \
      --hs-domain hs.example.com \
      --allow-ips "203.0.113.10 198.51.100.0/24" \
      --ufw
```

---

## Who may reach the panel

By default the panel is open to everyone, so that a first install simply works.
Once you are in and have changed the password, narrow it down: the panel runs
your whole network and has no business being open to the world.

```bash
cd /opt/noderoost
sudo nano .env          # NODEROOST_ALLOW_IPS=203.0.113.10 198.51.100.0/24
sudo docker compose up -d caddy
```

Addresses are space-separated; single addresses and subnets both work. Easy to
verify: from any other address the panel stops answering (the connection is
dropped) while the control server keeps working — nodes are unaffected.

Leaving the variable empty closes the panel to everyone (`127.0.0.1` is
substituted). That is deliberate: a forgotten setting must not publish an admin
panel to the internet.

---

## Firewall (optional, but worth it)

`--ufw` at install time does this for you. By hand:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw route allow 80/tcp
sudo ufw route allow 443/tcp
sudo ufw route allow 3478/udp
sudo ufw enable
```

**About `route allow`.** Ports published by Docker traverse the FORWARD chain,
not INPUT — a plain `ufw allow 443/tcp` does not cover them and the rule simply
never matches. It has to be `ufw route allow`.

You may close 3478/udp, but then nodes behind NAT will fall back to a relay more
often instead of connecting directly, which is slower.

---

## Joining machines

In the panel: **Servers → Add server** (or **Devices → Add device**). Give it a
name, pick the OS, and the panel shows a ready script carrying a single-use key.
Copy it and run it on the machine as root:

```bash
sudo sh -c "the script you copied"
```

It installs the right Tailscale version — from the panel's local mirror first,
falling back to the official site — and joins the machine. It shows up in the
list within seconds; the dialog updates by itself, there is nothing to wait for.

The key is single-use and lives for an hour. The script's first line turns off
shell history so the key never lands there.

**Servers and devices.** This split is the panel's own; headscale has no such
notion. Access can be opened to a server, never to a device — devices cannot see
each other at all. The class is detected automatically (roles, an approved
subnet or gateway mode mean a server) and can be set by hand on the node's card.

**The agent.** Needed if you want the panel to drive what a node advertises:
subnets, exit mode, routing directions. Its install command is on the node's
card under "Agent". Without it the node still works — you just configure routes
on the machine itself.

---

## Next

- **Change the password** and turn on the second factor: ⚙ → Change password, ⚙ → Two-factor.
- **Check access.** Nothing is allowed right after install: until you write a
  rule, nodes cannot see each other.
- **Set up alerts** (⚙ → Alerts): Telegram or a webhook. They fire when a server
  goes down and when a key is about to expire.
- **Backups** run daily on their own (⚙ → Backups). An archive is a consistent
  snapshot of the headscale database and the panel's settings; restoring is
  covered by `ops/restore.sh`. You can restore onto another machine too — the
  script issues the panel a fresh key to the headscale database it just restored.
- **A watchdog** is already in place: `/lib65/noderoost/panel-watchdog.sh` checks
  the panel's pulse every five minutes and, if it goes quiet, raises the alarm
  itself — outside the panel. Nobody else would be left to report its death.

---

## Installing without the script

Nothing magic happens in it — the same steps by hand:

```bash
git clone https://github.com/mihsergeev/NodeRoost.git /opt/noderoost
cd /opt/noderoost
cp .env.example .env
# fill in: domains, NODEROOST_ALLOW_IPS, secrets (openssl rand -hex 32),
# NODEROOST_VERSION, COMPOSE_FILE=compose.yml:compose.tls.yml

mkdir -p data/headscale/config
sed -e "s|^server_url:.*|server_url: https://hs.example.com|" \
    -e "s|^\( *ipv4: *\)[0-9.]*|\1203.0.113.10|" \
    deploy/headscale/config.example.yaml > data/headscale/config/config.yaml

docker compose up -d
# the panel comes up without a headscale key and says so on its health page
docker compose exec headscale headscale apikeys create --expiration 3650d
# put the key into .env → NODEROOST_HEADSCALE_API_KEY
docker compose up -d backend
```

**Your own reverse proxy instead of Caddy.** Drop `compose.tls.yml` from
`COMPOSE_FILE` — the frontend publishes `NODEROOST_BIND` again
(`127.0.0.1:8080` by default), proxy to that. For the control server's domain
reproduce the rules from `deploy/Caddyfile`: `/api/v1*` and `/swagger*` → 404,
`/pkgs/*` and `/agent/*` → the frontend, everything else → `headscale:8080`.
For caddy-docker-proxy there is a ready override: `compose.caddy.yml`.

---

## When something is wrong

**The panel does not open and there is no certificate.** Check that both domains
resolve to this server and that 80/443 are reachable from outside — Let's
Encrypt validates over port 80. Logs: `docker compose logs caddy --tail 30`.

**Locked out: the password, the second factor, or both are gone.** The panel has no
"forgot password" link on purpose — it would be a way in. Recovery goes through the
server, because whoever holds the server holds the panel anyway:

```bash
cd /opt/noderoost
sed -i 's/^NODEROOST_ADMIN_PASSWORD_RESET=.*/NODEROOST_ADMIN_PASSWORD_RESET=1/' .env
sudo docker compose up -d backend      # sets the password back to NODEROOST_ADMIN_PASSWORD
                                       # and switches the second factor off
sed -i 's/^NODEROOST_ADMIN_PASSWORD_RESET=.*/NODEROOST_ADMIN_PASSWORD_RESET=0/' .env
sudo docker compose up -d backend      # put the switch back, or the next restart resets again
```

Sign in with the password from `.env`, change it and turn the second factor on again.
Every session issued earlier stops working, and the reset is written to the log.

**No certificate after several reinstalls.** The caddy log says `too many
certificates (5) already issued for this exact set of identifiers`. Let's Encrypt
allows five certificates per week for the same name; the counter resets on its
own and the error states when. If you cannot wait, install under another name.
Keep the `data/caddy` directory across reinstalls — it holds the certificates
already issued, so nothing has to be issued again.

**The panel refuses the connection from one address and works from another.**
The address list did that; see `NODEROOST_ALLOW_IPS` in `.env`.

**Nodes will not join.** `curl https://hs.example.com/health` must return 200
from anywhere. A 404 means you are hitting the panel's domain, not the control
server's.

**headscale is in a restart loop.** `docker compose logs headscale --tail 5`
gives the reason, usually a hand-edited `config.yaml`. A `.bak` sits next to it.

**A node is listed but its routes are not applied.** Check the agent on the
node's card — it should say "applied". If it says "not installed", install it
with the command shown there.

**The panel cannot see headscale (`headscale: unconfigured`).**
`NODEROOST_HEADSCALE_API_KEY` is empty in `.env`. Create one with
`docker compose exec headscale headscale apikeys create --expiration 3650d`.

---

## Updating and removing

```bash
cd /opt/noderoost && sudo ops/update.sh
```

It fetches the new code, carries the version number into `.env` and brings the
stack up. A plain `git pull` is not enough: image tags come from
`NODEROOST_VERSION` in `.env`, which is yours and git leaves it alone — every
command succeeds and the panel stays on the old build.

Remove everything including the data:

```bash
cd /opt/noderoost && sudo docker compose down -v
sudo rm -rf /opt/noderoost /lib65/noderoost
sudo rm -f /etc/systemd/system/noderoost-hs-* && sudo systemctl daemon-reload
```
