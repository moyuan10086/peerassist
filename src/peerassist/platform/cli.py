"""Private operator CLI for platform bootstrap and global identity controls."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import IO
from uuid import UUID

from common.config import PlatformSettings

from .models import Actor, ActorKind
from .ports import UnitOfWorkFactory
from .services.bootstrap import (
    BootstrapOrganization,
    BootstrapOrganizationService,
    ChangeIdentity,
    IdentityOperatorService,
)


class _UsageError(Exception):
    pass


_MAX_OPERATOR_SECRET_LENGTH = 4096


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _UsageError


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="peerassist-platform", add_help=True)
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the local operator password from protected standard input",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    bootstrap = commands.add_parser("bootstrap-organization")
    bootstrap.add_argument("--issuer", required=True)
    bootstrap.add_argument("--subject", required=True)
    bootstrap.add_argument("--slug", required=True)
    bootstrap.add_argument("--name", required=True)
    bootstrap.add_argument("--idempotency-key", required=True)

    for name in ("disable-identity", "unlink-identity"):
        identity = commands.add_parser(name)
        identity.add_argument("--issuer", required=True)
        identity.add_argument("--subject", required=True)
        identity.add_argument("--expected-version", required=True, type=int)
        identity.add_argument("--audit-organization-id", required=True, type=UUID)
        identity.add_argument("--idempotency-key", required=True)
    return parser


def _authenticate_operator(
    *,
    password_stdin: bool,
    environ: Mapping[str, str],
    stdin: IO[str],
) -> Actor:
    password = stdin.readline(_MAX_OPERATOR_SECRET_LENGTH + 1).rstrip("\r\n") if password_stdin else environ.get(
        "PEERASSIST_OPERATOR_PASSWORD", ""
    )
    credential = environ.get("PEERASSIST_OPERATOR_PASSWORD_CREDENTIAL", "")
    if (
        not password
        or len(password) > _MAX_OPERATOR_SECRET_LENGTH
        or len(credential) > _MAX_OPERATOR_SECRET_LENGTH
    ):
        raise _UsageError
    try:
        scheme, iterations_text, salt_hex, expected_hex = credential.split("$", 3)
        iterations = int(iterations_text)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
        if (
            scheme != "pbkdf2-sha256"
            or not 100_000 <= iterations <= 2_000_000
            or not 16 <= len(salt) <= 64
            or len(expected) != 32
        ):
            raise ValueError
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        if not hmac.compare_digest(actual, expected):
            raise ValueError
        operator_id = UUID(environ["PEERASSIST_OPERATOR_ID"])
    except (KeyError, UnicodeEncodeError, ValueError):
        raise _UsageError from None
    del password
    return Actor(operator_id, ActorKind.OPERATOR)


def _safe_write(stream: IO[str], payload: dict[str, object]) -> None:
    stream.write(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")


def run_cli(
    argv: Sequence[str],
    *,
    uow_factory: UnitOfWorkFactory,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    environ: Mapping[str, str] | None = None,
    stdin: IO[str] | None = None,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
) -> int:
    environment = os.environ if environ is None else environ
    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    error_stream = sys.stderr if stderr is None else stderr
    try:
        args = _parser().parse_args(list(argv))
        actor = _authenticate_operator(
            password_stdin=args.password_stdin,
            environ=environment,
            stdin=input_stream,
        )
        request_id = f"operator-{actor.actor_id.hex}"
        if args.command == "bootstrap-organization":
            result = BootstrapOrganizationService(uow_factory, clock=clock).execute(
                actor,
                BootstrapOrganization(
                    args.issuer,
                    args.subject,
                    args.slug,
                    args.name,
                    args.idempotency_key,
                    request_id,
                ),
            )
            _safe_write(
                output_stream,
                {
                    "membership_id": str(result.membership.id),
                    "organization_id": str(result.organization.id),
                    "status": "ok",
                },
            )
            return 0
        change = ChangeIdentity(
            args.issuer,
            args.subject,
            args.expected_version,
            args.idempotency_key,
            request_id,
            args.audit_organization_id,
        )
        service = IdentityOperatorService(uow_factory, clock=clock)
        identity = (
            service.disable(actor, change)
            if args.command == "disable-identity"
            else service.unlink(actor, change)
        )
        _safe_write(
            output_stream,
            {"identity_id": str(identity.id), "status": "ok", "version": identity.version},
        )
        return 0
    except _UsageError:
        _safe_write(error_stream, {"error": {"code": "invalid_operator_input"}})
        return 2
    except Exception as error:
        code = getattr(error, "code", "platform_error")
        _safe_write(error_stream, {"error": {"code": code}})
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    try:
        settings = PlatformSettings()
        if settings.environment == "production":
            from .adapters.postgres import PostgresUnitOfWorkFactory

            uow_factory = PostgresUnitOfWorkFactory.from_url(
                settings.database_url.get_secret_value()
            )
        else:
            from .adapters.memory import MemoryUnitOfWorkFactory

            uow_factory = MemoryUnitOfWorkFactory()
    except Exception:
        _safe_write(sys.stderr, {"error": {"code": "platform_error"}})
        return 1
    return run_cli(
        sys.argv[1:] if argv is None else argv,
        uow_factory=uow_factory,
    )


if __name__ == "__main__":
    raise SystemExit(main())
