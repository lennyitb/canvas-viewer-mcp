# Deploying canvas-viewer-mcp

Running your own copy on a machine you control. There are three ways to reach
it from the internet, and one command that sets up any of them.

If you would rather not run a machine at all, the one-click Railway install in
[README.md](README.md#install) deploys the same container with no DNS, no
proxy and no shell. Nothing here is a prerequisite for it.

---

## For an assistant with a shell on this machine

This is the intended path: someone says *"deploy this server on this machine"*
to Claude Code or a similar agent, and the agent works through this section.
If that is you, read to the end of it before running anything.

**You will not handle the Canvas token.** It grants full access to the user's
Canvas account, and it is not going to pass through your context, your argv,
or this machine's process list. `scripts/setup.sh` collects it from a terminal
with echo off. When it detects it is not attached to one — which is how it
sees you — it writes everything that is not secret, stops, and prints the
single command for the user to run themselves. That is expected, not a
failure.

```bash
git clone https://github.com/lennyitb/canvas-viewer-mcp
cd canvas-viewer-mcp
./scripts/setup.sh --help
```

### 1. Decide the mode

Ask the user only what you cannot determine yourself.

| Mode | When | What you need from the user |
| --- | --- | --- |
| `tls` | The machine has a public IP, a DNS name already points at it, and ports 80 and 443 are free. Caddy obtains the certificate. | the hostname |
| `proxy` | nginx, Caddy or Traefik already runs here. The container binds `127.0.0.1` and you add a vhost. | the hostname |
| `tunnel` | No public IP — a home connection, CGNAT, a laptop. A Cloudflare tunnel dials out. | a tunnel created in Cloudflare, and its hostname |
| `local` | Nothing is set up yet and you want to prove the container works first. `http://localhost:8000`, no TLS, not reachable by Claude. | nothing |

Things worth checking before you ask:

```bash
ss -ltnp | grep -E ':(80|443)\s'          # something already serving? -> proxy
curl -s https://ifconfig.me; echo         # public IP...
dig +short <the hostname they gave>       # ...and does DNS point at it?
```

`local` first is a reasonable move on an unfamiliar machine: it needs no
secrets of consequence and proves Docker, the image and the volume all work
before DNS is involved.

### 2. Prepare

```bash
./scripts/setup.sh \
  --dir /srv/canvas-viewer-mcp \
  --mode tls \
  --canvas-url https://yourschool.instructure.com \
  --public-url https://canvas-viewer-mcp.example.com
```

`--canvas-url` tolerates a pasted course link; everything after the host is
trimmed. Omit `--public-url` for `local`.

It checks Docker, checks the ports the mode needs, writes `.env` and
`compose.yaml` into `--dir`, and exits **3** with the command for the user.
Other exit codes: **4** preflight failed (read the message — it says what is
missing), **2** bad usage.

### 3. Hand over

Tell the user to run, in their own terminal on this machine:

```bash
/path/to/canvas-viewer-mcp/scripts/setup.sh --dir /srv/canvas-viewer-mcp
```

It keeps everything you already answered as defaults, asks for the Canvas
token and a connector password with echo off, checks the token against Canvas
before writing it, starts the containers, waits for health, and prints the
connector URL. If they would rather not choose a password, `--credential
pairing` has the server issue itself a one-time code instead and print it to
the log.

### 4. Finish up

After they report it is running, you can verify it from here without seeing
any secret:

```bash
cd /srv/canvas-viewer-mcp
docker compose ps
curl -s http://127.0.0.1:8000/health
```

Then, depending on mode: for `proxy`, add the vhost from step 5 below; for
`tls`, check `docker compose --profile tls logs caddy` shows a certificate was
obtained; for `tunnel`, confirm the tunnel is healthy in Cloudflare.

The last step is the user's: add the connector in claude.ai, and install the
skills. Both are in [README.md](README.md#2-connect-claude-to-it).

---

## Doing it by hand

Everything the wizard does, in case you would rather do it yourself or need to
understand what it wrote. Replace `canvas-viewer-mcp.example.com` with your own
hostname and `yourschool.instructure.com` with your institution's Canvas host.
Nothing is baked into the repository: `compose.yaml` takes every
deployment-specific value from `.env` and refuses to start rather than guess.

### 1. DNS

Only for `tls` and `proxy`. One record, pointing at the public IP of the
machine:

```
canvas-viewer-mcp.example.com.  A  <your public IP>
```

If the record is proxied through Cloudflare (orange cloud), set SSL/TLS mode to
**Full (strict)** so the origin certificate is actually verified. Do not use
Flexible: it re-encrypts to your origin over plain HTTP, and the OAuth bearer
tokens this server issues would cross that leg in the clear.

The `tunnel` mode needs no record — Cloudflare creates the hostname with the
tunnel. The `local` mode needs no DNS at all.

### 2. The Canvas token

One secret, and it does not belong in the repo or in shell history. The
container mounts it as a file.

```bash
install -d -m 700 /srv/canvas-viewer-mcp/secrets
install -m 600 /dev/null /srv/canvas-viewer-mcp/secrets/canvas_token
read -rs CANVAS_TOKEN && printf '%s' "$CANVAS_TOKEN" > /srv/canvas-viewer-mcp/secrets/canvas_token
```

`read -rs` keeps it out of your history and off your screen. Leave the file
owned by root if you like: the server runs as an unprivileged user inside the
container and cannot read a root-owned `600` file, so the entrypoint copies it
at start to a private path that user owns, still `600` and still a file rather
than an environment variable. Before v0.2.2 this combination failed outright
with `permission denied`.

If you would rather not manage a file, put `CANVAS_TOKEN=` in `.env` instead —
it wins over the mount. The mount source still has to exist for compose to
start a container at all, so `touch secrets/canvas_token` first.

There is no second secret to prepare. The connector login is gated by a pairing
code the server issues itself on first run and prints once to its logs; step 4
picks it up. To choose your own instead, set one of these in step 3:

- `AUTH_PASSWORD` — a plaintext password, at least 12 characters, hashed at
  startup. It then sits in `.env` beside the path to the Canvas token, which is
  the stronger credential of the two.
- `AUTH_PASSWORD_HASH` — an argon2 hash from `canvas-probe hash-password`, if
  you would rather the plaintext were never stored at all.

Setting either one deletes a pairing code already issued, which is how a code
that reached your logs gets revoked.

### 3. Environment

`/srv/canvas-viewer-mcp/.env` carries everything that differs between
deployments.

```
CANVAS_BASE_URL=https://yourschool.instructure.com
PUBLIC_BASE_URL=https://canvas-viewer-mcp.example.com
CANVAS_TOKEN_HOST_FILE=/srv/canvas-viewer-mcp/secrets/canvas_token

# Optional, pick at most one. Neither means a pairing code is issued instead.
# AUTH_PASSWORD=a-password-you-chose
# AUTH_PASSWORD_HASH='$argon2id$v=19$...'

# tunnel mode only, from Cloudflare Zero Trust -> Networks -> Tunnels.
# TUNNEL_TOKEN=...
```

The first two are required: `compose.yaml` fails to parse without them, naming
the one that is missing. If you do set `AUTH_PASSWORD_HASH`, quote it — it
contains `$`, which an unquoted value will mangle.

`CANVAS_TOKEN_HOST_FILE` is a path **on the host**. The variable inside the
container is `CANVAS_TOKEN_FILE`, it means `/run/secrets/canvas_token`, and
`compose.yaml` pins it so the two cannot be confused. An older `.env` that
spells the host path `CANVAS_TOKEN_FILE` still works; the name changed because
one spelling meaning two different paths is a trap.

`CANVAS_BASE_URL` tolerates a pasted course link; everything after the host is
trimmed.

`PUBLIC_BASE_URL` is the one to get right. The server advertises OAuth metadata
built from it, so a wrong value sends Claude to authorize against whatever host
it names — which means someone else's server, if you copied their value. On
Railway, Render or Fly it can be left out entirely because the platform
publishes its own hostname and the server reads that; here there is no platform
to ask, so it is required.

For a first run with nothing else set up, `PUBLIC_BASE_URL=http://localhost:8000`
is accepted — `http` and a local address are allowed for exactly this case. The
server comes up fully and you can `curl` it. Claude cannot reach it, so this is
a smoke test rather than an install.

### 4. Run

Write `.env` before fetching `compose.yaml`: with values missing, every compose
subcommand refuses to parse the file, `logs` and `pull` included.

```bash
cd /srv/canvas-viewer-mcp
curl -O https://raw.githubusercontent.com/lennyitb/canvas-viewer-mcp/main/compose.yaml

docker compose up -d                    # proxy, or local
docker compose --profile tls up -d      # Caddy in front, automatic HTTPS
docker compose --profile tunnel up -d   # Cloudflare tunnel
docker compose logs
```

The profile you choose is the one you have to keep passing: `docker compose
logs` without `--profile tls` will not show you Caddy.

Without a profile the container listens on `127.0.0.1:8000` and never on a
public interface. `BIND_ADDR` and `BIND_PORT` in `.env` move it if 8000 is
taken.

Unless you set a password, the log carries the pairing code:

```
  No password is set, so this server issued itself a
  pairing code. Use it as the password when Claude sends you to
  the login page.

      pairing code:   NY5D-FFF5-NYRN-KJ5T
      connector URL:  https://canvas-viewer-mcp.example.com
```

Keep it for step 7. It is printed once and stored only as an argon2 hash, so
nothing can read it back out; `canvas-probe reset-pairing` followed by a restart
issues a new one. It survives restarts and upgrades along with the rest of the
OAuth state in the `/data` volume.

Worth knowing what you are trading: a code in a log file is less protected than
a hash in a file only root reads, and it follows wherever you ship logs. If that
matters to you, set `AUTH_PASSWORD` or `AUTH_PASSWORD_HASH` and the code is
never issued.

### 5. Reverse proxy

Only for the `proxy` mode — the `tls` profile runs Caddy for you, and the
`tunnel` profile has no origin port at all.

Point the vhost at `127.0.0.1:8000`. Caddy:

```caddy
canvas-viewer-mcp.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

nginx:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;

    # MCP streams responses. Buffering makes tool calls appear to hang.
    proxy_buffering off;
    proxy_cache off;

    # nginx sends `Connection: close` upstream by default, which closes a
    # server-sent event stream the moment it is opened. Clearing it is the
    # other half of the SSE recipe, and its absence looks identical to
    # buffering: calls that hang and then time out.
    proxy_set_header Connection '';

    proxy_read_timeout 300s;
}
```

Caddy needs none of this: it streams server-sent events without buffering by
default, which is why the `tls` profile uses it.

### 6. Verify before touching Claude

```bash
curl -s https://canvas-viewer-mcp.example.com/health
curl -s https://canvas-viewer-mcp.example.com/.well-known/oauth-authorization-server | jq .

# Must be 401 -- an anonymous caller has no business here.
curl -s -o /dev/null -w '%{http_code}\n' \
  -X POST https://canvas-viewer-mcp.example.com/ \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Every URL in the metadata must begin `https://canvas-viewer-mcp.example.com`.
If any says `http://` or a local address, `PUBLIC_BASE_URL` is wrong and Claude
will refuse the connector.

### 7. Add the connector

On **claude.ai** (not mobile — new connectors cannot be added there):
Settings → Connectors → Add custom connector →
`https://canvas-viewer-mcp.example.com`

The bare URL is the endpoint, with no path on the end. `/mcp` answers
identically, for connectors added before v0.3.0.

Claude registers itself dynamically, then sends you to the login page. Enter
the pairing code from step 4, or your own password if you set one. Once
authorized, the connector is attached to your account and the iOS and Android
apps pick it up automatically.

The skills are a separate install; see
[README.md](README.md#3-install-the-skills).

---

## Upgrading

```bash
docker compose pull && docker compose up -d
```

With the same `--profile` you started with, if any.

**Upgrading from a compose.yaml that hardcoded the host.** Earlier revisions
carried `CANVAS_BASE_URL` and `PUBLIC_BASE_URL` as literals in `compose.yaml`.
They are now required `.env` values. Add both to `.env` *before* pulling the
new `compose.yaml`; in that order there is no window where compose cannot read
its own file. A running container is unaffected either way — an unparseable
compose.yaml stops nothing that is already up, it only blocks further compose
commands.

The OAuth database lives in the `canvas-viewer-mcp-data` volume, so the
connector stays authorized across upgrades. Delete that volume and you will be
sent back to the browser to authorize again.

## Troubleshooting

**"Couldn't reach the MCP server"** — Claude connects from Anthropic's cloud,
not your device. Reaching the URL from your laptop proves nothing about
whether Anthropic can. Check the host resolves publicly and no firewall or
geo-rule blocks it.

**Authorized, but every tool call fails** — usually the Canvas token rather
than the connector. Check `docker compose logs`; a 401 from Canvas means the
token was revoked or expired, and a fresh one goes in the secrets file.
Re-running `scripts/setup.sh` is the quickest way to replace it, and it checks
the new one against Canvas before writing it.

**Tool calls hang, then time out** — proxy buffering, or `Connection: close`.
See step 5.

**Caddy never gets a certificate** — `docker compose --profile tls logs caddy`.
Almost always DNS not yet pointing here, or port 80 reachable only from inside
your network; Let's Encrypt has to reach it from outside.

**`bind source path does not exist`** — the token file named by
`CANVAS_TOKEN_HOST_FILE` is not there. compose mounts it, so it has to exist
before the container starts, even when the token is coming from `CANVAS_TOKEN`.

**Re-authorized after a redeploy** — the `/data` volume was not persisted.

## Revoking access

Revoke the Canvas token at **Canvas → Account → Settings → Approved
Integrations**. That cuts access immediately and independently of anything
here, which is the control worth remembering: the connector's own password
only gates who may authorize, whereas the Canvas token is what actually reads
your account.
