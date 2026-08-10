# Build stage: resolve and install dependencies, nothing else.
FROM python:3.14-slim AS builder

# Pinned rather than :latest, so a rebuild six months from now produces the
# same image instead of a surprise.
COPY --from=ghcr.io/astral-sh/uv:0.7.12 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Only the lockfiles first: this layer is cached and rebuilt only when
# dependencies actually change, not on every source edit.
COPY pyproject.toml uv.lock ./

# --frozen fails if the lockfile disagrees with pyproject, so an image can
# never be built from dependencies nobody resolved. --no-dev leaves pytest,
# ruff and ty out of the runtime image.
RUN uv sync --frozen --no-dev --no-install-project


# Runtime stage: the interpreter, the virtualenv, and the source. No uv, no
# build tools, no dev dependencies.
FROM python:3.14-slim AS runtime

# Runs unprivileged: a container process that does not need root should not
# have it, so a code-execution bug does not start as root.
RUN groupadd --system app && useradd --system --gid app --home /app app

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app src ./src

USER app

EXPOSE 8080

# Liveness only. Readiness belongs to the orchestrator, which can take an
# instance out of rotation; Docker's healthcheck can only restart it.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

# One worker per container: scale with replicas instead, and remember that
# rate limit buckets and the circuit breaker are per process either way.
CMD ["uvicorn", "src.runserver:app", "--host", "0.0.0.0", "--port", "8080"]