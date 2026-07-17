# PeerAssist Documentation

Repository documents are the source of truth for product intent, architecture decisions, API behavior, engineering workflow, and security boundaries.

## Start Here

- [Agent repository guide](../AGENTS.md)
- [Contributing](../CONTRIBUTING.md)
- [Security policy](../SECURITY.md)
- [PeerAssist platform PRD](product/peerassist-platform-prd.md)
- [API conventions](api/conventions.md)

## Architecture Decisions

- [ADR 0001: Progressive modular monolith](adr/0001-progressive-modular-monolith.md)
- [ADR 0002: Provider ports](adr/0002-provider-ports.md)

## Approved Design And Delivery Context

- [Platform, canvas, and Agent design](superpowers/specs/2026-07-17-peerassist-platform-canvas-agent-design.md)
- [M0 engineering baseline](superpowers/plans/2026-07-17-peerassist-m0-engineering-baseline.md)

The approved design explains the full target and invariants. The PRD summarizes product outcomes and milestones. ADRs record decisions and consequences. API conventions define the public contract. `AGENTS.md`, `CONTRIBUTING.md`, and `SECURITY.md` define how changes are delivered safely.

When behavior or a decision changes, update the narrowest authoritative document and its executable contract in the same change. Do not add dated status logs to architecture or product documents; use version history for chronology.
