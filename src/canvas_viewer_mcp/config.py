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
    host = os.environ.get("CANVAS_BASE_URL", "").strip() or _file_str(file_config, "base_url")
    if not host:
        raise ConfigError(
            "No Canvas host. Set CANVAS_BASE_URL, or write base_url into "
            f"{_config_file_path()} (e.g. https://yourschool.instructure.com)."
        )
    if not host.startswith(("http://", "https://")):
        host = f"https://{host}"
    return host.rstrip("/")


def _read_public_base_url() -> str:
    """The URL Anthropic reaches this server on.

    Taken from configuration and never inferred from the inbound request.
    Behind a reverse proxy the request scheme is http, so deriving OAuth
    metadata URLs from it would advertise http:// endpoints and the connector
    would be rejected. This sidesteps the whole X-Forwarded-Proto question.
    """
    url = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if not url:
        raise ConfigError(
            "PUBLIC_BASE_URL is not set (e.g. https://canvas-viewer-mcp.example.com). "
            "It must be the public HTTPS URL, not the local bind address."
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


@dataclass(frozen=True)
class AuthConfig:
    """Configuration for the OAuth server that fronts the MCP endpoint."""

    public_base_url: str
    password_hash: str
    db_path: Path

    @classmethod
    def from_env(cls) -> AuthConfig:
        password_hash = os.environ.get("AUTH_PASSWORD_HASH", "").strip()
        if not password_hash:
            raise ConfigError(
                "AUTH_PASSWORD_HASH is not set. Generate one with "
                "`canvas-probe hash-password` and store the hash, not the password."
            )
        if not password_hash.startswith("$argon2"):
            raise ConfigError("AUTH_PASSWORD_HASH does not look like an argon2 hash.")

        db = os.environ.get("DB_PATH", "").strip()
        return cls(
            public_base_url=_read_public_base_url(),
            password_hash=password_hash,
            db_path=Path(db)
            if db
            else Path.home() / ".local" / "share" / "canvas-viewer-mcp" / "auth.sqlite",
        )

    def __repr__(self) -> str:
        # The password hash is not a password, but it is still an offline
        # cracking target; keep it out of logs.
        return f"AuthConfig(public_base_url={self.public_base_url!r}, password_hash=<redacted>)"
