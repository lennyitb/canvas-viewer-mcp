"""SQLite persistence for OAuth clients, codes, and tokens.

Persistence is the point. Holding this state in memory would mean every
restart -- every container redeploy -- silently invalidates the connector and
forces re-authorization from a browser, which is exactly the sort of chore
that gets a self-hosted tool abandoned.

Access is synchronous. The operations are single-row primary-key lookups on a
local file, measured in microseconds, and this server has one user; wrapping
them in a thread pool would add failure modes without buying anything.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    client_id  TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_codes (
    code       TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS access_tokens (
    token      TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    expires_at REAL
);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token      TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    expires_at REAL
);
CREATE TABLE IF NOT EXISTS pending_logins (
    login_id   TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS token_links (
    access_token  TEXT PRIMARY KEY,
    refresh_token TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS server_secrets (
    name       TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


class OAuthStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()
        # The file holds bearer tokens; keep it off other local accounts.
        db_path.chmod(0o600)

    def close(self) -> None:
        self._db.close()

    # ---- generic helpers -----------------------------------------------------

    def _put(
        self, table: str, key_col: str, key: str, data: dict[str, Any], expires_at: float | None
    ) -> None:
        self._db.execute(
            f"INSERT OR REPLACE INTO {table} ({key_col}, data, expires_at) VALUES (?, ?, ?)",
            (key, json.dumps(data), expires_at),
        )
        self._db.commit()

    def _get(self, table: str, key_col: str, key: str) -> dict[str, Any] | None:
        row = self._db.execute(
            f"SELECT data, expires_at FROM {table} WHERE {key_col} = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] is not None and row["expires_at"] < time.time():
            self._delete(table, key_col, key)
            return None
        result: dict[str, Any] = json.loads(row["data"])
        return result

    def _delete(self, table: str, key_col: str, key: str) -> None:
        self._db.execute(f"DELETE FROM {table} WHERE {key_col} = ?", (key,))
        self._db.commit()

    # ---- clients -------------------------------------------------------------

    def put_client(self, client_id: str, data: dict[str, Any]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO clients (client_id, data, created_at) VALUES (?, ?, ?)",
            (client_id, json.dumps(data), time.time()),
        )
        self._db.commit()

    def get_client(self, client_id: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT data FROM clients WHERE client_id = ?", (client_id,)
        ).fetchone()
        result: dict[str, Any] | None = json.loads(row["data"]) if row else None
        return result

    # ---- authorization codes -------------------------------------------------

    def put_auth_code(self, code: str, data: dict[str, Any], expires_at: float) -> None:
        self._put("auth_codes", "code", code, data, expires_at)

    def get_auth_code(self, code: str) -> dict[str, Any] | None:
        return self._get("auth_codes", "code", code)

    def delete_auth_code(self, code: str) -> None:
        self._delete("auth_codes", "code", code)

    # ---- tokens --------------------------------------------------------------

    def put_access_token(self, token: str, data: dict[str, Any], expires_at: float | None) -> None:
        self._put("access_tokens", "token", token, data, expires_at)

    def get_access_token(self, token: str) -> dict[str, Any] | None:
        return self._get("access_tokens", "token", token)

    def delete_access_token(self, token: str) -> None:
        self._delete("access_tokens", "token", token)

    def put_refresh_token(self, token: str, data: dict[str, Any], expires_at: float | None) -> None:
        self._put("refresh_tokens", "token", token, data, expires_at)

    def get_refresh_token(self, token: str) -> dict[str, Any] | None:
        return self._get("refresh_tokens", "token", token)

    def delete_refresh_token(self, token: str) -> None:
        self._delete("refresh_tokens", "token", token)

    def link_tokens(self, access_token: str, refresh_token: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO token_links (access_token, refresh_token) VALUES (?, ?)",
            (access_token, refresh_token),
        )
        self._db.commit()

    def paired_tokens(
        self, *, access_token: str | None = None, refresh_token: str | None = None
    ) -> tuple[str | None, str | None]:
        """Return the (access, refresh) pair given either half, for revocation."""
        if access_token is not None:
            row = self._db.execute(
                "SELECT refresh_token FROM token_links WHERE access_token = ?", (access_token,)
            ).fetchone()
            return access_token, row["refresh_token"] if row else None
        row = self._db.execute(
            "SELECT access_token FROM token_links WHERE refresh_token = ?", (refresh_token,)
        ).fetchone()
        return (row["access_token"] if row else None), refresh_token

    def unlink(self, access_token: str) -> None:
        self._db.execute("DELETE FROM token_links WHERE access_token = ?", (access_token,))
        self._db.commit()

    # ---- pending logins ------------------------------------------------------

    def put_pending_login(self, login_id: str, data: dict[str, Any], expires_at: float) -> None:
        self._put("pending_logins", "login_id", login_id, data, expires_at)

    def take_pending_login(self, login_id: str) -> dict[str, Any] | None:
        """Fetch and consume a pending login. Single-use by construction."""
        data = self._get("pending_logins", "login_id", login_id)
        if data is not None:
            self._delete("pending_logins", "login_id", login_id)
        return data

    # ---- server secrets ------------------------------------------------------
    #
    # The self-issued pairing code's argon2 hash lives here rather than in the
    # environment, so it survives restarts without being printed again and
    # disappears with the volume -- which is the same event that already
    # deauthorizes the connector.

    def get_server_secret(self, name: str) -> str | None:
        row = self._db.execute(
            "SELECT value FROM server_secrets WHERE name = ?", (name,)
        ).fetchone()
        return str(row["value"]) if row else None

    def put_server_secret(self, name: str, value: str) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO server_secrets (name, value, created_at) VALUES (?, ?, ?)",
            (name, value, time.time()),
        )
        self._db.commit()

    def delete_server_secret(self, name: str) -> None:
        self._db.execute("DELETE FROM server_secrets WHERE name = ?", (name,))
        self._db.commit()

    def purge_expired(self) -> None:
        now = time.time()
        for table in ("auth_codes", "pending_logins", "access_tokens", "refresh_tokens"):
            self._db.execute(
                f"DELETE FROM {table} WHERE expires_at IS NOT NULL AND expires_at < ?", (now,)
            )
        self._db.commit()
