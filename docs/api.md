# API

The web app is a FastAPI backend (`src/webapp/api.py`, reports and pricing routes in `src/webapp/reports.py`) that stores every project as plain files (see [Architecture](architecture.md)) and serves the built React frontend from `webapp/dist` at `/`. Start it with `make web` (uvicorn, default port 8000; `make web WEB_PORT=8010`).

All endpoints are JSON under `/api/`. The table below is generated from `webapp/openapi.json`, which is the exported backend schema — see [the `make web-types` contract](#the-make-web-types-contract).

## Authentication

Auth is enabled as soon as the users file (`FA_USERS_FILE`, default `config/users.yaml`) contains at least one user; while it is empty every endpoint is public. When enabled, every `/api/*` request except the four public paths requires the session cookie `fa_session` (set by login; `httponly`, `samesite=lax`, 7-day expiry; its `secure` flag defaults to production/HTTPS and can be overridden with a valid `FA_COOKIE_SECURE` boolean spelling — see [Operations](operations.md)). Missing or expired sessions get `401 {"detail": "Authentication required"}`. The middleware lives in `src/webapp/auth.py`.

## Auth and health (public)

| Method | Path | Request | Response | Auth |
|---|---|---|---|---|
| POST | `/api/auth/login` | `LoginBody {username, password}` | `{username}` + `fa_session` cookie | public |
| POST | `/api/auth/logout` | — | `{status}`; deletes the cookie | public |
| GET | `/api/auth/me` | — | `AuthMeResponse {username, auth_enabled}` | public |
| GET | `/api/health` | — | `HealthResponse {status: "ok"\|"degraded", model_base_url, projects_root_writable, auth_enabled}` | public |

`/api/health` is for probes and reverse proxies: `degraded` when the projects root is not writable. Login failures are throttled per `(ip, username)`: 5 failures within 15 minutes lock that pair for 5 minutes (`429` with a `Retry-After` header).

## Projects

| Method | Path | Request | Response | Auth |
|---|---|---|---|---|
| GET | `/api/projects` | — | `ProjectListResponse[]` — project fields plus `job_status`, `module_count` | cookie |
| POST | `/api/projects` | multipart `files[]` + form `name` | `201 {id}` | cookie |
| GET | `/api/projects/{project_id}` | — | `ProjectDetailResponse {project, job, modules}` | cookie |

Project and quote uploads are capped by `FA_MAX_REQUEST_MB` (default 200 MB, total multipart body) and rejected with `413` before parsing when `Content-Length` exceeds the cap. Requests streamed without `Content-Length` are capped as chunks arrive. Project uploads also enforce `FA_MAX_UPLOAD_MB` (default 50 MB per file) and `FA_MAX_FILES` (default 50 files); these limits return `413`. Quote imports enforce the same per-file cap.

The detail response is the polling endpoint: `job` is the current analysis job (`status` `queued → running → done | error`, per-page `items` with `pending/running/done/skipped/error` and produced `module_ids`), and `modules` lists every module with `status`, `question_count`, `attention_count`, `refine_status` and error/warning `issue_counts`. A job left `queued`/`running` by a server restart is reported as `error` ("Analysis interrupted (server restarted)") — run the analysis again.

## Analysis

| Method | Path | Request | Response | Auth |
|---|---|---|---|---|
| POST | `/api/projects/{project_id}/analyze` | — | `202 StatusResponse {status: "queued"}` | cookie |

Queues the background job that renders every page, extracts dimension marks, runs the interpretation agent per page, resolves modules and rebuilds them (sequence diagram in [Architecture](architecture.md)). `409` if an analysis or model re-run is already in flight for the project.

## Modules

| Method | Path | Request | Response | Auth |
|---|---|---|---|---|
| GET | `/api/projects/{project_id}/modules/{module_id}` | — | `ModuleModel` | cookie |
| PUT | `/api/projects/{project_id}/modules/{module_id}` | `FurnitureModule` | `ModuleModel` | cookie |
| POST | `/api/projects/{project_id}/modules/{module_id}/answers` | `AnswersBody {overrides: Override[]}` | `ModuleModel` | cookie |
| POST | `/api/projects/{project_id}/modules/{module_id}/refine` | — | `202 StatusResponse {status: "running"}` | cookie |
| GET | `/api/projects/{project_id}/modules/{module_id}/versions` | — | `ModuleVersion[]` | cookie |
| GET | `/api/projects/{project_id}/modules/{module_id}/versions/{version}` | — | `ModuleModel` | cookie |
| POST | `/api/projects/{project_id}/modules/{module_id}/versions/{version}/restore` | — | `ModuleModel` | cookie |

- **PUT** is the editor save: the backend converts the edited module into overrides on the model's interpretation, merges them with existing ones, re-runs checks and rebuilds (see [Review workflow](review-workflow.md#editing)).
- **answers** applies review-tab answers; an override without values removes that target's override; an empty list just re-runs the checks and rebuilds.
- **refine** is the "Ask the model again" button — a background model re-run; poll the module until `refine_status` leaves `running`. The result is kept only if it closes gaps ([Review workflow](review-workflow.md#ask-the-model-again)).
- **restore** does not rewind: it saves the old version as a new version and re-runs the project cross-checks.

Module fields, override targets and issue codes are documented in [Data model](data-model.md).

## Review and activity

| Method | Path | Request | Response | Auth |
|---|---|---|---|---|
| GET | `/api/projects/{project_id}/review` | — | `ReviewEntry[]` — per module: id, title, group, kind, status, `questions` | cookie |
| GET | `/api/projects/{project_id}/activity` | — | `ActivityEntry[]` from `audit.jsonl` (`at`, `user`, `action`, `module_id`, `detail`) | cookie |

## Reports

| Method | Path | Request | Response | Auth |
|---|---|---|---|---|
| GET | `/api/projects/{project_id}/summary/materials` | — | `MaterialSummary` — boards (with sheet counts and unplaceable parts), edges, hardware | cookie |
| GET | `/api/projects/{project_id}/summary/processes` | — | `ProcessSummary` — process matrix per module and totals in minutes | cookie |
| GET | `/api/projects/{project_id}/valuation` | — | `Valuation` — lines, totals, `per_module`, `per_group`, notes | cookie |
| GET | `/api/projects/{project_id}/export/valuation.xlsx` | — | `.xlsx` file | cookie |

Valuation semantics and unpriced rules: [Pricing and quotes](pricing.md).

## Pricing

| Method | Path | Request | Response | Auth |
|---|---|---|---|---|
| GET | `/api/pricing` | — | `Pricing` — the workspace rates from `config/pricing.yaml` | cookie |
| PUT | `/api/pricing` | `Pricing` | `Pricing` | cookie |
| GET | `/api/projects/{project_id}/pricing` | — | `ProjectPricing {pricing, inherited}` | cookie |
| PUT | `/api/projects/{project_id}/pricing` | `Pricing` | `ProjectPricing` | cookie |
| DELETE | `/api/projects/{project_id}/pricing` | — | `ProjectPricing` — reverts to the workspace rates | cookie |

`inherited` is `true` while the project uses the workspace rates; a project PUT writes `pricing.yaml` inside the project directory. Both changes are audited as `pricing_change`.

## Quote

| Method | Path | Request | Response | Auth |
|---|---|---|---|---|
| POST | `/api/projects/{project_id}/quote` | multipart `file` (`.xlsx`) | `QuoteComparison` | cookie |
| GET | `/api/projects/{project_id}/quote` | — | `QuoteComparison`; `404` until one is uploaded | cookie |

The upload is stored as `quote.xlsx` (original) and `quote.json` (parsed lines) in the project directory and re-compared on every GET. Accepted row format and the suggested-markup calculation: [Pricing and quotes](pricing.md#client-quote-import).

## Exports

| Method | Path | Request | Response | Auth |
|---|---|---|---|---|
| GET | `/api/projects/{project_id}/modules/{module_id}/export/{fmt}` | `fmt` ∈ `cutlist.csv`, `cutlist.xlsx`, `sheets.svg`, `panels.dxf` | file | cookie |
| GET | `/api/projects/{project_id}/export/{fmt}` | same formats | file — all modules of the project | cookie |

## Pages

| Method | Path | Request | Response | Auth |
|---|---|---|---|---|
| GET | `/api/projects/{project_id}/pages/{file_index}/{page_index}.png` | — | rendered page PNG (cached under `pages/`) | cookie |

## Error codes worth knowing

| Code | When |
|---|---|
| 400 | upload with no files or an unsupported extension; client quote that is not `.xlsx` |
| 401 | missing/expired session on a non-public path; wrong credentials at login |
| 404 | unknown project, module, version or page; no client quote uploaded yet |
| 409 | `analyze` while an analysis or model re-run is in flight; `refine` while one is already running or the project is being analysed |
| 413 | a request exceeds `FA_MAX_REQUEST_MB`, a file exceeds `FA_MAX_UPLOAD_MB`, or more than `FA_MAX_FILES` files are uploaded to one project |
| 422 | request validation; module assembly errors on PUT/answers (e.g. an override a module cannot take); refine on a hand-made module (no model interpretation); unreadable quote workbook |
| 429 | login throttled (5 failures per ip+username within 15 min → 5 min lock) |

## The `make web-types` contract

Frontend types are generated, never written by hand:

1. `src/webapp/openapi.py:typed_openapi()` exports the backend's OpenAPI schema to `webapp/openapi.json` (`uv run python -m src.webapp.openapi webapp/openapi.json`).
2. `webapp/src/api-types.ts` is generated from that schema (`cd webapp && npm run gen:types`).
3. `webapp/src/types.ts` only aliases the generated types — extend or map there, do not redefine shapes.

Both steps run via `make web-types`. Two gates fail when the committed artifacts are stale: `tests/test_webapp_openapi.py::test_frontend_schema_matches_backend` (pytest) and the CI job `types-drift`, which re-exports the schema and diffs it against `webapp/openapi.json`. So: change the backend → run `make web-types` → commit `webapp/openapi.json` and `webapp/src/api-types.ts` together. Responses are exported with every field present, so the generated types mark them required even where Python has defaults.
