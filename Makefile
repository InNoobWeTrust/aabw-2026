.PHONY: help lock sync fix format lint quality test check dev dev-up doctor \
        build docker-run docker-update docker-stop docker-logs docker-nuke \
        example-video prepare-real-video llm-probe \
        frontend-install frontend-api-types frontend-build frontend-rebuild demo

CONTAINER_NAME := robodata
IMAGE_NAME     := robodata:latest
DATA_VOLUME    := $(PWD)/data
EXAMPLE_DIR    := $(DATA_VOLUME)/examples
EXAMPLE_VIDEO  := $(EXAMPLE_DIR)/example_test_video.mp4
REAL_VIDEO     := $(EXAMPLE_DIR)/real_capture_prepared.mp4

# Frontend (Next.js static export → frontend/out/, mounted by FastAPI at /)
FRONTEND_DIR   := frontend
FRONTEND_OUT   := $(FRONTEND_DIR)/out
FRONTEND_INDEX := $(FRONTEND_OUT)/index.html
PKG_MANAGER    ?= $(if $(wildcard $(FRONTEND_DIR)/package-lock.json),npm,$(shell command -v bun >/dev/null 2>&1 && echo bun || echo npm))

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

dev: frontend-build  ## Start dev server after rebuilding frontend dependencies, generated API types, and static export
	uv run uvicorn backend.server:app --host 0.0.0.0 --reload --port 8080

dev-local: frontend-build  ## Start localhost-only dev server after rebuilding frontend prerequisites
	uv run uvicorn backend.server:app --host 127.0.0.1 --reload --port 8080

dev-up:  ## Bootstrap dev environment from scratch
	uv lock && uv sync --extra dev

doctor:  ## Verify the FastAPI app imports cleanly
	uv run python -c "from backend.server import app; print(f'OK: {app.title} v{app.version}')"

llm-probe:  ## Probe configured OpenAI-compatible LLM endpoint
	uv run python -m backend.llm_probe

example-video:  ## Generate a synthetic MP4 sample for upload testing
	@mkdir -p "$(EXAMPLE_DIR)"
	ffmpeg -y -f lavfi -i "testsrc=duration=5:size=640x480:rate=30" \
		-f lavfi -i "sine=frequency=440:duration=5" \
		-c:v libx264 -pix_fmt yuv420p -shortest "$(EXAMPLE_VIDEO)"
	@printf "Wrote synthetic sample: %s\n" "$(EXAMPLE_VIDEO)"

prepare-real-video:  ## Transcode a real recorded clip: make prepare-real-video INPUT=/path/to/source.mp4
	@test -n "$(INPUT)" || (printf "Usage: make prepare-real-video INPUT=/path/to/source.mp4\n" && exit 1)
	@mkdir -p "$(EXAMPLE_DIR)"
	ffmpeg -y -i "$(INPUT)" \
		-vf "fps=30,scale='min(1280,iw)':-2:flags=lanczos" \
		-c:v libx264 -pix_fmt yuv420p -c:a aac -movflags +faststart "$(REAL_VIDEO)"
	@printf "Wrote prepared real sample: %s\n" "$(REAL_VIDEO)"

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

# ── Frontend (Next.js → static export) ────────────────────────────────────────

frontend-install:  ## Install frontend dependencies with lockfile-aware package manager behavior
	@echo "Using package manager: $(PKG_MANAGER)"
	@if [ "$(PKG_MANAGER)" = "npm" ]; then \
		cd $(FRONTEND_DIR) && npm ci; \
	elif [ "$(PKG_MANAGER)" = "bun" ]; then \
		cd $(FRONTEND_DIR) && bun install --frozen-lockfile; \
	else \
		cd $(FRONTEND_DIR) && $(PKG_MANAGER) install; \
	fi

frontend-api-types:  ## Generate frontend TypeScript API types from backend OpenAPI
	cd $(FRONTEND_DIR) && $(PKG_MANAGER) run gen:api-types

frontend-build: frontend-install frontend-api-types  ## Build the Next.js static export into frontend/out/
	@echo "Building Next.js static export (package manager: $(PKG_MANAGER))..."
	cd $(FRONTEND_DIR) && $(PKG_MANAGER) run build
	@test -f "$(FRONTEND_INDEX)" || (echo "ERROR: $(FRONTEND_INDEX) not produced — build failed." && exit 1)
	@echo "Frontend built: $(FRONTEND_INDEX)"

frontend-rebuild:  ## Clean rebuild of the frontend (removes .next and out)
	rm -rf $(FRONTEND_DIR)/.next $(FRONTEND_OUT)
	$(MAKE) frontend-build

demo:  ## One-shot: sync Python deps, build frontend, then start dev server
	uv lock
	uv sync --extra dev
	$(MAKE) frontend-build
	@if [ ! -f .env ]; then \
		echo "[make demo] .env not found — copying from .env.example. Edit it before running again."; \
		cp .env.example .env; \
	fi
	@echo
	@echo "================================================================"
	@echo " RoboData is ready. Starting dev server..."
	@echo "   UI:      http://localhost:8080"
	@echo "   API:     http://localhost:8080/api/health"
	@echo "   Stop:    Ctrl-C"
	@echo "================================================================"
	@echo
	uv run uvicorn backend.server:app --host 0.0.0.0 --reload --port 8080
