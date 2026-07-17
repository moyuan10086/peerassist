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
