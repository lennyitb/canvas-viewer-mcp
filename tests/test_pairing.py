"""The self-issued pairing code that stands in for AUTH_PASSWORD_HASH.

The point of it is a deployment that starts with no credential prepared, so
what matters is that the code is issued exactly once, survives the restarts a
container does routinely, and stops working the moment a real password is
configured.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from canvas_viewer_mcp.auth.provider import (
    PAIRING_SECRET_NAME,
    SingleUserOAuthProvider,
    generate_pairing_code,
    hash_password,
)
from canvas_viewer_mcp.auth.store import OAuthStore
from canvas_viewer_mcp.config import AuthConfig

PUBLIC_URL = "https://mcp.example.com"
CODE_RE = re.compile(r"pairing code:\s+(\S+)")


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    db = tmp_path / "auth.sqlite"
    monkeypatch.setenv("PUBLIC_BASE_URL", PUBLIC_URL)
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.delenv("AUTH_PASSWORD_HASH", raising=False)
    return db


def _start(capsys: pytest.CaptureFixture[str]) -> tuple[SingleUserOAuthProvider, str]:
    """Start a provider and return it with whatever it printed to stderr."""
    provider = SingleUserOAuthProvider(AuthConfig.from_env())
    return provider, capsys.readouterr().err


def test_a_code_is_issued_and_authorizes(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    provider, printed = _start(capsys)
    match = CODE_RE.search(printed)
    assert match, f"no pairing code in the announcement: {printed!r}"

    assert provider.verify_password(match.group(1))
    assert not provider.verify_password("NOTT-HECO-DEAT-ALL1")
    provider.store.close()


def test_the_code_is_printed_once_and_survives_a_restart(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A container restarts for reasons that have nothing to do with auth.
    Reprinting would put the plaintext in the logs again and again; reissuing
    would silently break the connector."""
    first, printed = _start(capsys)
    code = CODE_RE.search(printed).group(1)  # type: ignore[union-attr]
    first.store.close()

    second, reprinted = _start(capsys)
    assert reprinted == ""
    assert second.verify_password(code)
    second.store.close()


def test_only_the_hash_is_stored(env: Path, capsys: pytest.CaptureFixture[str]) -> None:
    provider, printed = _start(capsys)
    code = CODE_RE.search(printed).group(1)  # type: ignore[union-attr]
    provider.store.close()

    store = OAuthStore(env)
    stored = store.get_server_secret(PAIRING_SECRET_NAME)
    store.close()
    assert stored is not None
    assert stored.startswith("$argon2")
    assert code not in stored


def test_configuring_a_password_revokes_the_issued_code(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Setting AUTH_PASSWORD_HASH is the documented way to retire a code that
    was printed into a log aggregator, so it has to actually retire it."""
    provider, printed = _start(capsys)
    code = CODE_RE.search(printed).group(1)  # type: ignore[union-attr]
    provider.store.close()

    monkeypatch.setenv("AUTH_PASSWORD_HASH", hash_password("a-chosen-password"))
    configured, _ = _start(capsys)

    assert configured.verify_password("a-chosen-password")
    assert not configured.verify_password(code)
    configured.store.close()


def test_resetting_issues_a_different_code_and_invalidates_the_old_one(
    env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    provider, printed = _start(capsys)
    old = CODE_RE.search(printed).group(1)  # type: ignore[union-attr]
    provider.store.close()

    store = OAuthStore(env)
    store.delete_server_secret(PAIRING_SECRET_NAME)
    store.close()

    reissued, printed_again = _start(capsys)
    new = CODE_RE.search(printed_again).group(1)  # type: ignore[union-attr]

    assert new != old
    assert reissued.verify_password(new)
    assert not reissued.verify_password(old)
    reissued.store.close()


def test_the_code_avoids_characters_that_get_misread() -> None:
    """It is read off a terminal and retyped into a browser."""
    for _ in range(50):
        code = generate_pairing_code()
        assert re.fullmatch(r"[A-Z2-9]{4}(-[A-Z2-9]{4}){3}", code), code
        assert not set("ILO01") & set(code)


def test_auth_config_no_longer_demands_a_password_hash(env: Path) -> None:
    """The whole point: a first run needs nothing but a public URL."""
    assert AuthConfig.from_env().password_hash is None


def test_repr_says_whether_one_is_configured_without_leaking_it(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "password_hash=None" in repr(AuthConfig.from_env())

    digest = hash_password("a-chosen-password")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", digest)
    rendered = repr(AuthConfig.from_env())
    assert "<redacted>" in rendered
    assert digest not in rendered
