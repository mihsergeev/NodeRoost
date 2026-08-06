# Security Policy

NodeRoost manages a headscale control server (VPN mesh), so security matters.

## Reporting a vulnerability

Please report security issues privately via GitHub Security Advisories
("Report a vulnerability" on the repository's Security tab) rather than opening a
public issue. We aim to acknowledge reports within a few days.

## Hardening notes

**A backup archive is the network.** `data/backups/*.tar.gz` carries the
second-factor secret in the clear, the admin's password hash, the node agents'
tokens, headscale's database and its private keys — noise and DERP. Anyone holding
the file can generate your 2FA codes, work on the password hash offline and stand up
a control server with your identity. The panel writes archives `0600` inside a `0700`
directory; keep them somewhere you would keep a private key. `ops/backup-offsite.sh`
ships them into a restic repository, which is encrypted — mail attachments, object
storage without encryption and ticket systems are not.

**The agent installs nothing the panel could have written.** The agent applies a
fixed set of commands to what the panel asks for — routes, exit node, certificate
files — so a compromised panel can rearrange a node's networking, which is bad
enough, but it cannot run code there. Updating the agent itself is the one thing
that would, and it is signed: an administrator asks for the update from the panel,
and the node installs the new script only if it verifies against the public key
baked in when a human first installed the agent, matches the sha256 in the signed
manifest, and carries a release number higher than the one it runs. The private key
lives on the maintainer's machine and never touches the panel's host, so a panel in
the wrong hands can withhold updates or offer an old signed one — the anti-rollback
check refuses that — but cannot forge a new one. The same reasoning is why the
post-certificate reload hook is a file the node's own administrator writes.

**The panel's own CA is as trusted as the panel.** Names issued by it are trusted by
every device where its root is installed, and that root's private key sits in the
panel's database — so a panel in the wrong hands can mint a certificate for any
internal name and, combined with the DNS records it hands out, impersonate an internal
service to your own machines. That is the price of not needing a public domain; where
it is too high, use Let's Encrypt instead, whose every issuance lands in public
certificate-transparency logs. The certificates' own private keys are not affected
either way: those are generated on the nodes and never sent to the panel.

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
