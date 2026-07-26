<!-- status: historical-plan -->

# PeerAssist Platform, Evidence Canvas, and Autonomous Agent Design

**Status:** Approved design, implementation contracts reviewed

**Date:** 2026-07-17

**Product stage:** Alpha, controlled deployment only

## 1. Objective

Evolve the existing PeerAssist research prototype into a production-oriented review platform that supports both private deployment and public SaaS operation. The target product keeps PDF review as the primary reading workflow and adds an infinite evidence canvas for understanding relationships among sections, claims, evidence, citations, concerns, agent findings, and reports.

The work must preserve the existing evidence-grounded review core, recoverable ReviewJob state machine, citation audit, human confirmation, and immutable report finalization. The migration must be incremental and must not require a full rewrite.

## 2. Decisions

The following decisions were confirmed with the product owner:

- Use a progressive modular-monolith architecture.
- Migrate the frontend from React/Vite to Next.js incrementally.
- Migrate the HTTP boundary to FastAPI incrementally while preserving Python domain modules.
- Support private deployment and public SaaS from the same core codebase.
- Use replaceable OIDC, PostgreSQL, and S3-compatible adapters.
- Treat Supabase as one possible SaaS implementation, not a required domain dependency.
- Keep PDF reading as the primary workflow; use the canvas as an evidence relationship view.
- Build persistent single-user canvas editing first; defer realtime collaboration and CRDT.
- Allow high Agent autonomy for reversible work.
- Require human approval for external data transfer, destructive human-content changes, final-report publication, and external sharing.

## 3. Lessons Applied from the Reference Workflow

The reference Lovart recreation workflow is useful as an engineering method, not as a product template. PeerAssist should adopt the following practices:

1. Keep product requirements, architecture, API contracts, reference material, and acceptance criteria in the repository.
2. Add a concise repository-level `AGENTS.md` with supported setup, build, test, lint, review, and security commands.
3. Use MCP only for capabilities outside the repository, such as managed databases, object storage, identity providers, issue trackers, and design references.
4. Resolve missing environment variables and ambiguous requirements before implementation.
5. Use Codex persistent goals to track a large objective, while storing detailed instructions in versioned repository documents.
6. Define completion with executable checks rather than an Agent's self-reported success.

The project must not copy Lovart's image-generation domain, Next.js choice, or Supabase choice without evaluating them against PeerAssist's existing architecture and confidentiality requirements.

## 4. Non-Goals

The first delivery does not include:

- image or video generation workflows;
- a canvas-first replacement for PDF reading;
- realtime multiplayer cursors or CRDT synchronization;
- a microservice split by domain;
- removal of the existing review pipeline before compatibility is proven;
- autonomous publication of final reports without a human gate;
- direct model or Agent access to database credentials;
- a one-time rewrite of the existing frontend or backend.

## 5. Target Architecture

```text
Browser
  |
  v
Next.js Web
  - authentication and organization switcher
  - projects and papers
  - PDF review workspace
  - React Flow evidence canvas
  - Agent conversation, run timeline, and approval center
  |
  v
FastAPI Gateway
  - auth, session, and RBAC
  - project, paper, and canvas APIs
  - ReviewJob and artifact APIs
  - Agent tool API
  - SSE event stream
  |
  +-- PostgreSQL: identity references, tenancy, projects, canvas, audit
  +-- S3 adapter: PDFs, images, reports, immutable artifacts
  +-- Review worker: existing PeerAssist domain and ReviewJob execution
```

The initial target repository layout is:

```text
peerassist/
├── apps/
│   └── web/                 # Next.js application
├── services/
│   └── api/                 # FastAPI gateway and dependency wiring
├── src/peerassist/          # existing review domain
├── workers/
│   └── review/              # ReviewJob and Agent execution
├── packages/
│   ├── contracts/           # generated TypeScript API client
│   └── canvas-model/        # canvas node, edge, and command contracts
├── infrastructure/
│   ├── compose/
│   ├── nginx/
│   └── systemd/
├── docs/
└── AGENTS.md
```

This structure is a target, not an instruction to move all existing files immediately. Each move must accompany an executable migration and compatibility test.

## 6. Frontend Migration

### 6.1 Application shell

Create a Next.js App Router application that initially owns:

- sign-in and session refresh;
- organization and project selection;
- project and paper lists;
- upload and ReviewJob creation;
- global navigation, errors, approvals, and account settings.

The existing Vite workspace remains available through a controlled compatibility route during migration.

### 6.2 Module migration

Move functionality in the following order:

1. shared API client and generated contracts;
2. ReviewJob list and persistent timeline;
3. approval and human-confirmation views;
4. artifact list and report viewer;
5. PDF reader and evidence navigation;
6. remaining legacy routes.

Do not copy the existing 2,000-line `main.tsx` into a Next.js client component. Extract bounded components, hooks, and API services as each workflow moves.

### 6.3 Evidence canvas

Implement the canvas only in the Next.js application. Use React Flow for nodes, edges, viewport, grouping, selection, resizing, and interaction. Keep server-side rendering outside the interactive canvas component; load the canvas as a client boundary with explicit loading and error states.

The first canvas vertical slice supports:

- zoom, pan, fit view, selection, and keyboard focus;
- domain-backed paper, claim, evidence, concern, and note nodes;
- node dragging and persisted positions;
- read-only domain-derived evidence edges;
- PDF-to-canvas and canvas-to-PDF navigation;
- command history, snapshots, and explicit version conflicts.

Groups, resizing, user-created presentation edges, undo/redo, search, filtering, minimap,
deterministic layout, Agent-assisted layout, the remaining node types, mobile editing, and
1,000-node performance are separate M3 checkpoints. Mobile in the first slice is read-only.

## 7. Backend Migration

FastAPI becomes the authenticated public API boundary. Existing `src/peerassist` modules remain framework-independent domain services. The existing standard-library HTTP servers become compatibility adapters and are removed only after Next.js and FastAPI cover their routes.

FastAPI owns:

- OIDC callback and session exchange;
- organization, user, project, and role authorization;
- paper metadata and upload orchestration;
- ReviewJob commands and views;
- canvas commands, snapshots, and event replay;
- approval creation and resolution;
- artifact discovery and authorized download;
- SSE event streaming;
- OpenAPI generation.

The Review worker owns long-running review and Agent execution. API processes do not perform long model or parsing work inline.

## 8. Identity, Tenancy, and Storage

### 8.1 Identity

Use an internal identity abstraction backed by OIDC/OAuth providers.

- Private deployments may use Keycloak, Authentik, or an enterprise OIDC provider.
- SaaS deployments may use Supabase Auth, Auth0, or another managed OIDC provider.
- Browser sessions use `HttpOnly`, `Secure`, and appropriate `SameSite` cookies.
- Third-party access tokens are encrypted or kept in provider-managed storage and are never exposed to the browser or Agent tools.

### 8.2 Authorization

Initial roles are:

| Role | Scope |
| --- | --- |
| Organization Admin | members, identity configuration, quotas, audit |
| Project Owner | project settings, members, papers, sharing, deletion |
| Reviewer | run reviews, edit canvas, resolve concerns, draft reports |
| Viewer | read authorized papers, canvas, and artifacts |
| Service Agent | short-lived, tool-scoped machine permissions |

Every Paper, ReviewJob, Canvas, Approval, and Artifact query must be constrained by organization and project authorization. Resource UUIDs are not authorization controls.

### 8.3 Data stores

PostgreSQL is authoritative for relational metadata, workflow state, canvas commands, approvals, and audit events. S3-compatible storage is authoritative for PDFs and large immutable artifacts. Local filesystem adapters remain available for development and migration, but production domain logic does not depend on absolute server paths.

Private deployment uses PostgreSQL, MinIO, and an OIDC provider. SaaS uses managed PostgreSQL, S3-compatible object storage, and managed OIDC. Supabase may satisfy multiple SaaS adapters without entering domain interfaces.

## 9. Domain Model

Primary entities are:

- `Organization` and `User`;
- `Project` and `ProjectMembership`;
- `Paper` and `PaperVersion`;
- `ReviewJob`, `ReviewAttempt`, and `ReviewEvent`;
- `Claim`, `Evidence`, and `CitationFinding`;
- `Concern`, `Decision`, and `ReportVersion`;
- `Canvas`, `CanvasNode`, `CanvasEdge`, `CanvasCommand`, and `CanvasSnapshot`;
- `AgentRun`, `ToolInvocation`, and `Approval`;
- `Artifact` and `AuditEvent`.

### 9.1 Canvas authority boundary

Review facts belong to the review domain. The canvas stores presentation and relationship state. A domain-backed node contains a stable reference:

```json
{
  "node_id": "canvas-node-id",
  "node_type": "concern",
  "domain_ref": {
    "type": "concern",
    "id": "concern-id",
    "revision": 3
  },
  "position": { "x": 120, "y": 340 },
  "size": { "width": 320, "height": 180 }
}
```

The canvas must not duplicate mutable concern severity, confirmation status, or report publication status as authoritative values. Those fields are resolved from domain APIs.

### 9.2 Node types

The first version supports:

- paper;
- section;
- claim;
- evidence;
- citation;
- concern;
- agent;
- group;
- note;
- report.

Notes, groups, viewport, size, and layout are canvas-owned. Review entities are domain-backed.

### 9.3 Canvas persistence

Use an append-only command log plus periodic snapshots.

- Every command records actor, base version, resulting version, timestamp, and idempotency key.
- Optimistic version checks prevent an Agent from overwriting newer user changes.
- Undo and redo use compensating commands rather than history rewriting.
- Loading reads the latest snapshot and replays later commands.
- ReviewJob reruns reconcile nodes through stable domain IDs and preserve manual layout.
- Realtime multiplayer and CRDT are deferred. Present invariants are immutable command IDs,
  explicit actors, monotonically increasing aggregate versions, and deterministic replay.

## 10. Agent Architecture

Use a persistent Review Supervisor that coordinates:

- Intake Agent;
- Evidence Planner;
- existing specialist review Agents;
- Adversarial Agent;
- Canvas Curator;
- Evidence Verifier;
- Report Synthesizer.

The durable execution sequence is:

```text
plan
→ collect_evidence
→ run_specialists
→ challenge_findings
→ verify_grounding
→ curate_canvas
→ synthesize_report
→ await_approval
→ finalize
```

Each `AgentRun` stores goal, input revision, prompt version, evidence scope, tools, budgets, checkpoints, retries, provider identity, token usage, cost estimate, output IDs, external data-transfer record, and final status.

### 10.1 Tool boundary

Agents never write the database directly. They call tools such as:

```text
canvas.query
canvas.create_node
canvas.move_nodes
canvas.connect
canvas.group
canvas.focus_evidence
review.start
review.retry
concern.propose
concern.resolve
report.finalize
artifact.share
```

Every write tool requires a run ID, idempotency key, actor, base version, and policy classification.

### 10.2 Autonomy and approvals

| Level | Examples | Behavior |
| --- | --- | --- |
| L0 read-only | query paper, evidence, canvas, job | execute automatically |
| L1 reversible write | create, move, connect, group, propose concern | execute and audit |
| L2 high-risk | external transfer, override human content, delete, share, finalize | create Approval and wait |

The following gates cannot be bypassed by model output:

- every external OCR, search, or model transfer unless a still-valid scoped grant matches it;
- deletion or replacement of human-authored review content;
- final report publication;
- external sharing or sending.

The Agent may autonomously produce a report draft. It may not publish a final report without approval.

### 10.3 Reliability limits

- Evidence references must belong to the input scope.
- Each stage has token, time, tool-call, and cost budgets.
- A failed external call receives at most two automatic retries before degradation or approval.
- User canvas changes invalidate stale Agent layout plans.
- Completion is evaluated by structured checks, not by free-form Agent claims.
- Provider adapters isolate OpenAI, Codex-authenticated, and other compatible models from the domain.

## 11. API and Event Flow

The main flow is:

```text
upload PDF
→ create Paper and Project binding
→ create ReviewJob
→ parse, evidence, claims, citations, specialist reviews
→ project stable review entities into Canvas
→ Agent queries and proposes reversible canvas changes
→ policy engine executes or creates Approval
→ SSE emits job, canvas, Agent, and approval events
→ reviewer resolves approvals and concerns
→ final report is published as an immutable version
```

Use versioned APIs under `/api/v1`. Generate the TypeScript client from FastAPI OpenAPI instead of maintaining handwritten duplicate request types. Keep domain schemas versioned independently when persisted artifacts require longer compatibility.

SSE is the initial event transport. Clients reconnect with an event ID and fall back to bounded polling. WebSockets are deferred until realtime collaboration is implemented.

## 12. Security and Recovery

### 12.1 Security controls

- Default Python services to loopback or private container networks.
- Expose only a TLS gateway with authentication, request size limits, rate limits, and security headers.
- Enforce organization and project scopes in repositories, not only route handlers.
- Use short-lived signed URLs or authorized streaming for artifacts.
- Separate retention rules for papers, model context, artifacts, and audit logs.
- Record the destination, scope, purpose, and approval for external data transfer.
- Keep secrets out of browser state, Agent prompts, logs, repository files, and report artifacts.
- Add dependency scanning, secret scanning, SBOM generation, and container provenance before production release.

### 12.2 Recovery controls

- Use idempotency keys for uploads and commands.
- Use worker leases and heartbeats; interrupted jobs remain recoverable.
- Commit each stage independently and preserve the last valid checkpoint.
- Upload artifacts to temporary object keys, validate size and digest, then publish atomically.
- Return `409` for canvas version conflicts and provide reload/merge UX.
- Use exponential backoff and circuit breakers for external providers.
- Keep report versions immutable and move only an atomic current pointer.
- Test backup restoration for PostgreSQL and object storage together.

## 13. Deployment Profiles

### 13.1 Private deployment

```text
Next.js + FastAPI + Review Worker + PostgreSQL queue/outbox
+ MinIO + Keycloak/Authentik
```

Provide Docker Compose for development and small private installations. Larger private installations may use Kubernetes or equivalent orchestration.

### 13.2 SaaS deployment

```text
Next.js + FastAPI + Worker Pool + Managed PostgreSQL queue/outbox
+ S3-compatible storage + Managed OIDC
```

Both profiles use the same domain interfaces and OpenAPI contracts. Provider-specific deployment code lives under `infrastructure/`.

## 14. Migration Milestones

### M0: Engineering baseline

Deliver:

- `AGENTS.md`, PRD, architecture records, and API conventions;
- repair the current failing test and Ruff baseline;
- CI for Python tests, Ruff, frontend build, secret scan, and documentation links;
- Docker Compose development environment;
- unified configuration loading and secure defaults.

Acceptance:

- a clean clone starts the development environment through one documented command;
- default tests, Ruff, frontend build, and documentation checks pass;
- the fixed route-parity fixtures in Section 24 match the legacy status, normalized fields,
  artifact digests, transitions, and terminal state.

### M1: Platform and authorization

Split delivery into M1a FastAPI compatibility facade, M1b PostgreSQL tenancy and legacy
read-only registration, M1c OIDC/RBAC, and M1d S3 plus new-write cutover. Deliver audit,
idempotency, and adapter contract suites with each checkpoint.

Acceptance:

- organizations cannot access each other's papers, jobs, canvas documents, or artifacts;
- private and SaaS identity adapters pass the same contract suite;
- current ReviewJob artifacts remain readable.

### M2: Next.js shell

Deliver App Router, authentication, organization and project flows, paper upload, task list, generated API client, compatibility route, and first review-workspace migrations.

Acceptance:

- the complete primary review path defined in Section 24 passes through Next.js and FastAPI;
- the Vite workspace remains a tested fallback;
- no authentication or project checks are implemented only in client code.

### M3: Infinite evidence canvas

Split delivery into M3a the paper/claim/evidence/concern/note read-persist-navigate vertical
slice, M3b editing history and remaining node types, M3c search/layout/1,000-node performance,
and M3d Agent canvas tools.

Acceptance:

- M3a meets the 200-node desktop and mobile read-only gates in Section 24, and M3c separately
  meets the 1,000-node performance trace gate;
- ReviewJob reruns preserve manual layout;
- every domain-backed node opens its authoritative source;
- stale Agent commands fail rather than overwrite user work.

### M4: Highly autonomous Agent

Split delivery into M4a tool policy, M4b durable orchestration, M4c Approval Center, and M4d
evidence verification plus report synthesis. Logical stages remain in one Supervisor process
unless permissions, budgets, prompts, failure isolation, or retry requirements justify a
separate runtime Agent.

Acceptance:

- an Agent can progress from an uploaded paper to a report draft;
- all L2 actions stop at approval;
- evidence-scope violations are rejected;
- interrupted Agent runs resume from durable checkpoints.

### M5: Migration closure and production hardening

Deliver removal of legacy Vite and standard-library HTTP entrypoints, security hardening, browser test coverage, dependency locks, images, SBOM, release automation, and private/SaaS operations guides.

Acceptance:

- security, restore, browser, and deployment smoke suites pass;
- no production route depends on the legacy gateways;
- rollback completes inside the 14-day compatibility window and the restore exercise meets the
  RPO/RTO and reconstructed-artifact checks in Section 24.

## 15. Testing Strategy

Use the following layered test strategy:

```text
domain unit tests
→ PostgreSQL and S3 adapter tests
→ OpenAPI contract tests
→ FastAPI integration tests
→ Next.js component tests
→ Playwright user workflows
→ Agent tool and approval tests
→ permission-isolation, fault-injection, and recovery tests
```

Required release checks include:

- Python 3.11 and 3.12 fast suites;
- Ruff and type checks;
- Next.js type check, lint, unit tests, and production build;
- generated-client drift check;
- migration upgrade and rollback tests;
- secret and dependency scans;
- PDF upload, ReviewJob recovery, canvas conflict, approval, and report finalization browser flows;
- desktop and mobile screenshots with overlap and overflow checks;
- private and SaaS deployment smoke tests.

External model, OCR, retrieval, and Docker execution tests run in gated scheduled or manual workflows and never receive confidential fixtures.

## 16. Rollout and Compatibility

Each milestone must be independently deployable and reversible. During migration:

- keep existing file-backed artifacts readable;
- use compatibility adapters rather than bulk-rewriting historical runs;
- dual-write only when reconciliation and rollback are specified;
- expose migration status and failures in operations endpoints;
- remove a legacy path only after parity tests and a defined rollback window;
- avoid unrelated package or directory renaming.

## 17. Definition of Done

The full program is complete only when:

- the engineering baseline is green in CI;
- private and SaaS deployments use the same domain contracts;
- Next.js owns the supported user experience;
- FastAPI owns the authenticated API boundary;
- the evidence canvas is persistent, traceable, and linked to PDF sources;
- the Agent can autonomously produce a grounded report draft;
- high-risk actions always require approval;
- legacy HTTP and Vite entrypoints are removed after parity verification;
- security and disaster-recovery exercises pass;
- the documentation matches executable setup and release procedures.

## 18. Migration Ownership Contract

Only one component may own writes for a resource in a milestone. Compatibility layers may
translate or proxy, but they may not create an independent source of truth.

| Milestone | Browser route owner | Public API owner | Write authority | Legacy access |
| --- | --- | --- | --- | --- |
| M0 | Vite | standard-library HTTP | file-backed repositories | unchanged, loopback/private only |
| M1a | Vite | FastAPI facade | existing file-backed repositories through domain ports | direct legacy ports bind loopback; FastAPI is the only public route |
| M1b-M1c | Vite with Next.js sign-in shell | FastAPI | PostgreSQL for tenancy/session; legacy review files remain authoritative for existing jobs | signed internal service token only |
| M1d-M2 | Next.js for platform routes; Vite compatibility path for review UI | FastAPI | PostgreSQL/S3 for new papers and jobs; registered legacy jobs are read-only | `/legacy/*` requires the same session and project authorization |
| M3-M4 | Next.js | FastAPI | PostgreSQL/S3 and domain services | compatibility path read-only; no legacy mutations |
| M5 | Next.js | FastAPI | PostgreSQL/S3 and domain services | removed after two releases and parity evidence |

FastAPI terminates the browser session and issues a short-lived, audience-bound internal token
when it must call a legacy adapter. Direct access to legacy ports is denied by network policy.
Next.js never forwards an identity header supplied by the browser.

Historical file-backed runs are registered with organization, project, immutable source path,
artifact digests, and schema version. They remain read-only until an explicit import verifies
every declared artifact and commits a migration manifest. New writes never dual-write by
default. A checkpoint may enable dual-write only with a reconciliation report, failure queue,
and a tested switch back to the prior writer.

Cutover requires route-by-route parity fixtures for success, validation failure, authorization,
range download, idempotency, cancellation, retry, confirmation, and finalization. Rollback keeps
the previous binary and writer schema for 14 days; database migrations in that window must be
expand/contract and backward-readable.

## 19. Ports and Adapter Contracts

Framework-independent application services depend on these ports:

| Port | Required operations and invariants |
| --- | --- |
| `IdentityProvider` | validate issuer/audience/signature; map stable subject and verified claims; refresh; RP logout; JWKS rotation; reject removed/disabled identity |
| `MembershipRepository` | provision/link users; resolve organization/project roles; tenant-scoped queries; membership revocation visible within 60 seconds |
| `PaperRepository` | idempotent create by content digest; version binding; tenant-scoped lookup; retention/tombstone |
| `ReviewRepository` | compare-and-swap state; attempts; leases; checkpoints; immutable events; legacy read adapter |
| `ObjectStore` | temporary upload; size/digest validation; atomic publish; tenant key prefix; signed read with expiry; delete/tombstone; local filesystem parity |
| `CanvasRepository` | atomic command append and aggregate version increment; snapshot publish; deterministic replay; tombstones |
| `PolicyEngine` | classify tool command; find matching grant; create/revoke approval; stale-input check |
| `WorkDispatcher` | enqueue after commit; claim/heartbeat/ack/nack; visibility timeout; deduplicate job/attempt/stage key; dead-letter inspection |
| `EventPublisher` | append ordered aggregate event through outbox; replay by cursor; retention metadata |
| `ModelProvider` | declared destination/model; bounded request; cancellation; usage; no hidden retry; structured error classification |

Each port has an in-memory fake and a provider contract suite. Provider implementations must
pass the same suite without provider-specific tables, claims, bucket rules, or error names
leaking into application services. Supabase may implement OIDC, PostgreSQL, and S3 ports, but
Supabase RLS supplements rather than replaces application repository authorization.

The canonical contract flow is:

```text
Python Pydantic domain and command models
→ versioned FastAPI request/view schemas
→ OpenAPI artifact
→ generated TypeScript client
```

Persisted review artifacts keep their existing versioned schemas and are translated by explicit
adapters. `packages/canvas-model` contains generated or mechanically mirrored canvas view and
command types; it cannot define a competing schema. CI fails on OpenAPI or generated-client
drift.

## 20. Worker Dispatch and Transaction Consistency

Both private and SaaS profiles use a PostgreSQL-backed queue/outbox abstraction initially. A
managed queue may replace it only by implementing `WorkDispatcher` and its contract suite.

In one database transaction, an API command commits domain state, an outbox event, and a work
item. A dispatcher claims committed work using `FOR UPDATE SKIP LOCKED`, lease expiry, and a
deduplication key `(job_id, attempt_id, stage, input_revision)`. A Worker writes large output to
a temporary object, verifies its digest, then commits the stage manifest and publish pointer in
one database transaction. An asynchronous janitor removes unreferenced temporary objects older
than 24 hours.

Events are aggregate-ordered by a database sequence. Delivery is at least once; consumers
deduplicate by immutable event ID. Work execution is at least once, so all stage commits and
tool writes are idempotent. A crash before database commit leaves only a temporary object. A
crash after commit but before acknowledgement causes safe redelivery. A database commit that
cannot publish SSE remains in the outbox and is replayed after recovery.

Cancellation is a durable command checked before each external call and stage commit. Quota
exhaustion enters `blocked` with a required action. Three expired leases for the same work item
move it to a dead-letter state and require an operator retry with a new attempt.

## 21. Canvas Authority and Reconciliation Contract

| Node type | Authoritative owner | Stable reference | Canvas-owned writes | Missing/replaced domain entity |
| --- | --- | --- | --- | --- |
| paper | Paper domain | paper ID + paper version | position, size | old version is retained and marked superseded |
| section | Review domain | paper version + section ID | position, collapsed state | tombstone; show source unavailable |
| claim | Review domain | finding lineage ID + revision | position | reconcile to newer lineage revision or tombstone |
| evidence | Review domain | evidence ID + ledger version | position | tombstone; never silently rebind by text |
| citation | Citation domain | citation lineage ID + revision | position | reconcile by lineage or tombstone |
| concern | Concern domain | finding lineage ID + revision | position | reflect resolved/deleted state; preserve audit node |
| agent | AgentRun domain | AgentRun ID | position | immutable historical run remains viewable |
| report | Report domain | report version ID | position | immutable; superseded reports remain viewable |
| group | Canvas | node ID | all fields | normal canvas tombstone |
| note | Canvas | node ID + revision | all fields | normal canvas tombstone; human delete is L2 |

Domain-derived edges such as `claim_supported_by_evidence`, `concern_about_claim`, and
`citation_observed_at_evidence` are read-only projections owned by the review domain. Canvas
presentation edges use a separate `visual_relation` namespace, have no evidentiary meaning, and
may be created or deleted at L1. The UI must visually distinguish these classes.

Reconciliation consumes a complete projection manifest containing domain ID, lineage ID,
revision, and source artifact digest. It updates existing nodes by lineage, creates new nodes,
and tombstones missing nodes. It never reuses a canvas node ID for another domain entity. A
tombstone can be hidden but remains in command history. A user may explicitly archive it; only
an authorized retention job may physically purge it.

Command validation and version increment are one database transaction. A duplicate
idempotency key returns the original status/body. Invalid commands append no event. Undo creates
a validated compensating command; if its precondition no longer holds it returns `409
undo_conflict` without partial mutation. Snapshot digests are verified on load; a corrupt
snapshot is ignored and rebuilt from the command log, while corrupt command history blocks the
canvas and raises an operator incident.

## 22. Approval, Confirmation, and Report State Machines

An Approval is scoped, not a blanket boolean. It contains actor, eligible roles, action,
destination/provider, purpose, organization/project/paper, input revision, payload digest or
allowed field set, cost/tool budget, created/expiry timestamps, and optional reusable-grant
scope. States are:

```text
pending → approved → consumed
       ↘ denied
       ↘ expired
       ↘ cancelled
approved → revoked
```

Project Owner and Reviewer may approve external analysis and concern actions. Only Project
Owner may approve external sharing, retention deletion, an override of human-authored content,
or final publication. Organization policy may require Organization Admin as an additional
approver. A reusable external-service grant has a maximum 24-hour expiry and exact provider,
purpose, paper version, and data-field scope. Changed input revision or payload scope makes it
stale. Resume uses an atomic approval-consumption compare-and-swap, so a one-time approval
starts its command exactly once.

An Approval aggregate contains immutable requirements such as `Reviewer:1`, `ProjectOwner:1`,
or `ProjectOwner:1 + OrganizationAdmin:1`. Each eligible user records one signed decision with
role-at-decision, timestamp, reason, and input digest. In one transaction, the repository locks
the aggregate, rejects duplicate decisions, evaluates all role counts, and moves `pending` to
`approved` only when every requirement is satisfied. Any required approver denial moves the
aggregate to `denied`; expiry or cancellation closes it without consumption. Revocation before
consumption returns it to `revoked`; after consumption it prevents later reuse but cannot undo
the audited command. A consumed command that has not committed may retry with the same approval,
command ID, and idempotency key; a changed command or input requires a new Approval.

Classification details:

- `concern.propose`, canvas layout, presentation-edge changes, and domain-node hide are L1.
- `concern.resolve` is a versioned human confirmation, not an Approval, when the actor is a
  Reviewer; an Agent request to resolve requires L2 Approval.
- human-note deletion, permanent domain deletion, and replacement of human text are L2.
- undo/redo inherits the highest risk of the command it compensates.
- `report.finalize` and `artifact.share` are always L2.

Concern lifecycle is `candidate → pending_confirmation → confirmed|rewritten|downgraded|
deleted`. If the concern revision or evidence ledger changes after a Decision, it enters the
distinct `needs_reconciliation` state. A Reviewer must compare the old Decision with the new
evidence and transition it to `pending_confirmation` or submit a new confirmed/rewritten/
downgraded/deleted Decision. `needs_reconciliation` blocks report approval and finalization.
Every Decision records concern revision, evidence ledger version, actor, and reason.

Report lifecycle is:

```text
draft → ready_for_approval → approved → published → stale|superseded
```

`report.finalize` is the single approved publication command. It verifies the Approval,
confirmation revision, evidence snapshot digest, and absence of unresolved core or
`needs_reconciliation` concerns; writes and validates immutable artifacts; then commits the
published manifest and current pointer atomically. A failure before the database transaction
leaves temporary objects for cleanup and the report `approved`; retry uses the same command and
Approval. No externally visible `finalized` intermediate state exists. Sharing is a separate
Approval after publication. Later evidence or concern changes do not mutate the report; they
mark it `stale` and produce a new draft/version. Publishing a replacement moves the prior
version to `superseded`. Revocation disables future sharing but does not rewrite audit history.

## 23. API, Event, and Error Contracts

Commands validate authorization, tenant scope, aggregate version, idempotency, policy, quotas,
and dependency availability before mutation. API errors use a stable envelope with `code`,
`message`, `request_id`, `retryable`, and safe `details`.

| HTTP | Required semantics |
| --- | --- |
| 400/422 | malformed request or domain validation; no mutation |
| 401 | absent/expired session; refresh once, then sign in |
| 403 | authenticated but prohibited action when revealing resource existence is safe |
| 404 | missing resource and cross-tenant resource to prevent enumeration |
| 409 | aggregate version, idempotency payload, approval, or reconciliation conflict |
| 413 | upload over configured limit before persistence |
| 429 | tenant quota or provider throttle with bounded `Retry-After` |
| 502/503/504 | classified provider/dependency failure; no secret or upstream body leakage |

SSE authorization is rechecked on connection and at least every 60 seconds. Events contain an
immutable ID, aggregate, aggregate sequence, type, schema version, timestamp, and redacted
payload. Clients deduplicate by ID. Event retention is 30 days in the hot store, followed by an
authorized archive. A cursor older than retention returns `409 event_cursor_expired` and the
client reloads an authorized snapshot. Polling fallback uses 3-second intervals, exponential
backoff to 30 seconds, and stops after session expiry or terminal state.

A stale canvas client receives current version plus a bounded list of changed command metadata.
It may reload and reapply non-conflicting position changes; note text, deletion, domain
reconciliation, and risk-level changes require explicit user choice. Server-side last-write-wins
is prohibited.

## 24. Measurable Acceptance Gates

M0 names the current regression as
`tests/peerassist/test_confirmation_server.py::test_workspace_frontend_contains_pdfjs_review_reader`.
Completion requires zero default pytest failures, zero Ruff findings, a successful production
frontend build, zero documentation-link failures, and zero detected test credentials on a clean
clone using the documented commands.

Route parity uses fixed public PDF, ReviewJob, concern, citation, and finalized-report fixtures.
The Next.js/FastAPI path must match the legacy path for required status, normalized response
fields, artifact digests, Range bytes, confirmation transitions, and terminal job state. “Primary
review path” means sign in, create/open project, upload PDF, create job, reach human confirmation,
open evidence in PDF, decide a concern, finalize, and download the report.

The M3a paper, claim, evidence, concern, and note slice is tested at 1440x900 desktop and 390x844
mobile read-only. Desktop commands must
acknowledge locally within 100 ms at p95 excluding network, and persisted command API latency
must be below 500 ms p95 in the Compose reference environment with 200 nodes/300 edges. M3c
raises the desktop fixture to 1,000 nodes/1,500 edges at 1440x900 in Chromium stable on the CI
performance runner: 4 dedicated x86-64 vCPU, 8 GiB RAM, hardware acceleration disabled, no CPU
throttling, and a local Compose backend. The trace repeats wheel zoom, pointer pan, fit view,
search focus, and selection for 30 seconds. Input-to-paint must remain below 50 ms p95, no long
task may exceed 200 ms, and no horizontal overflow or uncaught console error is allowed. Mobile
remains read-only at M3c and is tested for load, focus, PDF navigation, and overflow, not the
1,000-node interaction threshold.

A grounded report draft fixture requires every active factual concern to reference an allowed
evidence ID, zero unknown evidence IDs, zero unsupported numeric facts in deterministic audit,
and explicit degraded-provider disclosure. Recovery tests kill a Worker after object upload and
after database commit, then require the same attempt to reach the expected durable state without
duplicate stage manifests within 120 seconds.

Backup objectives use one coordinated recovery watermark recorded only after both the database
backup/WAL position and object replication inventory are durable. PostgreSQL may retain an RPO
of 5 minutes, but a restored publish pointer cannot advance beyond the most recent object-store
watermark, whose maximum lag is 1 hour. Restore reconciliation verifies every referenced object
digest; pointers newer than the watermark are rolled back to the latest complete immutable
version, affected jobs enter `recovery_reconciliation`, and missing artifacts are never reported
as available. RTO is 60 minutes for the reference private deployment. A quarterly restore test
must reconstruct papers, current ReviewJob state, canvas replay, approvals, and report digests
in an isolated environment. Deployment smoke requires health, auth, upload, one local-only
review, canvas load, approval denial, and artifact download for both private and SaaS profiles.

Retention, tenant deletion, paper replacement, provider outage, quota exhaustion, cancellation,
approval abandonment, stale approval, and dead-letter recovery are mandatory fault-matrix cases.
