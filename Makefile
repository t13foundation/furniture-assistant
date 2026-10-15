.PHONY: install sync serve serve-lmstudio run single simple benchmark test lint typecheck check clean help hooks step-by-step compare evaluate run-project run-model web web-ui web-build web-types web-e2e docs docs-build

# Config
PYTHON := uv run python
CONFIG  := config/default.yaml
DATA    := sample-data
OUTPUT  := results
MODEL   := qwen3.6-35b
WEB_PORT ?= 8000

# File types present in furniture project data (PDFs, renders/scans, CAD exports)
DRAW_EXTS := -name "*.pdf" -o -name "*.PDF" -o -name "*.TIF" -o -name "*.tif" -o -name "*.tiff" -o -name "*.png" -o -name "*.PNG" -o -name "*.jpg" -o -name "*.jpeg" -o -name "*.dwg" -o -name "*.DWG" -o -name "*.p3d" -o -name "*.P3D"

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ── Setup ──────────────────────────────────────────────────────────

install: ## Install Python dependencies
	uv sync

sync: ## Sync dependencies (run after pyproject.toml changes)
	uv lock && uv sync

hooks: ## Install pre-commit git hooks
	uv run pre-commit install

# ── Model Server ───────────────────────────────────────────────────

serve: serve-lmstudio ## Open LM Studio; load the configured model and start its local server

serve-lmstudio: ## Open LM Studio (load model and enable the server on :1234 manually)
	open -a "LM Studio"

# ── Pipeline ───────────────────────────────────────────────────────

run: ## Process all drawings (batch mode)
	$(PYTHON) main.py -i "$(DATA)" -o $(OUTPUT) -v

single: ## Process a single drawing (first found under sample-data)
	@FILE=$$(find "$(DATA)" -type f \( $(DRAW_EXTS) \) | sort | head -1); \
		if [ -z "$$FILE" ]; then echo "No drawings found in $(DATA)"; exit 1; fi; \
		echo "Processing: $$FILE"; \
		$(PYTHON) main.py -i "$$FILE" -o $(OUTPUT) --single -v

simple: ## Simple tile extraction (no agent loop, first found)
	@FILE=$$(find "$(DATA)" -type f \( $(DRAW_EXTS) \) | sort | head -1); \
		if [ -z "$$FILE" ]; then echo "No drawings found in $(DATA)"; exit 1; fi; \
		echo "Simple extraction: $$FILE"; \
		$(PYTHON) main.py -i "$$FILE" -o $(OUTPUT) --simple -v

step-by-step: ## Step-by-step tile analysis (all 9 tiles, default VL model)
	@FILE=$$(find "$(DATA)" -type f \( $(DRAW_EXTS) \) | sort | head -1); \
		if [ -z "$$FILE" ]; then echo "No drawings found in $(DATA)"; exit 1; fi; \
		echo "Step-by-step analysis: $$FILE"; \
		$(PYTHON) main.py --step-by-step -i "$$FILE" -o $(OUTPUT) -v

benchmark: ## Run multi-model benchmark comparison
	$(PYTHON) main.py -i "$(DATA)" -o $(OUTPUT) --benchmark -v

compare: ## Compare OCR backends (VLM vs OLMOCR vs GLM-OCR)
	@FILE=$$(find "$(DATA)" -type f \( $(DRAW_EXTS) \) | sort | head -1); \
		if [ -z "$$FILE" ]; then echo "No drawings found in $(DATA)"; exit 1; fi; \
		echo "Comparing OCR backends: $$FILE"; \
		$(PYTHON) main.py --compare -i "$$FILE" -o $(OUTPUT) -v

# ── Per-project targets (sample-data/<PROJECT>) ───────────────────

run-project: ## Process all drawings of one project: make run-project PROJECT="<dir under sample-data>"
	@if [ -z "$(PROJECT)" ]; then echo 'Usage: make run-project PROJECT="<dir under $(DATA)>"'; exit 1; fi
	$(PYTHON) main.py -i "$(DATA)/$(PROJECT)" -o $(OUTPUT) -v

run-model: ## Process all drawings with a specific model: make run-model MODEL=qwen3.5-122b
	$(PYTHON) main.py -i "$(DATA)" -o $(OUTPUT) --orchestrator $(MODEL) --vision $(MODEL) -v

run-lmstudio: ## Process single drawing via LM Studio (Gemma 4 + InternVL3)
	@FILE=$$(find "$(DATA)" -type f \( $(DRAW_EXTS) \) | sort | head -1); \
		if [ -z "$$FILE" ]; then echo "No drawings found in $(DATA)"; exit 1; fi; \
		echo "Processing: $$FILE"; \
		$(PYTHON) main.py -i "$$FILE" -o $(OUTPUT) --single -v \
			--api-base "http://localhost:1234/v1" \
			--orchestrator "google/gemma-4-26b-a4b" \
			--vision "internvl3-78b-instruct"

# ── Web app (drawing → 3D) ─────────────────────────────────────────

web: ## Run the web app API (serves webapp/dist when built) on :$(WEB_PORT); e.g. make web WEB_PORT=8010
	uv run uvicorn src.webapp.api:app --host 127.0.0.1 --port $(WEB_PORT) --reload

web-ui: ## Run the frontend dev server on :5173 (proxies /api to :8000)
	cd webapp && npm run dev

web-build: ## Build the frontend into webapp/dist
	cd webapp && npm ci && npm run build

web-types: ## Generate TypeScript types from the backend OpenAPI schema
	uv run python -m src.webapp.openapi webapp/openapi.json
	cd webapp && npm run gen:types

web-e2e: ## Browser tests (Playwright) of upload, review, edit, history and valuation against a fake model
	cd webapp && npm run build && npx playwright install chromium && npm run e2e

# ── Documentation ──────────────────────────────────────────────────

docs: ## Serve the mkdocs site on :8001
	uv run --group docs mkdocs serve -a 127.0.0.1:8001

docs-build: ## Build the mkdocs site into site/ (fails on warnings)
	uv run --group docs mkdocs build --strict

# ── Testing ────────────────────────────────────────────────────────

evaluate: ## Evaluate extraction accuracy against ground truth
	$(PYTHON) -m src.evaluation.accuracy --results-dir $(OUTPUT) --output $(OUTPUT)/accuracy_report.md

test: ## Run test suite
	uv run pytest -v

test-cov: ## Run tests with coverage
	uv run pytest -v --tb=short -q

lint: ## Check Python files with ruff
	uv run ruff check . --fix

typecheck: ## Type-check with ty (Astral)
	uvx ty check

check: lint typecheck ## Run lint + type checks

# ── Cleanup ────────────────────────────────────────────────────────

clean: ## Remove generated results
	rm -rf $(OUTPUT)

clean-all: clean ## Remove results + Python caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache
