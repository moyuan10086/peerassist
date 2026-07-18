from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.utils import to_base64url_uint

from peerassist.platform.adapters.oidc import OidcIdentityProvider
from peerassist.platform.errors import AuthenticationRequired
from peerassist.platform.models import AuthenticatedIdentity


class OidcFixtureServer:
    """In-process discovery/JWKS provider with generated rotating RSA keys."""

    def __init__(self, *, issuer: str = "https://issuer.example") -> None:
        self.issuer = issuer.rstrip("/")
        self.available = True
        self.discovery_status = 200
        self.jwks_status = 200
        self.discovery_requests = 0
        self.jwks_requests = 0
        self._keys: dict[str, rsa.RSAPrivateKey] = {}
        self._published_kids: set[str] = set()
        self.active_kid = self.generate_key(publish=True)
        self.transport = httpx.MockTransport(self._handle)

    def generate_key(self, *, publish: bool) -> str:
        kid = f"key-{len(self._keys) + 1}"
        self._keys[kid] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if publish:
            self._published_kids.add(kid)
        return kid

    def rotate(self) -> str:
        kid = self.generate_key(publish=True)
        self._published_kids = {kid}
        self.active_kid = kid
        return kid

    def publish(self, kid: str) -> None:
        self._published_kids.add(kid)

    def issue(
        self,
        claims: dict[str, object],
        *,
        kid: str | None = None,
        algorithm: str | None = None,
        typ: str | None = "JWT",
    ) -> str:
        selected_kid = kid or self.active_kid
        selected_algorithm = algorithm
        if selected_algorithm is None:
            claim_algorithm = claims.get("alg")
            selected_algorithm = claim_algorithm if isinstance(claim_algorithm, str) else "RS256"
        headers: dict[str, object] = {"kid": selected_kid}
        if typ is not None:
            headers["typ"] = typ
        key: object = self._keys[selected_kid]
        if selected_algorithm.startswith("HS"):
            key = b"fixture-symmetric-key-32-bytes!!"
        elif selected_algorithm == "none":
            key = ""
        return jwt.encode(claims, key, algorithm=selected_algorithm, headers=headers)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if not self.available:
            raise httpx.ConnectError("fixture identity provider unavailable", request=request)
        path = urlsplit(str(request.url)).path
        issuer_path = urlsplit(self.issuer).path.rstrip("/")
        if path == f"{issuer_path}/.well-known/openid-configuration":
            self.discovery_requests += 1
            return httpx.Response(
                self.discovery_status,
                request=request,
                json={
                    "issuer": self.issuer,
                    "jwks_uri": f"{self.issuer}/jwks",
                    "authorization_endpoint": f"{self.issuer}/authorize",
                    "token_endpoint": f"{self.issuer}/token",
                },
            )
        if path == f"{issuer_path}/jwks":
            self.jwks_requests += 1
            return httpx.Response(
                self.jwks_status,
                request=request,
                json={"keys": [self._jwk(kid) for kid in sorted(self._published_kids)]},
            )
        return httpx.Response(404, request=request)

    def _jwk(self, kid: str) -> dict[str, object]:
        public = self._keys[kid].public_key().public_numbers()
        return {
            "alg": "RS256",
            "e": to_base64url_uint(public.e).decode(),
            "kid": kid,
            "kty": "RSA",
            "n": to_base64url_uint(public.n).decode(),
            "use": "sig",
        }


class FixtureIdentityProvider:
    """Contract harness around the production discovery/JWKS adapter."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        accepted_algorithms: frozenset[str],
        clock: Callable[[], datetime],
        disabled_identities: set[tuple[str, str]] | None = None,
        cache_ttl_seconds: float = 300,
        server: OidcFixtureServer | None = None,
    ) -> None:
        self.server = server or OidcFixtureServer(issuer=issuer)
        client = httpx.Client(transport=self.server.transport)
        self._provider = OidcIdentityProvider(
            issuer=issuer,
            audience=audience,
            accepted_algorithms=accepted_algorithms,
            clock=clock,
            disabled_identities=disabled_identities,
            cache_ttl_seconds=cache_ttl_seconds,
            http_client=client,
        )
        self._callback_claims: dict[tuple[UUID, str], dict[str, object]] = {}
        self._consumed_transactions: set[UUID] = set()

    def issue(self, claims: dict[str, object], **kwargs: Any) -> str:
        return self.server.issue(claims, **kwargs)

    def issue_and_validate(self, claims: dict[str, object]) -> AuthenticatedIdentity:
        return self.validate_bearer(self.issue(claims))

    def validate_bearer(self, bearer: str) -> AuthenticatedIdentity:
        return self._provider.validate_bearer(bearer)

    def build_authorization_url(self, transaction_id: UUID) -> str:
        return self._provider.build_authorization_url(transaction_id)

    def issue_callback(self, transaction_id: UUID, claims: dict[str, object]) -> str:
        code = uuid4().hex
        self._callback_claims[(transaction_id, code)] = copy.deepcopy(claims)
        return code

    def exchange_callback(
        self, transaction_id: UUID, authorization_code: str
    ) -> AuthenticatedIdentity:
        key = (transaction_id, authorization_code)
        claims = self._callback_claims.pop(key, None)
        if claims is None or transaction_id in self._consumed_transactions:
            raise AuthenticationRequired()
        self._consumed_transactions.add(transaction_id)
        return self.issue_and_validate(claims)
