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

## Layout

```
src/
  configuration.py       Settings (pydantic-settings)
  factory.py             create_app() + lifespan (owns the shared httpx client)
  runserver.py           Production entrypoint
  endpoints/             Routers — thin; parse, delegate, return
  models/                Request/response schemas — our public contract
  services/              Upstream calls; the only place httpx and vendor JSON exist
  dependencies/          Depends() providers (settings, http client, services)
  repositories/          Database access               (phase 7)
  errors/                Error taxonomy + handlers     (phase 3)
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
| 3 | Error taxonomy | ⬜ | `AppError` hierarchy, one exception handler, stable error codes |
| 4 | Resilience | ⬜ | Retry with exponential backoff + jitter, circuit breaker |
| 5 | Client auth | ⬜ | Hashed API keys, caller identity via `Depends()` |
| 6 | Rate limiting | ⬜ | Token bucket per caller, `429` + `Retry-After` |
| 7 | Persistence | ⬜ | Postgres via SQLAlchemy async + alembic: keys, usage, jobs |
| 8 | Webhooks | ⬜ | `202` + background work + HMAC-signed callback with retries |
| 9 | Observability | ⬜ | Request IDs, structured logs, readiness checks |
| 10 | Tests | 🟡 | Grows with each phase; upstream mocked with `respx` |
| 11 | Ship | ⬜ | Dockerfile, compose, deployment notes |

### Known gap (phase 3)

`services/completion.py` calls `raise_for_status()` with nothing catching it, so **any**
upstream failure becomes a bare `500 Internal Server Error` with a traceback in the logs.

Reproduce it by requesting a model that isn't pulled:

```bash
curl -X POST http://localhost:8080/v1/complete \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "hi", "model": "not-a-real-model"}'
```

Ollama returns `404 model not found`; the client sees `500`. That should be a `4xx` with a
usable message — a client mistake is not a server crash. Response parsing is equally
fragile: an unexpected upstream body makes `data["choices"][0]` raise `KeyError`, which
also surfaces as a 500.

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