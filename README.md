# Production API Wrapper

A FastAPI service that sits in front of an upstream API and owns the relationship with it:
auth, quotas, retries, error normalisation, logging, and async delivery via webhooks.

```
client ──► this service ─────────────► upstream API
        (auth, rate limit,        (the real work,
         retries, logging,         our secret key)
         our error contract)
```

The client never talks to the upstream and never sees the upstream key. Because every call
passes through here, it can be metered, cached, retried, billed, and re-pointed at a
different provider without the client noticing.

The upstream is currently a local **Ollama** instance speaking the OpenAI-compatible API.
It is configuration, not code — any OpenAI-compatible provider works by changing `.env`.

This is a learning project, built in phases. See [Roadmap](#roadmap).

## Quick start

Prerequisites: [uv](https://docs.astral.sh/uv/), [just](https://just.systems/), and Ollama.

```bash
brew install ollama
brew services start ollama
ollama pull llama3.2:3b

just install
just dev
```

Then:

```bash
curl -X POST http://localhost:8080/v1/complete \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "Say hello."}'
```

Interactive docs at <http://localhost:8080/docs>.

> `just dev` does not auto-reload unless `ENVIRONMENT` is not `production`.
> Restart it after code changes, and check nothing stale is holding the port
> (`lsof -nP -i :8080`).

## Commands

| Command | What it does |
| --- | --- |
| `just install` | Sync dependencies into `.venv` |
| `just dev` | Run the API on `$PORT` (default 8080) |
| `just test` | Run pytest (upstream is mocked; Ollama not required) |
| `just codestyle` | Auto-fix with ruff |
| `just check-codestyle` | ruff lint + format check + `ty` type check |
| `just kill 8080` | Kill whatever holds a port |
| `just up-system-dependencies` | Start postgres (phase 7 — not wired yet) |
| `just migrate` | Alembic migrations (phase 7 — not wired yet) |

## Configuration

Settings come from environment variables or a `.env` file, parsed and type-checked by
pydantic-settings at startup, so bad config fails on boot rather than on first request.

| Variable | Default | Notes |
| --- | --- | --- |
| `ENVIRONMENT` | `production` | `local` / `development` enable uvicorn reload |
| `HOST` / `PORT` | `0.0.0.0` / `8080` | |
| `DEBUG` | `false` | |
| `UPSTREAM_BASE_URL` | `http://localhost:11434/v1` | Any OpenAI-compatible base URL |
| `UPSTREAM_API_KEY` | `ollama` | Ignored by Ollama; real key for hosted providers |
| `UPSTREAM_MODEL` | `llama3.2:3b` | Default when the request omits `model` |
| `UPSTREAM_TIMEOUT_SECONDS` | `60.0` | Read timeout — models are legitimately slow |
| `UPSTREAM_CONNECT_TIMEOUT_SECONDS` | `2.0` | Connect timeout — unreachable should fail fast |
| `RETRY_MAX_ATTEMPTS` | `3` | Total attempts, not extra ones. `1` disables retrying |
| `RETRY_INITIAL_BACKOFF_SECONDS` | `0.5` | First backoff; doubles each attempt |
| `RETRY_MAX_BACKOFF_SECONDS` | `8.0` | Ceiling for any single wait, `Retry-After` included |
| `RETRY_BUDGET_SECONDS` | `30.0` | Total wall-clock allowance; a wait that would exceed it is not taken |
| `CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` | Consecutive infrastructure failures before the circuit opens |
| `CIRCUIT_BREAKER_RESET_SECONDS` | `30.0` | How long it stays open before a probe request |

## API

### `GET /health`

Liveness only. Does not yet check the upstream or database (phase 9).

### `POST /v1/complete`

```jsonc
// request
{
  "prompt": "Say hello.",      // required, 1–8000 chars
  "model": "llama3.2:3b",      // optional, defaults to UPSTREAM_MODEL
  "temperature": 0.7,          // 0.0–2.0
  "max_tokens": 256            // optional
}

// response
{
  "id": "chatcmpl-275",
  "model": "llama3.2:3b",
  "content": "Hello!",
  "usage": { "prompt_tokens": 30, "completion_tokens": 2 }
}
```

Note this is deliberately **not** the OpenAI schema — a flat `prompt` in, flat `content`
out. The contract belongs to this service, not to the vendor.

### Errors

Every failure — ours, the upstream's, or a bad request — comes back in one envelope:

```jsonc
{
  "error": {
    "code": "model_not_found",
    "message": "model 'gpt-9' not found",
    "details": { "model": "gpt-9" }   // optional
  }
}
```

Branch on `code`, which is stable. `message` is for humans and may be reworded.

| Status | `code` | Cause |
| --- | --- | --- |
| 400 | `model_not_found` | Requested a model the provider doesn't have |
| 400 | `upstream_rejected_request` | Provider refused the request for another reason |
| 422 | `validation_error` | Request body failed schema validation; `details.fields` lists them |
| 429 | `upstream_rate_limited` | Provider throttled us; `Retry-After` echoed when supplied |
| 500 | `internal_error` | A bug on our side. Traceback goes to the logs, never the response |
| 502 | `upstream_error` | Provider returned 5xx |
| 502 | `upstream_auth_failed` | Provider rejected *our* credentials — a caller can't fix this, so not a 401 |
| 502 | `invalid_upstream_response` | Provider returned a body we couldn't parse |
| 503 | `upstream_unavailable` | Couldn't reach the provider at all |
| 503 | `upstream_circuit_open` | Provider is failing consistently; calls are paused |
| 504 | `upstream_timeout` | Provider didn't respond in time |

## Layout

```
src/
  configuration.py       Settings (pydantic-settings)
  factory.py             create_app() + lifespan (owns the shared httpx client)
  runserver.py           Production entrypoint
  endpoints/             Routers — thin; parse, delegate, return
  models/                Request/response schemas — our public contract
  services/              Upstream calls; the only place httpx and vendor JSON exist
    resilience.py        Retry policy + circuit breaker (provider-agnostic)
  dependencies/          Depends() providers (settings, http client, services)
  errors/                Error taxonomy + exception handlers
  repositories/          Database access               (phase 7)
  middleware/            Cross-cutting per-request work (phase 9)
tests/
```

The rule that keeps this honest: **endpoints stay ~3 lines**. Anything more belongs in a
service.

## Roadmap

| # | Phase | Status | What it adds |
| --- | --- | --- | --- |
| 1 | Contract | ✅ | Request/response models, generated OpenAPI docs |
| 2 | Upstream client | ✅ | Lifespan-managed pooled `httpx` client, service layer, split timeouts |
| 3 | Error taxonomy | ✅ | `AppError` hierarchy, one envelope, stable error codes |
| 4 | Resilience | ✅ | Retry with exponential backoff + jitter, budget, circuit breaker |
| 5 | Client auth | ⬜ | Hashed API keys, caller identity via `Depends()` |
| 6 | Rate limiting | ⬜ | Token bucket per caller, `429` + `Retry-After` |
| 7 | Persistence | ⬜ | Postgres via SQLAlchemy async + alembic: keys, usage, jobs |
| 8 | Webhooks | ⬜ | `202` + background work + HMAC-signed callback with retries |
| 9 | Observability | ⬜ | Request IDs, structured logs, readiness checks |
| 10 | Tests | 🟡 | Grows with each phase; upstream mocked with `respx` |
| 11 | Ship | ⬜ | Dockerfile, compose, deployment notes |

### How retrying decides

`AppError.retryable` is the whole policy. A failure that could plausibly succeed on a
repeat (`429`, `5xx`, timeouts, connection errors) sets it; one that will fail identically
forever (`400`, `404 model_not_found`, `422`) does not. The retry loop reads that flag and
nothing else, so extending the taxonomy extends the policy for free.

Waits are exponential with **full jitter** — a uniform pick from `[0, cap]` rather than the
cap itself. Without jitter, every client that failed at the same instant retries at the
same instant and flattens a recovering provider again. A provider's own `Retry-After` beats
our guess when present, but is still capped.

The circuit breaker counts only retryable failures, since a rejected prompt says nothing
about provider health. Its state lives in the process, so several uvicorn workers each hold
their own view — the same limitation the phase 6 rate limiter will have, and for the same
reason.

### Known gap (phase 5)

Anyone who can reach the service can spend our provider quota. There is no notion of a
caller: no API keys, no identity, and therefore nothing to attribute usage to or to rate
limit against. Phase 5 adds hashed API keys resolved through a `Depends()`, which phase 6
then keys its token buckets on.

## Notes for the curious

- **One `httpx.AsyncClient` per process, not per request.** Built in the lifespan hook,
  injected via `Depends()`. A per-request client throws away connection pooling and
  exhausts sockets under load.
- **`create_app(settings)` takes settings** so tests can build an app pointed at a fake
  upstream. No globals, no monkeypatching.
- **Tests mock at the HTTP layer** (`respx`), so service code runs unmodified and failures
  that are hard to provoke live — `429`, timeouts, malformed bodies — become one-liners.
- **`TestClient` must be used as a context manager**, or lifespan never runs and
  `app.http_client` won't exist.
- **Status codes carry blame.** A bad model name is the caller's fault (`400`), a rejected
  API key is ours (`502`, not `401` — the caller cannot fix our credentials), and an
  unparseable provider response is nobody's fault but still not a `500`.
- **Upstream error text is forwarded selectively.** A "model not found" message is useful
  to the caller; an auth message may name our key, so it is dropped.
- **Retries need a budget, not just a cap.** Three attempts against a 60s read timeout is
  potentially three minutes of held connection. The budget is what stops one slow request
  from becoming an outage.
- **The clock and the sleep are injectable** (`RetryPolicy(clock=..., sleep=...)`), so
  backoff, budget exhaustion, and breaker recovery are tested without the suite waiting.