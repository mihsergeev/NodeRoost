# Changelog

[Русская версия](CHANGELOG.ru.md)

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and the
project follows [semantic versioning](https://semver.org/).

## [0.11.0] — 2026-08-06

### Changed

- **The root is constrained by exclusion rather than permission — and a new project
  now needs nothing.** The permit-list is baked into the root, so every new project
  meant issuing a new root and visiting every laptop and phone with it. That is
  exactly the work a panel exists to remove. It is the other way round now: every
  top-level domain that really exists on the internet (the IANA root zone, 1438 of
  them, `backend/app/data/public_tlds.txt`) and every IP address are forbidden to the
  root; everything else is free. A new project's domain does not exist on the
  internet, so it is not forbidden, and `grafana.newproj` gets its certificate
  immediately, with nothing done to the root.
  The protection did not weaken — it got more honest: a compromised panel still
  cannot mint a certificate for a bank or a mail service, and a leaf for
  `www.google.com` is rejected with "excluded subtree violation" by every client that
  enforces constraints (checked with `openssl verify`). The flip side is stated
  plainly: a name in a REAL domain (`nas.example.com`) will not be signed either.
  The list is refreshed with `python ops/build-tlds.py`. Roots from earlier versions
  keep working with their permit-list, and the panel offers to issue a new one.
- **The root no longer travels inside the certificate file.** It weighs a couple of
  dozen kilobytes (it carries the whole list of forbidden domains), and there is no
  point sending that in every handshake: a client that has the root installed does
  not need it in the chain.
- **Only the lifetime is left in the card.** The domains field is gone — the panel
  does not need to be told about them. In its place: "Signs: any internal name except
  1438 public domains", and a name in a real domain is flagged in advance.

## [0.10.0] — 2026-08-06

### Changed

- **The domains are yours to invent, and the panel now asks to add them.** A name
  like `loki.mirabah` or `portainer-dev.bironex` could always be created, but it
  silently got no certificate: the root may sign only the domains it lists, and
  adding one means issuing a new root — something you had to guess by opening
  "Configure". The panel now works the domains out itself and, as soon as a ticked
  name falls outside them, says in the DNS section which domain is not allowed and
  what follows from that, with a button that adds it to the list. Any domain works:
  there is no public DNS involved here, so whether the domain is "real" is beside
  the point.
- **The root lives 20 years instead of 10.** It is installed by hand on every
  laptop and phone; ten years was not security but a promise to walk around all the
  machines again.

## [0.9.1] — 2026-08-06

### Fixed

- **The collector cycle had been dying halfway through since 0.5.1.** It called
  `certs.forget` without importing it, so every pass raised `NameError` on that
  line. Metrics, node-down and key-expiry alerts and the internal-name file came
  before it and worked; everything after it never ran at all: auto-approving routes
  requested in the panel, pruning spent one-time keys, and ACL self-healing (a
  policy rebuild when the set of nodes changed outside the panel). From outside it
  did not look like breakage, just like those things not happening. Found while
  shipping this release — the traceback had been in the log every minute with
  nobody reading it.
  The test now holds the whole pass rather than its individual pieces: remove the
  import and it fails.

## [0.9.0] — 2026-08-06

### Changed

- **Let's Encrypt is gone; certificates come from the panel's own CA only.** It
  arrived here as the familiar answer to "what about https", but for a name that
  does not and should not exist in public DNS a public CA cannot work by
  construction: getting a certificate meant publishing a wildcard record, keeping
  port 80 open and handing the name to CT logs — publishing exactly what you set out
  not to publish. The own CA needs none of that, and keeping both paths around for
  the sake of the unused one doubles the settings and the explanations. Removed: the
  ACME client, the per-name issuer choice, `NODEROOST_CERT_DOMAIN` and its block in
  the reverse proxy, the public `/.well-known/acme-challenge/` endpoint, the
  after-failure pause (it existed for LE's limits) and the `retry_after` column.
- **The root's validity and domains are set in the panel.** The lifetime used to be
  hardcoded (10 years) and the domains changed through a button you had to guess
  existed. The "Root certificate" card now has "Configure": domains as a
  comma-separated list and the lifetime in years (1–30), both applied by one reissue.
- **The root card stopped being a wall of text.** Three paragraphs that showed
  neither the domains nor where to change them collapsed into four lines: the
  auto-install tick, the domains, the expiry and the fingerprint. The per-OS install
  instructions moved under a disclosure, and the tick itself now sits on one line —
  the global label style used to stretch the checkbox to full width, leaving it
  hanging in the middle of the card for no reason.

## [0.8.0] — 2026-08-06

### Added

- **The panel hands out its CA root itself, and that root is constrained to your
  zones** (agent release 5). A certificate for an internal name solved half the
  problem: the name opened over https, and every browser and every `curl` complained
  until the root was installed by hand — on every machine, including the ones joined
  five minutes ago. Now a server receives the root inside its enrollment script, and a
  node running the agent puts it into the system trust store and keeps the right one
  there: the state carries the fingerprint, the node fetches the file by it and checks
  what it got. Clearing "install the root on nodes automatically" removes it again, and
  so does removing the agent — trust switched off has to disappear, not merely stop
  being refreshed.
  Handing every machine a root that can sign anything would have been worse than the
  disease: whoever took the panel could mint a certificate for any public domain and
  our own machines would believe it. So the root carries X.509 name constraints — it
  may sign only the listed zones (`mesh`, `lan`, `int.example.com`) and never an IP
  address. The first zone comes from the first name; the list changes with "Reissue the
  root", which is also what you press for a name in a new zone. Reissuing is named for
  what it is: the panel reorders the names' certificates itself, but on laptops and
  phones the old root has to be replaced by hand.
  Roots created earlier carry no constraints — they are worth reissuing.

## [0.7.1] — 2026-08-06

### Security

- **A certificate name from the panel could have escaped its directory** (agent
  release 4). The agent wrote `/etc/noderoost/certs/<name>.crt` with the name taken
  from the panel and checked by nobody on the node — so a panel in the wrong hands
  could have sent `../../ssl/certs/ca-certificates` and had the agent overwrite the
  machine's trust store as root, which hands over the machine. That contradicted the
  rule the rest of the agent follows: what the panel says is applied, never trusted.
  The node now accepts only what can be a DNS name — no slashes, no `..`, nothing
  outside `a-z0-9.-`.
- **A CSR was read into memory whole, however large it was.** The body arrives from
  another machine; a node (or whoever reached its token) could have sent a gigabyte
  and spent the panel's memory on one request. It is capped at 16 KB — a CSR is two.
- **Repeated requests could burn the Let's Encrypt weekly limit for the whole
  domain,** taking other names down with them: every CSR started a fresh order, even
  with a valid certificate already issued. While one is valid and not due for
  renewal, the panel returns it instead of ordering again. A CSR whose signature does
  not check out is refused on the spot rather than sent upstream.

## [0.7.0] — 2026-08-06

### Added

- **The panel can be its own certificate authority.** Let's Encrypt only signs names
  that exist in public DNS, and publishes every one of them in the transparency logs —
  so a service nobody outside should know about is exactly the thing it cannot serve.
  Each name now picks its issuer: Let's Encrypt as before, or the panel's own CA. The
  latter asks for no domain, no DNS record, no open port and no internet at all; the
  name can be `nas.mesh` or `router.lan`, issuing is instant, and nothing about it
  leaves the network. The root is created with the first such name; the panel hands it
  out as a file and shows its fingerprint, because installing a root certificate you
  cannot check is not much better than not checking certificates at all.
  The certificate's private key still never leaves the node — the panel signs the CSR
  it is sent, exactly as it does with Let's Encrypt — and renewal is the same
  machinery, a month before expiry. What the CA does cost is stated plainly in
  SECURITY.md: its own key lives in the panel's database, so whoever holds the panel
  can mint a certificate for any internal name. That is the trade for not needing a
  public domain, and where it is too steep, Let's Encrypt remains one dropdown away.

## [0.6.2] — 2026-08-06

### Fixed

- **An update was cleared before it could happen** (agent release 3). The request
  travelled in the state the agent compares and hashes, so it counted as a change:
  the agent applied the state, reported that it had, and the panel took the report as
  proof the update was done and dropped the request — while the node still ran the
  old release. Found by pressing the button on a live node and watching nothing
  happen. The request is now outside the compared state, the agent reports its
  release number alongside, and the panel clears the request only when that number
  reaches the one it asked for. A refused update no longer retries every minute
  either — the reason it was refused does not disappear on its own.

## [0.6.1] — 2026-08-06

### Changed

- **The certificate hook is told which names changed** (agent release 2). It used to
  be called with nothing at all, so the only thing a node could do on a renewal was
  reload everything that might be involved — work and risk out of proportion to one
  renewed name.

## [0.6.0] — 2026-08-06

### Added

- **Agent releases are signed, and a node installs nothing else.** Updating the
  agent means running a script as root on somebody's machine, so the question is not
  whether it is convenient but who is allowed to author it. The answer now is: not
  the panel. A release is signed on the maintainer's machine with a key that never
  touches the panel's host; the panel only hands out the manifest, the signature and
  the script. The node verifies the signature against the public key baked into it
  when a human first ran the install, checks the sha256 of what it received, and
  refuses any release not newer than its own — so a panel in the wrong hands can
  withhold an update or offer an old signed one, but cannot write one.
  The update itself is asked for by a person: the node's card says the agent is from
  an earlier release and offers a button. Shipping a new panel no longer runs
  anything anywhere on its own, and the `NODEROOST_AGENT_SELF_UPDATE` switch from the
  previous release is gone — there is nothing left for it to guard.
  `agent-signing/` carries the tooling: `keygen.py` for the key pair, `protect_key.py`
  to close it with a passphrase, `release.py` to sign the current script. It signs
  the very text the panel serves, taken from the panel's own code rather than a
  second copy, because two copies of one script drift apart and then the signed one
  is not the one that runs.

## [0.5.2] — 2026-08-06

### Security

- **Agent self-update is off unless you switch it on.** Shipped a release earlier the
  same day, it quietly moved a boundary: until then the agent only ever ran a fixed
  set of commands, so a compromised panel could rearrange a node's networking but not
  run code on it. Self-update makes the panel able to hand every node a script that
  runs as root within a minute — which is exactly the reason the certificate reload
  hook is a file the node's own administrator writes rather than a command from the
  panel. It now takes `NODEROOST_AGENT_SELF_UPDATE=1` in the panel's `.env` — not the
  database, so the panel cannot grant itself the privilege. Off, the panel still says
  a node's agent is out of date and offers the command; a human runs it.

## [0.5.1] — 2026-08-06

### Added

- **The agent updates itself, and the panel says when it has not.** The agent is a
  script that lives on the node, so a machine set up in July knew exactly what July
  knew — a name given a certificate in the panel simply sat there, because that
  node's agent had never heard of certificates. Nothing said so: it looked like
  "ticked it, nothing happened". The state now carries the version of the script the
  panel would install, an agent whose own version differs reinstalls itself (once an
  hour at most, so a version that refuses to match cannot turn into a loop), and the
  version travels back with the applied-state report — so the panel can say plainly
  that a node runs an agent from an earlier release, and offer the one command that
  fixes it.

### Fixed

- **A certificate outlived the name it belonged to.** Names are dropped along with a
  deleted node, not only by editing the list, and the certificate row stayed behind —
  showing the administrator something nothing refers to any more.

## [0.5.0] — 2026-08-04

### Added

- **The panel issues certificates for names inside the network.** A name that leads
  inside is only half the way to opening a service over https: the certificate is the
  other half, and the usual road to one runs into the name not facing the world —
  Let's Encrypt has nowhere to come and check. The panel closes that: one DNS record,
  once (`*.int.example.com` at the panel's address), a tick next to the name, and a
  minute later the service has a real certificate that renews itself. No DNS-provider
  API keys, no plugins built into the proxy — the way most guides tell you to do it —
  so it works the same for whoever's DNS hosting has no API at all.
  **The private key is generated on the node and never leaves it:** only a request to
  sign travels up, so the panel has nothing to lose if it is broken into. The agent
  writes the files and runs a hook the node's own admin left there — the panel sends
  no commands to execute.
  A node can only ask for names the panel assigned to it; without that check, whoever
  owned any node could issue certificates for other people's names on the network.
  A failed attempt waits fifteen minutes before the next one: Let's Encrypt counts
  five failed checks an hour per name, and an agent asking every minute would burn
  that in five.

## [0.4.2] — 2026-08-04

### Changed

- **The section is called DNS, and everything about DNS lives in it.** Naming it
  after the names alone left the panel with two places about DNS — a section for
  names and a card in Settings for the resolvers and MagicDNS — with nothing to
  say which held what. MagicDNS, the base domain and the resolvers moved in as the
  second block of the section, below the names; Settings keeps the API keys, the
  Tailscale client, DERP and the mesh address range.
- **The list of names reads as a table.** Rows were a wrapping flex line — every
  row a different width, the address landing somewhere new each time. Columns line
  up now, under headings, with the arrow from a name to its node, the mesh address
  as a chip, and a plain × instead of a "Remove" button. A switched-off name says
  where it leads instead of showing an address it no longer hands out.

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
