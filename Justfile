install:
  uv sync

dev:
  uv run python -m src.runserver

up-system-dependencies:
  docker compose up -d --wait postgres

down-system-dependencies:
  docker compose down -v

migrate:
  uv run alembic upgrade head

makemigration message:
  uv run alembic revision --autogenerate -m "{{message}}"

new-api-key name="unnamed":
  @uv run python -m src.services.authentication "{{name}}"

check-codestyle:
  uv run ruff check src tests
  uv run ruff format --check src tests
  uv run ty check src

codestyle:
  uv run ruff check --fix src tests
  uv run ruff format src tests

test:
  uv run pytest --ignore=tests/integration

test-integration:
  @just up-system-dependencies
  uv run pytest tests/integration

check-test: test test-integration

kill port:
  kill $(lsof -ti :{{port}})