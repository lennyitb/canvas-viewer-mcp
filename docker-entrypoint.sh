#!/bin/sh
# Take ownership of what the app needs, then stop being root.
#
# Two things only become knowable once the container starts, which is why
# neither can be settled in the Dockerfile:
#
#   The data volume. A Docker named volume is seeded from the image and
#   inherits the ownership set at build time; a bind mount -- which is what
#   Railway and most platforms actually use -- is not, and arrives owned by
#   root whatever the image said. A process running as uid 10001 then cannot
#   create its database. The difference is invisible until you deploy.
#
#   The mounted secret. compose mounts the Canvas token read-only with the
#   host's ownership, and the documented way to prepare it leaves it readable
#   only by root.
#
# Root is used for exactly these two fixups and dropped before any application
# code runs.
set -e

DB_PATH="${DB_PATH:-/data/auth.sqlite}"
DATA_DIR=$(dirname "$DB_PATH")

if [ "$(id -u)" != "0" ]; then
    # Already unprivileged: nothing to drop, and nothing we could chown.
    exec "$@"
fi

mkdir -p "$DATA_DIR"
# The directory only, not -R: the database is already owned correctly once it
# exists, and may be large.
chown canvas:canvas "$DATA_DIR" || echo "warning: could not chown $DATA_DIR" >&2

# A token the app user cannot read is copied somewhere it can, rather than
# chowned: the mount is read-only, so chown would fail. The copy is mode 600
# and owned by canvas, and it stays a file rather than becoming an environment
# variable -- which is the whole reason the token is mounted in the first
# place, since the environment shows up in a process listing.
TOKEN_FILE="${CANVAS_TOKEN_FILE:-}"
if [ -n "$TOKEN_FILE" ] && [ -f "$TOKEN_FILE" ] && ! gosu canvas test -r "$TOKEN_FILE"; then
    PRIVATE_TOKEN=/tmp/canvas_token
    # Created 600 and root-owned, written, and only then handed over. Doing it
    # in that order matters twice: the file is never world-readable for even an
    # instant, and root cannot write to a file it has already given away --
    # DAC_OVERRIDE is not always there to fall back on, and is not something to
    # depend on anyway.
    install -m 600 /dev/null "$PRIVATE_TOKEN"
    cat "$TOKEN_FILE" > "$PRIVATE_TOKEN"
    chown canvas:canvas "$PRIVATE_TOKEN"
    CANVAS_TOKEN_FILE="$PRIVATE_TOKEN"
    export CANVAS_TOKEN_FILE
fi

# exec so the server is PID 1 and receives the platform's stop signal
# directly, instead of a shell swallowing it and forcing a hard kill.
exec gosu canvas "$@"
