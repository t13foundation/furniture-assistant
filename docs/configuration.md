# Configuration

Values are layered; the last layer that sets a value wins:

1. `config/default.yaml` — checked-in defaults.
2. `FA_*` environment variables. Outside production, the web app also loads a `.env` file from the repo root at startup. `FA_ENV` is resolved only from the process environment before `.env` is loaded; a value in `.env` is ignored. In production (`FA_ENV=production`), `.env` is not loaded; secrets must be provided by the process environment.
3. CLI arguments (`main.py --api-base …`, `--orchestrator …`) — legacy pipeline only. The web app has no CLI layer.

The loader is `AppConfig.from_yaml` in `config/settings.py`. The web app reads the same file and env at startup (lifespan in `src/webapp/api.py`); the legacy pipeline additionally applies CLI args on top.

!!! warning "Renamed prefix, no compatibility shim"
    Environment keys are read only with the `FA_` prefix. A stale `.env` or shell profile that still holds keys with the previous prefix is **silently ignored** — no warning is logged. Rename the keys when upgrading.

## Environment variables mapped to config fields

Source of truth: the mapping tables in `config/settings.py`. Defaults below are the values from `config/default.yaml` (or the dataclass fallback when the key is absent from the YAML).

### `api` — model server

| Env | Field | Meaning | Default |
|---|---|---|---|
| `FA_API_BASE` | `api.base_url` | Model server endpoint, OpenAI-compatible (LM Studio by default). A URL containing `api.openai.com` switches the web app to OpenAI's Responses API. | `http://127.0.0.1:1234/v1` |
| `FA_API_TIMEOUT` | `api.timeout` | Model request timeout, seconds. | `120` |

`api.api_key` has **no** env override; set it in the YAML if your server needs a key. The LM Studio placeholder `lm-studio` is the default, and `src/furniture/interpret.py` falls back to `OPENAI_API_KEY` for OpenAI URLs.

### `models`

| Env | Field | Meaning | Default |
|---|---|---|---|
| `FA_ORCHESTRATOR_MODEL` | `models.orchestrator` | Model that interprets dimension marks and runs refine passes. Used by both the web app and the legacy pipeline. | `qwen/qwen3.6-35b-a3b` |
| `FA_VISION_MODEL` | `models.vision` | Vision model for legacy-pipeline image tools. | `internvl3-78b-instruct` |
| `FA_VISION_API_BASE` | `models.vision_api_base` | Separate endpoint for the vision model (normally the same server). | `http://localhost:1234/v1` |
| `FA_BENCHMARK_MODELS` | `models.benchmark` | Comma-separated model list for `make benchmark`. | `qwen3.6-35b,qwen3.5-35b` |

### `pipeline` — legacy extraction loop

| Env | Field | Meaning | Default |
|---|---|---|---|
| `FA_CONFIDENCE_THRESHOLD` | `pipeline.confidence_threshold` | Confidence gate, `LOW` / `MEDIUM` / `HIGH`. | `MEDIUM` |
| `FA_MAX_PASSES` | `pipeline.max_passes` | Extraction passes per document. | `3` |
| `FA_MAX_ITERATIONS` | `pipeline.max_iterations` | Agent iterations per pass. | `20` |
| `FA_RETRY_ATTEMPTS` | `pipeline.retry_attempts` | Model-call retries. | `3` |
| `FA_CONTEXT_WINDOW` | `pipeline.context_window` | Token budget for the orchestrator. | `262144` |
| `FA_PIPELINE_MODE` | `pipeline.mode` | Compatibility field; the flow is fixed. | `single` |
| `FA_VALIDATION_MODE` | `pipeline.validation_mode` | `confidence-based` or `always-critical`. | `always-critical` |

### `output`

| Env | Field | Meaning | Default |
|---|---|---|---|
| `FA_OUTPUT_DIR` | `output.dir` | Results root. The web app stores projects under `<dir>/webapp/projects`. | `results` |
| `FA_OUTPUT_FORMAT` | `output.format` | Legacy report format: `xlsx` / `json` / `both`. | `xlsx` |

### `ocr` — legacy OCR backends

| Env | Field | Meaning | Default |
|---|---|---|---|
| `FA_OCR_BACKEND` | `ocr.backend` | `vlm` / `olmocr` / `glm-ocr`. | `vlm` |
| `FA_OCR_API_BASE` | `ocr.api_base` | Endpoint for the OCR backend. | unset |
| `FA_OCR_API_KEY` | `ocr.api_key` | API key for the OCR backend. | unset |
| `FA_OCR_MODEL` | `ocr.model_name` | OCR model name. | unset |
| `FA_OCR_MODE` | `ocr.mode` | `api` / `sdk` (GLM-OCR only). | `api` |

### `tracing`

| Env | Field | Meaning | Default |
|---|---|---|---|
| `FA_TRACING_ENABLED` | `tracing.enabled` | `true` / `1` / `yes`. Gates Langfuse/Phoenix tracing in the legacy pipeline orchestrator; forced off in e2e runs. | `true` |
| `FA_TRACING_BACKEND` | `tracing.backend` | `langfuse` / `phoenix` / `none`. | `langfuse` |

Tracing also needs `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` in the environment. A local Langfuse stack: `docker compose -f docker-compose.dev.yml up -d` (UI on `http://localhost:3000`).

### `input_dir`

| Env | Field | Meaning | Default |
|---|---|---|---|
| `FA_INPUT_DIR` | `input_dir` | Default input directory for the legacy pipeline. | `sample-data` |

## Web-app-only environment

Read directly from the environment (not part of `AppConfig`):

| Env | Read in | Meaning | Default |
|---|---|---|---|
| `FA_ENV` | `src/webapp/api.py`, `src/webapp/auth.py` | Runtime mode, read from the process environment before dotenv loading; `.env` cannot select the mode. Exact values `development` or `production` (case-insensitive) only; any other value fails startup. Production disables `.env` loading and requires `FA_SESSION_SECRET`; it also defaults the session cookie to `Secure`. | `development` |
| `FA_PUBLIC_URL` | `src/webapp/api.py` | Public app URL; an `https://` URL defaults the session cookie to `Secure`. | unset |
| `FA_USERS_FILE` | `src/webapp/auth.py` | Path to `users.yaml`. Auth is enabled iff the file exists and lists at least one user. | `config/users.yaml` |
| `FA_SESSION_SECRET` | `src/webapp/auth.py` | HMAC key signing session cookies. Required in production; in development, absent value uses a local fallback file. | unset |
| `FA_COOKIE_SECURE` | `src/webapp/api.py` | Explicit `Secure` flag for the session cookie; accepts exactly `true`/`false`, `yes`/`no`, or `1`/`0` without surrounding whitespace, case-insensitively, and overrides the production / HTTPS defaults. Invalid values fail startup. | production or HTTPS URL: `true`; otherwise `false` |
| `FA_MAX_UPLOAD_MB` | `src/webapp/api.py` | Per-file upload limit; larger files get HTTP 413. | `50` |
| `FA_MAX_FILES` | `src/webapp/api.py` | Maximum files per project; more get HTTP 413. | `50` |
| `OPENAI_API_KEY` | `src/furniture/interpret.py` | API key used when `FA_API_BASE` points at `api.openai.com` and no real key is configured. Read from process environment; `.env` only in development. | unset |

In production, set `FA_ENV=production` in the process environment (values are case-insensitive). `.env` cannot select the runtime mode; if `FA_ENV` is absent from the process environment, the mode remains `development` even if `.env` contains an `FA_ENV` entry. Provide `FA_SESSION_SECRET` from a protected environment source (for example, a systemd credential or secret manager). The app fails at startup if the secret is missing or `FA_COOKIE_SECURE` is set to an invalid value. The cookie is `Secure` in production or when `FA_PUBLIC_URL` uses HTTPS, unless a valid explicit `FA_COOKIE_SECURE` value overrides it.

## `config/default.yaml`

The YAML sections mirror the tables above. Two extra points:

- `document:` (image rendering: overview/detail sizes, `pdf_render_scale`, `tile_overlap`, `multipage`) has **no env overrides** — edit the YAML. The web app uses this section when rendering project pages to PNG.
- The web app reads `api.base_url`, `api.api_key`, `api.timeout`, `models.orchestrator`, `document` and `output.dir` at startup; changes require a restart.

## `config/furniture.yaml`

Shop construction rules for the builder, loaded once at startup by `load_rules()` in `src/furniture/rules.py` (frozen dataclasses; a missing file, section or key falls back to built-in defaults, so partial edits are safe). Values are shop placeholders — edit to match the workshop standard; no code changes needed.

| Section | Keys (examples) | Used by |
|---|---|---|
| Construction constants (top level) | `carcass_thickness_mm`, `front_thickness_mm`, `back_thickness_mm`, `front_gap_mm`, `plinth_default_mm`, `plinth_recess_mm`, `depth_default_mm` (applied when a page shows no depth; flagged `DEPTH_ASSUMED`), `shelf_setback_mm`, drawer-box dimensions, sheet nesting (`sheet_length_mm`, `sheet_width_mm`, `sheet_trim_mm`, `kerf_mm`), cladding (`cladding_thickness_mm`, batten size/spacing) | `src/furniture/build.py`, `src/furniture/nest.py` |
| `plausibility:` | `width_max_mm`, `height_max_mm`/`min`, `depth_max_mm`/`min`, `wall_cladding_depth_min_mm`, `column_width_min_mm`, `cell_height_min_mm`, `plinth_max_mm` | plausibility checks that raise module issues |
| `hardware:` | `hinge_height_thresholds_mm` + `hinge_counts`, `runner_lengths_mm`, leg counts, `connector_per_joint`, `back_fixing_perimeter_step_mm`, `disabled_categories_by_kind` (e.g. `bench: [leg]`) | `src/furniture/hardware.py` |
| `processes:` | minutes per unit: `cut_part_minutes`, `edge_band_per_metre_minutes`, `carcass_assembly_minutes`, `installation_per_module_minutes`, … | `src/furniture/processes.py` (labour time for valuation) |

## `config/pricing.yaml`

Placeholder rates: `currency`, `boards` (match pattern, kind, `thickness_mm`, `price_per_m2`), `edge_banding_per_m`, unit prices per hardware category, `labour_rate_per_hour`. The web app copies this file into a project on first pricing edit; per-project overrides and markups are stored there. See [Pricing and quotes](pricing.md).
