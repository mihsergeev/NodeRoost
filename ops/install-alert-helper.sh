#!/bin/sh
# Install the host alert sender and the shared outbound address policy.
set -eu
cd "$(dirname "$0")/.."
if ! command -v python3 >/dev/null 2>&1; then
    apt-get update -qq </dev/null
    apt-get install -y -qq python3 </dev/null
fi
target=/lib65/scripts/noderoost
install -d -m755 "$target"
install -m644 backend/app/urlguard.py "$target/urlguard.py"
install -m755 ops/send-host-alert.py "$target/send-host-alert.py"
