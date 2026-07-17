# PeerAssist API Conventions

These conventions define the public FastAPI contract and compatibility behavior during migration. Domain and persisted-artifact schemas may version independently when their retention period requires it.

## Versioning And Resources

- Public endpoints live under `/api/v1`.
- Use plural resource names and stable opaque IDs. IDs never grant authorization.
- Generate the TypeScript client from OpenAPI; do not maintain competing handwritten request types.
- Additive response fields are backward compatible. Removing, renaming, or changing semantics requires a new API version or a documented compatibility window.
- During migration, legacy adapters must normalize responses to this contract and pass route-parity tests.

## Authentication And Tenant Safety

- Authentication establishes an actor; repository queries enforce organization, project, and role scope.
- Return `403 Forbidden` when an authenticated actor is known to lack permission for an operation on an otherwise visible scope.
- Return tenant-safe `404 Not Found` when revealing whether a cross-tenant resource exists would disclose information.
- Do not trust organization IDs, project IDs, roles, or identity headers supplied by the browser without server-side authorization.

## Requests, Versions, And Idempotency

- Mutating commands accept an `Idempotency-Key`. Repeating the same key and payload returns the original status and body; key reuse with a different payload is a conflict.
- Commands that update an aggregate include its expected aggregate version. A stale version returns `409 Conflict` without partial mutation.
- Validate commands before appending events. A rejected command creates no domain event or work item.
- Use `202 Accepted` for durable asynchronous work and return the job or operation resource used to observe it.

## Errors

Every non-streaming error uses this error envelope:

```json
{
  "error": {
    "code": "canvas_version_conflict",
    "message": "The canvas changed after this command was prepared.",
    "request_id": "opaque-request-id",
    "details": {},
    "retryable": false
  }
}
```

`code` is stable and machine readable; `message` is safe for an authorized user; `details` contains structured, non-secret context. Never expose stack traces, provider private responses, credentials, tenant existence, or manuscript text in errors.

## Events And SSE

- SSE is the initial server-to-client event transport for jobs, canvas commands, Agents, and approvals.
- Each event has an immutable event ID, aggregate ID, aggregate version, type, timestamp, and authorized view payload.
- The event cursor is the last successfully processed event ID. Clients reconnect with `Last-Event-ID` and deduplicate repeated delivery.
- Event delivery is at least once and ordered within an aggregate. A bounded polling endpoint provides compatibility fallback when SSE is interrupted.
- Retention expiry returns a typed response requiring snapshot reload rather than silently skipping history.

## Backward Compatibility

The backward compatibility contract covers status codes, normalized fields, error codes, range downloads, idempotency behavior, event ordering, state transitions, and artifact digests. Legacy routes remain private compatibility adapters until equivalent `/api/v1` routes pass parity fixtures and the rollback window closes.
