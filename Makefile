.PHONY: dev test lint fmt migrate revision up up-all down logs llm-up ollama-check

dev:
	uv run uvicorn botpit.app:create_app --factory --reload --port 8000

test:
	uv run pytest

lint:
	uv run ruff check
	uv run ruff format --check
	uv run mypy

fmt:
	uv run ruff format
	uv run ruff check --fix

migrate:
	uv run alembic upgrade head

revision:
	uv run alembic revision --autogenerate -m "$(m)"

up:
	docker compose up -d postgres redis
	scripts/wait-for-services.sh postgres redis

up-all:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

llm-up:
	docker compose --profile llm up -d llm

ollama-check:
	curl -s http://localhost:11434/api/tags | python -m json.tool | head
