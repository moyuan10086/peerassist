# ADR 0001: Progressive Modular Monolith

**Status: Accepted**
**Date:** 2026-07-17

## Context

PeerAssist already contains a substantial Python review domain, recoverable jobs, a React/Vite workspace, and standard-library HTTP adapters. The platform needs authenticated tenancy, a durable canvas, generated contracts, and clearer deployment boundaries without pausing product delivery for a rewrite.

## Decision

Adopt a progressive modular monolith. Keep framework-independent domain and application services in Python, with explicit module boundaries and provider ports. Introduce FastAPI incrementally as the authenticated public API and Next.js incrementally as the supported application shell.

The existing Vite workspace and HTTP servers remain compatibility adapters until route and workflow parity are proven. Only one component owns writes for an aggregate in each milestone. Long-running review and Agent execution runs in workers, not HTTP request handlers.

Directory moves follow executable migrations; the target layout is not justification for speculative renaming. A module may be split into a service only when permission, scaling, availability, or failure-isolation evidence requires an independent boundary.

## Consequences

- Existing evidence-grounded behavior remains reusable and testable during migration.
- FastAPI and Next.js can advance route by route with rollback and compatibility fixtures.
- Module boundaries, schemas, and write ownership require explicit tests and discipline.
- Temporary adapters add complexity, but that complexity is bounded by parity gates and removal milestones.
- A premature microservice split and a one-time frontend or backend rewrite are rejected.
