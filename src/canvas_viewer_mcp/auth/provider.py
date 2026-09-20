"""A single-user OAuth 2.1 authorization server.

FastMCP's ``OAuthProvider`` supplies the protocol mechanics -- Dynamic Client
Registration, PKCE, the well-known metadata documents, and the token
endpoints. What it leaves to the implementer is storage and the one thing that
actually matters for security here: deciding whether the human at the browser
is allowed in.

The flow differs from the bundled in-memory provider in exactly one respect,
and it is the important one. That provider issues an authorization code
immediately, simulating a user who always consents. This one cannot: anyone on
the internet can reach ``/authorize``. So ``authorize()`` issues no code. It
parks the request and redirects to a password-gated login page, and only a
correct password turns a parked request into a code.

Single user throughout. There is no user table, no registration, no account
recovery -- one argon2 hash. That hash comes from AUTH_PASSWORD_HASH, or from
a plaintext AUTH_PASSWORD hashed at startup, or, when neither is set, from a
pairing code the server issues itself on first run and prints once to the logs.
Configuring either password form wins outright and deletes the stored code,
which is how a code printed into a log aggregator gets revoked.
"""

from __future__ import annotations

import secrets
import sys
import time
from urllib.parse import urlencode

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from fastmcp.server.auth import AccessToken, OAuthProvider
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from ..config import AuthConfig
from .store import OAuthStore

AUTH_CODE_TTL = 5 * 60
ACCESS_TOKEN_TTL = 60 * 60
REFRESH_TOKEN_TTL = 60 * 60 * 24 * 90
PENDING_LOGIN_TTL = 10 * 60

_hasher = PasswordHasher()

PAIRING_SECRET_NAME = "pairing_code_hash"

# No I, L, O, 0 or 1: the code gets read off a terminal and retyped into a
# browser, and those are the characters that get read wrong.
_PAIRING_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_PAIRING_LENGTH = 16


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def generate_pairing_code() -> str:
    """A random code shaped like something a person can retype.

    Sixteen characters from a 31-character alphabet is a shade under 80 bits,
    which is far past what the login page's throttling and argon2 already make
    unguessable.
    """
    raw = "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(_PAIRING_LENGTH))
    return "-".join(raw[i : i + 4] for i in range(0, _PAIRING_LENGTH, 4))


def _announce_pairing_code(code: str, public_base_url: str) -> None:
    """Print the code once, to stderr so it lands in the container logs.

    This is the one moment the plaintext exists outside the operator's head,
    and it is a real trade: a code in a log file is less protected than a hash
    in a file only root reads. It buys a deployment that starts without any
    credential having to be generated, quoted and pasted first.
    """
    print(
        "\n"
        "  ---------------------------------------------------------------\n"
        "  No password is set, so this server issued itself a\n"
        "  pairing code. Use it as the password when Claude sends you to\n"
        "  the login page.\n"
        "\n"
        f"      pairing code:   {code}\n"
        f"      connector URL:  {public_base_url}/mcp\n"
        "\n"
        "  Printed once; only its hash is stored. `canvas-probe\n"
        "  reset-pairing` then a restart issues a new one. Setting\n"
        "  AUTH_PASSWORD replaces it and revokes this code.\n"
        "  ---------------------------------------------------------------\n",
        file=sys.stderr,
        flush=True,
    )


class SingleUserOAuthProvider(OAuthProvider):
    """OAuth server for exactly one human, backed by SQLite."""

    def __init__(self, auth_config: AuthConfig) -> None:
        super().__init__(
            base_url=auth_config.public_base_url,
            # Dynamic Client Registration is not optional: Claude's mobile
            # apps register themselves and cannot use a preconfigured client.
            client_registration_options=ClientRegistrationOptions(enabled=True),
        )
        self.auth_config = auth_config
        self.store = OAuthStore(auth_config.db_path)
        self.store.purge_expired()
        self._password_hash = self._resolve_credential()

    # ---- password ------------------------------------------------------------

    def _resolve_credential(self) -> str:
        """The one argon2 hash this server will accept.

        Three sources, in order: AUTH_PASSWORD_HASH, a plaintext AUTH_PASSWORD
        hashed here, and failing both a pairing code the server issues itself.

        A configured credential wins outright *and* deletes any stored pairing
        code rather than sitting alongside it. Otherwise a code printed into a
        log aggregator months ago would keep working after a password was set,
        and worse, would silently come back to life the day that password was
        cleared. Setting a password is meant to be how you revoke a code, so
        it has to actually destroy it.
        """
        configured = self.auth_config.password_hash or (
            hash_password(self.auth_config.password) if self.auth_config.password else None
        )
        if configured is not None:
            self.store.delete_server_secret(PAIRING_SECRET_NAME)
            return configured

        stored = self.store.get_server_secret(PAIRING_SECRET_NAME)
        if stored is not None:
            return stored

        code = generate_pairing_code()
        digest = hash_password(code)
        self.store.put_server_secret(PAIRING_SECRET_NAME, digest)
        _announce_pairing_code(code, self.auth_config.public_base_url)
        return digest

    def verify_password(self, password: str) -> bool:
        try:
            _hasher.verify(self._password_hash, password)
        except (VerifyMismatchError, VerificationError):
            return False
        return True

    # ---- clients -------------------------------------------------------------

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = self.store.get_client(client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id is None:
            raise ValueError("client_id is required for client registration")
        self.store.put_client(client_info.client_id, client_info.model_dump(mode="json"))

    # ---- authorization -------------------------------------------------------

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Park the request and send the browser to the login page.

        No authorization code is minted here. Anyone can reach this endpoint,
        so issuing a code before authenticating the human would hand account
        access to whoever asked for it.
        """
        if client.client_id is None or self.store.get_client(client.client_id) is None:
            raise AuthorizeError(
                error="unauthorized_client",
                error_description="Client is not registered.",
            )

        login_id = secrets.token_urlsafe(32)
        self.store.put_pending_login(
            login_id,
            {
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "state": params.state,
                "scopes": params.scopes or [],
                "code_challenge": params.code_challenge,
            },
            time.time() + PENDING_LOGIN_TTL,
        )
        query = urlencode({"login_id": login_id})
        return f"{self.auth_config.public_base_url}/login?{query}"

    def complete_login(self, login_id: str, password: str) -> str | None:
        """Turn a parked request into a code, if the password is right.

        Returns the redirect URI to send the browser to, or None when the
        login is wrong or expired.

        A wrong password leaves the parked request in place so the person can
        simply type it again. Consuming it on failure would mean that one
        mistyped character sends them back to Claude to restart authorization
        from the beginning -- and it buys nothing, because anyone guessing
        passwords can mint a fresh ``login_id`` by starting their own
        ``/authorize`` at any time. Guessing is bounded by the login route's
        per-IP throttle, which is the control that actually applies.

        A correct password still consumes it, so a code is issued once.
        """
        pending = self.store.get_pending_login(login_id)
        if pending is None:
            return None
        if not self.verify_password(password):
            return None
        self.store.take_pending_login(login_id)

        code = secrets.token_urlsafe(32)
        self.store.put_auth_code(
            code,
            {
                "code": code,
                "client_id": pending["client_id"],
                "redirect_uri": pending["redirect_uri"],
                "redirect_uri_provided_explicitly": pending["redirect_uri_provided_explicitly"],
                "scopes": pending["scopes"],
                "expires_at": time.time() + AUTH_CODE_TTL,
                "code_challenge": pending["code_challenge"],
            },
            time.time() + AUTH_CODE_TTL,
        )
        return construct_redirect_uri(pending["redirect_uri"], code=code, state=pending["state"])

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        data = self.store.get_auth_code(authorization_code)
        if data is None or data["client_id"] != client.client_id:
            return None
        return AuthorizationCode.model_validate(data)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if self.store.get_auth_code(authorization_code.code) is None:
            raise TokenError("invalid_grant", "Authorization code not found or already used.")
        # Consume immediately: an authorization code is single-use, and
        # replaying one is a standard attack rather than an edge case.
        self.store.delete_auth_code(authorization_code.code)

        if client.client_id is None:
            raise TokenError("invalid_client", "Client ID is required")
        return self._issue_tokens(client.client_id, list(authorization_code.scopes))

    # ---- tokens --------------------------------------------------------------

    def _issue_tokens(self, client_id: str, scopes: list[str]) -> OAuthToken:
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        access_expires = time.time() + ACCESS_TOKEN_TTL
        refresh_expires = time.time() + REFRESH_TOKEN_TTL

        self.store.put_access_token(
            access,
            {
                "token": access,
                "client_id": client_id,
                "scopes": scopes,
                "expires_at": int(access_expires),
            },
            access_expires,
        )
        self.store.put_refresh_token(
            refresh,
            {
                "token": refresh,
                "client_id": client_id,
                "scopes": scopes,
                "expires_at": int(refresh_expires),
            },
            refresh_expires,
        )
        self.store.link_tokens(access, refresh)

        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL,
            refresh_token=refresh,
            scope=" ".join(scopes),
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        data = self.store.get_refresh_token(refresh_token)
        if data is None or data["client_id"] != client.client_id:
            return None
        return RefreshToken.model_validate(data)

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if not set(scopes).issubset(set(refresh_token.scopes)):
            raise TokenError(
                "invalid_scope", "Requested scopes exceed those authorized by the token."
            )
        # Rotate: the presented refresh token and its access token both die here.
        self._revoke_pair(refresh_token=refresh_token.token)

        if client.client_id is None:
            raise TokenError("invalid_client", "Client ID is required")
        return self._issue_tokens(client.client_id, scopes or list(refresh_token.scopes))

    async def load_access_token(self, token: str) -> AccessToken | None:
        data = self.store.get_access_token(token)
        return AccessToken.model_validate(data) if data else None

    async def verify_token(self, token: str) -> AccessToken | None:
        return await self.load_access_token(token)

    # ---- revocation ----------------------------------------------------------

    def _revoke_pair(
        self, *, access_token: str | None = None, refresh_token: str | None = None
    ) -> None:
        access, refresh = self.store.paired_tokens(
            access_token=access_token, refresh_token=refresh_token
        )
        if access:
            self.store.delete_access_token(access)
            self.store.unlink(access)
        if refresh:
            self.store.delete_refresh_token(refresh)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """Revoke both halves of a pair.

        Dropping only the presented token would leave its partner live, so a
        revoked session could be resurrected with the refresh token.
        """
        if isinstance(token, AccessToken):
            self._revoke_pair(access_token=token.token)
        else:
            self._revoke_pair(refresh_token=token.token)
