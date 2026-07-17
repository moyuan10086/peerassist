# PeerAssist Security Policy

PeerAssist is an alpha research-review system. Report suspected vulnerabilities privately to the repository maintainers; do not include manuscript contents, credentials, exploit payloads against third parties, or other sensitive data in a public issue.

## Supported Deployment Boundary

The current application has no built-in authentication. It is supported only when bound to loopback for a single trusted user or isolated behind a trusted reverse proxy that provides TLS, authentication, authorization, request limits, and audit logging.

Do not expose the Python service or Vite compatibility workspace directly to an untrusted network. Resource identifiers are not authorization controls. A future public deployment must enforce organization and project scope in repositories and artifact access paths, use secure browser sessions, and pass the security and tenancy contract suites before launch.

## Confidential Manuscripts

Do not process confidential manuscripts in production until authentication, tenant isolation, encrypted storage, retention controls, audited approvals, and provider data-transfer controls are implemented and verified.

- Never commit manuscripts, credentials, private keys, access tokens, session cookies, provider private responses, or production artifacts.
- Use synthetic fixtures and redact logs, screenshots, reports, and issue descriptions.
- Treat external model, OCR, search, storage, and synchronization calls as data transfers.
- Require scoped human approval before external transmission, destructive changes to human content, final publication, or external sharing.
- Keep secrets outside Agent prompts, browser state, generated reports, and repository files.
- Revoke exposed credentials immediately and notify maintainers through the private reporting channel.

Security fixes must include a regression test where safe, an impact assessment, and deployment or credential-rotation instructions when relevant.
