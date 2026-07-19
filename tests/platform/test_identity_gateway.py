from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient
from services.api.app import create_app
from services.api.composition import PlatformDependencies

from common.config import PlatformSettings


class _IdentityHandler(BaseHTTPRequestHandler):
    observed_path = ""

    def do_GET(self) -> None:
        type(self).observed_path = self.path
        body = b'{"issuer":"proxied"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def test_identity_gateway_keeps_keycloak_private_and_preserves_the_public_path() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _IdentityHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings = PlatformSettings(
        environment="test",
        database_url="postgresql+psycopg://test:test@db/peerassist",
        oidc_issuer="http://testserver/identity/realms/peerassist",
        oidc_audience="peerassist-api",
        s3_endpoint="http://objects.test",
        s3_bucket="peerassist-test",
        public_base_url="http://testserver",
        allowed_origins=["http://testserver"],
        internal_legacy_audience="peerassist-legacy",
        identity_gateway_url=f"http://127.0.0.1:{server.server_port}/identity",
    )
    try:
        with TestClient(create_app(settings, PlatformDependencies.for_test())) as client:
            response = client.get("/identity/realms/peerassist/.well-known?probe=1")
        assert response.status_code == 200
        assert response.json() == {"issuer": "proxied"}
        assert _IdentityHandler.observed_path == "/identity/realms/peerassist/.well-known?probe=1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
