# AGENTS.md

## Project
`furniture-assistant` (product name **Furniture Assistant**) — turns architectural
furniture drawings into parametric 3D cabinets, cut lists, hardware/process lists and
valuations for CNC manufacturing. A legacy batch pipeline (`main.py`) extracts
cutting lists / BOMs directly; the web app (`src/webapp/` + `webapp/`) is the main product.

## Quick start
```bash
make install          # uv sync
make test             # uv run pytest -v
make lint             # uv run ruff check . --fix
make typecheck        # uvx ty check
make check            # lint + typecheck
make single           # process first drawing found under sample-data/
make simple           # simple direct extraction, no agent loop
make step-by-step     # tile-by-tile extraction with progressive output
make compare          # compare OCR backends on first sample-data drawing
make run              # batch process all drawings under sample-data/
make run-project PROJECT="<dir>"  # process all drawings of sample-data/<dir>
make benchmark        # multi-model benchmark comparison
make run-model MODEL=x  # override model, e.g. make run-model MODEL=qwen3.5-122b
make evaluate         # compare results/ against ground truth
make web-build        # build webapp/ frontend (Node); make web serves API + UI on :8000 (WEB_PORT=8010 to change)
make web-ui           # frontend dev server on :5173 (proxies /api to :8000)
make web-types        # backend OpenAPI → webapp/openapi.json → webapp/src/api-types.ts (run after API changes)
make web-e2e          # Playwright browser tests (real backend + built UI + fake model)
make docs             # mkdocs site (docs group) on :8001
```
Single test: `uv run pytest tests/test_document_loader.py -v`

## Environment / dependencies
- Python 3.13, managed by `uv` — **use `uv run`** for all commands, not bare `python`
- `.venv/` and `.env` are gitignored; `uv.lock` is committed
- Config: `config/default.yaml` → env vars (`FA_` prefix) → CLI args (three-layer precedence)
- Model server must be running before the pipeline: LM Studio on
  `http://localhost:1234/v1` (`make serve` opens the app; load models and enable
  the server manually).
  - Config defaults: orchestrator=`qwen/qwen3.6-35b-a3b`, vision=`internvl3-78b-instruct`
- Langfuse tracing (dev): `docker compose -f docker-compose.dev.yml up -d` → UI at `localhost:3000`

## Data paths
- `samples/` — the two committed sample sheets (`GLADIOGROUP_P02_PW-ZM05_12.pdf`, `06_A_METROPILITAN_NRF-zabudowy meblowe_widoki.pdf`); tests resolve drawings by file name through `tests/conftest.py::sample_file` (samples/ first, then sample-data/)
- `sample-data/<PROJECT>/` (gitignored) — full project packages: PDF (multi-sheet), PNG/JPG, DWG, P3D, XLSX
- `private/` (gitignored) — local-only customer reports, meeting notes and delivery archives; never commit
- `DRAWING_EXTENSIONS` includes PDF/image files, DWG, and P3D. DWG renders through local `dwg2dxf` + `ezdxf`/Matplotlib; P3D yields an explicit review record requiring a Palette CAD PDF, DWG, or DXF export. XLSX is not loaded directly.
- Tests in `conftest.py` return `None` for `sample_*_path` when no drawing is found → tests skip
- Output: `results/` (gitignored; JSON per drawing, XLSX summaries, review reports, token reports; web app projects under `results/webapp/projects/`)

## Architecture (short)

**Pipeline flow:** fixed explore → summarize → validate flow; `mode` is retained
only for backward-compatible configuration parsing.

```
main.py → DrawingPipeline.process_drawing()
  Phase 1: Exploration agent with all tools → evidence log
  Phase 2: Summarizer → ExtractionResult
  Phase 3: Validation diff → scalar-field corrections
  Phase 4: Deterministic panel/unit checks
  (on failure: falls back to the simple extractor)
```

**Multi-page:** `DocumentLoader` renders **all** pages of a PDF. Every public method
(`load`, `get_overview`, `get_tiles`, `crop_region`, `crop_title_block_cells`) takes an
optional `page_index` (0-based, default 0). `loader.page_count` / `loader.iter_pages()`
expose the full page list.

**Key modules:**
- `src/pipeline/orchestrator.py` — `DrawingPipeline`, main orchestration logic
- `src/pipeline/agent.py` — `DrawingDeps` dataclass, agent factory functions
- `src/document/loader.py` — PDF (pypdfium2, **multi-page**) / images → PIL, overview/crop/tile utils
- `src/schema/extraction.py` — `ExtractionResult` (furniture: `panels[]`, `hardware[]`, `EdgeBanding`, `Panel`)
- `src/schema/enums.py` — `DrawingType` enum (ELEVATION/FLOOR_PLAN/WALL_PLAN/SECTION/CEILING_PLAN/ELECTRICAL/COST_SHEET/IMAGE/OTHER) + `classify_drawing()`
- `src/schema/validation.py` — cross-field validation
- `src/tools/registry.py` — central tool registration with cache wrapping
- `src/pipeline/benchmark.py` — `ModelBenchmarkRunner`
- `src/output/` — JSON/XLSX writers, review/token/benchmark reports
- `src/ocr/` — VLM, OLMOCR, GLM-OCR backends + factory
- `config/agent_prompts.py` — agent prompts (furniture cutting-list domain)
- `src/reference/keywords.py`, `src/reference/standards_db.py` — furniture catalogs (board types, edge materials, hardware)
- `src/furniture/` — drawing → 3D: PDF text-layer dimension marks/chains (`marks.py`), material legend parser (`legend.py`), interpretation + refine agents (`interpret.py`), resolution with provenance, cabinet kind/group, plausibility, cross-view/cross-drawing checks, questions and overrides (`resolve.py`), per-kind builder (`build.py`, rules in `config/furniture.yaml`), hardware/processes, summaries, valuation (`pricing.py`, `config/pricing.yaml`, per-project override), client quote import/compare (`quote.py`), nesting/exports
- `src/webapp/` — FastAPI app (`api.py`, `reports.py`), project store with module versions and `audit.jsonl`, optional auth (`auth.py`, users CLI `users.py`), OpenAPI export (`openapi.py`), analysis job with one automatic model re-run per module with open questions; frontend in `webapp/` (Vite + React + R3F; `src/types.ts` aliases the generated `src/api-types.ts` — never hand-write API shapes; e2e tests in `webapp/e2e/`)

## Important details
- Type checking: `make typecheck` runs `ty` (Astral); `make lint` runs ruff; `make check` runs both.
- `.env` is gitignored — do not commit it
- `sample-data/`, `private/`, `results*/` are gitignored — customer data, do not commit
- Docs live in `docs/` (mkdocs, `mkdocs.yml` at the repo root); branching is `feat/*` → `dev` → `stage` → `main` (see `docs/development.md`)
- Drawing discovery matches `.pdf, .PDF, .TIF, .tif, .tiff, .png, .jpg, .jpeg`
- Main flow: exploration + summarization + validation; critical fields are always checked
- OCR backends: `vlm` (default), `olmocr`, `glm-ocr` — switch with `--ocr` / `--compare`
- GLM-OCR SDK mode needs optional `glmocr` dependency (`uv sync --extra ocr-glm`)
