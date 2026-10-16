# Development

Repository conventions, test layers and the release process. End-user configuration lives in [Configuration](configuration.md); deployment in [Operations](operations.md).

## Repository layout

| Path                                                                           | Contents                                                                                                                                                                                         |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `src/furniture/`                                                               | Drawing → 3D domain: `marks`, `legend`, `annotate`, `interpret`, `resolve`, `build`, `hardware`, `processes`, `nest`, `export`, `reports`, `pricing`, `quote`, `rules`, `schema`                 |
| `src/webapp/`                                                                  | FastAPI app: `api`, `analysis` (background jobs), `store` (file-backed project store), `auth`, `users` (CLI), `reports`, `openapi`                                                               |
| `src/{document,ocr,pipeline,tools,reference,schema,output,vision,evaluation}/` | Legacy extraction pipeline (batch CLI, `main.py`)                                                                                                                                                |
| `webapp/`                                                                      | React + react-three-fiber frontend (`src/`): route-level lazy pages, per-report modules and split module header/editor; Playwright e2e (`e2e/`), generated `openapi.json` and `src/api-types.ts` |
| `config/`                                                                      | `default.yaml` + `settings.py` (loader), `furniture.yaml` (shop rules), `pricing.yaml`, `agent_prompts.py`                                                                                       |
| `tests/`                                                                       | pytest suite (see below)                                                                                                                                                                         |
| `samples/`                                                                     | Two committed sample sheets (public; used by tests and the demo)                                                                                                                                 |
| `docs/`                                                                        | This mkdocs site                                                                                                                                                                                 |
| `sample-data/`, `private/`, `results*/`                                        | Gitignored: project packages, local customer material, run outputs                                                                                                                               |

`main.py` is the legacy pipeline CLI; the web app entry point is `src.webapp.api:app`.

## Make targets

Full list: `make help`. The ones used daily:

| Target                                                      | Purpose                                                           |
| ----------------------------------------------------------- | ----------------------------------------------------------------- |
| `make install` / `make sync`                                | `uv sync` / `uv lock && uv sync`                                  |
| `make hooks`                                                | Install pre-commit git hooks                                      |
| `make web WEB_PORT=8010`                                    | Run the web app (default :8000); serves `webapp/dist`             |
| `make web-ui`                                               | Vite dev server on :5173 (proxies `/api` to :8000)                |
| `make web-build`                                            | `npm ci && npm run build` into `webapp/dist`                      |
| `make web-types`                                            | Regenerate `webapp/openapi.json` + `webapp/src/api-types.ts`      |
| `make web-e2e`                                              | Build frontend, install chromium, run Playwright                  |
| `make docs` / `make docs-build`                             | Serve mkdocs on :8001 / strict build into `site/`                 |
| `make test` / `make check`                                  | pytest / ruff + ty                                                |
| `make run-project PROJECT="<dir under sample-data>"`        | Legacy pipeline over one project package                          |
| `make run`, `make single`, `make benchmark`, `make compare` | Legacy pipeline batch/single/model-comparison/OCR-comparison runs |

## Test layers

1. **Unit / domain** — `uv run pytest -q`. `tests/test_furniture_*.py` cover marks, interpretation, resolve/build, hardware, processes, exports, pricing and quotes against fixtures; `tests/test_pipeline.py`, `tests/test_schema.py` etc. cover the legacy pipeline.
2. **API** — `tests/test_webapp_api.py`, `tests/test_webapp_improvements.py`, `tests/test_webapp_openapi.py` drive the FastAPI app in-process via `TestClient`: no server, no model, real on-disk project stores under tmp dirs.
3. **Browser (e2e)** — `make web-e2e` runs Playwright chromium (`webapp/e2e/main-flows.spec.ts`) against the built frontend and a real backend on :8011 whose model calls go to a deterministic fake model (`webapp/e2e/fake_llm.py`, served on :8799). The config (`webapp/playwright.config.ts`) sets `FA_API_BASE` to the fake, `FA_TRACING_ENABLED=false` and a throw-away `FA_OUTPUT_DIR` — no secrets, one worker, sequential tests. Traces land in `webapp/test-results` on failure.

### Sample data policy

Tests resolve drawings by **file name**, never by customer directory: `tests/conftest.py::sample_file(pattern)` globs the committed `samples/` first, then anywhere under the gitignored `sample-data/`; a test skips itself when neither matches. Consequently the suite passes in CI with only `samples/` present and locally with full project packages. Never commit anything from `sample-data/` or `private/` (both gitignored; `check-added-large-files` also excludes the demo video and `samples/` from its 500 KB limit).

## Generated types contract

`webapp/openapi.json` and `webapp/src/api-types.ts` are generated, not hand-edited:

```bash
make web-types   # python -m src.webapp.openapi webapp/openapi.json && openapi-typescript
```

Run it after any change to request/response models in `src/webapp/`, and commit the result together with the backend change. The CI `types-drift` job regenerates the schema and diffs it against the committed file, so a stale `openapi.json` fails the build even when tests pass.

## Pre-commit

```bash
make hooks   # uv run pre-commit install
```

Hooks: ruff lint + format, file hygiene (whitespace, YAML/TOML/JSON validity, merge conflicts), `no-commit-to-branch` for `main`, `check-added-large-files` (500 KB; `docs/app-demo.mp4` and `samples/*` are excluded). `ty check` runs as a manual-stage hook. Run everything: `uv run pre-commit run --all-files`.

## Branching strategy

```
feat/<topic> ──► dev ──(fast-forward)──► stage ──(fast-forward)──► main ──► vX.Y.Z
```

- `dev` — default branch and integration target. All work lands via PRs from `feat/<topic>` or `fix/<topic>` branches.
- `stage` — pre-production. Fast-forward merge from `dev` only after `make check && make test && make web-e2e` pass (CI green is the PR gate; the stage merge is the release gate).
- `main` — production. Fast-forward from `stage` only, tagged `vX.Y.Z` with the `version` bump in `pyproject.toml` in the same commit.
- Hotfixes branch from `main`, merge back to `main` (tagged), then back-merge into `stage` and `dev`.

No direct pushes to `stage` or `main` (enforced by branch protection, see below; `no-commit-to-branch` additionally guards `main` locally).

## Release checklist

1. On `dev`: feature PR merged, all CI jobs green.
2. Locally from a clean `dev` checkout: `make check && make test && make web-e2e`.
3. `git checkout stage && git merge --ff-only dev`; deploy staging and verify (`GET /api/health`, a sample analysis run).
4. Bump `version` in `pyproject.toml` on `dev` (PR or maintainer commit), fast-forward `stage` again.
5. `git checkout main && git merge --ff-only stage && git tag vX.Y.Z && git push origin main stage dev --tags`.
6. Hotfix path: `fix/<topic>` from `main` → PR to `main` → tag → merge `main` back into `stage` and `dev`.

## CI (`.github/workflows/ci.yml`)

Runs on pushes to `dev`/`stage`/`main` and on pull requests; in-progress runs cancel on new pushes. Jobs:

| Job | Steps |
|---|---|
| `backend` | `uv sync`, `ruff check .`, `ruff format --check .`, `ty check` (whole repository), `pytest -q` |
| `frontend` | `npm ci`, `tsc --noEmit`, `npm run lint` (oxlint), `npm run build` |
| `types-drift` | regenerate OpenAPI, `diff` against `webapp/openapi.json` |
| `e2e` (needs backend + frontend) | Playwright with the fake model; uploads `webapp/test-results` on failure |

## GitHub settings for the maintainer

Branch protection is a GitHub-settings concern, not a repo file. After the first push:

- Set the default branch to `dev`.
- Protect `main` and `stage`: require pull requests before merging, require status checks `backend`, `frontend`, `types-drift`, `e2e`, forbid force pushes and deletions.
- Recommended for `dev`: same required checks, direct pushes allowed for maintainers.
