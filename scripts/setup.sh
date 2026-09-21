#!/usr/bin/env bash
# Configure and start a self-hosted canvas-viewer-mcp.
#
# Two audiences, one script.
#
#   A person at a terminal runs it with no arguments and answers questions.
#
#   An assistant with a shell -- which is the install path DEPLOY.md is
#   written for -- runs it with flags. It then does everything that is not
#   secret and stops, printing the one command the person has to run
#   themselves. The Canvas token and the connector password are only ever read
#   from a terminal, a file, or the environment, never from the command line,
#   so they do not pass through the assistant's context or the process list.
#
# Everything this writes lives in one directory: .env, secrets/canvas_token,
# and a copy of compose.yaml. Re-running it is safe; existing answers become
# the defaults.

set -euo pipefail

# Anything created below is private from the moment it exists, rather than
# being created world-readable and chmodded a line later.
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
RAW_COMPOSE_URL=https://raw.githubusercontent.com/lennyitb/canvas-viewer-mcp/main/compose.yaml

DIR=$(pwd)
MODE=""
CANVAS_URL=""
PUBLIC_URL=""
PORT=""
TUNNEL_TOKEN_FILE=""
TOKEN_FILE_IN=""
PASSWORD_FILE_IN=""
CREDENTIAL=""          # password | pairing
PREPARE_ONLY=0
START=1
VERIFY=1

# Exit codes an assistant can branch on.
E_USAGE=2
E_NEEDS_HUMAN=3
E_PREFLIGHT=4

die() { printf '\nerror: %s\n' "$*" >&2; exit 1; }
note() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }

usage() {
    cat <<'USAGE'
Usage: setup.sh [options]

Writes .env and secrets/canvas_token, then starts the stack.
With no options, at a terminal, it asks for everything it needs.

  --dir PATH           where to install (default: the current directory)
  --mode MODE          tls | proxy | tunnel | local   (default: asked, or tls)
  --canvas-url URL     your school's Canvas address
  --public-url URL     the public HTTPS URL Claude will reach this server on
                       (tls, proxy); omit for tunnel and local
  --port N             host port to bind (proxy, local; default 8000)

  --credential KIND    password | pairing  (default: password)
                       pairing skips the prompt: the server issues its own
                       code on first run and prints it to the log once.
  --token-file PATH    read the Canvas token from this file rather than asking
  --password-file PATH read the connector password from this file
  --tunnel-token-file PATH  read the Cloudflare tunnel token from this file

  --prepare            do everything except the secrets, then stop and print
                       the command to finish. Implied when stdin is not a
                       terminal, which is how an assistant runs this.
  --no-start           write the configuration but do not start containers
  --no-verify          skip checking the Canvas token against Canvas
  -h, --help           this

Exit codes: 2 bad usage, 3 needs a person at a terminal, 4 preflight failed.
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dir) DIR=${2:?--dir needs a path}; shift 2 ;;
        --mode) MODE=${2:?--mode needs a value}; shift 2 ;;
        --canvas-url) CANVAS_URL=${2:?--canvas-url needs a value}; shift 2 ;;
        --public-url) PUBLIC_URL=${2:?--public-url needs a value}; shift 2 ;;
        --port) PORT=${2:?--port needs a value}; shift 2 ;;
        --credential) CREDENTIAL=${2:?--credential needs a value}; shift 2 ;;
        --token-file) TOKEN_FILE_IN=${2:?--token-file needs a path}; shift 2 ;;
        --password-file) PASSWORD_FILE_IN=${2:?--password-file needs a path}; shift 2 ;;
        --tunnel-token-file) TUNNEL_TOKEN_FILE=${2:?--tunnel-token-file needs a path}; shift 2 ;;
        --prepare) PREPARE_ONLY=1; shift ;;
        --no-start) START=0; shift ;;
        --no-verify) VERIFY=0; shift ;;
        -h|--help) usage; exit 0 ;;
        # A token passed as an argument would be visible in `ps` and in the
        # assistant's own transcript, which is the thing this script exists to
        # avoid. Refuse rather than quietly accept it.
        --token|--password|--tunnel-token)
            printf 'error: %s takes no value here -- secrets are never passed on the command line.\n' "$1" >&2
            printf 'Use %s-file, or run this at a terminal and be asked.\n' "$1" >&2
            exit $E_USAGE ;;
        *) usage >&2; exit $E_USAGE ;;
    esac
done

INTERACTIVE=0
if [ -t 0 ] && [ -t 1 ]; then
    INTERACTIVE=1
else
    # No terminal means no one to ask, which is exactly how an assistant
    # runs this. Do the non-secret half and stop.
    PREPARE_ONLY=1
fi

# ---- preflight ---------------------------------------------------------------

step "Checking this machine"

command -v docker >/dev/null 2>&1 || {
    note "Docker is not installed. Install it first: https://docs.docker.com/engine/install/"
    exit $E_PREFLIGHT
}
docker compose version >/dev/null 2>&1 || {
    note "The docker compose plugin is missing (docker-compose v1 will not do)."
    note "https://docs.docker.com/compose/install/linux/"
    exit $E_PREFLIGHT
}
docker info >/dev/null 2>&1 || {
    note "Docker is installed but this user cannot talk to it."
    note "Either add yourself to the docker group and log back in, or re-run with sudo."
    exit $E_PREFLIGHT
}
note "docker: ok"

mkdir -p "$DIR/secrets"
DIR=$(cd -- "$DIR" && pwd)
ENV_FILE=$DIR/.env
TOKEN_FILE=$DIR/secrets/canvas_token

# Existing answers become defaults, so a re-run is a way to change one thing.
old() {
    [ -f "$ENV_FILE" ] || return 0
    sed -n "s/^$1=//p" "$ENV_FILE" | tail -n1
}

# ---- what kind of deployment -------------------------------------------------

ask() {
    # ask VARNAME "prompt" "default"
    local __var=$1 __prompt=$2 __default=${3:-} __reply=""
    if [ "$INTERACTIVE" = 0 ]; then
        [ -n "$__default" ] || die "--${__var,,} is required when this is not run at a terminal"
        printf -v "$__var" '%s' "$__default"
        return
    fi
    if [ -n "$__default" ]; then
        read -r -p "$__prompt [$__default]: " __reply || true
        [ -n "$__reply" ] || __reply=$__default
    else
        while [ -z "$__reply" ]; do read -r -p "$__prompt: " __reply || true; done
    fi
    printf -v "$__var" '%s' "$__reply"
}

# A re-run should not make anyone choose the same thing twice.
STORED_MODE=$(old COMPOSE_MODE)

if [ -z "$MODE" ]; then
    if [ "$INTERACTIVE" = 1 ]; then
        cat <<'MODES'

How should Claude reach this server?

  1) tls      This machine has a public IP and a DNS name pointing at it,
              and ports 80 and 443 are free. Caddy gets a certificate for
              you. Nothing else to configure.
  2) proxy    You already run nginx, Caddy or Traefik here. The server binds
              127.0.0.1 and you point a vhost at it.
  3) tunnel   No public IP -- home connection, CGNAT, a laptop. A Cloudflare
              tunnel dials out; no ports to forward, no DNS to edit.
  4) local    Just try it on this machine first, over http://localhost.
              Claude in a browser cannot reach this; it is a smoke test.

MODES
        case "$STORED_MODE" in
            tls) mode_default=1 ;; proxy) mode_default=2 ;;
            tunnel) mode_default=3 ;; local) mode_default=4 ;;
            *) mode_default=1 ;;
        esac
        ask MODE_CHOICE "Choose 1-4" "$mode_default"
        case "$MODE_CHOICE" in
            1|tls) MODE=tls ;; 2|proxy) MODE=proxy ;;
            3|tunnel) MODE=tunnel ;; 4|local) MODE=local ;;
            *) die "not one of 1-4: $MODE_CHOICE" ;;
        esac
    else
        MODE=${STORED_MODE:-tls}
    fi
fi
case "$MODE" in tls|proxy|tunnel|local) ;; *) die "unknown --mode $MODE" ;; esac
note "mode: $MODE"

if [ "$MODE" = tls ]; then
    # Caddy cannot get a certificate if something else already answers on 80.
    for p in 80 443; do
        if command -v ss >/dev/null 2>&1 && ss -ltn "sport = :$p" 2>/dev/null | grep -q LISTEN; then
            note ""
            note "Port $p is already in use, and the tls mode needs it for the certificate."
            note "Something else is serving here -- most likely the reverse proxy you"
            note "already run, in which case --mode proxy is the right choice."
            exit $E_PREFLIGHT
        fi
    done
fi

# ---- the values that are not secret ------------------------------------------

step "Configuration"

ask CANVAS_URL "Your school's Canvas address" "${CANVAS_URL:-$(old CANVAS_BASE_URL)}"
# Keep scheme and host, drop any path -- the instruction people can follow is
# "copy the address bar", and that yields a link to a course. The server trims
# this too; doing it here means .env reads back the way it was meant.
[ "${CANVAS_URL#http}" != "$CANVAS_URL" ] || CANVAS_URL="https://$CANVAS_URL"
CANVAS_URL=$(printf '%s' "$CANVAS_URL" | sed -E 's#^(https?://[^/]+).*#\1#')

case "$MODE" in
    tls|proxy)
        ask PUBLIC_URL "The public HTTPS URL Claude will reach this server on" \
            "${PUBLIC_URL:-$(old PUBLIC_BASE_URL)}"
        [ "${PUBLIC_URL#https://}" != "$PUBLIC_URL" ] \
            || die "PUBLIC_BASE_URL must start with https:// -- got $PUBLIC_URL"
        PUBLIC_URL=${PUBLIC_URL%/}
        ;;
    tunnel)
        # Cloudflare owns the hostname, so it is whatever the tunnel's public
        # hostname was set to in the dashboard.
        ask PUBLIC_URL "The public hostname you gave the Cloudflare tunnel (https://...)" \
            "${PUBLIC_URL:-$(old PUBLIC_BASE_URL)}"
        [ "${PUBLIC_URL#https://}" != "$PUBLIC_URL" ] \
            || die "the tunnel hostname must start with https:// -- got $PUBLIC_URL"
        PUBLIC_URL=${PUBLIC_URL%/}
        ;;
    local)
        PORT=${PORT:-8000}
        PUBLIC_URL="http://localhost:$PORT"
        ;;
esac
PORT=${PORT:-8000}

[ -n "$CREDENTIAL" ] || CREDENTIAL=password
case "$CREDENTIAL" in password|pairing) ;; *) die "--credential is password or pairing" ;; esac

# ---- the secrets -------------------------------------------------------------
#
# Read from a file if one was named, from the environment for an automated
# run, and otherwise from the terminal with echo off. An assistant reaches
# none of these paths: it has no terminal, so it stops below instead.

read_secret() {
    # read_secret VARNAME "prompt" [file] [env-var-name]
    local __var=$1 __prompt=$2 __file=${3:-} __envvar=${4:-} __value="" __again=""
    if [ -n "$__file" ]; then
        [ -f "$__file" ] || die "no such file: $__file"
        __value=$(tr -d '\r\n' < "$__file")
    elif [ -n "$__envvar" ] && [ -n "${!__envvar:-}" ]; then
        __value=${!__envvar}
    elif [ "$INTERACTIVE" = 1 ]; then
        while [ -z "$__value" ]; do
            read -r -s -p "$__prompt: " __value; printf '\n'
        done
    fi
    printf -v "$__var" '%s' "$__value"
}

needs_human() {
    cat >&2 <<EOF

This is as far as an automated run can go: what is left is secret, and it is
not going to be typed into anything but a terminal.

Ask the person deploying this to run, in their own shell on this machine:

    $SCRIPT_DIR/setup.sh --dir $DIR

Everything else is already written; it will keep these answers as defaults and
only ask for the $1.
EOF
    exit $E_NEEDS_HUMAN
}

CANVAS_TOKEN_VALUE=""
AUTH_PASSWORD_VALUE=""
TUNNEL_TOKEN_VALUE=""

if [ "$PREPARE_ONLY" = 0 ]; then
    step "Secrets"
    if [ ! -s "$TOKEN_FILE" ] || [ -n "$TOKEN_FILE_IN" ]; then
        note "Canvas → Account → Settings → Approved Integrations → \"+ New Access Token\"."
        note "Leave the expiry blank. Canvas shows it once. Nothing is echoed as you type."
        read_secret CANVAS_TOKEN_VALUE "Canvas token" "$TOKEN_FILE_IN" CANVAS_TOKEN
        [ -n "$CANVAS_TOKEN_VALUE" ] || die "no Canvas token given"
    else
        note "Canvas token: keeping the one already in secrets/canvas_token"
    fi

    if [ "$CREDENTIAL" = password ]; then
        if [ -n "$PASSWORD_FILE_IN" ] || [ -z "$(old AUTH_PASSWORD)" ]; then
            note ""
            note "Now a password for the connector itself -- Claude asks for it once,"
            note "when you connect. At least 12 characters, and not one you reuse."
            while :; do
                read_secret AUTH_PASSWORD_VALUE "Connector password" "$PASSWORD_FILE_IN" AUTH_PASSWORD
                [ ${#AUTH_PASSWORD_VALUE} -ge 12 ] || { note "Too short -- 12 characters minimum."; AUTH_PASSWORD_VALUE=""; continue; }
                if [ -n "$PASSWORD_FILE_IN" ] || [ "$INTERACTIVE" = 0 ]; then break; fi
                read -r -s -p "Again, to be sure: " confirm; printf '\n'
                if [ "$confirm" = "$AUTH_PASSWORD_VALUE" ]; then break; fi
                note "Those did not match."
                AUTH_PASSWORD_VALUE=""
            done
        else
            AUTH_PASSWORD_VALUE=$(old AUTH_PASSWORD)
            note "Connector password: keeping the one already in .env"
        fi
    fi

    if [ "$MODE" = tunnel ]; then
        if [ -n "$TUNNEL_TOKEN_FILE" ] || [ -z "$(old TUNNEL_TOKEN)" ]; then
            note ""
            note "The tunnel token from Cloudflare Zero Trust → Networks → Tunnels."
            note "Point the tunnel's public hostname at http://canvas-viewer-mcp:8000."
            read_secret TUNNEL_TOKEN_VALUE "Cloudflare tunnel token" "$TUNNEL_TOKEN_FILE" TUNNEL_TOKEN
            [ -n "$TUNNEL_TOKEN_VALUE" ] || die "no tunnel token given"
        else
            TUNNEL_TOKEN_VALUE=$(old TUNNEL_TOKEN)
        fi
    fi
fi

# ---- check the token actually works ------------------------------------------
#
# Better to hear "that token is not valid" here than to hear it as every tool
# call failing after the connector is already attached in Claude.

if [ "$VERIFY" = 1 ] && [ -n "$CANVAS_TOKEN_VALUE" ] && command -v curl >/dev/null 2>&1; then
    step "Checking the token against Canvas"
    hdr=$(mktemp); trap 'rm -f "$hdr"' EXIT
    printf 'Authorization: Bearer %s\n' "$CANVAS_TOKEN_VALUE" > "$hdr"
    # The header comes from a file rather than the command line: an argument
    # is visible to every other user on this box for as long as curl runs.
    if who=$(curl -fsS --max-time 20 -H @"$hdr" "$CANVAS_URL/api/v1/users/self" 2>/dev/null); then
        name=$(printf '%s' "$who" | sed -n 's/.*"name":"\([^"]*\)".*/\1/p' | head -n1)
        note "Canvas says hello to ${name:-you}."
    else
        note "Canvas would not accept that token at $CANVAS_URL."
        note "Usual causes: the address is not your school's Canvas, the token was"
        note "truncated on the way in, or it has been revoked."
        if [ "$INTERACTIVE" = 1 ]; then
            ask CONTINUE_ANYWAY "Write it anyway and carry on? (y/N)" "N"
            case "$CONTINUE_ANYWAY" in y|Y|yes) ;; *) exit 1 ;; esac
        else
            exit 1
        fi
    fi
    rm -f "$hdr"; trap - EXIT
fi

# ---- write it out ------------------------------------------------------------

step "Writing $DIR"

if [ ! -f "$DIR/compose.yaml" ]; then
    if [ -f "$REPO_DIR/compose.yaml" ]; then
        cp "$REPO_DIR/compose.yaml" "$DIR/compose.yaml"
    else
        curl -fsSL "$RAW_COMPOSE_URL" -o "$DIR/compose.yaml" \
            || die "could not fetch compose.yaml from $RAW_COMPOSE_URL"
    fi
    chmod 644 "$DIR/compose.yaml"
    note "compose.yaml: written"
fi

if [ -n "$CANVAS_TOKEN_VALUE" ]; then
    # 600 before a byte of it exists, and replaced atomically so a reader
    # never sees a half-written token.
    tmp=$(mktemp "$DIR/secrets/.token.XXXXXX")
    printf '%s' "$CANVAS_TOKEN_VALUE" > "$tmp"
    chmod 600 "$tmp"
    mv "$tmp" "$TOKEN_FILE"
    note "secrets/canvas_token: written (600)"
elif [ ! -e "$TOKEN_FILE" ]; then
    # compose bind-mounts this path and refuses to start a container when it
    # does not exist, so it has to be there even while it is still empty.
    : > "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE"
fi

tmp=$(mktemp "$DIR/.env.XXXXXX")
{
    echo "# Written by setup.sh -- re-run it to change any of this."
    echo "# Keep this file to yourself; it names a token file and may hold a password."
    echo
    echo "CANVAS_BASE_URL=$CANVAS_URL"
    echo "PUBLIC_BASE_URL=$PUBLIC_URL"
    echo "CANVAS_TOKEN_HOST_FILE=$TOKEN_FILE"
    case "$MODE" in local|proxy) echo "BIND_PORT=$PORT" ;; esac
    if [ "$CREDENTIAL" = password ] && [ -n "$AUTH_PASSWORD_VALUE" ]; then
        echo "AUTH_PASSWORD=$AUTH_PASSWORD_VALUE"
    else
        echo "# No AUTH_PASSWORD: the server issues a pairing code on first run and"
        echo "# prints it once to \`docker compose logs\`."
    fi
    if [ -n "$TUNNEL_TOKEN_VALUE" ]; then echo "TUNNEL_TOKEN=$TUNNEL_TOKEN_VALUE"; fi
    echo "COMPOSE_MODE=$MODE"
} > "$tmp"
chmod 600 "$tmp"
mv "$tmp" "$ENV_FILE"
note ".env: written (600)"

if [ "$PREPARE_ONLY" = 1 ]; then
    needs_human "Canvas token and the connector password"
fi

# ---- start -------------------------------------------------------------------

profile_args=()
case "$MODE" in
    tls) profile_args=(--profile tls) ;;
    tunnel) profile_args=(--profile tunnel) ;;
esac

profile_note=""
if [ ${#profile_args[@]} -gt 0 ]; then profile_note=" ${profile_args[*]}"; fi

if [ "$START" = 0 ]; then
    step "Not starting (--no-start)"
    note "When you are ready:  cd $DIR && docker compose$profile_note up -d"
    exit 0
fi

step "Starting"
cd "$DIR"
docker compose ${profile_args[@]+"${profile_args[@]}"} up -d

printf 'Waiting for the server to answer'
ok=0
for _ in $(seq 1 60); do
    if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then ok=1; break; fi
    printf '.'; sleep 1
done
printf '\n'

if [ "$ok" = 0 ]; then
    note ""
    note "It did not come up. What the container said:"
    docker compose ${profile_args[@]+"${profile_args[@]}"} logs --tail 40 canvas-viewer-mcp || true
    exit 1
fi
note "health: ok"

# ---- what to do next ---------------------------------------------------------

step "Done"
cat <<EOF

Add this in claude.ai (a browser, not the mobile apps):

    Settings → Connectors → Add custom connector

    $PUBLIC_URL

EOF

if [ "$CREDENTIAL" = pairing ] || [ -z "$AUTH_PASSWORD_VALUE" ]; then
    cat <<'EOF'
The login is a pairing code the server issued itself. It is in the log, printed
exactly once:

    docker compose logs canvas-viewer-mcp | grep -A2 "pairing code"

EOF
else
    note "The login is the password you just chose."
    note ""
fi

case "$MODE" in
    proxy)
        cat <<EOF
Still to do: point your reverse proxy at 127.0.0.1:$PORT, for the host in
$PUBLIC_URL. DEPLOY.md step 5 has the nginx and Caddy stanzas -- the
buffering settings there are not optional, MCP streams its responses.
EOF
        ;;
    tls)
        cat <<EOF
Caddy is getting a certificate for $PUBLIC_URL now. If the DNS record does not
yet point here, that will keep failing until it does:

    docker compose --profile tls logs caddy
EOF
        ;;
    tunnel)
        cat <<EOF
Check the tunnel came up healthy in Cloudflare Zero Trust → Networks → Tunnels,
and that its public hostname routes to http://canvas-viewer-mcp:8000.
EOF
        ;;
    local)
        cat <<EOF
This is a local smoke test: claude.ai cannot reach http://localhost. When you
are satisfied it works, re-run this script and choose tls, proxy or tunnel.
EOF
        ;;
esac

note ""
note "The skills are a separate install -- see README.md, step 3."
