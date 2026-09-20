"""Configuration tests, with emphasis on secrets never reaching a log line."""

from __future__ import annotations

from pathlib import Path

import pytest

from canvas_viewer_mcp.config import AuthConfig, Config, ConfigError

SECRET = "9112~notarealtokenbutlongenoughtolooklikeone"
HASH = "$argon2id$v=19$m=65536,t=3,p=4$abc$def"


@pytest.fixture(autouse=True)
def config_file_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point config.toml at a path that does not exist.

    Without this, a real ~/.config/canvas-viewer-mcp/config.toml on the
    machine running the tests would supply base_url, and the "missing host"
    test would pass for the developer who has run setup while failing in CI.
    """
    monkeypatch.setenv("CANVAS_CONFIG_FILE", str(tmp_path / "absent.toml"))


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


# ---- config.toml -------------------------------------------------------------
#
# The file exists so the plugin works with nothing exported in a shell profile.
# Everything below guards the precedence rule, because getting it backwards
# would let a developer's home directory override a deployed container.


def _write_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body)
    monkeypatch.setenv("CANVAS_CONFIG_FILE", str(path))
    return path


def test_base_url_comes_from_the_config_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_config(tmp_path, monkeypatch, 'base_url = "https://x.instructure.com"\n')
    monkeypatch.delenv("CANVAS_BASE_URL", raising=False)
    monkeypatch.setenv("CANVAS_TOKEN", SECRET)

    assert Config.from_env().base_url == "https://x.instructure.com"


def test_environment_wins_over_the_config_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The container is configured from the environment. A leftover config
    file must not be able to redirect a deployed server at another school."""
    _write_config(tmp_path, monkeypatch, 'base_url = "https://stale.instructure.com"\n')
    monkeypatch.setenv("CANVAS_BASE_URL", "https://live.instructure.com")
    monkeypatch.setenv("CANVAS_TOKEN", SECRET)

    assert Config.from_env().base_url == "https://live.instructure.com"


def test_token_file_location_can_come_from_the_config_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_file = tmp_path / "elsewhere" / "token"
    token_file.parent.mkdir()
    token_file.write_text(f"{SECRET}\n")
    _write_config(
        tmp_path,
        monkeypatch,
        f'base_url = "https://x.instructure.com"\ntoken_file = "{token_file}"\n',
    )
    monkeypatch.delenv("CANVAS_TOKEN", raising=False)
    monkeypatch.delenv("CANVAS_TOKEN_FILE", raising=False)

    assert Config.from_env().token == SECRET


def test_the_token_is_not_read_out_of_the_config_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """config.toml is meant to stay safe to paste into a bug report, so a
    token written there is ignored rather than honoured."""
    _write_config(
        tmp_path,
        monkeypatch,
        f'base_url = "https://x.instructure.com"\ntoken = "{SECRET}"\n',
    )
    monkeypatch.delenv("CANVAS_TOKEN", raising=False)
    monkeypatch.setenv("CANVAS_TOKEN_FILE", str(tmp_path / "nothing-here"))

    with pytest.raises(ConfigError, match="No Canvas token"):
        Config.from_env()


def test_a_missing_config_file_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The container ships without one."""
    monkeypatch.setenv("CANVAS_BASE_URL", "x.instructure.com")
    monkeypatch.setenv("CANVAS_TOKEN", SECRET)

    assert Config.from_env().base_url == "https://x.instructure.com"


def test_malformed_config_file_is_reported_not_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Silently falling back would strand the user on the 'no Canvas host'
    error while a file they just edited sat there looking correct."""
    path = _write_config(tmp_path, monkeypatch, "base_url = this is not toml\n")
    monkeypatch.delenv("CANVAS_BASE_URL", raising=False)
    monkeypatch.setenv("CANVAS_TOKEN", SECRET)

    with pytest.raises(ConfigError, match="not valid TOML"):
        Config.from_env()
    assert path.exists()


def test_wrong_type_in_config_file_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _write_config(tmp_path, monkeypatch, "base_url = 42\n")
    monkeypatch.delenv("CANVAS_BASE_URL", raising=False)
    monkeypatch.setenv("CANVAS_TOKEN", SECRET)

    with pytest.raises(ConfigError, match="must be a string"):
        Config.from_env()


def test_token_defaults_to_a_file_beside_the_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Relocating CANVAS_CONFIG_FILE must relocate the token with it, or a
    test run would quietly read the developer's real token from $HOME."""
    _write_config(tmp_path, monkeypatch, 'base_url = "https://x.instructure.com"\n')
    (tmp_path / "token").write_text(f"{SECRET}\n")
    monkeypatch.delenv("CANVAS_TOKEN", raising=False)
    monkeypatch.delenv("CANVAS_TOKEN_FILE", raising=False)

    assert Config.from_env().token == SECRET


# ---- the two values a one-click install must not have to ask for -------------


def test_a_pasted_canvas_deep_link_is_trimmed_to_its_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The instruction people can follow is "copy the address bar", and that
    yields a course URL. Every request joins a path onto this value, so a
    leftover path would corrupt all of them."""
    monkeypatch.setenv(
        "CANVAS_BASE_URL", "https://yourschool.instructure.com/courses/12345/assignments"
    )
    monkeypatch.setenv("CANVAS_TOKEN", SECRET)

    assert Config.from_env().base_url == "https://yourschool.instructure.com"


def test_a_bare_host_still_gets_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANVAS_BASE_URL", "yourschool.instructure.com/")
    monkeypatch.setenv("CANVAS_TOKEN", SECRET)

    assert Config.from_env().base_url == "https://yourschool.instructure.com"


@pytest.mark.parametrize(
    ("variable", "value", "expected"),
    [
        (
            "RAILWAY_PUBLIC_DOMAIN",
            "canvas-abc123.up.railway.app",
            "https://canvas-abc123.up.railway.app",
        ),
        (
            "RENDER_EXTERNAL_URL",
            "https://canvas-viewer.onrender.com",
            "https://canvas-viewer.onrender.com",
        ),
        ("FLY_APP_NAME", "canvas-viewer", "https://canvas-viewer.fly.dev"),
    ],
)
def test_the_platform_supplies_the_public_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, variable: str, value: str, expected: str
) -> None:
    """PUBLIC_BASE_URL cannot be known before the first deploy, because the
    platform assigns the hostname. Reading it from the platform is what makes
    a deploy form with no such field possible."""
    for name in ("PUBLIC_BASE_URL", "RAILWAY_PUBLIC_DOMAIN", "RENDER_EXTERNAL_URL", "FLY_APP_NAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(variable, value)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "auth.sqlite"))

    assert AuthConfig.from_env().public_base_url == expected


def test_an_explicit_public_url_beats_the_platform(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A custom domain in front of the platform is the reason to set it."""
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "canvas-abc123.up.railway.app")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://canvas.example.com")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "auth.sqlite"))

    assert AuthConfig.from_env().public_base_url == "https://canvas.example.com"


def test_no_url_anywhere_is_an_error_naming_both_routes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("PUBLIC_BASE_URL", "RAILWAY_PUBLIC_DOMAIN", "RENDER_EXTERNAL_URL", "FLY_APP_NAME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "auth.sqlite"))

    with pytest.raises(ConfigError, match="no hosting platform supplied one"):
        AuthConfig.from_env()


def test_a_short_auth_password_is_refused_at_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """It is the only thing between a public URL and a Canvas account, and a
    deploy form cannot enforce a length itself."""
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://canvas.example.com")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "auth.sqlite"))
    monkeypatch.setenv("AUTH_PASSWORD", "canvas")

    with pytest.raises(ConfigError, match="at least 12 characters"):
        AuthConfig.from_env()
