# Changelog

[Русская версия](CHANGELOG.ru.md)

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and the
project follows [semantic versioning](https://semver.org/).

## [0.4.1] — 2026-08-04

### Changed

- **Names inside the network are a section of their own now.** They arrived as a
  card at the bottom of Settings, between the DNS block and the mesh address range
  — technical company for something that is really about how machines on the
  network find each other, and three scrolls away from anyone looking for it. The
  section sits in the top navigation next to Access and Routing, with room for what
  the thing does and an empty state that says what to put there.

## [0.4.0] — 2026-08-04

### Added

- **A name can be switched between leading inside the network and leading out,
  without losing the record.** Handing a name out is the whole of it: once a name
  points at an address on the network, that is where every machine on the network
  goes. The tick in front of each record turns that off — the record stays, the
  nodes stop being told about it, and the name leads where it does for everyone
  else. Switching takes effect in seconds and does not restart headscale. It is
  network-wide by nature: headscale hands DNS to the whole tailnet, not machine by
  machine, and the panel says so rather than pretending otherwise.

### Fixed

- **A service behind Docker never saw the address of the machine that came in
  over the network, and nothing said why.** Tailscale replaces the sender's address
  on traffic it forwards onward — into a container, into the LAN behind the node —
  so a service that decides who may come in by address turned its own people away:
  the connection and TLS went through and the request was cut off, which the browser
  reports as `ERR_HTTP2_PROTOCOL_ERROR` and curl as an empty reply. The install
  guide now names that symptom, the cause and the one command that fixes it.

## [0.3.1] — 2026-08-04

### Fixed

- **The path to the file of names was written under the wrong heading.** ruamel
  appends a new key at the end of its block, and the end of the block is already
  past the comment introducing the next section — so the line read as part of a
  section it has nothing to do with, and editing that section would have carried it
  off. It goes first in the DNS block now. Valid YAML either way; this is about the
  person who opens the file.

## [0.3.0] — 2026-08-04

### Added

- **A name can now lead inside the network.** A service closed behind an address
  allowlist is already reachable over the mesh by its address — but not by its
  name: DNS hands out the public address, so the browser goes around the world and
  is turned away at the front door. The panel now gives the nodes a *name →
  address on the network* record. Public DNS is left alone: from outside the name
  leads where it always did, and only machines on the network see the difference.
  A record points at a node, not at the address that node happens to have today,
  so reconnecting or deleting a machine can never leave a name aimed at a
  stranger. headscale picks the file up without restarting — the very first name
  is the one exception, since its path has to be written into the config once.
  A name that would take the control server itself inside the network is refused:
  that one cannot be undone, as the nodes would learn of the undoing from the very
  server they had just lost.

### Changed

- **A backup now carries the file of names.** Not to keep the names safe — those
  live in the panel's database — but because a restored `config.yaml` pointing at a
  file the host does not have stops headscale from starting at all. Restoring an
  older archive, whose config already knew the path, creates an empty one.

## [0.2.14] — 2026-08-01

### Changed

- **The "agent gone quiet" alert now reads like something a person can act on.** It
  said "the agent on node «24» has been quiet for over 10 min — routes from the panel
  no longer reach it", which means nothing to whoever picks up the phone. It now
  names the server, says it is still online while its agent is not, how long it has
  been quiet, what will not take effect until it returns, what still works, and the
  one command to run on the machine.

## [0.2.13] — 2026-07-31

### Fixed

- **The restore tool could not unpack anything, and had not been able to since
  0.2.5.** The line that extracts the archive was rewritten then to give a damaged
  file a readable message, and its line continuation collapsed into a stray `n`
  argument — so `tar` failed on every archive, sound or not. The check made at the
  time only covered the corrupt-file path, which passed for the wrong reason. A test
  now reads that line back and refuses a collapsed continuation.
- **A machine could not be moved here from another control server.** Tailscale
  refuses to change the login server without `--force-reauth`, so the join command
  died on its own English one-liner — the ordinary case of moving a server between
  panels or in from another tailnet. The script recognises that refusal, says what
  it is doing and asks again with the flag.

## [0.2.12] — 2026-07-31

### Security

- **A backup archive is the network, and nothing said so.** It carries the
  second-factor secret in the clear, the admin's password hash, the node agents'
  tokens, headscale's database and its private keys — enough to generate the 2FA
  codes, work on the hash offline and stand up a control server with that identity.
  The panel, the install guide and SECURITY.md now say it plainly, and archives are
  written `0600` rather than `0644`: the directory is closed, but the file outlives
  the directory once it is downloaded or copied away.

### Fixed

- **Setting a node's routes left no trace in the audit log.** It decides what that
  machine announces to the whole network — a subnet behind it, a way out to the
  internet — and it was the only edit in the panel that went unrecorded. Starting a
  two-factor setup is written down now as well.

## [0.2.11] — 2026-07-31

### Fixed

- **An access rule that could never work was accepted and quietly dropped.** The
  policy builder discards rules aimed at a device, at a role a device wears, or from
  a node to itself — the first two would break the isolation of personal machines,
  the third means nothing. That filter is right and stays; what was wrong is that
  the panel answered 200 and kept the rule in its list, so the administrator saw
  access granted that the network never had. Such a rule is refused now, with the
  reason.

## [0.2.10] — 2026-07-31

### Fixed

- **A hung headscale reported itself as nothing at all.** A control server that has
  stopped answering — rather than crashed — is its most common failure, and httpx
  leaves a timeout with an empty message, so what reached the admin was "headscale
  unavailable: ", a colon with nothing after it. It now says how long it waited.

## [0.2.9] — 2026-07-31

### Fixed

- **The agent switched a node's VPN back on behind its owner.** Its systemd unit
  *wanted* `tailscaled`, so every time the timer fired systemd started the daemon
  again: an admin who stopped Tailscale on their own machine found it running a
  minute later with nothing to explain it. The ordering stays, the starting does
  not, and an agent that finds the daemon stopped now leaves quietly instead of
  failing once a minute.
- **A request field that does not exist was accepted in silence.** pydantic drops
  unknown fields by default, so a typo answered 200 and changed nothing —
  `node_offline_minutes`, a field this panel has never had, was accepted for days
  of testing. Request bodies now name the field they did not recognise.

## [0.2.8] — 2026-07-31

### Added

- **A way back in for a locked-out administrator.** Losing both the password and the
  second factor left no path anyone could find: the break-glass switch has been in
  `.env` since the first release and nothing mentioned it. The troubleshooting
  section walks through it, and SECURITY.md states what it means — whoever can edit
  `.env` owns the panel, so that file and the host under it are the trust boundary.

### Fixed

- **A rejected key was reported as a dead headscale.** With the panel's API key
  expired or revoked, headscale is alive and answering 401: what it needs is a new
  key, not a restart. The panel said "headscale unavailable" either way and sent the
  admin to fix a service that was working.

## [0.2.7] — 2026-07-31

### Fixed

- **A panel upgraded from 0.1.x could not apply DNS changes.** Editing DNS writes
  the config and asks the host helper to restart headscale, and 0.1.x had no such
  helper — so the request sat there while the panel said the restart was under way
  and the change never took effect. `ops/update.sh` installs the helpers wherever
  they are missing, and the panel reports a restart still pending instead of
  claiming one that never happened.
- **A node whose panel record was deleted filled its journal with curl.** Its agent
  kept polling an address answering 404, logging `curl: (22) The requested URL
  returned error: 404` once a minute — a line that says neither what happened nor
  what to do. It now says the panel no longer knows this node and gives the command
  that removes the agent.
- The join script read the machine's own view of itself the instant after
  registering, when it still held the previous name and address, and announced the
  address the node had a second ago.

## [0.2.6] — 2026-07-31

### Changed

- **English is now the repository's primary language.** The reasons, the goals and
  the use cases from the site moved into [docs/why.md](docs/why.md), this changelog
  is in English with the Russian one alongside it in
  [CHANGELOG.ru.md](CHANGELOG.ru.md), and the CI step names people read on the
  Actions tab are no longer Russian.

### Fixed

- **The join command reported success when nothing had happened.** On a machine
  already on the network `tailscale up` returns 0 whatever key it is handed — it does
  not need one — so running the command after its key had expired printed "node
  connected" while the panel gained nothing. The script now reads back the name the
  control server gave and says either that, or that the machine was already here
  under another name.
- **The by-hand install broke on its third command.** It redirects the headscale
  config into a directory a fresh clone does not have. The two-phase start it
  described was fiction as well: bringing up the frontend brings the backend with it.
- Seven strings in the interface still fell back to Russian for an English reader.

## [0.2.5] — 2026-07-31

### Fixed

- **A node whose key had expired was shown as online.** headscale keeps the flag set
  long after it has itself dropped the machine: the node said "Logged out" while the
  panel showed it green, and the "server is down" alert never fired because to it the
  node was still alive. A node with an expired key cannot be connected at all, and
  the panel now says so.
- **Turning off "exit gateway" left the links behind.** Devices kept pointing at it:
  the card still said "goes out through web-fra" while the grant was already gone,
  and a device set to send all its traffic that way went on doing so — into a node
  the policy no longer lets out to the internet, so it simply lost the internet.
  Ticking the box again silently handed the old permissions back.
- **A node's own card did not show a working route as working.** headscale answers a
  single-node request with `subnetRoutes` empty, while the same field is filled in
  the list. The panel now works out what is in force itself: approved *and*
  announced.
- **The panel offered an IPv6 address no rule of its own covers.** Copying it from
  the card and connecting was refused while the panel still showed the access as
  granted. Only the address the rules cover is shown.
- **A failed manual backup answered with a bare "500 Internal Server Error"** — no
  disk space, no directory, no reason. The cause is named now, as it already was in
  the scheduled backup's alert.
- `ops/restore.sh` met a damaged file with gzip's complaint about stdin instead of a
  plain "this does not read as a backup archive". The panel was never at risk — the
  check runs before anything is stopped.

## [0.2.4] — 2026-07-31

### Fixed

- **A node could take over another node's name.** MagicDNS does not distinguish case,
  headscale does: `WEB-FRA` next to an existing `web-fra` passed the uniqueness
  check, after which both names resolved to the *other* machine — addressing a server
  by name quietly reached a different one. A node's name is now a single lowercase
  DNS label, and collisions are checked case-insensitively.
- **Naming DNS servers quietly hijacked DNS on every node.** `override_local_dns` was
  switched on together with the list, so a server stopped seeing the internal names
  its previous resolver knew. It is a separate checkbox now, off by default.
- **A direction could be aimed at an address every machine has of its own.**
  `127.0.0.1` is the machine asking; `169.254.169.254` is the cloud metadata service
  that hands out account credentials — traffic to both was routed through another
  node. Those are refused now, on the address typed in and on whatever a domain
  resolves to later.
- **The panel and the network could disagree for a minute.** Two simultaneous rule
  edits interleaved as save-A, save-B, push-B, push-A: one set listed, another in
  force, until the next self-heal. Saving and pushing now happen under one lock.
- **A hand-written policy was accepted and then vanished.** `PUT /api/policy`
  answered 200 while the self-heal pass put the panel's own version back a minute
  later. The endpoint refuses now and says where the rules live.
- **The code that switched two-factor on still opened the panel** for the rest of its
  half-minute: enabling and disabling 2FA only checked the code, unlike signing in.
- Names with a dot or an underscore, and roles typed in capitals, were accepted by
  the panel and refused by headscale — the admin got a 502 carrying its internals.
- Ports `0`, `70000` and ranges written backwards reached headscale, which then
  rejected the whole policy, along with every later push, while the bad rule sat in
  the panel.

## [0.2.3] — 2026-07-30

### Fixed

- **The panel answered 502 after an update.** nginx resolved the backend's name once
  at startup and held that address: a rebuilt backend came up on a new container IP,
  and the panel — along with the node agents — stayed silent until somebody thought
  to restart the frontend too.
- **Updating did not update.** `git pull && docker compose pull && up -d` ran without
  a single error and left the old build in place: image tags come from
  `NODEROOST_VERSION` in `.env`, which git never touches. `ops/update.sh` carries the
  release version across, builds locally when a registry image is missing, and
  refreshes the host helpers.
- **Reconnecting a node took its access away.** Rules refer to a node id and a
  reconnect issues a new one: the rule stayed in the list and stopped working. Rules,
  routing directions and the agent's own settings now follow the node.
- **A deleted node left traces.** Its rules stayed in the list, reading as working
  access, and its agent record had the panel calling for help about a node that no
  longer existed. Both go with the node now; a rule pointing at a node deleted
  outside the panel is labelled as such.
- **Restoring onto another machine left the panel without headscale.** The archive
  carries headscale's database with its own list of keys, while the panel's key lives
  in `.env` — on a new machine that key was its own, and the restored panel showed
  "headscale: down". `ops/restore.sh` checks the key and issues a new one when
  needed.
- **The panel's watchdog called for help about a healthy panel.** It is installed in
  `/lib65`, outside the application directory, so working the root out from its own
  location led it away from the heartbeat file. The installer writes the real path in
  and now installs the watchdog and its cron entry itself.
- **A machine already on the network joined into nowhere.** headscale recognises it
  by key and creates no second record — the wizard reported success while the list
  did not change. The record is renamed to the name that was asked for, and the
  wizard says whose record it took over.
- The key-expiry alert truncated the remainder: forty minutes before a key died it
  said "in 0 days".
- The scripts in `ops/` shipped without the executable bit, although the guide calls
  them by path.

## [0.2.2] — 2026-07-30

### Fixed

- **Reconnecting a node wiped its settings in the panel.** "Reconnect" creates the
  node again with a new id, while the panel's notes (type, admin flag, description,
  group, "no alerts", exit gateway) are keyed by that id — the machine came back
  blank and the access to it stopped working. Notes are now stashed by name and
  returned once the node is connected.

## [0.2.1] — 2026-07-30

### Fixed

- **Restoring from a backup did not work after a scripted install.** The scripts in
  `ops/` assumed the panel lived in `/app/noderoost` while the installer puts it in
  `/opt/noderoost` — `ops/restore.sh` died on its first line. The application root is
  derived from the script's own location now.
- The panel reported version 0.1.0 on a 0.2.0 install: the version string lives in
  three places and only the image tag had been moved.

## [0.2.0] — 2026-07-30

### Added

- **One-command install** — `ops/install.sh`: installs Docker, generates the secrets,
  brings up the panel, headscale and Caddy with automatic certificates, creates the
  API key, optionally configures ufw and the host helpers. Guides:
  [docs/install.md](docs/install.md) and [docs/install.ru.md](docs/install.ru.md).
- **Caddy included** (`compose.tls.yml` + `deploy/Caddyfile`): Let's Encrypt TLS, an
  address allowlist for the panel, headscale's management API not exposed. An
  external reverse proxy is no longer required.
- **A country flag next to a node's name.** The country is resolved from the node's
  public address (which the panel already read from headscale's database) **offline**,
  against `backend/app/data/geoip.csv.gz` — DB-IP IP-to-Country Lite (CC BY 4.0).
  Refresh with `python ops/build-geoip.py`. RIR data will not do: it gives the country
  of whoever owns the range, not where the address is.
- A downed-server alert now links **straight to that node's card** rather than the
  front page; the icon for it is 🔥.

### Fixed

- **An exit gateway could not be switched off, nor the last role removed**: headscale
  0.29 refuses to strip a node's last tag and the panel answered 502. An empty tag
  list is replaced by a marker tag, which is not shown among the roles.
- **Editing DNS could take the control server down.** Clearing the list of DNS servers
  left `override_local_dns: true`, which headscale will not start with — the container
  went into a restart loop. The flag now follows the state of the list.
- **Joining and reconnecting failed** on a machine where Tailscale had already been
  configured (`--exit-node-allow-lan-access` after using a gateway, for instance):
  `tailscale up` refuses to change settings unless every non-default flag is
  restated. The join scripts pass `--reset`.
- **The "exit gateway" checkbox ignored a node's stored type**: a server marked by
  hand but carrying no tags or routes was auto-detected as a device, and the panel
  refused to make it a gateway.
- Responses to node changes came back without client data — the interface redrew the
  card and lost the OS, the address and the country flag until the list refreshed.

## [0.1.0] — 2026-07-28

First public release.

### Nodes

- Split into servers and personal devices; devices are isolated from one another and
  are never a rule's destination (enforced in the policy engine, not in the UI).
- Joining a node: a single-use pre-auth key and a ready command for Linux, Windows,
  macOS and Android; a local mirror of the Tailscale binaries with sha256
  verification.
- Reconnect keeping the name, grouping by organisation and project, descriptions, and
  "send no alerts" per node.

### Access

- *Who → where → on which port* rules instead of hand-edited HuJSON: granted by
  clicking, to several sources at once or from inside a node's page.
- Roles (headscale tags) as groups of servers; an admin device with access to every
  server.
- The policy is assembled and pushed to headscale automatically and rolled back if it
  is refused; a self-heal pass compares the policy in force with the expected one.

### Network

- Directions — *these nodes reach that address through this node* — with domains
  re-resolved automatically.
- Internet egress through chosen gateways: a per-device set of allowed exit nodes
  (`via` grants) and a forced tunnel for a single node, which stays reachable on its
  public IP.
- An agent on the node (POSIX sh + a systemd timer): applies the routes and the exit
  flag from the panel and reports the hash of what it applied.

### Operations

- Backups: a consistent snapshot of headscale's database and the panel's settings, a
  self-test of the archive, scheduled backups with retention, a restore procedure and
  an offsite copy.
- Monitoring: uptime history, Telegram and webhook alerts (server down, key expiring,
  agent gone quiet), the panel's own self-check and a host watchdog.
- Settings: headscale API keys, DNS/MagicDNS and mesh range editing, a pinned
  Tailscale client version, an audit log and diagnostics.

### Security

- Sign-in: JWT with token versioning, TOTP two-factor with replay protection, attempt
  limits, an audit log; the panel will not start with a weak secret or the default
  password.
- Every tag is owned by an empty group, so no node can apply one to itself.
- A node's class and its tags follow only what the administrator approved.
- Routes and direction addresses are checked for reaching into the mesh and for being
  too wide; approvals are withdrawn along with the intent behind them.
- SSRF protection on alert addresses, anti-traversal on backup downloads, a strict CSP
  and security headers on the frontend.
- Built from a hash-verified dependency lock, with base images pinned by digest.
