"""The password-gated login page that stands in front of authorization.

This is the only place a human proves who they are, so it is the only thing
between the public internet and the user's Canvas account. Three properties
matter more than looks:

* **Uniform failures.** A wrong password, an expired login, and a fabricated
  login id all produce the same message. Distinguishing them would tell an
  attacker which half of the guess was right.
* **Single-use parked requests.** The pending login is consumed on the first
  attempt whatever the outcome, so a guessed ``login_id`` cannot be hammered.
* **Throttling.** argon2 already makes guessing slow; a per-address backoff
  makes it slower still and bounds the damage from a leaked login id.
"""

from __future__ import annotations

import html
import time
from collections import defaultdict
from typing import Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from .provider import SingleUserOAuthProvider

MAX_ATTEMPTS = 8
LOCKOUT_SECONDS = 15 * 60

_attempts: dict[str, list[float]] = defaultdict(list)


def _client_ip(request: Request) -> str:
    # Behind a reverse proxy the socket address is the proxy, so prefer the
    # forwarded address when one is present. This is throttling, not
    # authorization, so a spoofable header is an acceptable input here.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _throttled(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _attempts[ip] if now - t < LOCKOUT_SECONDS]
    _attempts[ip] = recent
    return len(recent) >= MAX_ATTEMPTS


def _record_attempt(ip: str) -> None:
    _attempts[ip].append(time.time())


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>canvas-viewer-mcp</title>
<style>
  :root {{ color-scheme: light dark; --fg: #1a1a1a; --bg: #fafafa;
           --card: #fff; --border: #ddd; --accent: #2a5db0; --error: #b3261e; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --fg: #e8e8e8; --bg: #131313; --card: #1e1e1e;
             --border: #383838; --accent: #7aa2e3; --error: #f2b8b5; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
          padding: 16px; background: var(--bg); color: var(--fg);
          font: 16px/1.5 system-ui, -apple-system, sans-serif; }}
  .card {{ width: 100%; max-width: 380px; background: var(--card);
           border: 1px solid var(--border); border-radius: 12px; padding: 28px; }}
  h1 {{ margin: 0 0 4px; font-size: 1.15rem; }}
  p.sub {{ margin: 0 0 20px; color: #888; font-size: .875rem; }}
  label {{ display: block; font-size: .8rem; font-weight: 600;
           margin-bottom: 6px; letter-spacing: .02em; }}
  input {{ width: 100%; padding: 10px 12px; font-size: 1rem; color: var(--fg);
           background: var(--bg); border: 1px solid var(--border);
           border-radius: 8px; }}
  input:focus {{ outline: 2px solid var(--accent); outline-offset: 1px; }}
  button {{ width: 100%; margin-top: 16px; padding: 11px; font-size: .95rem;
            font-weight: 600; color: #fff; background: var(--accent);
            border: 0; border-radius: 8px; cursor: pointer; }}
  .error {{ margin: 0 0 16px; padding: 10px 12px; font-size: .875rem;
            color: var(--error); border: 1px solid var(--error);
            border-radius: 8px; background: transparent; }}
</style>
</head>
<body>
  <main class="card">
    <h1>canvas-viewer-mcp</h1>
    <p class="sub">Authorize this connector to read your Canvas account.</p>
    {error}
    <form method="post" action="/login">
      <input type="hidden" name="login_id" value="{login_id}">
      <label for="password">Password</label>
      <input id="password" name="password" type="password"
             autocomplete="current-password" autofocus required>
      <button type="submit">Authorize</button>
    </form>
  </main>
</body>
</html>
"""

GENERIC_ERROR = "That didn't work. Start the connection again from Claude and retry."


def _render(login_id: str, error: str | None = None) -> HTMLResponse:
    block = f'<p class="error">{html.escape(error)}</p>' if error else ""
    status = 401 if error else 200
    return HTMLResponse(
        PAGE.format(login_id=html.escape(login_id), error=block), status_code=status
    )


def register_login_routes(mcp: FastMCP[Any], provider: SingleUserOAuthProvider) -> None:
    """Attach GET and POST /login to a FastMCP server."""

    @mcp.custom_route("/login", methods=["GET"])
    async def login_form(request: Request) -> Response:
        return _render(request.query_params.get("login_id", ""))

    @mcp.custom_route("/login", methods=["POST"])
    async def login_submit(request: Request) -> Response:
        ip = _client_ip(request)
        if _throttled(ip):
            return _render("", "Too many attempts. Try again later.")

        form = await request.form()
        login_id = str(form.get("login_id") or "")
        password = str(form.get("password") or "")

        redirect = provider.complete_login(login_id, password)
        if redirect is None:
            _record_attempt(ip)
            return _render(login_id, GENERIC_ERROR)

        # 303 so the browser reissues as GET and a refresh cannot repost.
        return RedirectResponse(redirect, status_code=303)
