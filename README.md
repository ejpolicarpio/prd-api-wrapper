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
just up-system-dependencies   # postgres, via docker
just migrate
```

The service **fails closed**: with no keys in the database, every request is refused. Mint
one — it's printed once and only its digest is stored:

```bash
just new-api-key "Local dev"
```

Then run it and call it:

```bash
just dev

curl -X POST http://localhost:8080/v1/complete \
  -H 'Authorization: Bearer sk-...' \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "Say hello."}'
```

To skip authentication entirely while poking around, set `REQUIRE_API_KEY=false`.

Interactive docs at <http://localhost:8080/docs>.

> `just dev` does not auto-reload unless `ENVIRONMENT` is not `production`.
> Restart it after code changes, and check nothing stale is holding the port
> (`lsof -nP -i :8080`).

## Commands

| Command | What it does |
| --- | --- |
| `just install` | Sync dependencies into `.venv` |
| `just dev` | Run the API on `$PORT` (default 8080) |
| `just test` | Fast suite — no docker, no Ollama, upstream and database both faked |
| `just test-integration` | Repository tests against real Postgres (starts it first) |
| `just check-test` | Both suites |
| `just makemigration "msg"` | Autogenerate a migration from the models |
| `just codestyle` | Auto-fix with ruff |
| `just check-codestyle` | ruff lint + format check + `ty` type check |
| `just kill 8080` | Kill whatever holds a port |
| `just new-api-key "Name"` | Mint an API key; prints the key once |
| `just up` / `just down` | The whole stack in containers: postgres, migrations, API |
| `just logs [service]` | Follow container logs (defaults to `api`) |
| `just image` | Build the production image |
| `just up-system-dependencies` | Start postgres only, for local `just dev` |
| `just migrate` | Alembic migrations (phase 7 — not wired yet) |

## Running it in containers

```bash
echo "WEBHOOK_SIGNING_SECRET=$(openssl rand -hex 32)" >> .env
just up
```

That builds the image, starts Postgres, runs migrations in a one-shot container, and only
then starts the API. Mint a key inside it:

```bash
docker compose exec api python -m src.services.authentication "Your client"
```

Ollama stays on the host — the containers reach it through `host.docker.internal`.

### Deployment notes

- **Set `WEBHOOK_SIGNING_SECRET`.** The app refuses to start in production without it,
  because an empty secret still produces a signature: deliveries would look signed while
  being forgeable. Compose fails the same way rather than substituting a placeholder.
- **Migrations run in their own container**, not on app startup. With several replicas,
  each one would otherwise race to migrate the same database.
- **Point the liveness probe at `/health`, the readiness probe at `/health/ready`.**
  Swapping them is the classic mistake: a liveness probe that checks the database gets the
  process killed during a database outage, which does not fix the database.
- **One worker per container**, scaling by replicas. Rate limit buckets and the circuit
  breaker are per process either way, so N replicas mean N times the rate limit until
  those move to shared storage.
- **The image runs as an unprivileged user** and contains no dev dependencies, no `uv` and
  no build tools — 318MB, mostly the interpreter and the virtualenv.

## Configuration

Settings come from environment variables or a `.env` file, parsed and type-checked by
pydantic-settings at startup, so bad config fails on boot rather than on first request.

| Variable | Default | Notes |
| --- | --- | --- |
| `ENVIRONMENT` | `production` | `local` / `development` enable uvicorn reload |
| `LOG_LEVEL` | `INFO` | |
| `LOG_JSON` | unset | Defaults to JSON unless `ENVIRONMENT=local` |
| `HOST` / `PORT` | `0.0.0.0` / `8080` | |
| `DEBUG` | `false` | |
| `UPSTREAM_BASE_URL` | `http://localhost:11434/v1` | Any OpenAI-compatible base URL |
| `UPSTREAM_API_KEY` | `ollama` | Ignored by Ollama; real key for hosted providers |
| `UPSTREAM_MODEL` | `llama3.2:3b` | Default when the request omits `model` |
| `REQUIRE_API_KEY` | `true` | Fails closed. `false` lets unauthenticated callers through as `anonymous` |
| `POSTGRESQL_HOST` / `_PORT` | `localhost` / `5432` | |
| `POSTGRESQL_USERNAME` / `_PASSWORD` / `_DB` | `postgres` ×3 | |
| `POSTGRESQL_SCHEMA` | `public` | Applied as the connection's `search_path` |
| `POSTGRESQL_ECHO` | `false` | Log every statement — useful once, exhausting always |
| `WEBHOOK_SIGNING_SECRET` | `""` | Callers verify deliveries with this. Per-caller secrets would be better |
| `WEBHOOK_TIMEOUT_SECONDS` | `10.0` | Per delivery attempt |
| `WEBHOOK_MAX_ATTEMPTS` | `4` | Delivery attempts before giving up; the job keeps its result |
| `RATE_LIMIT_ENABLED` | `true` | |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | `60` | Steady rate per caller; the bucket refills at this ÷ 60 per second |
| `RATE_LIMIT_BURST` | `10` | Bucket capacity: what an idle caller may fire at once |
| `UPSTREAM_TIMEOUT_SECONDS` | `60.0` | Read timeout — models are legitimately slow |
| `UPSTREAM_CONNECT_TIMEOUT_SECONDS` | `2.0` | Connect timeout — unreachable should fail fast |
| `RETRY_MAX_ATTEMPTS` | `3` | Total attempts, not extra ones. `1` disables retrying |
| `RETRY_INITIAL_BACKOFF_SECONDS` | `0.5` | First backoff; doubles each attempt |
| `RETRY_MAX_BACKOFF_SECONDS` | `8.0` | Ceiling for any single wait, `Retry-After` included |
| `RETRY_BUDGET_SECONDS` | `30.0` | Total wall-clock allowance; a wait that would exceed it is not taken |
| `CIRCUIT_BREAKER_FAILURE_THRESHOLD` | `5` | Consecutive infrastructure failures before the circuit opens |
| `CIRCUIT_BREAKER_RESET_SECONDS` | `30.0` | How long it stays open before a probe request |

## API

### Authentication

Every endpoint except `/health` requires an API key:

```
Authorization: Bearer sk-...
```

Keys are stored only as a SHA-256 digest, so the plaintext exists exactly once — at mint
time. A leaked `.env` or database dump yields nothing usable. Plain SHA-256 suffices here
because keys are long random strings; a *password*, being low-entropy, would need a
deliberately slow hash like argon2.

### Rate limits

Each caller gets a token bucket keyed on their key's identity. Every response — success or
failure — reports where they stand:

```
X-RateLimit-Limit: 10        bucket capacity
X-RateLimit-Remaining: 7     tokens left
X-RateLimit-Reset: 28        seconds until the bucket is full again
Retry-After: 8               (429 only) seconds until one token returns
```

Exceeding the limit returns `429 rate_limit_exceeded` without touching the provider.

### `POST /v1/jobs`

For work too slow to wait on. Accepts the same fields as `/v1/complete` plus a
`callback_url`, and returns immediately:

```jsonc
// request
{ "prompt": "Say hello.", "callback_url": "https://you.example.com/hooks" }

// 202 Accepted
{ "id": "264e4c2f751a8c99fbf777194f8a8657", "status": "pending" }
```

When the work finishes, we `POST` the result to `callback_url`:

```jsonc
// headers
X-Signature-Timestamp: 1754835600
X-Signature: sha256=a1b2c3…

// body
{
  "job_id": "264e4c2f…",
  "status": "succeeded",       // or "failed"
  "model": "llama3.2:3b",
  "content": "Hello!",
  "error_code": null,
  "error_message": null
}
```

**Verify before parsing.** The signature is `HMAC-SHA256(secret, "<timestamp>.<raw body>")`.
Recompute it over the raw bytes and compare with a constant-time function — re-serialising
the JSON can reorder keys and break the digest. Because the timestamp is *inside* the
signed material, rejecting old timestamps also rejects replays.

`job_id` is the idempotency key: delivery is retried, so the same one may arrive twice.

### `GET /v1/jobs/{id}`

The job's current state, including `content` once it's done. Scoped to the caller who
created it — someone else's job id returns `404`, identical to one that never existed.

Poll this when a delivery is missed: a failed callback never loses the result.

### `GET /health` and `GET /health/ready`

Both unauthenticated — a probe has no credentials to offer.

`/health` is **liveness**: is the process running? It deliberately checks nothing else. A
liveness probe that fails when the database is down gets the process killed and restarted,
which does not fix a database and does turn a partial outage into a total one.

`/health/ready` is **readiness**: should this process be sent traffic? It checks Postgres
and the provider, and answers `503` if either is unreachable — taking the instance out of
rotation without killing it, so it rejoins when they recover.

```jsonc
{ "status": "ready", "checks": { "database": true, "upstream": true } }
```

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

Branch on `code`, which is stable. `message` is for humans and may be reworded. Errors also
carry `request_id`, which is the string to quote when reporting a problem — it finds every
log line for that request, including work that happened after the response.

| Status | `code` | Cause |
| --- | --- | --- |
| 401 | `missing_credentials` | No `Authorization: Bearer` header |
| 401 | `invalid_credentials` | Key not recognised — identical response whether unknown, revoked or malformed |
| 400 | `model_not_found` | Requested a model the provider doesn't have |
| 400 | `upstream_rejected_request` | Provider refused the request for another reason |
| 422 | `validation_error` | Request body failed schema validation; `details.fields` lists them |
| 429 | `rate_limit_exceeded` | The caller spent their own allowance |
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
  models/                Every data structure: API contract, tables, value objects
    base.py              Declarative base; table names derived from class names
    tables.py            api_key, usage and job
  services/              Upstream calls; the only place httpx and vendor JSON exist
    resilience.py        Retry policy + circuit breaker (provider-agnostic)
    authentication.py    Key -> caller, plus minting; runnable as a script
    rate_limiter.py      Token bucket, one per caller
    jobs.py              Accept work, run it, deliver the result
    webhooks.py          Signing and delivery; sign()/verify() live here
  dependencies/          Depends() providers (settings, http client, services)
  errors/                Error taxonomy + exception handlers
  middleware/            Request ids and per-request log lines
  logs.py                Loguru configuration: JSON in production, readable locally
  databases/             Engine and session factory
  repositories/          Where rows are read and written, behind Protocols
migrations/              Alembic; the URL comes from Settings, not alembic.ini
  middleware/            Cross-cutting per-request work (phase 9)
tests/
```

Two rules keep this honest:

- **Endpoints stay ~3 lines.** Anything more belongs in a service.
- **Data structures live in `models/`, behaviour lives in `services/`.** That holds whether
  or not the type crosses a boundary — `RateLimitDecision` and `CircuitState` are internal,
  but they're still data, so they sit with the rest of it. One rule, nothing to remember.

## Roadmap

| # | Phase | Status | What it adds |
| --- | --- | --- | --- |
| 1 | Contract | ✅ | Request/response models, generated OpenAPI docs |
| 2 | Upstream client | ✅ | Lifespan-managed pooled `httpx` client, service layer, split timeouts |
| 3 | Error taxonomy | ✅ | `AppError` hierarchy, one envelope, stable error codes |
| 4 | Resilience | ✅ | Retry with exponential backoff + jitter, budget, circuit breaker |
| 5 | Client auth | ✅ | Hashed API keys, caller identity via `Depends()` |
| 6 | Rate limiting | ✅ | Token bucket per caller, `429` + `Retry-After` + `X-RateLimit-*` |
| 7 | Persistence | ✅ | Postgres via SQLAlchemy async + alembic: keys, usage |
| 8 | Webhooks | ✅ | `202` + background work + HMAC-signed callback with retries |
| 9 | Observability | ✅ | Request IDs through to background work, structured logs, readiness |
| 10 | Tests | ✅ | 107 across two suites; upstream mocked with `respx` |
| 11 | Ship | ✅ | Multi-stage Dockerfile, compose stack, deployment notes |

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

### Why authentication is a dependency, not middleware

Middleware sees every request, so an auth middleware would also intercept `/health`,
`/docs` and unmatched paths, leaving a path allowlist to maintain — the classic source of
an accidentally exposed endpoint. It also cannot take part in dependency injection, cannot
declare itself in the OpenAPI schema, and cannot be overridden per route or in tests.

A `Depends()` is opt-in per route, appears in `/docs` as an Authorize button, and returns
a value the endpoint can use. Middleware is for work that genuinely applies to every
request — request IDs, timing — which is phase 9.

### Why a token bucket, not a counter per minute

A fixed window ("60 requests per minute") lets a caller fire all 60 at 11:59:59 and 60 more
at 12:00:00 — 120 requests in two seconds, at a limit you believed was 60 a minute. A token
bucket has no window to straddle: tokens accrue continuously at the steady rate, capped at
the burst size, so idling buys a burst but never an unbounded backlog.

### Transactions

One session per request, committed if the handler returns and rolled back if it raises —
so a half-finished write cannot survive a failure.

Usage recording is the deliberate exception. It opens its **own** short transaction,
because sharing the request's would mean a failed request rolls its own usage row away,
losing exactly the records worth having: an outage is when you most want to know who was
calling.

### Tracing a request

Every request gets an id — taken from an inbound `X-Request-ID` when it looks safe,
generated otherwise — returned in the response header, included in error bodies, and bound
into every log line that request produces:

```
$ grep trace-abc123 logs
… POST /v1/jobs -> 202 in 37.66ms          {'request_id': 'trace-abc123'}
… webhook delivered for job 00043414…      {'request_id': 'trace-abc123', 'job_id': '00043414…'}
```

Note the second line happened 2.3 seconds *after* the response was sent. Background work
takes the id explicitly, because it runs outside the middleware's logging context — without
that, the most interesting lines would be the ones nothing links back to a request.

### Known limits

All eleven phases are done. What remains are deliberate limits, each with a clear upgrade:

- **Background jobs are in-process.** `BackgroundTasks` runs them on the serving worker's
  event loop, so a restart loses anything in flight. The `job` row already holds everything
  a real queue worker would need (prompt, model, callback URL, status), so moving to arq or
  Celery is mostly relocating `JobService.run`.
- **Rate limit buckets are per process**, so N workers enforce N times the limit.
- **One webhook signing secret for everyone.** Per-caller secrets would mean one leak
  compromises one client rather than all of them.

Natural next steps, none of them required: SSE token streaming (one-directional, so not
WebSockets), per-caller webhook secrets, a Redis-backed rate limiter, and a real queue for
jobs.

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
- **Key lookup is a dict keyed by digest**, so no secret is ever compared byte by byte —
  which sidesteps timing attacks rather than defending against them.
- **`ApiKeyRepository` is a `Protocol` and its method is `async`** even though the
  settings-backed implementation does no I/O. Phase 7 swaps in a Postgres one without
  anything that depends on it changing.
- **The rate limiter depends on the caller**, which is what orders it after
  authentication — FastAPI resolves the graph, so there is no ordering to remember.
- **An exception discards the `Response` a dependency wrote to.** Headers that must
  survive a failure (rate limit counts, since the token was spent regardless) are stashed
  on `request.state` and merged back in by the error handler.
- **The fast suite fakes the repositories, not the database.** `dependency_overrides`
  swaps in in-memory repositories, so routing, auth, rate limiting, retries and error
  mapping all run exactly as in production without a session ever being opened.
- **Integration tests use their own database** (`<db>_test`, created on demand). They drop
  every table on setup, so pointing them at the development database would delete real
  data — the isolation is enforced in `conftest`, not left to whoever runs the command.
- **Callbacks go out on a separate httpx client.** The upstream client carries the
  provider's `Authorization` header by default, and the callback URL is chosen by the
  caller — reusing it would hand them our provider key. There is a test for that.
- **A webhook is not a persistent connection.** Each delivery is a fresh, short-lived
  HTTP request in which *we* are the client and the caller is the server. That is why a
  publicly reachable URL is required, and why streaming tokens would be SSE instead.
- **Request ids are middleware, not a dependency**, so they also cover requests that never
  reach a route — 404s, malformed bodies, unhandled crashes — which are precisely the ones
  someone will later ask you to explain.
- **An inbound `X-Request-ID` is validated, not trusted.** It is echoed into headers and
  log lines, so an arbitrary string could forge log entries or inject headers.
- **Logging never expands local variables** (`diagnose=False`). Loguru's rich tracebacks
  are wonderful and would happily print an API key or a prompt into your log aggregator.
- **The app refuses to start in production without `WEBHOOK_SIGNING_SECRET`.** An empty
  secret still produces a signature, so deliveries would look signed while being forgeable.
  Better a failed deploy than a silent vulnerability.