install:
  uv sync

dev:
  uv run python -m src.runserver

up-system-dependencies:
  docker compose up -d postgres
  @echo "Waiting for PostgreSQL to be ready..."
  @sleep 2

down-system-dependencies:
  docker compose down -v postgres

migrate:
  uv run alembic upgrade head

check-codestyle:
  uv run ruff check src tests
  uv run ruff format --check src tests
  uv run ty check src

codestyle:
  uv run ruff check --fix src tests
  uv run ruff format src tests

test:
  uv run pytest

check-test: test

kill port:
  kill $(lsof -ti :{{port}})