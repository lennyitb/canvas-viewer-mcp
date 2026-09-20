---
description: Configure canvas-viewer — find your Canvas host, store an API token, and verify access
argument-hint: "[school name]"
allowed-tools: ["Bash", "Read", "AskUserQuestion"]
---

# Set up canvas-viewer

Configure this machine so the `canvas-viewer` MCP server can reach Canvas. Two
files, both under `~/.config/canvas-viewer-mcp/`:

- `config.toml` — the Canvas host. Not secret.
- `token` — the Canvas API token, mode `600`. Secret, and deliberately kept out
  of `config.toml` so that file stays safe to paste into a bug report.

Nothing needs to be exported in a shell profile, and the server does not need
restarting afterwards: it builds its client on the first tool call and retries
after a configuration failure.

School name, if the user gave one: $ARGUMENTS

## 1. Look at what is already there

```bash
ls -la ~/.config/canvas-viewer-mcp/ 2>/dev/null && cat ~/.config/canvas-viewer-mcp/config.toml 2>/dev/null
```

If a config already exists, show it and ask whether to reconfigure before
overwriting anything. Never print the contents of the `token` file — not to
check it, not to confirm it, not ever.

## 2. Find the Canvas host

Canvas runs a public, unauthenticated directory of institutions. Search it by
school name rather than asking the user for a hostname, which most people do
not know:

```bash
curl -s --get --data-urlencode "search_term=<school name>" \
  https://canvas.instructure.com/api/v1/accounts/search
```

Each result has `name` and `domain`. Several accounts commonly share one
domain (a district or state system), so **deduplicate by `domain`** before
offering choices — "Vermont State Colleges", "CCV" and "VTC" are all
`vsc.instructure.com`, and presenting them as three options is confusing.

Use `AskUserQuestion` to let the user pick when more than one distinct domain
comes back. Label each option with the domain and list the school names it
covers in the description.

If the search returns nothing useful, ask for the hostname directly — it is the
domain in the address bar when they are logged into Canvas, e.g.
`school.instructure.com` or `canvas.school.edu`. Some institutions are not in
the directory at all, which is not an error.

Confirm the host is really Canvas before writing it. An anonymous request to a
live Canvas API returns 401:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://<domain>/api/v1/users/self
```

`401` is the expected, correct answer here. `200` would be strange, and a
`000`, `404` or any HTML page means the host is wrong — stop and re-ask rather
than writing a bad config.

## 3. Write the config file

```bash
install -d -m 700 ~/.config/canvas-viewer-mcp
cat > ~/.config/canvas-viewer-mcp/config.toml <<'EOF'
# canvas-viewer-mcp. Environment variables override everything here.
base_url = "https://<domain>"
EOF
chmod 600 ~/.config/canvas-viewer-mcp/config.toml
```

## 4. Get the token

Canvas has no automatable flow for this. OAuth requires a developer key that
only a Canvas account administrator can create, so a personal access token,
minted by hand, is the only route available to a student. Do not go looking for
a way around it.

Give the user the direct link, filled in with their domain:

**`https://<domain>/profile/settings#access_tokens`** → **+ New Access Token** →
give it a purpose and leave the expiry blank → **Generate Token** → copy it.

Warn them, once and briefly: the token grants full access to their Canvas
account, including anything they can submit or change. This server only ever
reads, but the token itself is not limited to reading.

**Prefer the path where the token never enters this conversation.** Ask the
user to run this in their own terminal — it prompts without echoing, writes the
file, and leaves nothing in shell history:

```bash
install -d -m 700 ~/.config/canvas-viewer-mcp
read -rs -p "Paste Canvas token: " t && printf '%s' "$t" > ~/.config/canvas-viewer-mcp/token && chmod 600 ~/.config/canvas-viewer-mcp/token && unset t && echo " stored"
```

If they paste the token into the chat instead, write it to the file and set the
mode, then tell them plainly that it is now in the conversation transcript and
that they may want to delete it in Canvas and mint a fresh one using the
command above. Say it once; do not lecture.

## 5. Verify

Use the package's own probe, which reads the files exactly the way the MCP
server does and keeps the token off the command line and out of the process
list:

```bash
uvx --from git+https://github.com/lennyitb/canvas-viewer-mcp canvas-probe whoami
```

On success it prints the Canvas user. Then confirm the tools work end to end by
calling `list_courses` through the MCP server itself.

Common failures:

- `No Canvas host` — `config.toml` was not written, or is not where the server
  looks. Check `CANVAS_CONFIG_FILE` is not set to something else.
- `No Canvas token` — the token file is missing or empty. A `read -rs` that was
  cancelled leaves an empty file behind.
- `401` from Canvas — the token is wrong, was revoked, or belongs to a
  different institution than the host in `config.toml`.

## 6. Report

Two or three lines: the school and host configured, that the token is stored at
`~/.config/canvas-viewer-mcp/token` with mode 600, and the account `whoami`
came back as. Then suggest trying "what's due this week", which is what the
`canvas-week-report` skill is for.
