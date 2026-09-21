#!/usr/bin/env bash
# Behavioural tests for scripts/setup.sh.
#
# Static analysis cannot see the class of bug this covers (and a comment
# here must not open with the linter's own name, or it reads as a directive). "Re-run it to change one
# thing" is the documented way to change anything, so every value setup.sh
# writes has to survive a re-run that does not mention it. A built-in default
# that quietly outranks a stored answer does not fail loudly -- it moves a
# deployment that was working, and the next `docker compose up` collides with
# whatever already holds the default.
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
SETUP=$REPO/scripts/setup.sh
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

fail=0
check() { # check LABEL EXPECTED ACTUAL
    if [ "$2" = "$3" ]; then
        printf 'ok   %s\n' "$1"
    else
        printf 'FAIL %s\n       expected: %s\n       actual:   %s\n' "$1" "$2" "$3"
        fail=1
    fi
}

# With no terminal, setup.sh writes everything that is not secret and stops at
# exit 3. That is the path an assistant takes and the one under test here;
# any other exit code means it failed for an unrelated reason.
prepare() {
    local dir=$1; shift
    local rc=0
    "$SETUP" --dir "$dir" --mode proxy \
        --canvas-url https://example.instructure.com \
        --public-url https://mcp.example.com "$@" >"$WORK/log" 2>&1 || rc=$?
    [ "$rc" = 3 ] || { printf 'setup.sh exited %s, expected 3\n' "$rc"; cat "$WORK/log"; exit 1; }
}

value() { sed -n "s/^$2=//p" "$1/.env" | tail -n1; }

D=$WORK/deploy

prepare "$D" --port 8787
check "--port is written"              8787 "$(value "$D" BIND_PORT)"

prepare "$D"
check "a re-run keeps the port"        8787 "$(value "$D" BIND_PORT)"

prepare "$D" --port 9999
check "--port still overrides"         9999 "$(value "$D" BIND_PORT)"

# The answers that were already preserved, so a future change cannot drop one
# without a test noticing.
check "canvas url survives a re-run"   https://example.instructure.com "$(value "$D" CANVAS_BASE_URL)"
check "public url survives a re-run"   https://mcp.example.com         "$(value "$D" PUBLIC_BASE_URL)"
check "mode survives a re-run"         proxy                           "$(value "$D" COMPOSE_MODE)"

exit $fail
