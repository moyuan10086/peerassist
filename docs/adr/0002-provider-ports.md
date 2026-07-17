# ADR 0002: Replaceable Identity, Database, And Object Storage Ports

**Status: Accepted**
**Date:** 2026-07-17

## Context

PeerAssist must support private installations and a future SaaS profile. Binding domain code to one managed platform would complicate private deployment, testing, migration, tenant authorization, and recovery.

## Decision

Define framework-independent ports for OIDC identity, PostgreSQL repositories and transactions, and S3-compatible object storage. Domain and application services depend on those ports, never provider SDKs, provider claims, table conventions, bucket policy names, or private error payloads.

Each port has an in-memory fake and a shared provider contract suite. Private deployments may use enterprise OIDC, PostgreSQL, and MinIO. SaaS may use managed OIDC, PostgreSQL, and S3-compatible storage.

Supabase is optional: it may implement one or more ports, but it is not a domain dependency. Supabase authorization features supplement rather than replace application repository authorization.

## Consequences

- Private and SaaS profiles share domain behavior, API contracts, and tests.
- Provider replacement and failure simulation are practical.
- Adapter code must translate identity, transaction, storage, and error semantics explicitly.
- Contract suites add upfront work but prevent provider behavior from leaking into the domain.
- Agents receive tool-scoped capabilities and never receive provider credentials or direct storage/database access.
