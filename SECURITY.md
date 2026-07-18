# PeerAssist Security Policy

PeerAssist is an alpha research-review system. Report suspected vulnerabilities privately to the repository maintainers; do not include manuscript contents, credentials, exploit payloads against third parties, or other sensitive data in a public issue.

## Supported Deployment Boundary

The M1 platform has Keycloak Authorization Code + PKCE login, server-side sessions, CSRF protection, organization/project RBAC, tenant-scoped repositories, PostgreSQL write authority, and immutable S3-compatible artifacts. The legacy M0 profile remains supported only on loopback for a single trusted user or as an explicitly registered read-only compatibility source.

Do not expose development profiles directly to an untrusted network. Resource identifiers are not authorization controls. Public deployments still require TLS termination, request limits, secure secret injection, retention policy, monitoring, and the M1 system/tenancy gates.

## Confidential Manuscripts

Do not process confidential manuscripts in production until deployment-specific encryption, retention controls, audited provider approvals and incident response are configured and verified. M1 supplies the application authorization boundary but cannot replace infrastructure policy.

- Never commit manuscripts, credentials, private keys, access tokens, session cookies, provider private responses, or production artifacts.
- Use synthetic fixtures and redact logs, screenshots, reports, and issue descriptions.
- Treat external model, OCR, search, storage, and synchronization calls as data transfers.
- Require scoped human approval before external transmission, destructive changes to human content, final publication, or external sharing.
- Keep secrets outside Agent prompts, browser state, generated reports, and repository files.
- Revoke exposed credentials immediately and notify maintainers through the private reporting channel.

Security fixes must include a regression test where safe, an impact assessment, and deployment or credential-rotation instructions when relevant.
