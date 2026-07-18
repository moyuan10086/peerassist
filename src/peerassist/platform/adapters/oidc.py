"""Generic OIDC discovery and rotating-JWKS identity validation."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import httpx
import jwt

from ..errors import AuthenticationRequired
from ..models import AuthenticatedIdentity, JsonValue

_ASYMMETRIC_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"})
_ACCESS_TOKEN_TYPES = frozenset({"JWT", "at+jwt"})
_MAX_CACHE_TTL_SECONDS = 86_400.0
_MAX_HTTP_TIMEOUT_SECONDS = 30.0
_MAX_DOCUMENT_BYTES = 1_048_576
_MAX_JWKS_KEYS = 64
_MAX_TOKEN_BYTES = 65_536


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class _Discovery:
    authorization_endpoint: str
    jwks_uri: str
    expires_at: float


@dataclass(frozen=True)
class _Jwks:
    keys: Mapping[str, Mapping[str, object]]
    expires_at: float


class OidcIdentityProvider:
    """Validate OIDC JWTs without retaining bearer values or provider responses."""

    name = "identity"

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        accepted_algorithms: frozenset[str],
        clock: Callable[[], datetime] = _utc_now,
        disabled_identities: set[tuple[str, str]] | None = None,
        require_https: bool = False,
        cache_ttl_seconds: float = 300.0,
        http_timeout_seconds: float = 5.0,
        clock_skew_seconds: float = 0.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        normalized_issuer = issuer.strip().rstrip("/")
        self._validate_url(normalized_issuer, require_https=require_https, label="issuer")
        if not audience.strip():
            raise ValueError("OIDC audience must not be empty")
        algorithms = frozenset(accepted_algorithms)
        if not algorithms or not algorithms <= _ASYMMETRIC_ALGORITHMS:
            raise ValueError("OIDC algorithms must use the asymmetric allowlist")
        if not 0 < cache_ttl_seconds <= _MAX_CACHE_TTL_SECONDS:
            raise ValueError("OIDC cache TTL is outside the supported bounds")
        if not 0 < http_timeout_seconds <= _MAX_HTTP_TIMEOUT_SECONDS:
            raise ValueError("OIDC HTTP timeout is outside the supported bounds")
        if not 0 <= clock_skew_seconds <= 300:
            raise ValueError("OIDC clock skew is outside the supported bounds")

        self.issuer = normalized_issuer
        self.audience = audience.strip()
        self.accepted_algorithms = algorithms
        self._clock = clock
        self._disabled_identities = frozenset(disabled_identities or ())
        self._require_https = require_https
        self._cache_ttl_seconds = float(cache_ttl_seconds)
        self._clock_skew_seconds = float(clock_skew_seconds)
        self._http_timeout = httpx.Timeout(float(http_timeout_seconds))
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(timeout=self._http_timeout, follow_redirects=False)
        self._discovery: _Discovery | None = None
        self._jwks: _Jwks | None = None
        self._cache_lock = threading.RLock()

    def validate_bearer(self, bearer: str) -> AuthenticatedIdentity:
        """Return only normalized claims after complete cryptographic validation."""

        if not isinstance(bearer, str) or not bearer or len(bearer.encode("utf-8")) > _MAX_TOKEN_BYTES:
            raise AuthenticationRequired()
        try:
            header = jwt.get_unverified_header(bearer)
            algorithm = header.get("alg")
            token_type = header.get("typ")
            kid = header.get("kid")
            if (
                not isinstance(algorithm, str)
                or algorithm == "none"
                or algorithm not in self.accepted_algorithms
                or token_type not in _ACCESS_TOKEN_TYPES
                or not isinstance(kid, str)
                or not kid
            ):
                raise AuthenticationRequired()
            with self._cache_lock:
                key = self._signing_key(kid, algorithm)
            claims = jwt.decode(
                bearer,
                key=key,
                algorithms=[algorithm],
                audience=self.audience,
                issuer=self.issuer,
                options={
                    "require": ["iss", "sub", "aud", "exp"],
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                    "verify_sub": True,
                },
            )
            return self._identity(claims)
        except AuthenticationRequired:
            raise
        except (httpx.HTTPError, jwt.PyJWTError, KeyError, TypeError, ValueError):
            raise AuthenticationRequired() from None

    def build_authorization_url(self, transaction_id: UUID) -> str:
        try:
            with self._cache_lock:
                discovery = self._load_documents()[0]
            return f"{discovery.authorization_endpoint}?{urlencode({'transaction_id': str(transaction_id)})}"
        except AuthenticationRequired:
            raise
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            raise AuthenticationRequired() from None

    def exchange_callback(
        self, transaction_id: UUID, authorization_code: str
    ) -> AuthenticatedIdentity:
        del transaction_id, authorization_code
        raise AuthenticationRequired()

    async def check(self) -> bool:
        try:
            with self._cache_lock:
                self._refresh_all()
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return False
        return True

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        if self._owns_client:
            self._http.close()

    def _signing_key(self, kid: str, algorithm: str) -> object:
        _, jwks = self._load_documents()
        raw_key = jwks.keys.get(kid)
        if raw_key is None:
            jwks = self._refresh_jwks()
            raw_key = jwks.keys.get(kid)
        if raw_key is None:
            raise AuthenticationRequired()
        if raw_key.get("use") not in {None, "sig"}:
            raise AuthenticationRequired()
        key_ops = raw_key.get("key_ops")
        if key_ops is not None and (
            not isinstance(key_ops, list) or "verify" not in key_ops
        ):
            raise AuthenticationRequired()
        advertised_algorithm = raw_key.get("alg")
        if advertised_algorithm is not None and advertised_algorithm != algorithm:
            raise AuthenticationRequired()
        expected_key_type = "RSA" if algorithm.startswith("RS") else "EC"
        if raw_key.get("kty") != expected_key_type:
            raise AuthenticationRequired()
        try:
            return jwt.PyJWK.from_dict(dict(raw_key), algorithm=algorithm).key
        except jwt.PyJWTError:
            raise AuthenticationRequired() from None

    def _load_documents(self) -> tuple[_Discovery, _Jwks]:
        now = self._timestamp()
        if (
            self._discovery is None
            or self._jwks is None
            or self._discovery.expires_at <= now
            or self._jwks.expires_at <= now
        ):
            return self._refresh_all()
        return self._discovery, self._jwks

    def _refresh_all(self) -> tuple[_Discovery, _Jwks]:
        now = self._timestamp()
        document = self._get_json(f"{self.issuer}/.well-known/openid-configuration")
        if document.get("issuer") != self.issuer:
            raise ValueError("OIDC discovery issuer mismatch")
        jwks_uri = self._endpoint(document, "jwks_uri")
        authorization_endpoint = self._endpoint(document, "authorization_endpoint")
        jwks = self._fetch_jwks(jwks_uri, now)
        discovery = _Discovery(
            authorization_endpoint=authorization_endpoint,
            jwks_uri=jwks_uri,
            expires_at=now + self._cache_ttl_seconds,
        )
        self._discovery = discovery
        self._jwks = jwks
        return discovery, jwks

    def _refresh_jwks(self) -> _Jwks:
        discovery = self._discovery
        now = self._timestamp()
        if discovery is None or discovery.expires_at <= now:
            return self._refresh_all()[1]
        jwks = self._fetch_jwks(discovery.jwks_uri, now)
        self._jwks = jwks
        return jwks

    def _fetch_jwks(self, uri: str, now: float) -> _Jwks:
        document = self._get_json(uri)
        raw_keys = document.get("keys")
        if not isinstance(raw_keys, list) or not 0 < len(raw_keys) <= _MAX_JWKS_KEYS:
            raise ValueError("OIDC JWKS has an invalid key collection")
        keys: dict[str, Mapping[str, object]] = {}
        for raw_key in raw_keys:
            if not isinstance(raw_key, dict) or any(not isinstance(key, str) for key in raw_key):
                raise ValueError("OIDC JWKS contains an invalid key")
            kid = raw_key.get("kid")
            if not isinstance(kid, str) or not kid or kid in keys:
                raise ValueError("OIDC JWKS contains an invalid key identifier")
            keys[kid] = dict(raw_key)
        return _Jwks(keys=keys, expires_at=now + self._cache_ttl_seconds)

    def _get_json(self, url: str) -> dict[str, object]:
        self._validate_url(url, require_https=self._require_https, label="OIDC endpoint")
        response = self._http.get(url, timeout=self._http_timeout)
        response.raise_for_status()
        if len(response.content) > _MAX_DOCUMENT_BYTES:
            raise ValueError("OIDC document exceeds the size limit")
        document: Any = response.json()
        if not isinstance(document, dict) or any(not isinstance(key, str) for key in document):
            raise ValueError("OIDC document must be a JSON object")
        return document

    def _endpoint(self, document: Mapping[str, object], name: str) -> str:
        value = document.get(name)
        if not isinstance(value, str):
            raise ValueError(f"OIDC discovery is missing {name}")
        self._validate_url(value, require_https=self._require_https, label=name)
        return value

    def _identity(self, claims: Mapping[str, object]) -> AuthenticatedIdentity:
        now = self._timestamp()
        issuer = claims.get("iss")
        subject = claims.get("sub")
        expires = claims.get("exp")
        not_before = claims.get("nbf")
        if issuer != self.issuer or not isinstance(subject, str) or not subject.strip():
            raise AuthenticationRequired()
        if isinstance(expires, bool) or not isinstance(expires, (int, float)):
            raise AuthenticationRequired()
        if expires <= now - self._clock_skew_seconds:
            raise AuthenticationRequired()
        if not_before is not None and (
            isinstance(not_before, bool)
            or not isinstance(not_before, (int, float))
            or not_before > now + self._clock_skew_seconds
        ):
            raise AuthenticationRequired()
        normalized: dict[str, JsonValue] = {
            "iss": issuer,
            "sub": subject,
            "aud": self._normalized_audience(claims.get("aud")),
        }
        email = claims.get("email")
        if claims.get("email_verified") is True and isinstance(email, str) and email.strip():
            normalized["email"] = email.strip().casefold()
            normalized["email_verified"] = True
        name = claims.get("name")
        if isinstance(name, str) and name.split():
            normalized["name"] = " ".join(name.split())
        if (issuer, subject) in self._disabled_identities:
            raise AuthenticationRequired()
        try:
            expires_at = datetime.fromtimestamp(float(expires), UTC)
        except (OverflowError, OSError, ValueError):
            raise AuthenticationRequired() from None
        return AuthenticatedIdentity(issuer, subject, normalized, expires_at)

    def _normalized_audience(self, audience: object) -> JsonValue:
        if isinstance(audience, str):
            return audience
        if isinstance(audience, list) and all(isinstance(value, str) for value in audience):
            return list(audience)
        raise AuthenticationRequired()

    def _timestamp(self) -> float:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("OIDC clock must return an aware datetime")
        return now.timestamp()

    @staticmethod
    def _validate_url(url: str, *, require_https: bool, label: str) -> None:
        parsed = urlsplit(url)
        allowed_schemes = {"https"} if require_https else {"http", "https"}
        if (
            parsed.scheme not in allowed_schemes
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            requirement = "HTTPS" if require_https else "HTTP(S)"
            raise ValueError(f"{label} must use safe {requirement}")
