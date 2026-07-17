# PeerAssist Platform Product Requirements

**Status:** Approved direction for incremental delivery
**Product stage:** Alpha, controlled deployment only

## Product Goals

PeerAssist turns an evidence-grounded review prototype into a production-oriented platform without discarding its existing review pipeline. It helps reviewers move from an uploaded paper to traceable evidence, concerns, human decisions, and a versioned report draft.

The primary experience remains PDF reading: reviewers inspect the manuscript, select text, navigate evidence, and resolve findings in source context. A persistent canvas relationship view complements the PDF by showing how papers, sections, claims, evidence, citations, concerns, notes, Agents, and reports relate. The canvas must never replace authoritative review facts with presentation copies.

The platform must support private deployments and future public SaaS through the same domain contracts. It must preserve recoverable jobs, immutable evidence and report versions, auditable Agent actions, and human approval for high-risk work.

## Users

- **Reviewer:** reads PDFs, runs reviews, inspects evidence, edits canvas layout, confirms concerns, and prepares report drafts.
- **Project Owner:** controls project membership, papers, publication, sharing, retention, and high-risk approvals.
- **Organization Administrator:** manages identity, organization policy, quotas, and audit access.
- **Viewer:** reads authorized papers, canvases, and published artifacts without mutation rights.
- **Service Agent:** performs bounded, short-lived, tool-scoped work with an auditable actor and budget.

## Core Workflow

1. An authorized user uploads a PDF and binds it to a project and paper version.
2. A recoverable ReviewJob parses the paper and produces stable claims, evidence, citation findings, and concerns.
3. The PDF workspace presents findings at their source locations.
4. Domain entities project into the canvas relationship view; reviewers arrange presentation state without changing evidence truth.
5. Agents may research, challenge, verify, organize, and draft through audited tools.
6. Reviewers resolve concerns and approval requests.
7. A final report becomes an immutable published version only after human approval.

## Constraints

- Migrate incrementally from React/Vite and standard-library HTTP services; do not perform a one-time rewrite.
- Use a progressive modular monolith with framework-independent Python domain services.
- Keep one write authority for each aggregate at every migration milestone.
- Use replaceable OIDC, PostgreSQL, and S3-compatible ports. Supabase may implement ports but is never required by the domain.
- Enforce organization and project authorization in data access boundaries.
- Use SSE first for ordered progress events and recover through cursors and bounded polling.
- Persist canvas commands with idempotency keys and optimistic aggregate versions.
- Keep PDF review usable throughout migration; the Vite route remains a tested fallback until parity is proven.
- Require approval for external data transfer, destructive human-content changes, final publication, and external sharing.
- Do not use confidential manuscripts in production until the documented security boundary is satisfied.

## Milestones

### M0 - Engineering baseline

Establish repository guidance, executable verification, clean lint and test baselines, API conventions, ADRs, secure defaults, CI, and a reproducible local environment.

### M1 - Platform and authorization

Introduce the FastAPI compatibility boundary, PostgreSQL tenancy, legacy read registration, OIDC/RBAC, S3-compatible artifacts, audit events, and idempotent commands. Prove cross-tenant isolation and adapter parity.

### M2 - Next.js application shell

Deliver authentication, organization/project navigation, paper upload, job timeline, approvals, generated API clients, and incremental review-workspace migration while retaining a tested compatibility route.

### M3 - Infinite evidence canvas

Deliver persistent paper, claim, evidence, concern, and note nodes; PDF-to-canvas navigation; command history; conflict handling; remaining node types; deterministic layout; search; and bounded performance. Mobile editing follows a read-only first slice.

### M4 - Autonomous review Agent

Deliver policy-classified tools, durable orchestration, approval center, evidence verification, canvas curation, and grounded report synthesis. Agents may produce drafts autonomously; high-risk actions wait for approval.

### M5 - Migration closure and hardening

Remove legacy entrypoints after parity, complete browser/security/restore coverage, add release provenance and operations guides, and validate private and SaaS deployment profiles and rollback.

## Success Criteria

- Reviewers can complete the primary PDF review flow with source-linked evidence.
- Canvas nodes resolve to authoritative domain entities and preserve manual layout across reruns.
- Interrupted jobs and Agent runs resume without duplicating committed work.
- Tenant, approval, idempotency, conflict, and artifact-access tests pass.
- Private and SaaS adapters satisfy the same port contracts.
- Published reports are immutable, traceable, and human approved.

## Non-Goals

- Replacing PDF reading with a canvas-first product.
- Realtime multiplayer cursors or CRDT synchronization in the first canvas delivery.
- A microservice split or wholesale rewrite.
- Image or video generation workflows.
- Autonomous final-report publication or external sharing.
- Direct Agent access to databases, object stores, or provider credentials.
- Making Supabase, Next.js, or FastAPI a domain dependency.
