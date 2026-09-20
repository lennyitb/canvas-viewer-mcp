"""Configuration loading.

Three sources, in descending precedence: the environment, a TOML file at
``~/.config/canvas-viewer-mcp/config.toml``, and built-in defaults. The
environment wins because the container passes its configuration that way, and
a stale file in a developer's home directory must never quietly override a
deployed setting.

The Canvas token is deliberately never read from a file inside the repository,
and never from ``config.toml`` either. It comes from CANVAS_TOKEN, or from a
file path given by CANVAS_TOKEN_FILE (which is how the container receives it,
via a mounted secret), or from a file beside the config. Keeping it in its own
file means ``config.toml`` stays safe to paste into a bug report.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "canvas-viewer-mcp"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.toml"
DEFAULT_TOKEN_FILE = DEFAULT_CONFIG_DIR / "token"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or unusable."""


def _config_file_path() -> Path:
    override = os.environ.get("CANVAS_CONFIG_FILE", "").strip()
    return Path(override).expanduser() if override else DEFAULT_CONFIG_FILE


def _default_token_file() -> Path:
    """The token sits beside the config file, so relocating one relocates both.

    With CANVAS_CONFIG_FILE unset this is exactly DEFAULT_TOKEN_FILE. Pointing
    CANVAS_CONFIG_FILE at another directory moves the whole configuration
    there rather than leaving the token behind in the home directory, which
    also keeps a developer's real token out of the test suite.
    """
    return _config_file_path().parent / "token"


def _load_file_config() -> Mapping[str, Any]:
    """Values from config.toml, or an empty mapping when there is no file.

    A missing file is the normal case for the container, which is configured
    entirely from the environment, so it is not an error.
    """
    path = _config_file_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except PermissionError:
        raise ConfigError(f"Cannot read {path}: permission denied.") from None

    try:
        loaded: dict[str, Any] = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from None
    return loaded


def _file_str(file_config: Mapping[str, Any], key: str) -> str:
    """One string value from the config file, or "" when absent."""
    value = file_config.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ConfigError(
            f"{_config_file_path()}: {key} must be a string, got {type(value).__name__}."
        )
    return value.strip()


def _read_token(file_config: Mapping[str, Any]) -> str:
    if token := os.environ.get("CANVAS_TOKEN"):
        return token.strip()

    override = os.environ.get("CANVAS_TOKEN_FILE") or _file_str(file_config, "token_file")
    path = Path(override).expanduser() if override else _default_token_file()
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raise ConfigError(f"No Canvas token. Set CANVAS_TOKEN, or place one at {path}.") from None
    except PermissionError:
        raise ConfigError(f"Cannot read token file at {path}: permission denied.") from None

    if not token:
        raise ConfigError(f"Token file at {path} is empty.")
    return token


def _read_base_url(file_config: Mapping[str, Any]) -> str:
    """The school's Canvas host, from whatever the installer pasted in.

    The instruction people can actually follow is "copy the address bar while
    you are looking at Canvas", and that yields a deep link such as
    ``https://yourschool.instructure.com/courses/12345/assignments``. Every
    request this server makes is built by joining a path onto this value, so a
    leftover path would corrupt all of them. Keep the scheme and host, discard
    the rest.
    """
    host = os.environ.get("CANVAS_BASE_URL", "").strip() or _file_str(file_config, "base_url")
    if not host:
        raise ConfigError(
            "No Canvas host. Set CANVAS_BASE_URL, or write base_url into "
            f"{_config_file_path()} (e.g. https://yourschool.instructure.com)."
        )
    if not host.startswith(("http://", "https://")):
        host = f"https://{host}"

    parts = urlsplit(host)
    if not parts.netloc:
        raise ConfigError(f"CANVAS_BASE_URL is not a usable URL: {host!r}.")
    return f"{parts.scheme}://{parts.netloc}"


def default_db_path() -> Path:
    """Where the OAuth state lives. The container mounts a volume on it."""
    db = os.environ.get("DB_PATH", "").strip()
    if db:
        return Path(db)
    return Path.home() / ".local" / "share" / "canvas-viewer-mcp" / "auth.sqlite"


def _platform_public_base_url() -> str | None:
    """The public URL the hosting platform already knows, or None.

    PUBLIC_BASE_URL is the one required value nobody can supply before the
    first deploy, because the platform assigns the hostname -- and the one
    that does real damage when it is copied out of someone else's
    instructions, since the OAuth metadata is built from it and would send
    Claude to authorize against whatever host it names. Reading it from the
    platform removes the ordering problem and the copy-paste hazard together,
    which is what makes a one-click install possible at all.

    An explicit PUBLIC_BASE_URL still wins, for a custom domain or a reverse
    proxy in front of the platform.
    """
    if domain := os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip():
        return f"https://{domain}"
    if url := os.environ.get("RENDER_EXTERNAL_URL", "").strip():
        return url
    if app := os.environ.get("FLY_APP_NAME", "").strip():
        return f"https://{app}.fly.dev"
    return None


def _read_public_base_url() -> str:
    """The URL Anthropic reaches this server on.

    Taken from configuration and never inferred from the inbound request.
    Behind a reverse proxy the request scheme is http, so deriving OAuth
    metadata URLs from it would advertise http:// endpoints and the connector
    would be rejected. This sidesteps the whole X-Forwarded-Proto question.
    """
    url = os.environ.get("PUBLIC_BASE_URL", "").strip() or _platform_public_base_url() or ""
    if not url:
        raise ConfigError(
            "PUBLIC_BASE_URL is not set and no hosting platform supplied one "
            "(e.g. https://canvas-viewer-mcp.example.com). It must be the public "
            "HTTPS URL Claude reaches this server on, not the local bind address."
        )
    if not url.startswith("https://") and "localhost" not in url and "127.0.0.1" not in url:
        raise ConfigError(f"PUBLIC_BASE_URL must be https:// for a public deployment, got {url!r}.")
    return url.rstrip("/")


@dataclass(frozen=True)
class Config:
    base_url: str
    token: str
    timeout_seconds: float = 30.0
    max_pages: int = 50
    """Safety stop for Link-header pagination so a loop cannot run away."""

    @classmethod
    def from_env(cls) -> Config:
        """Resolve from the environment, falling back to config.toml."""
        file_config = _load_file_config()
        return cls(base_url=_read_base_url(file_config), token=_read_token(file_config))

    def __repr__(self) -> str:
        # Never let the token reach a log line or traceback.
        return f"Config(base_url={self.base_url!r}, token=<redacted>)"


MIN_PASSWORD_LENGTH = 12


@dataclass(frozen=True)
class AuthConfig:
    """Configuration for the OAuth server that fronts the MCP endpoint."""

    public_base_url: str
    password_hash: str | None
    """A pre-computed argon2 hash from AUTH_PASSWORD_HASH, or None."""
    password: str | None
    """A plaintext password from AUTH_PASSWORD, hashed at startup, or None.

    Accepting the plaintext is what removes the last command line from the
    install: a hosting platform's deploy form can collect a password, but it
    cannot run `canvas-probe hash-password` to turn one into a hash. The
    platform then stores it the same way it already stores the Canvas token,
    which is the stronger credential of the two -- so this adds no exposure
    that the deployment did not already have. AUTH_PASSWORD_HASH remains for
    anyone who would rather the plaintext never be stored at all.
    """
    db_path: Path

    @classmethod
    def from_env(cls) -> AuthConfig:
        password_hash = os.environ.get("AUTH_PASSWORD_HASH", "").strip() or None
        if password_hash is not None and not password_hash.startswith("$argon2"):
            raise ConfigError("AUTH_PASSWORD_HASH does not look like an argon2 hash.")

        password = os.environ.get("AUTH_PASSWORD", "").strip() or None
        if password is not None and len(password) < MIN_PASSWORD_LENGTH:
            raise ConfigError(
                f"AUTH_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters; "
                f"the one set is {len(password)}. It is the only thing standing between "
                "a public URL and your Canvas account. Change it and redeploy, or "
                "clear it to have the server issue a pairing code instead."
            )

        return cls(
            public_base_url=_read_public_base_url(),
            password_hash=password_hash,
            password=password,
            db_path=default_db_path(),
        )

    def __repr__(self) -> str:
        # The hash is not a password, but it is still an offline cracking
        # target, and `password` is the real thing; keep both out of logs.
        # Which one is configured is not a secret, and knowing it is the
        # difference between debugging a wrong password and debugging a
        # pairing code.
        if self.password_hash:
            configured = "<AUTH_PASSWORD_HASH>"
        elif self.password:
            configured = "<AUTH_PASSWORD>"
        else:
            configured = "None (pairing code)"
        return f"AuthConfig(public_base_url={self.public_base_url!r}, credential={configured})"
