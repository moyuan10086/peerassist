"""OIDC PKCE browser transactions and server-side session resolution."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..errors import AuthenticationRequired
from ..models import (
    Actor,
    ActorKind,
    BrowserSession,
    ExternalIdentity,
    OidcTransaction,
    User,
)
from ..ports import IdentityProvider, UnitOfWorkFactory

_SYSTEM_ACTOR = Actor(uuid5(NAMESPACE_URL, "peerassist:browser-session-service"), ActorKind.SERVICE)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class SessionStarted:
    transaction_id: UUID
    authorization_url: str
    state: str = field(repr=False)


@dataclass(frozen=True)
class SessionCompleted:
    actor: Actor
    return_path: str
    session_token: str = field(repr=False)
    csrf_token: str = field(repr=False)


class BrowserSessionService:
    """Own the one-time OIDC transaction and opaque browser session lifecycle."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        identity_provider: IdentityProvider,
        *,
        key_ring: dict[str, bytes],
        redirect_uri: str,
        clock=_utc_now,
        transaction_ttl: timedelta = timedelta(minutes=10),
        absolute_ttl: timedelta = timedelta(hours=8),
        idle_ttl: timedelta = timedelta(minutes=30),
    ) -> None:
        if not key_ring or any(len(key) != 32 for key in key_ring.values()):
            raise ValueError("session key ring must contain 32-byte AEAD keys")
        if not redirect_uri.startswith(("http://", "https://")):
            raise ValueError("redirect_uri must be an absolute HTTP(S) URL")
        self._uow_factory = uow_factory
        self._identity_provider = identity_provider
        self._key_ring = dict(key_ring)
        self._active_key_id = next(iter(key_ring))
        self._redirect_uri = redirect_uri
        self._clock = clock
        self._transaction_ttl = transaction_ttl
        self._absolute_ttl = absolute_ttl
        self._idle_ttl = idle_ttl

    def begin(self, return_path: str = "/") -> SessionStarted:
        return_path = _return_path(return_path)
        now = self._clock()
        transaction_id = uuid4()
        state_secret = secrets.token_bytes(32)
        state = _encode_state(transaction_id, state_secret)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        challenge = _base64url(hashlib.sha256(verifier.encode("ascii")).digest())
        protected = self._encrypt(
            transaction_id,
            {"verifier": verifier, "nonce": nonce},
        )
        transaction = OidcTransaction(
            transaction_id,
            _digest(state),
            _digest(nonce),
            protected,
            self._active_key_id,
            return_path,
            now + self._transaction_ttl,
            now,
        )
        with self._uow_factory(_SYSTEM_ACTOR) as uow:
            uow.oidc_transactions.add(transaction)
            uow.commit()
        authorization_url = self._identity_provider.build_authorization_url(
            transaction_id,
            redirect_uri=self._redirect_uri,
            state=state,
            nonce=nonce,
            code_challenge=challenge,
        )
        return SessionStarted(transaction_id, authorization_url, state)

    def complete(self, state: str, authorization_code: str) -> SessionCompleted:
        transaction_id = _transaction_id(state)
        now = self._clock()
        with self._uow_factory(_SYSTEM_ACTOR) as uow:
            transaction = uow.oidc_transactions.get_for_update(transaction_id)
            if (
                transaction is None
                or transaction.consumed_at is not None
                or transaction.expires_at <= now
                or not hmac.compare_digest(transaction.state_digest, _digest(state))
            ):
                raise AuthenticationRequired()
            protected = self._decrypt(transaction)
            identity_claims = self._identity_provider.exchange_callback(
                transaction_id,
                authorization_code,
                redirect_uri=self._redirect_uri,
                code_verifier=protected["verifier"],
                expected_nonce=protected["nonce"],
            )
            identity = uow.users.get_identity(identity_claims.issuer, identity_claims.subject)
            if identity is None:
                user = User(
                    uuid4(),
                    "active",
                    _display_name(identity_claims.claims, identity_claims.subject),
                    now,
                    now,
                )
                identity = ExternalIdentity(
                    uuid4(),
                    user.id,
                    identity_claims.issuer,
                    identity_claims.subject,
                    identity_claims.claims,
                    now,
                    now,
                )
                uow.users.add(user)
                uow.users.add_identity(identity)
            else:
                if identity.disabled_at is not None:
                    raise AuthenticationRequired()
                user = uow.users.get(identity.user_id)
                if user is None or user.status != "active":
                    raise AuthenticationRequired()
                updated = replace(
                    identity,
                    verified_claims=identity_claims.claims,
                    last_seen_at=now,
                    version=identity.version + 1,
                )
                uow.users.save_identity(updated, identity.version)
                identity = updated
            session_token = secrets.token_urlsafe(48)
            csrf_token = secrets.token_urlsafe(32)
            session = BrowserSession(
                uuid4(),
                identity.user_id,
                identity.id,
                _digest(session_token),
                _digest(csrf_token),
                f"identity:{identity.id}",
                now + self._absolute_ttl,
                min(now + self._idle_ttl, now + self._absolute_ttl),
                now,
            )
            uow.browser_sessions.save(session)
            uow.oidc_transactions.delete(transaction.id)
            uow.commit()
        return SessionCompleted(
            Actor(identity.user_id, ActorKind.USER, identity.id),
            transaction.return_path,
            session_token,
            csrf_token,
        )

    def resolve_session(
        self,
        session_token: str,
        *,
        csrf_token: str | None = None,
        require_csrf: bool = False,
    ) -> Actor:
        if not session_token or len(session_token) > 512:
            raise AuthenticationRequired()
        now = self._clock()
        with self._uow_factory(_SYSTEM_ACTOR) as uow:
            session = uow.browser_sessions.get_by_digest(_digest(session_token))
            if (
                session is None
                or session.revoked_at is not None
                or session.expires_at <= now
                or session.idle_expires_at <= now
            ):
                raise AuthenticationRequired()
            if require_csrf and (
                not csrf_token
                or not hmac.compare_digest(session.csrf_digest, _digest(csrf_token))
            ):
                raise AuthenticationRequired()
            identity = uow.users.get_identity_by_id(session.identity_id)
            user = uow.users.get(session.user_id)
            if (
                identity is None
                or identity.user_id != session.user_id
                or identity.disabled_at is not None
                or user is None
                or user.status != "active"
            ):
                raise AuthenticationRequired()
            return Actor(user.id, ActorKind.USER, identity.id)

    def resolve_bearer(self, bearer: str) -> Actor:
        claims = self._identity_provider.validate_bearer(bearer)
        with self._uow_factory(_SYSTEM_ACTOR) as uow:
            identity = uow.users.get_identity(claims.issuer, claims.subject)
            if identity is None or identity.disabled_at is not None:
                raise AuthenticationRequired()
            user = uow.users.get(identity.user_id)
            if user is None or user.status != "active":
                raise AuthenticationRequired()
            return Actor(user.id, ActorKind.USER, identity.id)

    def logout(self, session_token: str, csrf_token: str) -> None:
        actor = self.resolve_session(
            session_token,
            csrf_token=csrf_token,
            require_csrf=True,
        )
        with self._uow_factory(_SYSTEM_ACTOR) as uow:
            session = uow.browser_sessions.get_by_digest(_digest(session_token))
            if session is None or session.user_id != actor.actor_id:
                raise AuthenticationRequired()
            uow.browser_sessions.revoke(session.id, self._clock())
            uow.commit()

    def _encrypt(self, transaction_id: UUID, payload: dict[str, str]) -> bytes:
        nonce = secrets.token_bytes(12)
        plaintext = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return nonce + AESGCM(self._key_ring[self._active_key_id]).encrypt(
            nonce,
            plaintext,
            transaction_id.bytes,
        )

    def _decrypt(self, transaction: OidcTransaction) -> dict[str, str]:
        key = self._key_ring.get(transaction.encryption_key_id)
        if key is None or len(transaction.encrypted_pkce_verifier) < 29:
            raise AuthenticationRequired()
        nonce = transaction.encrypted_pkce_verifier[:12]
        try:
            raw = AESGCM(key).decrypt(
                nonce,
                transaction.encrypted_pkce_verifier[12:],
                transaction.id.bytes,
            )
            payload = json.loads(raw)
        except Exception:
            raise AuthenticationRequired() from None
        if not isinstance(payload, dict) or not all(
            isinstance(payload.get(name), str) and payload[name]
            for name in ("verifier", "nonce")
        ):
            raise AuthenticationRequired()
        return {"verifier": payload["verifier"], "nonce": payload["nonce"]}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _encode_state(transaction_id: UUID, secret: bytes) -> str:
    return _base64url(transaction_id.bytes + secret)


def _transaction_id(state: str) -> UUID:
    try:
        raw = base64.urlsafe_b64decode(state + "=" * (-len(state) % 4))
        if len(raw) != 48:
            raise ValueError
        return UUID(bytes=raw[:16])
    except (ValueError, TypeError):
        raise AuthenticationRequired() from None


def _return_path(value: str) -> str:
    path = str(value or "/").strip()
    parsed = PurePosixPath(path)
    if (
        not path.startswith("/")
        or path.startswith("//")
        or "://" in path
        or ".." in parsed.parts
    ):
        raise ValueError("return_path must be a relative application path")
    return path


def _display_name(claims, subject: str) -> str:
    for name in ("name", "email"):
        value = claims.get(name)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:200]
    return subject[:200]
