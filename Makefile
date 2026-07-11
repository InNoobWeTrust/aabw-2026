.PHONY: help lock sync fix format lint quality test check dev dev-up doctor \
        build docker-run docker-update docker-stop docker-logs docker-nuke

CONTAINER_NAME := robodata
IMAGE_NAME     := robodata:latest
DATA_VOLUME    := $(PWD)/data

help:  ## Print target descriptions
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'

lock:  ## Generate/update uv.lock
	uv lock

sync:  ## Install all dependencies (including dev)
	uv sync --extra dev

fix:  ## Auto-fix lint issues and format code
	uv run ruff check --fix .
	uv run ruff format .

format:  ## Format code only (no lint fixes)
	uv run ruff format .
	uv run ruff format --check .

lint:  ## Lint and format check (no auto-fix)
	uv run ruff check .
	uv run ruff format --check .

quality: lint  ## Full quality gate: lint + compile-check
	uv run python -m compileall backend pipeline domain tests

test:  ## Run full test suite
	uv run pytest -v

check: fix quality test  ## Full verification: fix → lint → compile → test

dev:  ## Start development server with hot reload on 0.0.0.0
	uv run uvicorn backend.server:app --host 0.0.0.0 --reload --port 8080

dev-local:  ## Start development server on localhost only
	uv run uvicorn backend.server:app --host 127.0.0.1 --reload --port 8080

dev-up:  ## Bootstrap dev environment from scratch
	uv lock && uv sync --extra dev

doctor:  ## Verify the FastAPI app imports cleanly
	uv run python -c "from backend.server import app; print(f'OK: {app.title} v{app.version}')"

build:  ## Build production Docker image
	docker build -t $(IMAGE_NAME) .

# ── Docker container lifecycle ─────────────────────────────────────────────────

docker-run:  ## Build image (if needed) and start container in background
	@if ! docker image inspect $(IMAGE_NAME) >/dev/null 2>&1; then \
		$(MAKE) build; \
	fi
	@if docker ps -a --format '{{.Names}}' | grep -q '^$(CONTAINER_NAME)$$'; then \
		echo "Container $(CONTAINER_NAME) already exists — use 'make docker-update' or stop/rm it first."; \
		exit 1; \
	fi
	docker run -d --name $(CONTAINER_NAME) \
		-p 8000:8000 \
		-v $(DATA_VOLUME):/app/data \
		--env-file .env \
		$(IMAGE_NAME)

docker-update: build  ## Rebuild image + recreate container from scratch
	-docker stop $(CONTAINER_NAME) 2>/dev/null
	-docker rm $(CONTAINER_NAME) 2>/dev/null
	docker run -d --name $(CONTAINER_NAME) \
		-p 8000:8000 \
		-v $(DATA_VOLUME):/app/data \
		--env-file .env \
		$(IMAGE_NAME)

docker-stop:  ## Stop the running container
	-docker stop $(CONTAINER_NAME)

docker-logs:  ## Tail container logs
	docker logs -f $(CONTAINER_NAME)

docker-nuke:  ## Stop, remove container AND delete local data volume
	-docker stop $(CONTAINER_NAME) 2>/dev/null
	-docker rm $(CONTAINER_NAME) 2>/dev/null
	@echo "Removing local data volume: $(DATA_VOLUME)"
	rm -rf $(DATA_VOLUME)
