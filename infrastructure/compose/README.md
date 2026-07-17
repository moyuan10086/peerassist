# PeerAssist Compose profile

This development profile runs two containers from one non-root image:

- `review-api` stores jobs in the `peerassist-data` named volume and has no host port.
- `workspace` waits for the review API and publishes only `127.0.0.1:8766`.

No API key is embedded in the image or Compose file. To enable optional model review, provide
`PEERASSIST_OPENAI_API_KEY` at runtime through an environment file that is not committed.

## Start and verify

```bash
git lfs pull
docker compose -f infrastructure/compose/compose.yml up --build -d
curl --fail http://127.0.0.1:8766/api/health
curl --fail -sS -D /tmp/peerassist-range.headers \
  -H 'Range: bytes=0-1023' -o /tmp/peerassist-range.body \
  http://127.0.0.1:8766/paper.pdf
test "$(wc -c < /tmp/peerassist-range.body)" -eq 1024
head -c 1024 demos/Text/bert/paper.pdf | cmp - /tmp/peerassist-range.body
docker compose -f infrastructure/compose/compose.yml down
```

The PDF request should return `206 Partial Content` with a matching `Content-Range` header.
Only port 8766 is reachable from the host, and only through loopback; the review API remains on
the private Compose network.
