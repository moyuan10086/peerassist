# PeerAssist M1 Platform Authorization and Persistence Design

**Status:** Approved direction, written-spec review in progress

**Date:** 2026-07-17

**Milestone:** M1 - Platform and authorization

## 1. Objective

M1 introduces the authenticated platform boundary and durable multi-tenant storage needed by
later Next.js, canvas, and autonomous-Agent milestones. It delivers a FastAPI `/api/v1` service,
PostgreSQL-backed organizations/projects/new ReviewJobs, generic OIDC authentication with a
Keycloak reference profile, repository-enforced RBAC, S3-compatible PDF/artifact storage with a
MinIO reference profile, ordered audit/outbox records, and idempotent commands.

M1 is a complete deployable migration milestone, not a mock facade. New platform writes become
PostgreSQL/S3 authoritative by the end of M1. Existing file-backed jobs and artifacts remain
readable through an authorized compatibility adapter and the current Vite workspace remains an
operational fallback. M1 does not implement the Next.js application shell or evidence canvas.

## 2. Approved Decisions

- Use a progressive modular monolith and keep `src/peerassist` framework-independent.
- Use FastAPI as the only public API boundary; legacy Python servers bind only to the internal
  network or loopback.
- Use PostgreSQL as authority for identity mappings, organizations, projects, memberships,
  papers, new ReviewJobs, commands, work items, outbox events, and audit events.
- Use S3-compatible storage as authority for new PDF bytes and immutable large artifacts.
- Use generic OIDC discovery/JWKS validation; Keycloak is the local/private reference provider,
  not a domain dependency.
- Use MinIO as the local/private S3 reference provider, not a domain dependency.
- Resolve roles from PostgreSQL memberships. Never trust organization, project, or role headers
  supplied by a browser.
- Do not dual-write new resources to the legacy file repositories. Historical file-backed jobs
  are registered as read-only legacy resources.
- Preserve one writer per aggregate, transactional outbox/work-item creation, idempotency, and
  tenant-safe resource lookup.
- Keep external model/OCR transfer behind the existing consent gate. M1 platform storage does
  not grant an Agent direct database, identity-provider, or object-store credentials.

## 3. Scope and Non-Goals

M1 includes:

1. FastAPI application wiring, versioned schemas, OpenAPI, request IDs, safe errors, health and
   readiness endpoints.
2. PostgreSQL schema, migrations, transaction boundary, repositories, work queue, outbox, and
   audit ledger.
3. OIDC access-token validation, user provisioning/linking, organization and project RBAC, and
   membership revocation.
4. S3-compatible temporary upload, digest validation, atomic publication, authorized reads, and
   object metadata.
5. Organization/project/paper/ReviewJob APIs and authorized legacy-resource reads.
6. In-memory/fake adapters and shared provider contract suites.
7. Docker Compose integration with PostgreSQL, Keycloak, and MinIO, plus upgrade/rollback and
   cross-tenant acceptance tests.

M1 does not include:

- Next.js, a product application shell, or migrating the Vite UI beyond the minimal authenticated
  read-only compatibility mode required for rollback;
- canvas entities or commands;
- realtime collaboration;
- autonomous Agent orchestration beyond preserving existing worker and consent behavior;
- public report sharing;
- bulk migration or mutation of historical file runs;
- a microservice split;
- production TLS termination, multi-region failover, billing, or SaaS account lifecycle.

## 4. Delivery Checkpoints

M1 is delivered in four independently reviewable checkpoints. The final milestone is accepted
only after all four pass together.

### M1a - FastAPI compatibility boundary

- Add `services/api` as composition root and `src/peerassist/platform` as application/domain
  modules.
- Publish `/api/v1/health`, `/api/v1/ready`, OpenAPI, stable errors, and request IDs.
- Proxy or adapt the frozen ReviewJob reads/commands without changing the current file writer.
- Bind legacy services to the private network; only FastAPI publishes a host port.

### M1b - PostgreSQL tenancy and legacy registration

- Add organizations, users, projects, memberships, papers, paper versions, ReviewJobs, commands,
  work items, outbox, audit, legacy registrations, and migration metadata.
- Make tenant/project scope mandatory in repository methods.
- Register public deterministic M0 legacy fixtures read-only with verified digests.

### M1c - OIDC and RBAC

- Validate generic OIDC JWT access tokens using discovery and rotating JWKS.
- Provision/link users by `(issuer, subject)` and use verified email only as profile data, never
  as identity authority.
- Enforce organization/project permissions in application services and repositories.
- Run the same identity-claim contract against a deterministic fake and Keycloak.

### M1d - S3 and new-write cutover

- Store new PDFs and immutable artifacts in S3-compatible storage.
- Commit new Paper/ReviewJob state, outbox event, and work item transactionally in PostgreSQL.
- Bridge the existing review stage runner through a PostgreSQL-backed ReviewRepository and
  object-backed stage artifact adapter.
- Keep registered legacy resources read-only and expose a tested fallback route.

## 5. Target Module Layout

M1 introduces focused modules without moving unrelated review code:

```text
services/api/
  __init__.py
  app.py                   # FastAPI factory and dependency wiring
  dependencies.py          # actor, transaction and request context dependencies
  errors.py                # stable error envelope handlers
  routes/
    auth.py
    organizations.py
    projects.py
    papers.py
    review_jobs.py
    artifacts.py
    legacy.py

src/peerassist/platform/
  models.py                # framework/provider-independent platform models
  permissions.py           # role/action policy and tenant-safe decisions
  services.py              # platform use cases and transaction orchestration
  ports.py                 # identity, repository, object store, dispatcher contracts
  idempotency.py           # canonical payload digest and response replay
  audit.py                 # audit event construction and redaction
  adapters/
    memory.py              # deterministic contract-test adapters
    postgres.py            # SQLAlchemy repositories and transaction unit
    oidc.py                # generic OIDC discovery/JWKS validator
    s3.py                  # boto-compatible object store
    legacy.py              # read-only M0 repository/artifact adapter

infrastructure/migrations/ # Alembic environment and versioned migrations
infrastructure/keycloak/   # deterministic development realm/client import
infrastructure/minio/      # bucket bootstrap and lifecycle policy
infrastructure/compose/    # M1 services and smoke orchestration
tests/platform/contracts/  # reusable port contract suites
tests/platform/integration/# PostgreSQL/Keycloak/MinIO/FastAPI tests
```

`services/api` may depend on FastAPI. `adapters` may depend on SQLAlchemy, Authlib/PyJWT, HTTPX,
and boto-compatible libraries. `models`, `permissions`, `services`, and `ports` may not import
FastAPI, SQLAlchemy, Keycloak, MinIO, Supabase, or provider response types.

## 6. Domain and Persistence Model

All primary IDs are opaque UUIDs. Every project resource carries `organization_id` and
`project_id` where applicable. IDs are indexed but never authorize access.

| Aggregate/table | Required fields and invariants |
| --- | --- |
| `users` | internal ID, status, display profile, created/updated timestamps |
| `external_identities` | issuer + subject unique, user ID, verified claim snapshot, last seen |
| `browser_sessions` | opaque session digest, user/identity, token-expiry metadata, CSRF secret, expiry/revocation; no raw token in browser-visible state |
| `oidc_transactions` | one-time state/nonce/PKCE verifier digest, return path, expiry/consumption |
| `organizations` | ID, slug unique, name, status, version |
| `organization_memberships` | organization/user unique, role, status, version, revoked timestamp |
| `projects` | organization-scoped ID/name/status/version |
| `project_memberships` | project/user unique, role, status, version; project organization invariant |
| `papers` | organization/project, content SHA-256, current version, status; project-scoped digest idempotency |
| `paper_versions` | immutable source object ID, filename, media type, size, SHA-256, created actor |
| `review_jobs` | project/paper version, mode, stage/status/version, attempt, cancellation/error fields |
| `review_events` | job + aggregate sequence unique, immutable event ID/type/schema/redacted payload |
| `commands` | tenant, actor, route/action, idempotency key, payload digest, response/status |
| `work_items` | job/attempt/stage/input revision dedupe, lease, attempts, dead-letter fields |
| `outbox_events` | ordered ID, aggregate/sequence, payload, publication/attempt state |
| `artifacts` | tenant/project/job, logical name, immutable object ID, size, digest, schema/status |
| `legacy_registrations` | tenant/project, legacy type/opaque locator, manifest digest, read-only status |
| `audit_events` | actor/tenant/action/resource/outcome/request ID, safe metadata, append-only |

Foreign keys prevent cross-organization project membership and cross-project paper/job binding.
Rows use `created_at`/`updated_at` in UTC. Mutable aggregates have monotonically increasing
integer `version`. Deletes are tombstones during M1; physical retention deletion is deferred.

Database migrations use Alembic and expand/contract rules. Every migration has an upgrade test,
a downgrade test inside the M1 rollback window, and a schema-head assertion. Application startup
never auto-applies migrations.

For initial upload, `(project_id, content_sha256)` identifies one Paper and its initial immutable
PaperVersion. Re-uploading identical bytes to the same project replays that Paper/PaperVersion;
it does not create a second version or conflict because a filename changed. The accepted display
filename is metadata on the original version and a later alias does not rewrite it. The same bytes
in another project produce a different tenant-scoped Paper and object key. Replacing a manuscript
is an explicit version command with a new digest; implicit replacement through upload is forbidden.

## 7. Transaction and Work Consistency

Each mutating API command executes in one PostgreSQL transaction:

```text
authorize actor
→ lock/read tenant-scoped aggregate
→ reserve or replay idempotency command
→ validate expected version and payload
→ mutate aggregate
→ append audit and aggregate event
→ append outbox row
→ append work item when asynchronous
→ commit
```

The idempotency identity is `(organization_id, actor_id, operation, Idempotency-Key)`. The stored
payload digest is computed from canonical validated command data. A repeat with the same digest
returns the stored HTTP status/body. Reusing the key with another digest returns
`409 idempotency_conflict`. Reservation and mutation share one transaction, so concurrent
requests cannot both execute.

Workers claim with `FOR UPDATE SKIP LOCKED` and a lease. The work dedupe key is
`(job_id, attempt_id, stage, input_revision)`. Execution is at least once; stage publication is
idempotent. State, stage manifest metadata, event, outbox, and next work item commit together.
Large bytes are published before that transaction under a verified immutable object ID. A crash
before commit leaves an unreferenced object for the janitor. A crash after commit safely
redelivers the outbox/work item.

The existing stage adapters remain reusable. M1 adds repository/object ports around their
inputs and outputs; it does not make FastAPI execute parsing or model calls inline.

## 8. OIDC Authentication and Browser Session Bridge

M1 accepts OIDC bearer access tokens at FastAPI for automation and also implements a minimal
server-owned Authorization Code + PKCE session bridge for the existing Vite compatibility route.
This is authentication infrastructure, not the M2 product shell. FastAPI owns login, callback,
session refresh, logout, and the compatibility-route cookie. The Vite application never receives
an access or refresh token and sends same-origin API requests with the HttpOnly session cookie.

The browser flow is:

```text
GET /api/v1/auth/login?return_to=/compat/paper
→ create one-time state/nonce/PKCE transaction
→ OIDC authorization endpoint
→ GET /api/v1/auth/callback
→ validate state, nonce, code and token
→ provision/link internal identity
→ issue opaque HttpOnly Secure SameSite=Lax session cookie
→ redirect only to an allowlisted relative return path
```

Session records store only the server-side material needed to refresh or revalidate identity;
provider tokens are encrypted with a deployment key or retained by the provider adapter and are
never returned to Vite. Sessions have absolute and idle expiry, rotate on login/refresh, and are
revoked on logout, identity disablement, or provider rejection. Mutating cookie-authenticated
requests require an Origin matching the configured public origin plus a session-bound CSRF token.
Bearer clients do not use the cookie CSRF mechanism.

The generic OIDC adapter is configured with exact issuer, expected audience, accepted algorithms,
clock skew, discovery/JWKS timeouts, and optional required claims. It must:

- fetch only HTTPS discovery/JWKS URLs in non-development profiles;
- validate signature, issuer, audience, expiry, not-before, and token type;
- reject `alg=none`, unexpected algorithms, missing subject, disabled identity, and ambiguous
  issuer/subject mappings;
- cache discovery/JWKS with bounded lifetime and refresh once on unknown `kid`;
- fail closed when discovery/JWKS cannot be refreshed beyond its valid cache;
- never log or persist raw access/refresh tokens;
- normalize provider failures into stable authentication errors.

`(issuer, subject)` is the only external identity key. On first valid login, an internal user and
identity mapping may be provisioned, but it grants no organization/project access. Memberships
are managed in PostgreSQL. Disabling or unlinking an identity rejects subsequent requests.

Keycloak development configuration contains one realm, confidential automation client for tests,
public PKCE client for future browser use, and deterministic users representing each role in two
organizations. Development secrets are generated or supplied through ignored environment files;
no reusable credentials are committed.

The first organization is not created by a normal authenticated API. A private-deployment
operator runs `peerassist-platform bootstrap-organization` with exact issuer, subject, slug, name,
and an idempotency key. In one transaction it resolves an already provisioned identity, creates
the organization, grants Organization Admin, writes audit/outbox records, and stores the command
response. Concurrent repeats converge; changed payload conflicts. The command refuses a disabled
identity and reads secret inputs from protected stdin/environment without printing them. Future
SaaS self-service organization creation is deferred. After bootstrap, Organization Admins manage
memberships through versioned APIs. A newly provisioned user has no access until explicitly
granted membership.

## 9. RBAC and Tenant Safety

Roles are ordered by explicit permissions, not string comparison:

| Role | Representative permissions |
| --- | --- |
| Organization Admin | organization policy/members/audit; all projects in organization |
| Project Owner | project settings/members, papers, ReviewJobs, retention and publication gates |
| Reviewer | read project, upload paper, create/cancel/retry job, decide concerns, draft reports |
| Viewer | read authorized project, papers, jobs, events, and artifacts |
| Service Agent | only short-lived tool permissions embedded in an internal grant |

Project Owner, Reviewer, and Viewer are project memberships. Organization Admin is an
organization membership. The permission service returns an allow/deny decision; repositories
still require `organization_id` and `project_id` and include them in SQL predicates. Route-only
authorization is insufficient.

An authenticated user querying an ID outside every visible tenant receives `404`. A user who can
see the project but lacks the requested action receives `403`. Lists never include unauthorized
rows. Artifact authorization resolves the database metadata first; an object key alone cannot be
downloaded. Membership revocation becomes effective on the next request. M1 does not cache role
decisions across requests.

Internal legacy calls use a short-lived audience-bound service token generated by the FastAPI
composition root. Browser-supplied identity headers are stripped. Direct legacy ports are not
published by the M1 Compose profile.

Global identity disable/unlink is an operator CLI action, not an organization-admin API, because
one identity may belong to multiple organizations. It is versioned, idempotent, audited, revokes
all browser sessions, and never deletes audit history. Organization and project membership grants,
role changes, and revocations are API commands. Revocation is checked from PostgreSQL on the next
request and is not hidden behind an authorization cache.

## 10. Object Storage Contract

`ObjectStore` exposes provider-independent operations for temporary writes, verified publication,
immutable reads, metadata lookup, authorized download descriptors, and tombstones.

Object IDs/keys are server generated and tenant-prefixed:

```text
org/<organization-id>/project/<project-id>/paper/<paper-version-id>/source.pdf
org/<organization-id>/project/<project-id>/job/<job-id>/artifact/<artifact-id>
tmp/<upload-id>
```

Clients cannot choose a final key. Upload processing is bounded and streaming:

1. create a temporary object with expected maximum size;
2. stream bytes while computing SHA-256 and count;
3. validate PDF header/media type, declared/actual size, digest, and tenant context;
4. publish under an immutable final key using copy/conditional creation semantics;
5. write object metadata and PaperVersion in the database transaction;
6. delete temporary data after commit; janitor removes abandoned temporary objects after 24 h.

The adapter rejects overwrite of published objects and verifies downloaded digest in contract
tests. M1 defaults to authorized API streaming for PDFs/artifacts. Presigned URLs are optional,
short lived, response-header constrained, tenant-authorized before issue, and never written to
audit payloads.

The memory/local adapter and MinIO adapter run the same contract suite. A generic S3-compatible
endpoint/region/path-style configuration covers managed S3 profiles without provider-specific
domain behavior.

## 11. FastAPI Contract

All public routes live under `/api/v1`. Required M1 routes are:

```text
GET    /api/v1/health
GET    /api/v1/ready
GET    /api/v1/auth/login
GET    /api/v1/auth/callback
GET    /api/v1/auth/session
POST   /api/v1/auth/logout
GET    /api/v1/me
GET    /api/v1/organizations
GET    /api/v1/organizations/{organization_id}/projects
POST   /api/v1/organizations/{organization_id}/projects
GET    /api/v1/organizations/{organization_id}/members
POST   /api/v1/organizations/{organization_id}/members
PATCH  /api/v1/organizations/{organization_id}/members/{membership_id}
GET    /api/v1/organizations/{organization_id}/audit-events
GET    /api/v1/projects/{project_id}
GET    /api/v1/projects/{project_id}/members
POST   /api/v1/projects/{project_id}/members
PATCH  /api/v1/projects/{project_id}/members/{membership_id}
GET    /api/v1/projects/{project_id}/papers
POST   /api/v1/projects/{project_id}/papers
GET    /api/v1/projects/{project_id}/papers/{paper_id}
GET    /api/v1/projects/{project_id}/papers/{paper_id}/source
GET    /api/v1/projects/{project_id}/review-jobs
POST   /api/v1/projects/{project_id}/review-jobs
GET    /api/v1/projects/{project_id}/review-jobs/{job_id}
GET    /api/v1/projects/{project_id}/review-jobs/{job_id}/workspace
POST   /api/v1/projects/{project_id}/review-jobs/{job_id}/cancel
POST   /api/v1/projects/{project_id}/review-jobs/{job_id}/retry
POST   /api/v1/projects/{project_id}/review-jobs/{job_id}/consents/{service}
POST   /api/v1/projects/{project_id}/review-jobs/{job_id}/decisions
POST   /api/v1/projects/{project_id}/review-jobs/{job_id}/finalize
GET    /api/v1/projects/{project_id}/review-jobs/{job_id}/events
GET    /api/v1/projects/{project_id}/review-jobs/{job_id}/artifacts
GET    /api/v1/projects/{project_id}/review-jobs/{job_id}/artifacts/{artifact_id}
GET    /api/v1/projects/{project_id}/review-jobs/{job_id}/reports
GET    /api/v1/projects/{project_id}/review-jobs/{job_id}/reports/{report_id}
GET    /api/v1/projects/{project_id}/legacy/review-jobs/{registration_id}
GET    /api/v1/projects/{project_id}/legacy/review-jobs/{registration_id}/artifacts/{name}
```

Organization membership writes require Organization Admin. Project membership writes require
Organization Admin or Project Owner. Audit reads require Organization Admin, with optional project
filters that still apply tenant predicates. Membership changes require expected version and
idempotency. Cross-tenant membership IDs return `404`; visible-scope permission failures return
`403`. Organization creation, identity disable/unlink, and legacy registration are operator CLI
commands with the same command/idempotency/audit service, not hidden public routes.

`workspace` is an authorized normalized snapshot of current evidence, queue, actions, artifacts,
and consent/confirmation revisions. Consent commands accept only `granted` or `denied`, service,
reason, expected job version, and an idempotency key; Reviewer or Project Owner may decide. A
grant records destination/purpose/scope and never implies consent for another paper version.
Decision commands bind concern lineage/finding/revision, action, reason, expected confirmation
revision, and idempotency. Reviewer or Project Owner may decide. `finalize` requires Project Owner,
the expected job/confirmation revisions, absence of unresolved core or reconciliation findings,
and an idempotency key. It creates an immutable internal ReportVersion and artifacts; it does not
externally share them. Report sharing remains outside M1. Repeated identical commands replay;
stale versions or changed payloads return `409` without partial mutation.

The API acceptance flow is: authenticate, select an authorized project, upload a local-only PDF,
create a ReviewJob, observe local stages, grant or deny scoped model consent, reach human
confirmation, fetch workspace, decide concerns, finalize as Project Owner, and Range-download the
immutable report. The browser compatibility acceptance is deliberately read-only and does not
stand in for this API flow.

Exact schemas are generated from Pydantic models into a committed OpenAPI artifact; CI rejects
drift.

Every non-streaming error uses the existing safe envelope. Authenticated mutation routes require
`Idempotency-Key`; aggregate mutations require `expected_version`. Upload uses `202` only after
durable PaperVersion/job observation resources exist. HTTP Range reads preserve `206`,
`Accept-Ranges`, ETag, Last-Modified, and private-cache behavior.

SSE uses authorized event replay by immutable event ID. Authorization is checked on connection
and at most every 60 seconds. Cursor expiry uses `409 event_cursor_expired`. Polling through the
job resource remains the fallback.

## 12. Legacy Registration and Rollback

M1 never scans arbitrary server paths from an API request. An operator-only registration command
accepts an allowlisted legacy root plus a manifest. It validates:

- canonical path remains inside the configured root and is not a symlink escape;
- paper/job/artifact schema versions are supported;
- source and artifact sizes/SHA-256 match the manifest;
- target organization/project exists and operator is authorized;
- the registration digest has not already been used with conflicting metadata.

The resulting `legacy_registration` stores an opaque adapter locator and manifest digest, not an
absolute path in public views. It is read-only. Legacy cancel/retry/decision/finalize commands are
not exposed through the registration adapter. The existing Vite workspace remains reachable at
`/compat/*` after the FastAPI session bridge authenticates the browser. In M1 compatibility mode
it renders registered M0 resources read-only, shows an explicit banner, hides or disables every
mutation control, and receives a read-only bootstrap flag. FastAPI and the internal legacy adapter
still reject mutation if a modified client sends one. Operating the mutable M0 profile is a
deployment rollback, not an M1 request path; the two profiles are never simultaneous writers.

`peerassist-platform register-legacy` is the only M1 registration entry point. It requires an
operator context, configured allowlisted root, organization/project IDs, manifest, expected schema
versions, and idempotency key. It writes registration and audit records transactionally and emits
no public locator. API tests prove no legacy registration route exists.

The M1 Compose profile publishes FastAPI only. The M0 profile remains separately startable for
rollback. Rollback is supported for 14 days: migrations remain backward-readable or have tested
downgrades, the prior M0 image remains runnable, and new PostgreSQL/S3 resources are preserved
read-only rather than converted into unverifiable legacy files. No rollback claims that M1-created
jobs can be mutated by M0.

## 13. Configuration and Deployment

Typed configuration adds:

- PostgreSQL DSN, pool limits, statement/lock timeouts;
- OIDC issuer/audience/algorithms/cache/timeouts;
- S3 endpoint/region/bucket/path style and credential source;
- internal legacy audience/key source;
- upload limits, temporary-object TTL, service bind addresses;
- explicit development-profile switches.

DSN and object-store credentials are secret fields and never rendered by diagnostics. Production
configuration rejects default passwords, HTTP OIDC issuer, public legacy binds, wildcard CORS,
and missing secure gateway metadata. `public_base_url`, exact allowed browser origins, and
`trusted_proxy_cidrs` define that boundary. Forwarded host/protocol/client headers are ignored
unless the immediate socket peer is in an allowed proxy CIDR; untrusted forwarded headers never
change redirects, secure-cookie behavior, rate-limit identity, or generated URLs. Production
startup fails if the derived public scheme is not HTTPS. Development relaxations are explicit and
test-only/local.

Compose adds `api`, `worker`, `postgres`, `keycloak`, `minio`, and one-time migration/bootstrap
jobs. Only `api` is published on loopback by default. Health means process alive; readiness checks
schema head and required dependencies without leaking credentials. The worker and API run as
non-root identities and receive only their required credentials.

## 14. Worker Materialization and Publication

The existing review stages keep their local-path contract inside a bounded worker scratch area;
PostgreSQL/S3 remain authoritative outside that execution sandbox. M1 defines two explicit ports:

- `WorkspaceMaterializer.materialize(job, stage, input_manifest, destination)` downloads only
  declared source/prior artifacts, verifies size and SHA-256, rejects symlinks/special files, and
  produces a read-only input manifest inside a mode-0700 per-attempt/stage directory.
- `StageArtifactPublisher.publish(job, stage, output_spec, workspace)` accepts only explicitly
  declared relative outputs, rejects escapes/symlinks/special files and size-limit violations,
  computes digests, uploads temporary objects, verifies them, and returns immutable object
  descriptors without changing job state.

The worker lifecycle is:

```text
claim PostgreSQL work lease
→ check cancellation and input revision
→ create private scratch directory
→ materialize and verify inputs
→ run existing stage adapter against localized run directory
→ check cancellation
→ validate declared outputs and publish immutable objects
→ transactionally CAS job state + artifact manifests + stage event + audit + outbox + next work
→ acknowledge work
→ remove scratch
```

Scratch lives on a dedicated private volume, is not shared with API/Vite, uses per-attempt paths,
and is removed on success/failure/cancel; a startup/periodic janitor removes abandoned directories
older than the configured TTL. M1 does not claim forensic secure erasure from ordinary filesystems;
production operators must use encrypted ephemeral storage when that threat model applies. Logs and
database rows never expose scratch paths or manuscript content.

Recovery never trusts an existing scratch directory. An expired lease causes a fresh
materialization from authoritative object descriptors. Published-but-uncommitted objects remain
unreferenced and are removed by the object janitor. Committed stage manifests make redelivery
idempotent; a mismatched digest or input revision blocks the job and emits a safe operator event.
Cancellation is checked before materialization, before external calls, before publication, and
inside the final transaction. The materializer and publisher have memory/local and S3-backed
contract suites; kill tests cover failure after materialization, after object publication, and
after database commit.

## 15. Error, Audit, and Privacy Behavior

Stable API errors include `code`, safe `message`, `request_id`, `retryable`, and redacted
`details`. Provider bodies, SQL text, object keys, tokens, manuscript content, and tenant
existence are never exposed.

Audit records are append-only and capture actor, issuer/subject mapping ID, organization/project,
action, resource type/ID, command/idempotency ID, outcome, request ID, timestamp, and safe reason
metadata. They do not capture raw tokens, PDF text, provider responses, presigned URLs, or secrets.
Authentication failures before actor resolution are counted in operational logs with issuer and
error class only.

Dependency failures are classified. Database unavailability returns `503`; stale aggregate and
idempotency conflicts return `409`; oversized upload returns `413`; OIDC absence/expiry returns
`401`; visible-scope permission denial returns `403`; cross-tenant access returns `404`.
Rejected commands append no domain event or work item, but authorized validation/denial outcomes
may append a safe audit event when a transaction is available.

## 16. Verification Strategy

Implementation uses focused red/green tests per checkpoint and one concentrated final system
verification, matching the owner's preference to prioritize core progress.

### Shared contract suites

- identity: issuer/audience/signature/time validation, unknown `kid` refresh, disabled identity,
  no raw-token retention;
- membership/repository: tenant-scoped CRUD, role decisions, revocation, optimistic versioning;
- object store: bounded streaming, digest/size validation, immutable publish, tenant key isolation,
  Range/read parity, temporary cleanup;
- review/work/outbox: atomic command/state/event/work commit, idempotency payload conflict,
  lease/redelivery/dead-letter behavior;
- legacy: allowlisted registration, digest verification, read-only enforcement, safe locator view.

### Integration and system tests

- real PostgreSQL migrations, constraints, transaction rollback, concurrent idempotency and
  `SKIP LOCKED` claims;
- real Keycloak discovery/JWKS and role-independent membership enforcement;
- real MinIO temporary upload, immutable publication, digest and authorized Range download;
- FastAPI OpenAPI/error/request-ID/auth/tenant behavior;
- browser authorization-code/PKCE session, CSRF, logout and read-only Vite compatibility behavior;
- operator organization bootstrap, identity disable/unlink, legacy registration, and concurrent
  idempotent replay;
- two organizations with same-shaped IDs/objects cannot enumerate or access each other;
- membership revocation denies the next request;
- public deterministic legacy job and artifact remain readable only in the registered project;
- worker kill after object upload and after database commit recovers without duplicate manifest;
- migration upgrade/downgrade and M0 rollback profile smoke;
- existing M0 full repository verification remains green.

No confidential manuscript, reusable credential, raw OIDC token, or provider-private response is
stored in fixtures, logs, source, or Feishu. Integration credentials are ephemeral.

## 17. Acceptance Gates

M1 is complete only when all of the following are proven from a clean checkout:

1. FastAPI is the only published API service and required `/api/v1` routes/OpenAPI pass contract
   tests; legacy services remain private and the Vite fallback is reachable through the OIDC
   session bridge in enforced read-only compatibility mode.
2. PostgreSQL is authoritative for new tenant, project, paper metadata, ReviewJob state, work,
   outbox, command, and audit records; a forced rollback leaves no partial command.
3. Generic OIDC validation passes fake-provider and Keycloak suites, including JWKS rotation and
   disabled identity; Authorization Code + PKCE session/CSRF/logout tests pass; no browser-supplied
   role, tenant, identity, or forwarded header grants access.
4. Repository-level RBAC proves cross-tenant paper/job/artifact/legacy/audit isolation and immediate
   membership revocation.
5. MinIO and memory/local object adapters pass the same immutable publication and digest/Range
   contract; new PDF/artifact bytes are S3 authoritative.
6. Concurrent identical idempotent commands create one resource/work item and replay one response;
   changed payloads conflict without mutation.
7. A new local-only PDF can be uploaded, bound to a project, processed by the existing worker,
   observed through PostgreSQL-backed state/events, granted/denied scoped consent, taken through
   human decisions and Project-Owner finalization, and Range-downloaded from object storage.
8. A registered M0 fixture remains readable with matching artifact digests; mutation through the
   legacy registration is rejected.
9. Migration upgrade/downgrade, dependency failure, worker materialization/publication recovery,
   scratch/object cleanup, and M0 rollback smoke pass in the reference Compose environment.
10. Ruff, repository policy, M0 regression, M1 unit/contract/integration/system checks, OpenAPI
    drift, dependency checks, and frontend fallback build pass together.
11. The source worktree preservation evidence remains unchanged, the M1 branch is clean, system
    review documentation is updated, and verified progress is synchronized by revision-guarded
    edits to the existing ten-section Feishu document.

## 18. Deferred Work

M2 owns browser session UX in the supported Next.js shell, organization/project screens, and
generated TypeScript-client consumption. It reuses rather than replaces the M1 PKCE/session
boundary. M3 owns canvas state.
M4 owns high-autonomy Agent tools and approvals. M5 owns public TLS/rate-limit hardening, removal
of all legacy routes, coordinated PostgreSQL/object-store recovery watermarks, full backup/restore
exercises, release provenance, and production restore objectives. M1 records object digests and
transactional manifests needed for later recovery, but does not claim coordinated backup RPO/RTO.

These deferrals do not weaken M1 authentication or tenancy: all M1 APIs require real validated
OIDC bearer identity, all authorization is server-side, and only internal/loopback compatibility
access may bypass the future browser shell.
