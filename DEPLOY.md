# Deploying canvas-viewer-mcp

Serving the MCP endpoint directly, with TLS terminated by an existing reverse
proxy. This is the path for someone who already runs a box behind a proxy.

If you would rather not, the one-click Railway install in
[README.md](README.md#install) deploys the same container with the same
configuration and no DNS, no proxy and no shell. Nothing below is a
prerequisite for it.

Throughout this guide, replace `canvas-viewer-mcp.example.com` with your own
hostname and `yourschool.instructure.com` with your institution's Canvas host.
Nothing is baked into the repository: `compose.yaml` takes every
deployment-specific value from `.env` and refuses to start rather than guess.

## 1. DNS

One record, pointing at the public IP of the machine running the proxy:

```
canvas-viewer-mcp.example.com.  A  <your public IP>
```

If the record is proxied through Cloudflare (orange cloud), set SSL/TLS mode to
**Full (strict)** so the origin certificate is actually verified. Do not use
Flexible: it re-encrypts to your origin over plain HTTP, and the OAuth bearer
tokens this server issues would cross that leg in the clear.

## 2. The Canvas token

One secret, and it does not belong in the repo or in an environment variable in
shell history. The container mounts it as a file.

```bash
install -d -m 700 /srv/canvas-viewer-mcp/secrets
cp ~/.config/canvas-viewer-mcp/token /srv/canvas-viewer-mcp/secrets/canvas_token
chmod 600 /srv/canvas-viewer-mcp/secrets/canvas_token
```

There is no second secret to prepare. The connector login is gated by a pairing
code the server issues itself on first run and prints once to its logs; step 4
picks it up. To choose your own instead, set one of these in step 3:

- `AUTH_PASSWORD` -- a plaintext password, at least 12 characters, hashed at
  startup. It then sits in `.env` beside the path to the Canvas token, which is
  the stronger credential of the two.
- `AUTH_PASSWORD_HASH` -- an argon2 hash from `canvas-probe hash-password`, if
  you would rather the plaintext were never stored at all.

Setting either one deletes a pairing code already issued, which is how a code
that reached your logs gets revoked.

## 3. Environment

`/srv/canvas-viewer-mcp/.env` carries everything that differs between
deployments.

```
CANVAS_BASE_URL=https://yourschool.instructure.com
PUBLIC_BASE_URL=https://canvas-viewer-mcp.example.com
CANVAS_TOKEN_FILE=/srv/canvas-viewer-mcp/secrets/canvas_token

# Optional, pick at most one. Neither means a pairing code is issued instead.
# AUTH_PASSWORD=a-password-you-chose
# AUTH_PASSWORD_HASH='$argon2id$v=19$...'
```

The first two are required: `compose.yaml` fails to parse without them, naming
the one that is missing. If you do set `AUTH_PASSWORD_HASH`, quote it -- it
contains `$`, which an unquoted value will mangle.

`CANVAS_BASE_URL` tolerates a pasted course link; everything after the host is
trimmed. On Railway, Render or Fly, `PUBLIC_BASE_URL` can be left out entirely
because the platform publishes its own hostname and the server reads that --
here there is no platform to ask, so it is required.

`PUBLIC_BASE_URL` is the one to get right. The server advertises OAuth metadata
built from it, so a wrong value sends Claude to authorize against whatever host
it names -- which means someone else's server, if you copied their value.

## 4. Run

Write `.env` before fetching `compose.yaml`: with values missing, every compose
subcommand refuses to parse the file, `logs` and `pull` included.

```bash
cd /srv/canvas-viewer-mcp
curl -O https://raw.githubusercontent.com/lennyitb/canvas-viewer-mcp/main/compose.yaml
docker compose up -d
docker compose logs
```

The container listens on `127.0.0.1:8000` and never on a public interface.

Unless you set a password, the log carries the pairing code:

```
  No password is set, so this server issued itself a
  pairing code. Use it as the password when Claude sends you to
  the login page.

      pairing code:   NY5D-FFF5-NYRN-KJ5T
      connector URL:  https://canvas-viewer-mcp.example.com/mcp
```

Keep it for step 7. It is printed once and stored only as an argon2 hash, so
nothing can read it back out; `canvas-probe reset-pairing` followed by a restart
issues a new one. It survives restarts and upgrades along with the rest of the
OAuth state in the `/data` volume.

Worth knowing what you are trading: a code in a log file is less protected than
a hash in a file only root reads, and it follows wherever you ship logs. If that
matters to you, set `AUTH_PASSWORD` or `AUTH_PASSWORD_HASH` and the code is
never issued.

## 5. Reverse proxy

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
    proxy_read_timeout 300s;
}
```

`proxy_buffering off` matters. MCP uses server-sent events, and a buffering
proxy holds the stream until it fills, which surfaces as tool calls that hang
and then time out.

## 6. Verify before touching Claude

```bash
curl -s https://canvas-viewer-mcp.example.com/health
curl -s https://canvas-viewer-mcp.example.com/.well-known/oauth-authorization-server | jq .

# Must be 401 -- an anonymous caller has no business here.
curl -s -o /dev/null -w '%{http_code}\n' \
  -X POST https://canvas-viewer-mcp.example.com/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Every URL in the metadata must begin `https://canvas-viewer-mcp.example.com`.
If any says `http://` or a local address, `PUBLIC_BASE_URL` is wrong and Claude
will refuse the connector.

## 7. Add the connector

On **claude.ai** (not mobile -- new connectors cannot be added there):
Settings → Connectors → Add custom connector →
`https://canvas-viewer-mcp.example.com/mcp`

Claude registers itself dynamically, then sends you to the login page. Enter
the pairing code from step 4, or your own password if you set one. Once
authorized, the connector is attached to your account and the iOS and Android
apps pick it up automatically.

The skills are a separate install; see
[README.md](README.md#3-install-the-skills).

## Upgrading

```bash
docker compose pull && docker compose up -d
```

**Upgrading from a compose.yaml that hardcoded the host.** Earlier revisions
carried `CANVAS_BASE_URL` and `PUBLIC_BASE_URL` as literals in `compose.yaml`.
They are now required `.env` values. Add both to `.env` *before* pulling the
new `compose.yaml`; in that order there is no window where compose cannot read
its own file. A running container is unaffected either way -- an unparseable
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

**Tool calls hang, then time out** — proxy buffering. See step 5.

**Re-authorized after a redeploy** — the `/data` volume was not persisted.

## Revoking access

Revoke the Canvas token at **Canvas → Account → Settings → Approved
Integrations**. That cuts access immediately and independently of anything
here, which is the control worth remembering: the connector's own password
only gates who may authorize, whereas the Canvas token is what actually reads
your account.
