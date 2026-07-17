# PeerAssist M1 Platform Authorization Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the complete M1 authenticated platform boundary with FastAPI, PostgreSQL, generic OIDC/Keycloak, tenant-enforced RBAC, S3/MinIO persistence, PostgreSQL-backed review execution, and an authorized read-only legacy fallback.

**Architecture:** Extend the M0 modular monolith with a framework-independent `peerassist.platform` application core, provider ports, and memory/PostgreSQL/OIDC/S3/legacy adapters. FastAPI is the only public API, PostgreSQL/S3 own all new writes, and existing file-backed resources remain read-only registrations. The existing review stages execute in private scratch workspaces materialized from object storage and commit state/events/work atomically through PostgreSQL.

**Tech Stack:** Python 3.11/3.12, Pydantic 2, FastAPI, Uvicorn, SQLAlchemy 2, Psycopg 3, Alembic, PyJWT/Cryptography, HTTPX, boto3, PostgreSQL 16, Keycloak, MinIO, pytest, Ruff, Docker Compose, React/Vite compatibility UI.

**Design spec:** `docs/superpowers/specs/2026-07-17-peerassist-m1-platform-authorization-design.md`

---

## Scope and Execution Rules

M1 is complete only after Tasks 1-16 pass together. A memory-only implementation, a FastAPI
proxy over the file repository, mocked Keycloak/MinIO acceptance, or a route set that cannot pass
the upload-to-final-report flow is not M1 completion.

Use focused tests while implementing and reserve the complete repository/Compose matrix for Task
16. Every task follows red-green-refactor, stages only its exact files, runs `git diff --check`,
reviews the cached diff, and commits without amend. Do not reset, stash, clean, build, or import
changes from `/root/peerassist-review-system-20260710/peerassist`; compare its stored status and
hash evidence at final acceptance.

Use `M1_REVIEW_BASE=4b8e0a8`, the approved-spec commit, for the final implementation review. This
keeps the review limited to the implementation plan and M1 delivery while preserving the M0 final
baseline (`f6ac9f0`) as historical evidence. Provider commands source the mode-0600 file generated
by `scripts/m1_test_env.sh`; they never depend on ambient or undefined shell variables.

Before each real-provider command block, run this bounded prelude in the same shell; the EXIT trap
is mandatory so failed tests also remove containers, volumes, and generated credentials:

```bash
M1_ENV_FILE="$(mktemp)"
bash scripts/m1_test_env.sh create "$M1_ENV_FILE"
source "$M1_ENV_FILE"
trap 'bash scripts/m1_test_env.sh down "$M1_ENV_FILE"' EXIT
bash scripts/m1_test_env.sh up "$M1_ENV_FILE"
source "$M1_ENV_FILE"
bash scripts/m1_test_env.sh require-ready "$M1_ENV_FILE"
bash scripts/m1_test_env.sh wait "$M1_ENV_FILE"
```

All provider integration fixtures use generated ephemeral credentials and public deterministic
PDFs. Never commit a reusable password, private manuscript, raw token, provider response, object
credential, absolute private path, or reviewer-private content.

## File Responsibility Map

| Path | Responsibility |
| --- | --- |
| `src/peerassist/platform/models.py` | Provider/framework-independent tenant, identity, paper, job, command, event, artifact models |
| `src/peerassist/platform/ports.py` | Protocols for unit of work, identity, object store, materializer/publisher and legacy reads |
| `src/peerassist/platform/permissions.py` | Explicit role/action policy and tenant-safe visibility decisions |
| `src/peerassist/platform/idempotency.py` | Canonical command payload hashing and replay/conflict semantics |
| `src/peerassist/platform/services/*.py` | Use cases: bootstrap, membership, paper upload, review commands, legacy registration |
| `src/peerassist/platform/adapters/memory.py` | Deterministic fake adapters used by shared contract suites |
| `src/peerassist/platform/adapters/postgres.py` | SQLAlchemy repositories, UoW, queue/outbox/audit and tenant predicates |
| `src/peerassist/platform/adapters/oidc.py` | Discovery/JWKS JWT validation and authorization-code exchange |
| `src/peerassist/platform/adapters/s3.py` | Temporary upload, immutable publish and Range-capable object reads |
| `src/peerassist/platform/adapters/workspace.py` | Scratch materialization, output validation/publication and cleanup |
| `src/peerassist/platform/adapters/legacy.py` | Allowlisted, digest-verified, read-only M0 registration adapter |
| `services/api/app.py` | FastAPI factory, fixed router registry registration, and lifespan |
| `services/api/composition.py` | Settings-driven provider construction for memory tests and production PostgreSQL/OIDC/S3 |
| `services/api/dependencies.py` | Request context, actor/session, UoW and authorization dependencies |
| `services/api/errors.py` | Safe stable error envelope and request-ID middleware |
| `services/api/routes/__init__.py` | Fixed `platform_routers()` registry modified whenever a route module is added |
| `services/api/routes/*.py` | Thin versioned HTTP routes; no provider/domain logic |
| `services/worker/main.py` | PostgreSQL work claim and existing-stage execution loop |
| `infrastructure/migrations/*` | Alembic configuration and reversible PostgreSQL schema |
| `infrastructure/keycloak/*` | Reference realm/client bootstrap without committed credentials |
| `infrastructure/minio/*` | Bucket bootstrap and temporary-object lifecycle |
| `infrastructure/compose/compose.m1.test.yml` | Early private PostgreSQL/Keycloak/MinIO integration harness |
| `infrastructure/compose/compose.m1.yml` | Full M1 reference profile; only FastAPI host port published |
| `scripts/m1_test_env.sh` | Generate/source ephemeral integration credentials and concrete provider URLs |
| `contracts/openapi/peerassist-v1.json` | Deterministic committed OpenAPI artifact |
| `tests/platform/contracts/*` | Provider-neutral adapter contract suites |
| `tests/platform/integration/*` | Real PostgreSQL/Keycloak/MinIO/FastAPI tests |
| `tests/platform/system/*` | Full tenant isolation, workflow, recovery and rollback acceptance |

## Chunk 1: M1a FastAPI Boundary and Platform Core

### Task 1: Freeze M1 Dependencies, Packaging, and Repository Contracts

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/common/config.py`
- Modify: `.env.example`
- Modify: `tests/repository/test_packaging_contract.py`
- Create: `tests/platform/test_config.py`
- Create: `tests/platform/test_dependency_boundaries.py`

- [ ] **Step 1: Write failing dependency and configuration contracts**

Add tests asserting a `platform` optional dependency group contains FastAPI/Uvicorn,
SQLAlchemy/Psycopg/Alembic, PyJWT/Cryptography, boto3 and HTTPX; the wheel includes
`src/peerassist/platform`, `services/api`, and `services/worker`; and typed M1 configuration
rejects unsafe production defaults while redacting secrets.

```python
def test_production_platform_settings_reject_insecure_identity_and_public_legacy():
    with pytest.raises(ValidationError):
        PlatformSettings(
            environment="production",
            database_url="postgresql+psycopg://user:secret@db/app",
            oidc_issuer="http://keycloak/realms/peerassist",
            public_base_url="http://example.test",
            legacy_bind_host="0.0.0.0",
        )

def test_domain_modules_do_not_import_provider_frameworks():
    forbidden = {"fastapi", "sqlalchemy", "boto3", "jwt", "keycloak", "minio"}
    assert imported_roots("src/peerassist/platform", exclude={"adapters"}).isdisjoint(forbidden)
```

- [ ] **Step 2: Run red tests**

Run:

```bash
PYTHONPATH=src .venv/bin/pytest -q \
  tests/platform/test_config.py \
  tests/platform/test_dependency_boundaries.py \
  tests/repository/test_packaging_contract.py
```

Expected: FAIL because M1 settings, packages, and extras do not exist.

- [ ] **Step 3: Add dependencies and secure typed settings**

Add `platform` and `platform-dev` extras. Define `PlatformSettings` separately from legacy model
settings with secret fields and validators for environment, DSN, OIDC issuer/audience/algorithms,
S3 endpoint/bucket/path style, public base URL, origins, trusted proxy CIDRs, session key ring,
scratch root/TTL, and internal legacy audience. Production must reject HTTP issuer/public URL,
default credentials, wildcard origins, empty key ring, and public legacy binds.

- [ ] **Step 4: Run green tests and package smoke**

```bash
PYTHONPATH=src .venv/bin/pytest -q \
  tests/platform/test_config.py \
  tests/platform/test_dependency_boundaries.py \
  tests/repository/test_packaging_contract.py
.venv/bin/python -m pip wheel --no-deps . -w /tmp/peerassist-m1-wheel
```

Expected: tests PASS and the wheel contains both composition roots and the platform package.

- [ ] **Step 5: Commit**

```bash
git add -- pyproject.toml src/common/config.py .env.example \
  tests/repository/test_packaging_contract.py \
  tests/platform/test_config.py tests/platform/test_dependency_boundaries.py
git commit -m "build: add M1 platform dependency boundary"
```

### Task 2: Define Platform Models, Ports, and Permission Policy

**Files:**
- Create: `src/peerassist/platform/__init__.py`
- Create: `src/peerassist/platform/models.py`
- Create: `src/peerassist/platform/ports.py`
- Create: `src/peerassist/platform/errors.py`
- Create: `src/peerassist/platform/permissions.py`
- Create: `src/peerassist/platform/idempotency.py`
- Create: `tests/platform/test_models.py`
- Create: `tests/platform/test_permissions.py`
- Create: `tests/platform/test_idempotency.py`

- [ ] **Step 1: Write failing domain tests**

Cover strict UUID/UTC/version validation, explicit permissions for Organization Admin/Project
Owner/Reviewer/Viewer/Service Agent, cross-tenant visibility classification, canonical JSON
payload digests, and idempotency replay vs changed-payload conflict.

```python
def test_cross_tenant_resource_is_hidden_but_visible_scope_denial_is_forbidden():
    assert authorize(viewer_a, Action.READ_JOB, job_b).code == "not_found"
    assert authorize(viewer_a, Action.CANCEL_JOB, job_a).code == "forbidden"

def test_canonical_payload_digest_ignores_json_key_order_only():
    assert payload_digest({"a": 1, "b": 2}) == payload_digest({"b": 2, "a": 1})
    assert payload_digest({"a": " 1 "}) != payload_digest({"a": "1"})
```

- [ ] **Step 2: Run red tests**

```bash
PYTHONPATH=src .venv/bin/pytest -q \
  tests/platform/test_models.py tests/platform/test_permissions.py \
  tests/platform/test_idempotency.py
```

Expected: import failures.

- [ ] **Step 3: Implement strict models and protocols**

Implement models named in the design, `Actor`, `TenantScope`, `Role`, `Action`, `Decision`,
`CommandRecord`, `WorkItem`, `OutboxEvent`, `AuditEvent`, object descriptors, legacy registration,
and browser/OIDC transaction models. Protocols must require tenant scope in every project-resource
method and expose a transaction UoW rather than provider sessions.

- [ ] **Step 4: Run green tests and architecture check**

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/platform/test_models.py \
  tests/platform/test_permissions.py tests/platform/test_idempotency.py \
  tests/platform/test_dependency_boundaries.py
.venv/bin/ruff check src/peerassist/platform tests/platform
```

- [ ] **Step 5: Commit**

```bash
git add -- src/peerassist/platform/__init__.py src/peerassist/platform/models.py \
  src/peerassist/platform/ports.py src/peerassist/platform/errors.py \
  src/peerassist/platform/permissions.py src/peerassist/platform/idempotency.py \
  tests/platform/test_models.py tests/platform/test_permissions.py \
  tests/platform/test_idempotency.py
git commit -m "feat: define platform domain ports"
```

### Task 3: Build the Memory UoW and Shared Contract Harness

**Files:**
- Create: `src/peerassist/platform/adapters/__init__.py`
- Create: `src/peerassist/platform/adapters/memory.py`
- Create: `tests/platform/contracts/conftest.py`
- Create: `tests/platform/contracts/test_repository_contract.py`
- Create: `tests/platform/contracts/test_work_outbox_contract.py`
- Create: `tests/platform/contracts/test_object_store_contract.py`
- Create: `tests/platform/contracts/test_identity_contract.py`

- [ ] **Step 1: Write reusable failing contracts**

Define factory fixtures (`uow_factory`, `object_store_factory`, `identity_provider_factory`) so the
same tests can be parametrized later for PostgreSQL/MinIO/Keycloak. Require transactional rollback,
tenant predicates, optimistic versions, concurrent command replay, ordered events, work leases,
immutable object publish, digest validation, and identity claim normalization.
The object and identity contract modules expose adapter selectors so
`test_object_store_contract.py --adapter=memory|minio` and
`test_identity_contract.py --adapter=fake|keycloak` execute identical assertions against both
implementations; integration fixtures provide only construction/configuration, never a weaker
parallel test suite.

- [ ] **Step 2: Run red memory contracts**

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/platform/contracts --adapter=memory
```

Expected: fixture/adapter failures.

- [ ] **Step 3: Implement the deterministic memory adapter**

Use a lock-protected state object and copy-on-write transaction snapshot. Do not weaken contracts
for memory semantics. Concurrent identical idempotency commands must return the same stored
response; changed payload must raise `IdempotencyConflict`; rollback must discard state/event/work.

- [ ] **Step 4: Run green contracts**

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/platform/contracts --adapter=memory
```

- [ ] **Step 5: Commit**

```bash
git add -- src/peerassist/platform/adapters/__init__.py \
  src/peerassist/platform/adapters/memory.py tests/platform/contracts
git commit -m "test: establish platform adapter contracts"
```

### Task 4: Add the FastAPI Factory, Safe Errors, and Deterministic OpenAPI

**Files:**
- Create: `services/__init__.py`
- Create: `services/api/__init__.py`
- Create: `services/api/app.py`
- Create: `services/api/composition.py`
- Create: `services/api/dependencies.py`
- Create: `services/api/errors.py`
- Create: `services/api/routes/__init__.py`
- Create: `services/api/routes/system.py`
- Create: `services/api/routes/auth.py`
- Create: `scripts/export_openapi.py`
- Create: `contracts/openapi/peerassist-v1.json`
- Create: `tests/platform/test_fastapi_system.py`
- Create: `tests/platform/test_composition.py`
- Create: `tests/platform/test_openapi_contract.py`

- [ ] **Step 1: Write failing HTTP contracts**

Test `/api/v1/health`, readiness dependency status, request-ID propagation/generation, safe error
shape, unknown route, no stack/provider body leakage, auth placeholders, fixed registry inclusion,
memory composition, rejection of incomplete production composition, and byte-stable sorted OpenAPI
export/check behavior.

```python
def test_safe_error_envelope_has_request_id_and_no_exception_text(client):
    response = client.get("/api/v1/_test/failure", headers={"X-Request-ID": "req-1"})
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Request failed.",
                  "request_id": "req-1", "details": {}, "retryable": False}
    }
```

- [ ] **Step 2: Run red tests**

```bash
PYTHONPATH=src:. .venv/bin/pytest -q \
  tests/platform/test_fastapi_system.py tests/platform/test_openapi_contract.py
```

- [ ] **Step 3: Implement the minimal composition root**

Add `create_app(settings, dependencies)` with lifespan, request-ID middleware, trusted-proxy
handling, structured exception mapping, health/readiness, and every router returned by
`platform_routers()`. Add `build_dependencies(settings)` in `composition.py`; it may construct
memory providers only when explicitly selected for tests and must fail closed if production
PostgreSQL/OIDC/S3 providers are unavailable. Auth placeholders return stable `not_configured` in
pure memory tests. OpenAPI must use versioned operation IDs and no provider schemas.

- [ ] **Step 4: Export and verify OpenAPI**

```bash
PYTHONPATH=src:. .venv/bin/python scripts/export_openapi.py
PYTHONPATH=src:. .venv/bin/pytest -q \
  tests/platform/test_fastapi_system.py tests/platform/test_composition.py \
  tests/platform/test_openapi_contract.py
PYTHONPATH=src:. .venv/bin/python scripts/export_openapi.py --check
```

- [ ] **Step 5: Commit**

```bash
git add -- services scripts/export_openapi.py contracts/openapi/peerassist-v1.json \
  tests/platform/test_fastapi_system.py tests/platform/test_composition.py \
  tests/platform/test_openapi_contract.py
git commit -m "feat: add FastAPI platform boundary"
```

## Chunk 2: M1b PostgreSQL Tenancy and Durable Commands

### Task 5: Establish the Ephemeral Real-Provider Test Harness

**Files:**
- Create: `infrastructure/compose/compose.m1.test.yml`
- Create: `infrastructure/keycloak/realm-template.json`
- Create: `infrastructure/keycloak/bootstrap.sh`
- Create: `infrastructure/minio/bootstrap.sh`
- Create: `scripts/m1_test_env.sh`
- Create: `tests/repository/test_m1_provider_harness.py`
- Modify: `docs/superpowers/plans/2026-07-17-peerassist-m1-platform-authorization.md`

- [ ] **Step 1: Write failing harness contracts**

Assert the integration profile defines private PostgreSQL 16, Keycloak, and MinIO services with
health checks and generated credentials; no reusable/default secret is committed; and the env
helper creates a mode-0600 file defining concrete `M1_TEST_DATABASE_URL`, `M1_TEST_OIDC_ISSUER`,
`M1_TEST_S3_ENDPOINT`, credentials, bucket, client, and cleanup project name.

- [ ] **Step 2: Run red harness checks**

```bash
PYTHONPATH=src:. .venv/bin/pytest -q tests/repository/test_m1_provider_harness.py
```

- [ ] **Step 3: Implement the bounded integration harness**

`scripts/m1_test_env.sh create` safely replaces a caller-owned, mode-0600, empty regular `mktemp`
target with generated credentials and unresolved loopback endpoints marked
`M1_TEST_ENDPOINTS_READY=0`. `up` resolves Compose-assigned ports, atomically refreshes the file with
`M1_TEST_ENDPOINTS_READY=1`, and every consumer re-sources the file and calls `require-ready` before
`wait` or pytest. `up|wait|down` acts only on a generated Compose project name, and `down` removes
its volumes and env file. Keycloak imports a realm/client template and MinIO creates a private
immutable-artifact bucket. No service publishes a public host port; loopback ephemeral ports may be
used only for the test process.

- [ ] **Step 4: Prove provider readiness and cleanup**

```bash
M1_ENV_FILE="$(mktemp)"
bash scripts/m1_test_env.sh create "$M1_ENV_FILE"
source "$M1_ENV_FILE"
trap 'bash scripts/m1_test_env.sh down "$M1_ENV_FILE"' EXIT
bash scripts/m1_test_env.sh up "$M1_ENV_FILE"
source "$M1_ENV_FILE"
bash scripts/m1_test_env.sh require-ready "$M1_ENV_FILE"
bash scripts/m1_test_env.sh wait "$M1_ENV_FILE"
test "$M1_TEST_ENDPOINTS_READY" = 1 && test -n "$M1_TEST_DATABASE_URL" && \
  test -n "$M1_TEST_OIDC_ISSUER" && \
  test -n "$M1_TEST_S3_ENDPOINT"
bash scripts/m1_test_env.sh down "$M1_ENV_FILE"
trap - EXIT
```

- [ ] **Step 5: Commit**

```bash
git add -- infrastructure/compose/compose.m1.test.yml infrastructure/keycloak \
  infrastructure/minio scripts/m1_test_env.sh tests/repository/test_m1_provider_harness.py \
  docs/superpowers/plans/2026-07-17-peerassist-m1-platform-authorization.md
git commit -m "test: add ephemeral M1 provider harness"
```

### Task 6: Create Reversible PostgreSQL Schema and Migration Gates

**Files:**
- Create: `alembic.ini`
- Create: `infrastructure/migrations/env.py`
- Create: `infrastructure/migrations/script.py.mako`
- Create: `infrastructure/migrations/versions/0001_platform_m1.py`
- Create: `src/peerassist/platform/adapters/postgres_schema.py`
- Modify: `services/api/composition.py`
- Create: `tests/platform/test_migration_contract.py`
- Create: `tests/platform/integration/test_postgres_schema.py`
- Modify: `tests/platform/test_composition.py`

- [ ] **Step 1: Write failing schema/migration assertions**

Assert all design tables, foreign keys, tenant/project constraints, unique command identity,
project-scoped paper digest, event sequence, work dedupe, append-only audit protection, indexes,
and downgrade-to-base. Mark real DB tests `requires_docker`.

- [ ] **Step 2: Run red structural tests**

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/platform/test_migration_contract.py
```

- [ ] **Step 3: Implement SQLAlchemy metadata and Alembic migration**

Use PostgreSQL UUID/JSONB/timestamptz, explicit named constraints and no provider identity roles in
tables. Include sessions/OIDC transactions, stage manifests, report versions, and schema metadata.
Wire the PostgreSQL engine/session factory into `build_dependencies`; production composition must
instantiate it from validated settings and may not silently fall back to memory. Do not auto-migrate
at app startup.

- [ ] **Step 4: Run real upgrade/downgrade/upgrade**

```bash
M1_ENV_FILE="$(mktemp)"; bash scripts/m1_test_env.sh create "$M1_ENV_FILE"
source "$M1_ENV_FILE"
trap 'bash scripts/m1_test_env.sh down "$M1_ENV_FILE"' EXIT
bash scripts/m1_test_env.sh up "$M1_ENV_FILE" postgres
source "$M1_ENV_FILE"
bash scripts/m1_test_env.sh require-ready "$M1_ENV_FILE"
bash scripts/m1_test_env.sh wait "$M1_ENV_FILE" postgres
PEERASSIST_TEST_DATABASE_URL="$M1_TEST_DATABASE_URL" \
  PYTHONPATH=src .venv/bin/pytest -q -m requires_docker \
  tests/platform/integration/test_postgres_schema.py tests/platform/test_composition.py
bash scripts/m1_test_env.sh down "$M1_ENV_FILE"
trap - EXIT
```

Expected: head schema, downgrade to base, and second upgrade all PASS.

- [ ] **Step 5: Commit**

```bash
git add -- alembic.ini infrastructure/migrations \
  src/peerassist/platform/adapters/postgres_schema.py \
  services/api/composition.py tests/platform/test_composition.py \
  tests/platform/test_migration_contract.py tests/platform/integration/test_postgres_schema.py
git commit -m "feat: add M1 PostgreSQL schema"
```

### Task 7: Implement PostgreSQL UoW, Tenant Repositories, Work Queue, and Outbox

**Files:**
- Create: `src/peerassist/platform/adapters/postgres.py`
- Modify: `services/api/composition.py`
- Create: `tests/platform/integration/test_postgres_repository_contract.py`
- Create: `tests/platform/integration/test_postgres_concurrency.py`
- Modify: `tests/platform/test_composition.py`

- [ ] **Step 1: Bind shared contracts to PostgreSQL and observe red**

Parametrize repository/work/outbox contracts with a transaction-clean PostgreSQL factory. Add
16-thread/process idempotency tests and competing `SKIP LOCKED` claims.

```bash
M1_ENV_FILE="$(mktemp)"; bash scripts/m1_test_env.sh create "$M1_ENV_FILE"
source "$M1_ENV_FILE"
trap 'bash scripts/m1_test_env.sh down "$M1_ENV_FILE"' EXIT
bash scripts/m1_test_env.sh up "$M1_ENV_FILE" postgres
source "$M1_ENV_FILE"
bash scripts/m1_test_env.sh require-ready "$M1_ENV_FILE"
bash scripts/m1_test_env.sh wait "$M1_ENV_FILE" postgres
PEERASSIST_TEST_DATABASE_URL="$M1_TEST_DATABASE_URL" PYTHONPATH=src \
  .venv/bin/pytest -q -m requires_docker \
  tests/platform/integration/test_postgres_repository_contract.py \
  tests/platform/integration/test_postgres_concurrency.py
bash scripts/m1_test_env.sh down "$M1_ENV_FILE"
trap - EXIT
```

Expected: FAIL before adapter implementation.

- [ ] **Step 2: Implement transaction UoW and scoped repositories**

Every project read/write SQL predicate includes organization/project scope. Implement command
reservation/replay, aggregate CAS, event/audit/outbox append, work enqueue/claim/renew/ack/nack,
three-expiry dead letter, and session/identity/membership repositories. Translate SQL errors to
domain errors without SQL text leakage. Complete the PostgreSQL UoW/repository provider in
`build_dependencies` and assert production settings return it rather than a memory UoW.

- [ ] **Step 3: Run provider and memory parity**

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/platform/contracts --adapter=memory
M1_ENV_FILE="$(mktemp)"; bash scripts/m1_test_env.sh create "$M1_ENV_FILE"
source "$M1_ENV_FILE"
trap 'bash scripts/m1_test_env.sh down "$M1_ENV_FILE"' EXIT
bash scripts/m1_test_env.sh up "$M1_ENV_FILE" postgres
source "$M1_ENV_FILE"
bash scripts/m1_test_env.sh require-ready "$M1_ENV_FILE"
bash scripts/m1_test_env.sh wait "$M1_ENV_FILE" postgres
PEERASSIST_TEST_DATABASE_URL="$M1_TEST_DATABASE_URL" PYTHONPATH=src \
  .venv/bin/pytest -q -m requires_docker tests/platform/integration/test_postgres_*.py
bash scripts/m1_test_env.sh down "$M1_ENV_FILE"
trap - EXIT
```

- [ ] **Step 4: Commit**

```bash
git add -- src/peerassist/platform/adapters/postgres.py services/api/composition.py \
  tests/platform/test_composition.py \
  tests/platform/integration/test_postgres_repository_contract.py \
  tests/platform/integration/test_postgres_concurrency.py
git commit -m "feat: add tenant-scoped PostgreSQL repositories"
```

### Task 8: Add Bootstrap, Membership, Audit, and Identity Operator Services

**Files:**
- Create: `src/peerassist/platform/services/__init__.py`
- Create: `src/peerassist/platform/services/bootstrap.py`
- Create: `src/peerassist/platform/services/memberships.py`
- Create: `src/peerassist/platform/services/audit.py`
- Create: `src/peerassist/platform/cli.py`
- Modify: `pyproject.toml`
- Create: `services/api/routes/organizations.py`
- Create: `services/api/routes/projects.py`
- Modify: `services/api/routes/__init__.py`
- Create: `tests/platform/test_bootstrap_service.py`
- Create: `tests/platform/test_membership_service.py`
- Create: `tests/platform/test_management_api.py`

- [ ] **Step 1: Write failing service/API tests**

Cover concurrent operator bootstrap convergence, changed-payload conflict, user-with-no-membership,
organization/project grant/change/revoke, expected versions, immediate revocation, audit filtering,
cross-tenant `404`, visible `403`, operator identity disable/unlink and session revocation. CLI
tests capture stdout, stderr, exception messages, and persisted audit events and prove supplied
passwords, client secrets, raw tokens, and private paths cannot appear in any of them.

- [ ] **Step 2: Run red tests**

```bash
PYTHONPATH=src:. .venv/bin/pytest -q \
  tests/platform/test_bootstrap_service.py \
  tests/platform/test_membership_service.py tests/platform/test_management_api.py
```

- [ ] **Step 3: Implement use cases, thin routes, and CLI**

The CLI accepts secrets from protected stdin/environment, never prints them, and uses the same
UoW/idempotency/audit services as HTTP. No public organization-create, identity-disable, or legacy
register route may exist. Routes return safe `404/403/409` semantics. Add both management routers
to `platform_routers()` so their operations are reachable and exported.

- [ ] **Step 4: Run green tests and OpenAPI drift**

```bash
PYTHONPATH=src:. .venv/bin/pytest -q tests/platform/test_*service.py \
  tests/platform/test_management_api.py
PYTHONPATH=src:. .venv/bin/python scripts/export_openapi.py
PYTHONPATH=src:. .venv/bin/python scripts/export_openapi.py --check
```

- [ ] **Step 5: Commit**

```bash
git add -- src/peerassist/platform/services src/peerassist/platform/cli.py \
  services/api/routes/organizations.py services/api/routes/projects.py \
  services/api/routes/__init__.py \
  pyproject.toml contracts/openapi/peerassist-v1.json \
  tests/platform/test_bootstrap_service.py tests/platform/test_membership_service.py \
  tests/platform/test_management_api.py
git commit -m "feat: add tenant and membership administration"
```

## Chunk 3: M1c OIDC, Sessions, and Repository-Enforced RBAC

### Task 9: Implement Generic OIDC Discovery, JWKS Rotation, and Claim Validation

**Files:**
- Create: `src/peerassist/platform/adapters/oidc.py`
- Modify: `services/api/composition.py`
- Modify: `tests/platform/contracts/conftest.py`
- Create: `tests/platform/test_oidc_adapter.py`
- Create: `tests/platform/fixtures/oidc_server.py`
- Create: `tests/platform/integration/test_keycloak_identity_contract.py`
- Modify: `tests/platform/test_composition.py`

- [ ] **Step 1: Write failing identity contracts**

Test issuer/audience/algorithm/exp/nbf/typ/sub, `alg=none`, unknown `kid` single refresh, cache
expiry/fail-closed, disabled identity, no raw token retention/logging, HTTPS-only production, and
provider-error normalization. The fixture server signs generated RSA keys and rotates them.
Both the fake fixture and real Keycloak fixture must run
`tests/platform/contracts/test_identity_contract.py` unchanged through `--adapter` selection.

- [ ] **Step 2: Run red fake-provider tests**

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/platform/test_oidc_adapter.py
```

- [ ] **Step 3: Implement discovery/JWKS validator**

Use explicit algorithm allowlist, bounded HTTPX timeouts/cache, one refresh on unknown key, and
PyJWT validation. Return normalized verified claims only. Never place token strings in models or
errors. Wire the OIDC provider into `build_dependencies`; production composition must instantiate
the configured discovery/JWKS adapter and fail closed on missing identity settings.

- [ ] **Step 4: Run fake and real Keycloak contracts**

```bash
PYTHONPATH=src .venv/bin/pytest -q tests/platform/test_oidc_adapter.py \
  tests/platform/contracts/test_identity_contract.py --adapter=fake
M1_ENV_FILE="$(mktemp)"; bash scripts/m1_test_env.sh create "$M1_ENV_FILE"
source "$M1_ENV_FILE"
trap 'bash scripts/m1_test_env.sh down "$M1_ENV_FILE"' EXIT
bash scripts/m1_test_env.sh up "$M1_ENV_FILE" keycloak
source "$M1_ENV_FILE"
bash scripts/m1_test_env.sh require-ready "$M1_ENV_FILE"
bash scripts/m1_test_env.sh wait "$M1_ENV_FILE" keycloak callback-reservation
PEERASSIST_TEST_OIDC_ISSUER="$M1_TEST_OIDC_ISSUER" PYTHONPATH=src \
  .venv/bin/pytest -q -m requires_docker \
  tests/platform/contracts/test_identity_contract.py --adapter=keycloak \
  tests/platform/integration/test_keycloak_identity_contract.py \
  tests/platform/test_composition.py
bash scripts/m1_test_env.sh down "$M1_ENV_FILE"
trap - EXIT
```

- [ ] **Step 5: Commit**

```bash
git add -- src/peerassist/platform/adapters/oidc.py services/api/composition.py \
  tests/platform/contracts/conftest.py tests/platform/test_composition.py \
  tests/platform/test_oidc_adapter.py \
  tests/platform/fixtures/oidc_server.py \
  tests/platform/integration/test_keycloak_identity_contract.py
git commit -m "feat: validate generic OIDC identities"
```

### Task 10: Add PKCE Browser Sessions, CSRF, and Auth Dependencies

**Files:**
- Create: `src/peerassist/platform/services/sessions.py`
- Modify: `services/api/routes/auth.py`
- Modify: `services/api/dependencies.py`
- Create: `tests/platform/test_auth_sessions.py`
- Create: `tests/platform/test_auth_dependencies.py`
- Create: `tests/platform/integration/test_keycloak_browser_session.py`

- [ ] **Step 1: Write failing session-flow tests**

Cover state/nonce/PKCE generation, encrypted verifier recoverability, one-time row lock/consume,
10-minute expiry, relative allowlisted return paths, Secure HttpOnly SameSite cookie, rotation,
absolute/idle expiry, logout, disabled identity, origin/CSRF for cookie mutations, bearer without
CSRF, and rejection of browser identity/tenant/forwarded headers. Explicitly prove first login
provisions or links only by the unique `(issuer, subject)` tuple; verified email is profile data
only and never links identities; duplicate/ambiguous tuple mappings fail closed; disabling an
identity revokes all existing sessions before the next protected request.

- [ ] **Step 2: Run red tests**

```bash
PYTHONPATH=src:. .venv/bin/pytest -q \
  tests/platform/test_auth_sessions.py tests/platform/test_auth_dependencies.py
```

- [ ] **Step 3: Implement encrypted transactions and actor resolution**

Use an AEAD key ring with key ID and authenticated context; store state/nonce keyed digests and
encrypted verifier. Callback atomically consumes the transaction and deletes verifier ciphertext.
Resolve bearer/session to the same `Actor`; operator and internal service tokens are excluded from
public actor dependencies.

- [ ] **Step 4: Run fake and Keycloak browser flows and freeze OpenAPI**

```bash
PYTHONPATH=src:. .venv/bin/pytest -q tests/platform/test_auth_*.py
M1_ENV_FILE="$(mktemp)"; bash scripts/m1_test_env.sh create "$M1_ENV_FILE"
source "$M1_ENV_FILE"
trap 'bash scripts/m1_test_env.sh down "$M1_ENV_FILE"' EXIT
bash scripts/m1_test_env.sh up "$M1_ENV_FILE" keycloak
source "$M1_ENV_FILE"
bash scripts/m1_test_env.sh require-ready "$M1_ENV_FILE"
bash scripts/m1_test_env.sh wait "$M1_ENV_FILE" keycloak callback-reservation
PEERASSIST_TEST_OIDC_ISSUER="$M1_TEST_OIDC_ISSUER" PYTHONPATH=src:. \
  .venv/bin/pytest -q -m requires_docker \
  tests/platform/integration/test_keycloak_browser_session.py
bash scripts/m1_test_env.sh down "$M1_ENV_FILE"
trap - EXIT
PYTHONPATH=src:. .venv/bin/python scripts/export_openapi.py
PYTHONPATH=src:. .venv/bin/pytest -q tests/platform/test_openapi_contract.py
PYTHONPATH=src:. .venv/bin/python scripts/export_openapi.py --check
```

- [ ] **Step 5: Commit**

```bash
git add -- src/peerassist/platform/services/sessions.py services/api/routes/auth.py \
  services/api/dependencies.py tests/platform/test_auth_sessions.py \
  tests/platform/test_auth_dependencies.py \
  tests/platform/integration/test_keycloak_browser_session.py \
  contracts/openapi/peerassist-v1.json
git commit -m "feat: add OIDC browser session bridge"
```

### Task 11: Apply RBAC to All Platform Repositories and Routes

**Files:**
- Modify: `services/api/dependencies.py`
- Modify: `services/api/routes/organizations.py`
- Modify: `services/api/routes/projects.py`
- Create: `tests/platform/test_tenant_isolation.py`
- Create: `tests/platform/integration/test_postgres_tenant_isolation.py`

- [ ] **Step 1: Write a full role/tenant matrix first**

Create two organizations with same-shaped project/resource IDs and test list/get/mutate/audit for
each role. Explicitly bypass route dependencies and invoke repositories to prove SQL predicates,
not middleware alone, enforce scope. Revoke membership and require the next request to fail.

- [ ] **Step 2: Run red matrix**

```bash
PYTHONPATH=src:. .venv/bin/pytest -q tests/platform/test_tenant_isolation.py
```

- [ ] **Step 3: Wire permission decisions and scoped repositories**

All thin routes request actor and scope, application services authorize action, and repository
methods require the same scope. Ensure lists never leak counts/IDs and audit filters remain scoped.

- [ ] **Step 4: Run memory and PostgreSQL matrices**

```bash
PYTHONPATH=src:. .venv/bin/pytest -q tests/platform/test_tenant_isolation.py
M1_ENV_FILE="$(mktemp)"; bash scripts/m1_test_env.sh create "$M1_ENV_FILE"
source "$M1_ENV_FILE"
trap 'bash scripts/m1_test_env.sh down "$M1_ENV_FILE"' EXIT
bash scripts/m1_test_env.sh up "$M1_ENV_FILE" postgres
source "$M1_ENV_FILE"
bash scripts/m1_test_env.sh require-ready "$M1_ENV_FILE"
bash scripts/m1_test_env.sh wait "$M1_ENV_FILE" postgres
PEERASSIST_TEST_DATABASE_URL="$M1_TEST_DATABASE_URL" PYTHONPATH=src:. \
  .venv/bin/pytest -q -m requires_docker \
  tests/platform/integration/test_postgres_tenant_isolation.py
bash scripts/m1_test_env.sh down "$M1_ENV_FILE"
trap - EXIT
```

- [ ] **Step 5: Commit**

```bash
git add -- services/api/dependencies.py services/api/routes/organizations.py \
  services/api/routes/projects.py tests/platform/test_tenant_isolation.py \
  tests/platform/integration/test_postgres_tenant_isolation.py
git commit -m "feat: enforce tenant RBAC at data boundaries"
```

## Chunk 4: M1d S3 Persistence and New Review Write Authority

### Task 12: Implement S3/MinIO Immutable Object Storage and Paper Upload

**Files:**
- Create: `src/peerassist/platform/adapters/s3.py`
- Modify: `services/api/composition.py`
- Create: `src/peerassist/platform/services/papers.py`
- Create: `services/api/routes/papers.py`
- Modify: `services/api/routes/__init__.py`
- Modify: `tests/platform/contracts/conftest.py`
- Create: `tests/platform/test_paper_service.py`
- Create: `tests/platform/test_paper_api.py`
- Create: `tests/platform/integration/test_minio_object_contract.py`
- Modify: `tests/platform/test_composition.py`

- [ ] **Step 1: Bind object contracts to S3 and write upload API tests**

Cover bounded streaming, PDF header/media type, actual size/SHA-256, project-scoped digest replay,
tenant-prefixed server keys, temporary object, conditional immutable publish, overwrite denial,
cleanup, authorized HEAD/GET/Range, ETag/Last-Modified/private cache and cross-tenant `404`.
The memory and MinIO factories must execute
`tests/platform/contracts/test_object_store_contract.py` unchanged through `--adapter` selection.

- [ ] **Step 2: Run red tests**

```bash
PYTHONPATH=src:. .venv/bin/pytest -q \
  tests/platform/test_paper_service.py tests/platform/test_paper_api.py
```

- [ ] **Step 3: Implement object adapter and paper service**

The service uploads/verifies temporary bytes, reserves canonical paper identity in one command
transaction, conditionally publishes immutable source, commits Paper/PaperVersion/audit/outbox,
and cleans temporary objects. Handle DB failure after publication as unreferenced cleanup work.
Wire the S3 adapter into production `build_dependencies` and add the paper router to
`platform_routers()`; production settings must instantiate PostgreSQL + OIDC + S3 together, never
leave an adapter as memory/not-configured, and readiness must expose a safe unavailable state.

- [ ] **Step 4: Run memory and real MinIO contracts**

```bash
PYTHONPATH=src:. .venv/bin/pytest -q tests/platform/test_paper_*.py \
  tests/platform/contracts/test_object_store_contract.py --adapter=memory
M1_ENV_FILE="$(mktemp)"; bash scripts/m1_test_env.sh create "$M1_ENV_FILE"
source "$M1_ENV_FILE"
trap 'bash scripts/m1_test_env.sh down "$M1_ENV_FILE"' EXIT
bash scripts/m1_test_env.sh up "$M1_ENV_FILE" minio-bootstrap
source "$M1_ENV_FILE"
bash scripts/m1_test_env.sh require-ready "$M1_ENV_FILE"
bash scripts/m1_test_env.sh wait "$M1_ENV_FILE" minio minio-bootstrap
PEERASSIST_TEST_S3_ENDPOINT="$M1_TEST_S3_ENDPOINT" PYTHONPATH=src:. \
  .venv/bin/pytest -q -m requires_docker \
  tests/platform/contracts/test_object_store_contract.py --adapter=minio \
  tests/platform/integration/test_minio_object_contract.py \
  tests/platform/test_composition.py
bash scripts/m1_test_env.sh down "$M1_ENV_FILE"
trap - EXIT
```

- [ ] **Step 5: Export OpenAPI and commit**

```bash
PYTHONPATH=src:. .venv/bin/python scripts/export_openapi.py
PYTHONPATH=src:. .venv/bin/python scripts/export_openapi.py --check
git add -- src/peerassist/platform/adapters/s3.py \
  services/api/composition.py src/peerassist/platform/services/papers.py \
  services/api/routes/papers.py services/api/routes/__init__.py \
  contracts/openapi/peerassist-v1.json tests/platform/test_paper_service.py \
  tests/platform/test_paper_api.py tests/platform/contracts/conftest.py \
  tests/platform/test_composition.py tests/platform/integration/test_minio_object_contract.py
git commit -m "feat: persist tenant papers in S3"
```

### Task 13: Cut New ReviewJob Commands, Events, Work, and Reports to PostgreSQL

**Files:**
- Create: `src/peerassist/platform/services/reviews.py`
- Create: `services/api/routes/review_jobs.py`
- Create: `services/api/routes/artifacts.py`
- Modify: `services/api/routes/__init__.py`
- Create: `tests/platform/test_review_service.py`
- Create: `tests/platform/test_review_api.py`
- Create: `tests/platform/integration/test_postgres_review_commands.py`

- [ ] **Step 1: Write failing full command contracts**

Test create/list/get/workspace/cancel/retry, scoped consent grant/deny, concern decision binding,
Project-Owner finalize, report/artifact reads, SSE replay, expected versions, concurrent identical
idempotency replay, changed-payload conflicts, and atomic state/event/audit/outbox/work commits.

- [ ] **Step 2: Run red service/API tests**

```bash
PYTHONPATH=src:. .venv/bin/pytest -q \
  tests/platform/test_review_service.py tests/platform/test_review_api.py
```

- [ ] **Step 3: Implement PostgreSQL-authoritative review use cases**

Translate existing `ReviewJobState`, consent, confirmation and finalizer invariants without writing
the M0 file repository. Store normalized workspace/report metadata and immutable object descriptors.
SSE replays aggregate-ordered events and rechecks authorization. FastAPI never runs long work.
Register both review and artifact routers in `platform_routers()` and assert every command/read
operation appears in the exported contract.

- [ ] **Step 4: Run memory/PostgreSQL command suites**

```bash
PYTHONPATH=src:. .venv/bin/pytest -q tests/platform/test_review_*.py
M1_ENV_FILE="$(mktemp)"; bash scripts/m1_test_env.sh create "$M1_ENV_FILE"
source "$M1_ENV_FILE"
trap 'bash scripts/m1_test_env.sh down "$M1_ENV_FILE"' EXIT
bash scripts/m1_test_env.sh up "$M1_ENV_FILE" postgres
source "$M1_ENV_FILE"
bash scripts/m1_test_env.sh require-ready "$M1_ENV_FILE"
bash scripts/m1_test_env.sh wait "$M1_ENV_FILE" postgres
PEERASSIST_TEST_DATABASE_URL="$M1_TEST_DATABASE_URL" PYTHONPATH=src:. \
  .venv/bin/pytest -q -m requires_docker \
  tests/platform/integration/test_postgres_review_commands.py
bash scripts/m1_test_env.sh down "$M1_ENV_FILE"
trap - EXIT
```

- [ ] **Step 5: Export OpenAPI and commit**

```bash
PYTHONPATH=src:. .venv/bin/python scripts/export_openapi.py
PYTHONPATH=src:. .venv/bin/python scripts/export_openapi.py --check
git add -- src/peerassist/platform/services/reviews.py \
  services/api/routes/review_jobs.py services/api/routes/artifacts.py \
  services/api/routes/__init__.py \
  contracts/openapi/peerassist-v1.json tests/platform/test_review_service.py \
  tests/platform/test_review_api.py tests/platform/integration/test_postgres_review_commands.py
git commit -m "feat: cut review commands to PostgreSQL"
```

### Task 14: Materialize Worker Scratch, Publish Stage Artifacts, and Recover Leases

**Files:**
- Create: `src/peerassist/platform/adapters/workspace.py`
- Modify: `services/api/composition.py`
- Create: `services/worker/__init__.py`
- Create: `services/worker/main.py`
- Create: `tests/platform/test_workspace_materializer.py`
- Create: `tests/platform/test_stage_publisher.py`
- Create: `tests/platform/integration/test_worker_recovery.py`
- Modify: `src/peerassist/job_adapters.py`

- [ ] **Step 1: Write failing materializer/publisher contracts**

Test mode-0700 per-stage scratch, allowlisted inputs/outputs, digest and size verification,
symlink/special/escape rejection, read-only input manifest, cancellation checkpoints, no scratch
path/content logs, cleanup on success/failure/cancel, TTL janitor, object-published-before-DB crash,
DB-committed-before-ack crash and fresh rematerialization after lease expiry.

- [ ] **Step 2: Run red tests**

```bash
PYTHONPATH=src:. .venv/bin/pytest -q \
  tests/platform/test_workspace_materializer.py tests/platform/test_stage_publisher.py
```

- [ ] **Step 3: Implement worker bridge around existing stages**

Keep existing stage adapters local-path based inside scratch. Materialize declared prior objects,
run one claimed stage, validate/publish declared outputs, then transactionally CAS manifest/state/
event/audit/outbox/next work. Never trust leftover scratch on retry.
Production composition passes the same PostgreSQL/S3 providers to the worker materializer/publisher;
it must not construct a local file repository as a second writer.

- [ ] **Step 4: Run focused and real kill recovery**

```bash
PYTHONPATH=src:. .venv/bin/pytest -q \
  tests/platform/test_workspace_materializer.py tests/platform/test_stage_publisher.py \
  tests/peerassist/test_review_job_runner.py
M1_ENV_FILE="$(mktemp)"; bash scripts/m1_test_env.sh create "$M1_ENV_FILE"
source "$M1_ENV_FILE"
trap 'bash scripts/m1_test_env.sh down "$M1_ENV_FILE"' EXIT
bash scripts/m1_test_env.sh up "$M1_ENV_FILE" postgres minio-bootstrap
source "$M1_ENV_FILE"
bash scripts/m1_test_env.sh require-ready "$M1_ENV_FILE"
bash scripts/m1_test_env.sh wait "$M1_ENV_FILE" postgres minio minio-bootstrap
PEERASSIST_TEST_DATABASE_URL="$M1_TEST_DATABASE_URL" \
PEERASSIST_TEST_S3_ENDPOINT="$M1_TEST_S3_ENDPOINT" PYTHONPATH=src:. \
  .venv/bin/pytest -q -m requires_docker \
  tests/platform/integration/test_worker_recovery.py
bash scripts/m1_test_env.sh down "$M1_ENV_FILE"
trap - EXIT
```

- [ ] **Step 5: Commit**

```bash
git add -- src/peerassist/platform/adapters/workspace.py services/api/composition.py services/worker \
  tests/platform/test_workspace_materializer.py tests/platform/test_stage_publisher.py \
  tests/platform/integration/test_worker_recovery.py src/peerassist/job_adapters.py
git commit -m "feat: run reviews from durable platform work"
```

### Task 15: Add Digest-Verified Legacy Registration and Read-Only Vite Fallback

**Files:**
- Create: `src/peerassist/platform/adapters/legacy.py`
- Create: `src/peerassist/platform/services/legacy.py`
- Create: `services/api/routes/legacy.py`
- Modify: `services/api/routes/__init__.py`
- Modify: `services/api/composition.py`
- Modify: `src/peerassist/platform/cli.py`
- Modify: `src/peerassist/confirmation_server.py`
- Modify: `web/peerassist-workspace/src/main.tsx`
- Create: `tests/platform/test_legacy_registration.py`
- Create: `tests/platform/test_legacy_api.py`
- Modify: `tests/peerassist/test_confirmation_server.py`

- [ ] **Step 1: Write failing legacy safety/compatibility tests**

Cover configured root containment, symlink escape, supported schema, manifest size/digest, opaque
locator, concurrent registration replay/conflict, no public registration/mutation route, tenant
authorization, exact Range bytes, Vite `readOnlyCompatibility` bootstrap/banner, hidden/disabled
mutation controls, and server-side mutation rejection from a modified client.

- [ ] **Step 2: Run red tests**

```bash
PYTHONPATH=src:. .venv/bin/pytest -q \
  tests/platform/test_legacy_registration.py tests/platform/test_legacy_api.py \
  tests/peerassist/test_confirmation_server.py
```

- [ ] **Step 3: Implement operator registration and read-only adapter/UI**

The CLI registers only allowlisted, verified manifests. Public views omit paths. `/compat/*` uses
the FastAPI-owned session and authorized registration; both UI and server deny all legacy writes.
The mutable M0 Compose profile remains separately runnable as rollback, never alongside M1 writer.
Register only read routes in `platform_routers()` and wire a configured read-only legacy adapter
into production composition; no mutable legacy repository may be reachable from M1 dependencies.

- [ ] **Step 4: Verify fallback build, contracts, and OpenAPI**

```bash
PYTHONPATH=src:. .venv/bin/pytest -q tests/platform/test_legacy_*.py \
  tests/contracts/test_legacy_http_contract.py \
  tests/peerassist/test_confirmation_server.py
npm run build --prefix web/peerassist-workspace
PYTHONPATH=src:. .venv/bin/python scripts/export_openapi.py
PYTHONPATH=src:. .venv/bin/pytest -q tests/platform/test_openapi_contract.py
PYTHONPATH=src:. .venv/bin/python scripts/export_openapi.py --check
```

- [ ] **Step 5: Commit**

```bash
git add -- src/peerassist/platform/adapters/legacy.py \
  src/peerassist/platform/services/legacy.py services/api/routes/legacy.py \
  services/api/routes/__init__.py services/api/composition.py \
  src/peerassist/platform/cli.py src/peerassist/confirmation_server.py \
  web/peerassist-workspace/src/main.tsx web/peerassist-workspace/dist \
  tests/platform/test_legacy_registration.py tests/platform/test_legacy_api.py \
  tests/peerassist/test_confirmation_server.py contracts/openapi/peerassist-v1.json
git commit -m "feat: add authorized legacy fallback"
```

## Chunk 5: Reference Deployment, CI, and Final Acceptance

### Task 16: Deliver the M1 Compose Profile, System Acceptance, Documentation, and Feishu Sync

**Files:**
- Create: `infrastructure/compose/compose.m1.yml`
- Modify: `infrastructure/compose/compose.m1.test.yml`
- Create: `infrastructure/compose/Dockerfile.api`
- Create: `infrastructure/compose/Dockerfile.worker`
- Modify: `infrastructure/keycloak/realm-template.json`
- Modify: `infrastructure/keycloak/bootstrap.sh`
- Modify: `infrastructure/minio/bootstrap.sh`
- Modify: `scripts/m1_test_env.sh`
- Create: `scripts/m1_compose_smoke.sh`
- Modify: `scripts/verify_repository.py`
- Modify: `scripts/bootstrap_smoke.sh`
- Modify: `Makefile`
- Modify: `.github/workflows/ci.yml`
- Modify: `AGENTS.md`
- Modify: `CONTRIBUTING.md`
- Modify: `SECURITY.md`
- Modify: `docs/development.md`
- Modify: `docs/peerassist_operation_manual.md`
- Modify: `docs/system_review_2026-07-17.md`
- Modify: `docs/peerassist_lark_sync.md`
- Create: `tests/platform/system/test_m1_workflow.py`
- Create: `tests/platform/system/test_m1_tenant_isolation.py`
- Create: `tests/platform/system/test_m1_dependency_failures.py`
- Create: `tests/repository/test_m1_compose_contract.py`

- [ ] **Step 1: Write failing deployment and system contracts**

Assert M1 Compose contains API/worker/PostgreSQL/Keycloak/MinIO/migration/bootstrap jobs, only API
publishes loopback, no defaults/reusable secrets, non-root identities, health/readiness, private
legacy, private worker scratch, and ephemeral credentials. System tests cover:

```text
OIDC login/session
→ operator organization bootstrap
→ membership/project creation
→ PDF upload to MinIO
→ PostgreSQL ReviewJob + work/outbox
→ worker local stages
→ scoped consent
→ workspace + human decision
→ Project-Owner finalize
→ immutable report Range download
→ second-tenant 404 for every resource
→ membership revoke denies next request
→ registered M0 resource read-only fallback
```

- [ ] **Step 2: Run red deployment contracts**

```bash
PYTHONPATH=src:. .venv/bin/pytest -q tests/repository/test_m1_compose_contract.py
```

- [ ] **Step 3: Implement Compose/bootstrap/smoke and verification groups**

Promote and harden the already-used Task 5 provider harness into the full API/worker/migration/
bootstrap profile; do not introduce provider definitions for the first time here. Generate
credentials into a mode-0600 temporary env file. Bootstrap Keycloak clients/users and MinIO bucket
without committed secrets. Add `platform` check group for unit/contracts/OpenAPI and
`m1-system` entry for Compose. Preserve M0 `all`; add `m1-all` that runs M0 gates then M1 unit and
system acceptance. Cleanup containers, volumes, scratch and ephemeral secrets on every exit.

- [ ] **Step 4: Run focused M1 system acceptance**

```bash
bash scripts/m1_compose_smoke.sh
```

Expected: all real-provider workflow, tenant, failure, migration, recovery, cleanup, and rollback
checks PASS with exit 0; M0 host services are not stopped or reused.

- [ ] **Step 5: Run fresh clean-checkout acceptance**

From a tracked-file temporary checkout with real LFS PDF:

```bash
python scripts/verify_repository.py m1-all
```

Required evidence:

- complete pytest count with zero failures;
- Ruff, docs, secrets, dependency and frontend/dist checks;
- deterministic OpenAPI no drift;
- PostgreSQL upgrade/downgrade/upgrade;
- Keycloak fake/real identity and PKCE session contracts;
- MinIO memory/real object contract and exact Range bytes;
- concurrent idempotency and `SKIP LOCKED` evidence;
- worker kill/recovery and scratch/object cleanup;
- cross-tenant paper/job/artifact/legacy/audit denial;
- mutable M0 rollback profile and read-only M1 compatibility profile.

- [ ] **Step 6: Recheck source worktree integrity**

Run the exact integrity procedure below and require both comparisons to succeed:

```bash
cd /root/peerassist-review-system-20260710/peerassist
git status --porcelain=v1 -z > /tmp/peerassist-m0-source/status.m1-final.z
git ls-files -co --exclude-standard -z | sort -z |
  while IFS= read -r -d '' path; do
    if test -f "$path"; then sha256sum -- "$path"; fi
  done > /tmp/peerassist-m0-source/files.m1-final.sha256
cmp /tmp/peerassist-m0-source/status.before.z \
    /tmp/peerassist-m0-source/status.m1-final.z
cmp /tmp/peerassist-m0-source/files.before.sha256 \
    /tmp/peerassist-m0-source/files.m1-final.sha256
```

Expected: exact match. Return immediately to `/root/.worktrees/peerassist-m0`; do not run any
mutating command in the source worktree.

- [ ] **Step 7: Update system evidence and commit**

Record exact current commit, commands, counts, provider versions, route/OpenAPI count, migration
head, Range digest, recovery/tenant evidence, and residual risks. Do not record credentials,
tokens, manuscripts, provider bodies, object keys, private paths, or scratch locations.

```bash
git add -- infrastructure/compose/compose.m1.yml infrastructure/compose/compose.m1.test.yml \
  infrastructure/compose/Dockerfile.api \
  infrastructure/compose/Dockerfile.worker infrastructure/keycloak \
  infrastructure/minio scripts/m1_test_env.sh scripts/m1_compose_smoke.sh scripts/verify_repository.py \
  scripts/bootstrap_smoke.sh Makefile .github/workflows/ci.yml AGENTS.md CONTRIBUTING.md \
  SECURITY.md docs/development.md docs/peerassist_operation_manual.md \
  docs/system_review_2026-07-17.md docs/peerassist_lark_sync.md \
  tests/platform/system tests/repository/test_m1_compose_contract.py
git commit -m "feat: deliver M1 platform authorization baseline"
```

- [ ] **Step 8: Run independent final review**

Review `4b8e0a8..HEAD` against the approved M1 spec. `4b8e0a8` is the immutable approved-design
boundary; the final review therefore covers the plan and every M1 implementation commit without
re-reviewing completed M0 history. Resolve every Critical/Important and
rerun affected/final gates. No completion claim before the reviewer reports none remaining.

- [ ] **Step 9: Synchronize Feishu with optimistic revision guard**

Fetch the current document revision and verify exactly ten unique `h2` sections. Precisely update
only sections 三、七、八、十 with:

- final M1 commit and exact verification evidence;
- FastAPI/PostgreSQL/Keycloak/MinIO authority and tenant-isolation result;
- legacy read-only fallback and rollback status;
- M1 completion and M2 next action.

Use the fetched revision for the first write and each returned revision for the next. Do not add a
dated section. Re-fetch and verify ten sections, final revision, and the new commit/test keywords.

- [ ] **Step 10: Final completion audit**

Map all 11 design acceptance gates to authoritative current evidence. Confirm clean branch, exact
source integrity, no temporary test services/artifacts, and final Feishu verification. Only then
mark the persistent M1 goal complete.
