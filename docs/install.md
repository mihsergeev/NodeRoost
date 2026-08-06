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
- **Treat a backup as a private key.** The archive carries the second-factor
  secret, the admin's password hash, the agent tokens and headscale's own private
  keys — see [SECURITY.md](../SECURITY.md).
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

## Updating the agent on nodes

The agent is a small script that lives on the node. A new panel release does not
change it by itself: a person asks for the update, and the node installs **only what
is signed**.

On the node's card (Routes) the panel says the agent is from an earlier release and
offers **Update the agent**. The node then:

1. fetches the release manifest and its signature;
2. verifies the signature with the **public key baked into it at install time** — the
   one the panel had when a human started the installation;
3. checks the sha256 of the script it received against the signed manifest;
4. refuses a release no newer than its own (anti-rollback);
5. fills in its own values — the panel address and the verification key come from
   itself, not from the file it was sent — and reinstalls.

The private signing key **is not on the panel's host**. A panel in the wrong hands can
withhold updates or offer an old signed release (the rollback check refuses it), but
cannot hand out a script of its own.

**Running your own build?** Issue your own key pair:

```bash
python agent-signing/keygen.py            # once; the private key stays out of git
python agent-signing/protect_key.py       # close it with a passphrase
python agent-signing/release.py 2         # sign the current script (number only goes up)
```

`release.py` signs exactly the script text the panel hands to nodes and puts the
manifest and signature into the panel's image. Forget to sign, and the panel offers no
button and says plainly that the release is unsigned.

## Certificates for names inside the network

A service inside the network wants to open over `https://` without browser
warnings. A public certificate authority cannot help here by construction: it signs
only names visible from the internet and publishes every one of them in CT logs —
that is, it asks you to expose exactly what you did not want exposed.

So the panel issues them itself: it has its own root, and that root needs no
domain, no DNS record, no open port and no internet. The name can be anything
(`nas.mesh`, `router.lan`), issuing is instant and there are no rate limits.

In the **DNS** section every name that points at a node has a "certificate" tick.
Tick it and the service has its papers a minute later:

1. The key is generated **on the node** (`openssl` needed) and never leaves it:
   only the signing request (CSR) travels up.
2. The panel signs it with its root and answers with the chain.
3. The agent writes `/etc/noderoost/certs/<name>.crt` and `.key` and, if you put a
   `/lib65/noderoost-agent/cert-hook.sh` next to it, runs that — to reload nginx,
   caddy or a container. The panel sends no commands to execute: what to restart is
   decided on the node.
4. A month before expiry the whole thing repeats on its own.

### The root: domains, validity, installing it

The panel creates the root with the first such name and **hands it out on its
own**: a server joined with the enrollment script gets it while it joins, and a
node running the agent puts it into the system trust store and keeps the right one
there (the state carries the fingerprint, and the node fetches the file by it).
Clear "install the root on nodes automatically" in the DNS section and the nodes
remove it again — trust switched off has to disappear, not merely stop being
refreshed.

What is left by hand are the machines nobody joins with a script — laptops and
phones. The panel hands you the file and shows the fingerprint to check it against:

| System | Where |
|---|---|
| Windows | "Trusted Root Certification Authorities" in the **Local Machine** store |
| macOS | Keychain → "System", then set "Always Trust" |
| Linux | `/usr/local/share/ca-certificates/`, then `update-ca-certificates` |
| Android / iOS | install the profile and switch on full trust in settings |

**The domains are yours to invent, and there is nothing to add.** The root is
constrained by EXCLUSION rather than permission: every top-level domain that really
exists on the internet is forbidden to it (the IANA root zone — 1438 of them at the
time of issue), as is every IP address. Everything else is free, which means made-up
domains: `mesh`, `bironex`, `mirabah`.

Hence the important property: **a new project works immediately**. A `newproj`
appears tomorrow — create `grafana.newproj` and the certificate is issued; no
reissuing the root, no walking around with it. A permit-list would do the opposite:
every new domain would mean a new root and a trip to every machine.

The flip side is that a name in a real domain (`nas.example.com`) will not be
signed, and that is protection rather than a defect: otherwise whoever took the
panel could mint a certificate for a bank or a mail service and your machines would
believe it. You can check it from outside:

```
openssl verify -CAfile noderoost-ca.crt leaf.pem
# for www.google.com → error 48: excluded subtree violation
```

The list of forbidden domains is refreshed with `python ops/build-tlds.py` and goes
into the root when it is issued; in an already issued root the constraints are baked
in.

**The lifetime is set with "Configure"** — 20 years by default, up to 30. That is
the only reason worth issuing a new root: the old one stops working afterwards,
nodes pick the new one up within a minute, and laptops and phones have to be visited
by hand.

That CA's private key lives in the panel's database and travels in its backup:
whoever holds the panel can mint a certificate for any **internal** name. For a network the panel already governs that is acceptable; if it is not,
switch the automatic install off and place the root only where you want it.

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

**A name inside the network resolves, but the site will not open:
`ERR_HTTP2_PROTOCOL_ERROR` (`Empty reply from server` in curl).** That is what a
service that decides for itself who may come in — by the client's address — looks
like from outside: the connection and TLS go through, the request is cut off. If it
lives in Docker on that node, the address from the network never reaches it:
Tailscale by default replaces the sender's address on traffic it forwards onward
(into a container, into the LAN behind the node), so the service sees the docker
bridge and does not recognise its own. On the node:

```bash
sudo iptables -t nat -S ts-postrouting     # a MASQUERADE on mark 0x40000 — that's it
sudo tailscale set --snat-subnet-routes=false   # stop replacing the address
```

The setting lives in Tailscale itself and survives restarts, but `tailscale up
--reset` clears it — that is, joining the node again through the panel. Repeat the
command after that.

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
