"""The self-issued pairing code that stands in for AUTH_PASSWORD_HASH.

The point of it is a deployment that starts with no credential prepared, so
what matters is that the code is issued exactly once, survives the restarts a
container does routinely, and stops working the moment a real password is
configured.
"""

from __future__ import annotations

import re
import time
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
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
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


def test_repr_says_which_credential_is_configured_without_leaking_it(
    env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Which of the three is in play is not a secret, and knowing it is the
    difference between debugging a wrong password and debugging a code."""
    assert "credential=None (pairing code)" in repr(AuthConfig.from_env())

    digest = hash_password("a-chosen-password")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", digest)
    rendered = repr(AuthConfig.from_env())
    assert "<AUTH_PASSWORD_HASH>" in rendered
    assert digest not in rendered

    monkeypatch.delenv("AUTH_PASSWORD_HASH")
    monkeypatch.setenv("AUTH_PASSWORD", "a-chosen-password")
    rendered = repr(AuthConfig.from_env())
    assert "<AUTH_PASSWORD>" in rendered
    assert "a-chosen-password" not in rendered


# ---- plaintext AUTH_PASSWORD -------------------------------------------------
#
# The form a hosting platform's deploy page can actually collect: it cannot
# run `canvas-probe hash-password` to turn a password into a hash.


def test_a_plaintext_password_authorizes_and_is_never_stored(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AUTH_PASSWORD", "a-chosen-password")
    provider, printed = _start(capsys)

    assert provider.verify_password("a-chosen-password")
    assert not provider.verify_password("something-else")
    assert "pairing code" not in printed
    # Hashed on the way in; the plaintext has no business in the database.
    store = OAuthStore(env)
    assert store.get_server_secret(PAIRING_SECRET_NAME) is None
    store.close()
    provider.store.close()


def test_a_plaintext_password_destroys_a_code_already_issued(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Not merely outranks it -- deletes it. Otherwise clearing AUTH_PASSWORD
    later would bring a code printed into a log aggregator back to life."""
    provider, printed = _start(capsys)
    code = CODE_RE.search(printed).group(1)  # type: ignore[union-attr]
    provider.store.close()

    monkeypatch.setenv("AUTH_PASSWORD", "a-chosen-password")
    configured, _ = _start(capsys)
    assert not configured.verify_password(code)
    configured.store.close()

    monkeypatch.delenv("AUTH_PASSWORD")
    reissued, printed_again = _start(capsys)
    new = CODE_RE.search(printed_again).group(1)  # type: ignore[union-attr]
    assert new != code
    assert not reissued.verify_password(code)
    reissued.store.close()


def test_a_configured_hash_outranks_a_plaintext_password(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both set is a misconfiguration; resolve it the same way every time."""
    monkeypatch.setenv("AUTH_PASSWORD_HASH", hash_password("from-the-hash"))
    monkeypatch.setenv("AUTH_PASSWORD", "from-the-plaintext")
    provider, _ = _start(capsys)

    assert provider.verify_password("from-the-hash")
    assert not provider.verify_password("from-the-plaintext")
    provider.store.close()


# ---- retrying a mistyped password --------------------------------------------


def _park_login(provider: SingleUserOAuthProvider, login_id: str = "parked") -> None:
    """Put a pending login in the store the way /authorize would."""
    provider.store.put_pending_login(
        login_id,
        {
            "client_id": "client-1",
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "redirect_uri_provided_explicitly": True,
            "scopes": [],
            "code_challenge": "challenge",
            "state": "opaque-state",
        },
        expires_at=time.time() + 600,
    )


def test_a_mistyped_password_can_be_retried(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One wrong character must not send the user back to Claude to restart
    authorization. Consuming the parked request on failure bounded nothing --
    a guesser can mint a fresh login_id from /authorize whenever they like --
    while costing every honest typo the whole flow."""
    monkeypatch.setenv("AUTH_PASSWORD", "a-chosen-password")
    provider, _ = _start(capsys)
    _park_login(provider)

    assert provider.complete_login("parked", "a-chosen-passwerd") is None
    assert provider.complete_login("parked", "a-chosen-password") is not None
    provider.store.close()


def test_a_successful_login_is_still_single_use(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The parked request is spent once it has produced a code."""
    monkeypatch.setenv("AUTH_PASSWORD", "a-chosen-password")
    provider, _ = _start(capsys)
    _park_login(provider)

    assert provider.complete_login("parked", "a-chosen-password") is not None
    assert provider.complete_login("parked", "a-chosen-password") is None
    provider.store.close()


def test_an_unknown_login_id_is_refused(
    env: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AUTH_PASSWORD", "a-chosen-password")
    provider, _ = _start(capsys)

    assert provider.complete_login("never-parked", "a-chosen-password") is None
    provider.store.close()
