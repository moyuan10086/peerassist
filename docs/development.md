# Development

## Local setup

Use Python 3.12 and the official Python Package Index for reproducible dependency resolution:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --index-url https://pypi.org/simple -e '.[dev]'
npm --prefix web/peerassist-workspace ci
make verify-fast
```

Keep credentials in an untracked `.env`; never add keys to Dockerfiles, Compose files, or shell
history. Pull Git LFS assets before running checks that inspect the demo PDF.

## Two-service Compose profile

The development profile uses one fixed-UID non-root image. The Review API is private and writes
to a named volume; the Workspace is the only host-facing service and binds to loopback.

```bash
git lfs pull
docker compose -f infrastructure/compose/compose.yml config --quiet
docker compose -f infrastructure/compose/compose.yml up --build -d
curl --fail http://127.0.0.1:8766/api/health
curl --fail -sS -D /tmp/peerassist-range.headers \
  -H 'Range: bytes=0-1023' -o /tmp/peerassist-range.body \
  http://127.0.0.1:8766/paper.pdf
test "$(wc -c < /tmp/peerassist-range.body)" -eq 1024
head -c 1024 demos/Text/bert/paper.pdf | cmp - /tmp/peerassist-range.body
rg -i '^HTTP/[^ ]+ 206' /tmp/peerassist-range.headers
rg -i '^Content-Range: bytes 0-1023/[0-9]+' /tmp/peerassist-range.headers
docker compose -f infrastructure/compose/compose.yml down
```

The loopback binding is a development boundary, not an internet deployment. Do not publish port
8767 or change the Workspace binding to all interfaces without adding authentication, transport
security, and an explicit production threat review.

## M1 authorization platform

M1 moves browser identity, tenant data, uploaded PDFs, review commands and artifacts to the
FastAPI/PostgreSQL/Keycloak/MinIO boundary. The legacy workspace remains a browser shell and
read-only compatibility surface; it is no longer the authority for new review writes.

Run the focused real-provider acceptance with:

```bash
make m1-smoke
```

The script creates unique credentials and Compose resources, exercises PostgreSQL review commands,
MinIO immutable objects and Keycloak identity contracts, then removes containers, volumes and the
mode-0600 environment file. The reference profile is
`infrastructure/compose/compose.m1.yml`; provide every required environment variable through an
untracked secret manager or protected env file before running `make m1-compose-config`.
