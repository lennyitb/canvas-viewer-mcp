# The Railway template

This file is the source of truth for the one-click template the README links
to. Railway templates are configured in Railway's dashboard rather than from a
file in the repository, so without this there is no reviewable record of what
the button actually deploys, and no way to rebuild it if the template is lost.

Railway's own `railway.json` / `railway.toml` config-as-code is **not** used:
it is deprecated, new services cannot opt into it, and it stops being read on
2026-12-01. Everything below is dashboard state.

## Why a template at all

The audience is students, not operators. The three things a hosted MCP server
needs — a hostname, a place to keep its OAuth database, and somewhere to put
two secrets — are exactly the three things that make a self-hosted install a
weekend project. A template turns them into a form with three fields, and the
deploy is into the student's own Railway account: their project, their
container, their volume, their Canvas token. Nothing routes through the
maintainer's infrastructure, and there is no shared instance to be a single
point of failure or a single point of compromise.

## Service

| Setting | Value |
| --- | --- |
| Source | Docker image `ghcr.io/lennyitb/canvas-viewer-mcp:latest` |
| Healthcheck path | `/health` |
| Volume mount path | `/data` |
| Restart policy | On failure |

The image rather than a repository build: deploys take seconds instead of
minutes, and every install runs the exact artifact the release workflow
published. Building from the repository works too, and is the right choice for
a fork — point the source at it and leave everything else identical.

The volume is not optional. `/data` holds the OAuth database, and the
`DB_PATH=/data/auth.sqlite` default is already baked into the image. Without a
volume, every redeploy wipes the registered client and the student has to
authorize the connector in Claude again.

`PORT` is injected by Railway and read by the server; do not set it.

## Variables

Three, all required, in this order. The descriptions are what the person
deploying reads, so they are written for someone who has never seen a terminal.

**`CANVAS_BASE_URL`**

> Your school's Canvas address. Open Canvas in another tab and copy what's in
> the address bar — anything after the site name is trimmed off
> automatically. Example: `https://yourschool.instructure.com`

**`CANVAS_TOKEN`**

> Your Canvas access token. In Canvas go to Account → Settings, scroll to
> Approved Integrations, click "+ New Access Token", leave the expiry blank,
> and copy the token it shows you — it is shown only once. This grants full
> access to your Canvas account, so treat it like a password.

**`AUTH_PASSWORD`**

> A password you choose, at least 12 characters. Claude will ask for it once,
> when you connect. It is the only thing standing between this server's public
> address and your Canvas account, so don't reuse one.

No `PUBLIC_BASE_URL` variable. The server reads `RAILWAY_PUBLIC_DOMAIN`, which
Railway sets itself. This is deliberate: it is the one value that cannot be
known before the first deploy, because Railway assigns the hostname, and it is
the one that does real damage when copied out of someone else's instructions —
the OAuth metadata is built from it, so a wrong value sends Claude to
authorize against whatever host it names.

If you would rather Railway generate the password than have the student pick
one, set the `AUTH_PASSWORD` default to `${{ secret(24) }}`. The trade is that
the student then has to open the service's Variables tab to read it, instead
of knowing it because they typed it.

## After deploying

Railway assigns the service a public domain as part of deploying a template,
so the person installing does not have to configure networking. The connector
URL is that domain with `/mcp` on the end. Should a deploy come up without
one, Settings → Networking → **Generate Domain** produces it.

Check this when you first publish the template: the whole install depends on
the deployer being handed a URL without going looking for one.

## Publishing

Create it under Workspace Settings → Templates → New Template, fill in the
above, then copy the template URL from the composer and put it in the README
button.

The published template is **`https://railway.com/deploy/1cOY5u`**. Its
configuration can be read back without logging in, which is the quickest way
to check an edit actually took:

```bash
curl -s -X POST https://backboard.railway.com/graphql/v2 \
  -H 'Content-Type: application/json' \
  -d '{"query":"query($code:String!){template(code:$code){name description serializedConfig}}",
       "variables":{"code":"1cOY5u"}}' | python3 -m json.tool
```

The share URL carries a `referralCode`. That is Railway's default and it
credits the template author for signups; drop the parameter if the README
should not carry it.

### Still to fill in

The template deploys correctly, but three descriptions are empty, and they are
the part the design actually rests on. With no wizard, the deploy form *is*
the documentation: someone who has never opened a terminal sees three blank
boxes named `CANVAS_BASE_URL`, `CANVAS_TOKEN` and `AUTH_PASSWORD` and has
nothing telling them what belongs in any of them. The text to paste is under
**Variables** above.

Also unset: the template's own name, which Railway generated as `warm-wild`,
and its description. Both are what a stranger sees before deciding to trust it
with a Canvas token.

## Checking it still works

Deploy the template into a throwaway project and confirm, in order:

1. The deploy succeeds and the healthcheck goes green.
2. `https://<domain>/.well-known/oauth-authorization-server` lists URLs that
   all begin with `https://<domain>` — never `http://`, never a local address.
   This is the check that `RAILWAY_PUBLIC_DOMAIN` was picked up.
3. An unauthenticated `POST /mcp` returns 401.
4. Adding `https://<domain>/mcp` in claude.ai reaches the login page, the
   chosen password works, and `list_courses` returns real courses.
5. Redeploying does not send you back to the login page — that proves the
   volume is attached.

A deliberately wrong `CANVAS_BASE_URL` should crash the deploy with the reason
on the first line of the log, not start and fail every tool call.
