# Headscale reverse DNS backport

This optional image applies the IPv4 reverse-zone fix from upstream
[PR #3482](https://github.com/juanfont/headscale/pull/3482) to Headscale v0.29.7.
Octet-aligned pools, such as `172.16.0.0/16`, produce one reverse DNS route
instead of 256. Non-aligned prefixes retain their exact reverse-zone coverage.

The patch includes the IPv4 function and regression tests from commits
[`3fa0dcc`](https://github.com/juanfont/headscale/commit/3fa0dcc5725b8a9faf8ddfc896cb3736b7e888f6),
[`3a78e65`](https://github.com/juanfont/headscale/commit/3a78e652189ad7bdf51b28fb4e573ce3ff93c472),
and [`985467b`](https://github.com/juanfont/headscale/commit/985467bf5ec413240112d7ee4f5c6955f337be15).
It preserves explicit reverse routes, including compatibility with older
supported clients, and leaves the IPv6 algorithm unchanged.

Build from the repository root, using this directory as the build context:

```sh
docker build --progress=plain -t noderoost-headscale:dns-20261115 deploy/headscale
docker run --rm noderoost-headscale:dns-20261115 version -o json
```

The build checks the upstream revision, applies the patch, runs the complete
`hscontrol/util` test package, and compiles a static binary with VCS metadata.
Go derives the version from the v0.29.7 tag and marks the patched tree dirty,
so the binary identifies itself separately from the unmodified release.

The final image inherits the pinned official v0.29.7 runtime and replaces only
`/ko-app/headscale`. Its CA certificates, user, entrypoint, and environment stay
inherited. Source, Git metadata, build tools, and dependencies remain in the
builder stage. This directory does not change the deployment or panel version.

Before deployment, verify MagicDNS and PTR queries through both the OS resolver
and `100.100.100.100`, every configured split DNS resolver, unchanged mesh
identities and routes, and successful domain installation by systemd-resolved.
