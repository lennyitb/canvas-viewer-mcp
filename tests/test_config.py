"""Configuration tests, with emphasis on secrets never reaching a log line."""

from __future__ import annotations

from pathlib import Path

import pytest

from canvas_viewer_mcp.config import AuthConfig, Config, ConfigError

SECRET = "9112~notarealtokenbutlongenoughtolooklikeone"
HASH = "$argon2id$v=19$m=65536,t=3,p=4$abc$def"


def test_token_never_appears_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """A traceback or debug log that echoed the config must not disclose the
    Canvas token, which grants full account access."""
    monkeypatch.setenv("CANVAS_BASE_URL", "x.instructure.com")
    monkeypatch.setenv("CANVAS_TOKEN", SECRET)

    config = Config.from_env()
    assert config.token == SECRET
    assert SECRET not in repr(config)
    assert "<redacted>" in repr(config)


def test_password_hash_never_appears_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", HASH)

    auth = AuthConfig.from_env()
    assert auth.password_hash == HASH
    assert HASH not in repr(auth)


def test_bare_hostname_is_normalized_to_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANVAS_BASE_URL", "x.instructure.com")
    monkeypatch.setenv("CANVAS_TOKEN", SECRET)
    assert Config.from_env().base_url == "https://x.instructure.com"


def test_missing_canvas_host_raises_rather_than_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CANVAS_BASE_URL", raising=False)
    monkeypatch.setenv("CANVAS_TOKEN", SECRET)
    with pytest.raises(ConfigError, match="CANVAS_BASE_URL"):
        Config.from_env()


def test_public_base_url_must_be_https(monkeypatch: pytest.MonkeyPatch) -> None:
    """Advertising http:// OAuth metadata gets the connector rejected, and
    behind a proxy the inbound scheme is always http -- so this is configured,
    not inferred, and a plain-http value is refused outright."""
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://canvas-viewer-mcp.example.com")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", HASH)
    with pytest.raises(ConfigError, match="https"):
        AuthConfig.from_env()


def test_localhost_over_http_is_allowed_for_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8000")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", HASH)
    assert AuthConfig.from_env().public_base_url == "http://localhost:8000"


def test_password_hash_must_be_argon2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://example.com")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", "hunter2")
    with pytest.raises(ConfigError, match="argon2"):
        AuthConfig.from_env()


def test_token_file_path_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The container supplies the token as a mounted secret rather than an
    environment variable, so the file path must win when CANVAS_TOKEN is unset."""
    token_file = tmp_path / "canvas_token"
    token_file.write_text(f"{SECRET}\n")
    monkeypatch.delenv("CANVAS_TOKEN", raising=False)
    monkeypatch.setenv("CANVAS_BASE_URL", "x.instructure.com")
    monkeypatch.setenv("CANVAS_TOKEN_FILE", str(token_file))

    assert Config.from_env().token == SECRET
