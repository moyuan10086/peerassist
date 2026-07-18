# PeerAssist system review

This report records the verified M0 engineering baseline on 2026-07-17. It
contains only public engineering evidence; credentials, manuscript text,
provider responses, reviewer-private material, and machine-specific paths are
excluded.

## Delivered baseline

- Repository guidance: `AGENTS.md`, contribution and security boundaries,
  platform PRD, API conventions, and accepted architecture decisions.
- Unified verification: one fail-fast entry point for Ruff, Python tests,
  documentation, secret policy, frontend production build, dependency health,
  and committed distribution drift.
- Secure configuration: typed settings, loopback defaults, explicit external
  transfer consent, bounded upload and workspace paths, and secret-safe checks.
- Deployable development profile: two non-root Compose services, private Review
  API, loopback-only workspace gateway, persistent job volume, health probes,
  and dynamic loopback port support for isolated bootstrap verification.
- Legacy compatibility: 21 frozen routes, PDF Range support, deterministic JSON
  contracts, durable decision reconciliation, idempotent cancel/retry/finalize,
  packaged frontend and RefCopilot runtime assets, and secure systemd units with
  separate service identities and data paths.

## Verification evidence

The final repository verification command completed successfully with `941
passed, 1 skipped, 3 deselected`. Ruff, documentation links, secret scanning,
frontend production build, dependency checks, and committed distribution drift
all passed.

The clean-checkout bootstrap also completed successfully from a newly created
tracked-file checkout. It installed Python and Node dependencies, built and
installed the wheel, ran the same repository checks, built and started both
Compose services, waited for health, and verified a 1,024-byte PDF Range
response with `206`, `Content-Range`, and byte-for-byte comparison.

The delivered systemd units were installed and started in an isolated real
systemd environment. Both services and the gateway health checks passed, the
API and UI ran under separate identities, sibling data paths were inaccessible,
and cross-service process-root access was denied.

The original dirty source worktree was not built, reset, stashed, or cleaned.
Its final porcelain status and tracked/untracked file hashes exactly matched the
captured pre-execution evidence.

## Residual scope

M0 is an engineering baseline, not the production platform. OIDC, PostgreSQL,
S3-compatible storage, tenant-aware RBAC, the Next.js application shell, the
React Flow evidence canvas, production TLS and rate limiting, and full browser
regression remain later milestones. The existing frontend chunk-size warning is
accepted for M0 and should be addressed during the Next.js migration.

## M1 platform delivery

M1 adds real browser login, server sessions and CSRF; organization, project and member management;
tenant-scoped PostgreSQL repositories; MinIO/S3 paper uploads and immutable artifacts; durable
review commands, events, work leases and Worker publication; human decisions/finalization; and a
digest-verified read-only M0 compatibility route. The current React workspace now calls the M1
paper/review APIs for authenticated users and displays the generated Chinese paper summary and
report artifacts.

Focused platform verification is `317 passed, 18 deselected`. The real-provider smoke separately
passed PostgreSQL review commands (1), the unchanged MinIO object contract (6), MinIO integration
(1), and Keycloak discovery/JWKS token validation (1). OpenAPI has 35 unique operations with no
drift, the frontend production build succeeds, and Playwright verified `/login`, `/admin`, model
settings, real PDF upload, a durable Worker result, artifact reads, PDF `200/206` Range behavior,
and visible “这篇论文讲了什么” content.

The configured `gpt-5` credential in the current local environment is rejected by the upstream
provider with HTTP 401. The workspace now verifies model discovery before enabling model actions
and shows “连接失败” for this state; Worker publication falls back to evidence text extraction and
marks the summary accordingly. A valid provider credential is still required for model-generated
analysis.
