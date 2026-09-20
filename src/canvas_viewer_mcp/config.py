"""Configuration loading.

The Canvas token is deliberately never read from a file inside the repository.
It comes from CANVAS_TOKEN, or from a file path given by CANVAS_TOKEN_FILE
(which is how the container receives it, via a mounted secret).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TOKEN_FILE = Path.home() / ".config" / "canvas-viewer-mcp" / "token"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or unusable."""


def _read_token() -> str:
    if token := os.environ.get("CANVAS_TOKEN"):
        return token.strip()

    override = os.environ.get("CANVAS_TOKEN_FILE")
    path = Path(override) if override else DEFAULT_TOKEN_FILE
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raise ConfigError(f"No Canvas token. Set CANVAS_TOKEN, or place one at {path}.") from None
    except PermissionError:
        raise ConfigError(f"Cannot read token file at {path}: permission denied.") from None

    if not token:
        raise ConfigError(f"Token file at {path} is empty.")
    return token


def _read_base_url() -> str:
    host = os.environ.get("CANVAS_BASE_URL", "").strip()
    if not host:
        raise ConfigError("CANVAS_BASE_URL is not set (e.g. https://yourschool.instructure.com).")
    if not host.startswith(("http://", "https://")):
        host = f"https://{host}"
    return host.rstrip("/")


@dataclass(frozen=True)
class Config:
    base_url: str
    token: str
    timeout_seconds: float = 30.0
    max_pages: int = 50
    """Safety stop for Link-header pagination so a loop cannot run away."""

    @classmethod
    def from_env(cls) -> Config:
        return cls(base_url=_read_base_url(), token=_read_token())

    def __repr__(self) -> str:
        # Never let the token reach a log line or traceback.
        return f"Config(base_url={self.base_url!r}, token=<redacted>)"
