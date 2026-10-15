# Production readiness

Review of the Furniture Assistant web app against real-deployment requirements, written for the first public release. Every finding was re-verified against the current code (file:line anchors below); statuses reflect this release, not intentions. Configuration keys and operational constraints referenced here are covered in [Configuration](configuration.md) and [Operations](operations.md); the branching and CI context in [Development](development.md).

## Summary

| Priority | Findings | Done | Partly done | Open |
|---|---|---|---|---|
| P0 — blocks real deployment | 6 | 1 | 2 | 3 |
| P1 — needed before customer-facing use | 10 | 1 | 0 | 9 |
| P2 — quality and robustness | 7 | 0 | 0 | 7 |

Top 5 open items, in order of risk:

1. **Single-process state (P0).** Locks and job tracking live in process memory; a second uvicorn worker or replica corrupts projects and loses jobs. Exactly one worker must run until state moves to a queue/DB.
2. **Jobs do not survive a restart (P0).** An interrupted analysis is reported as an error and the user must click "Run analysis" again.
3. **No model-server readiness check on `analyze` (P0, remainder).** The health endpoint exists, but a job is still accepted when the model server is down and fails on its first page instead of getting a 503.
4. **`OPENAI_API_KEY` read from `.env` in the repo directory (P0).** No secret manager; anyone with repo-dir access can read the key.
5. **No authorization model (P1).** Every logged-in user sees every project; there are no roles.

Done in this release (the review's "do now" list, each covered by tests in `tests/test_webapp_improvements.py`):

- Session cookie `secure` flag via `FA_COOKIE_SECURE` (default `false`).
- Upload limits: `FA_MAX_UPLOAD_MB` per file and `FA_MAX_FILES` per project, both returning HTTP 413.
- Public `GET /api/health` endpoint (exempt from auth), reporting model URL, projects-root writability and auth state.
- React `ErrorBoundary` around the router, so a render error shows a "Reload" page instead of a blank screen.
- `WEB_PORT` Makefile variable (`make web WEB_PORT=8010`).

Explicitly **not** implemented from the same list: the `analyze` 503 probe of the model server, the per-request total upload cap, and auto-detection of the cookie `secure` flag from a public URL (it is env-only; see the P0 table).

## P0 — blocks real deployment

| Area | Finding | Evidence | Fix | Effort | Status |
|---|---|---|---|---|---|
| Concurrency | All mutable state is in-process: per-project locks and the analysis/refine task registry. More than one uvicorn worker (or two replicas behind a proxy) corrupts projects and loses job tracking. | `src/webapp/store.py:109-115` (`self._locks: dict[str, threading.RLock]`), `src/webapp/api.py:131` (`app.state.tasks = {}`), `src/webapp/api.py:296` (`asyncio.create_task`) | Run exactly one uvicorn worker (see [Operations](operations.md)); later move jobs to a queue (arq/RQ + Redis) and locks to file locks (`fcntl`) or a DB. | L | Open |
| Session security | The `fa_session` cookie was set without `secure`, so the session could be replayed over plain HTTP once the app is exposed. | `src/webapp/api.py:181-191` (`set_cookie(..., httponly=True, samesite="lax", secure=secure)`), `src/webapp/auth.py:18` (`COOKIE_NAME = "fa_session"`) | Done: `secure` comes from `FA_COOKIE_SECURE` (default `false`). Note: the auto-detection from a public URL proposed in the review was **not** implemented — set the flag explicitly once TLS terminates in front of the app. | S | Done |
| Upload limits | `create_project` previously accepted any number of any-size files (suffix check only); a 2 GB PDF or 500 files were rendered page-by-page into `pages/`. | `src/webapp/api.py:256-261` (suffix check, then `await f.read()` for every file, then `_check_upload_limits`), `src/webapp/api.py:274-282` (`FA_MAX_FILES` 50, `FA_MAX_UPLOAD_MB` 50 → 413) | Done: per-file and per-count limits with 413. Remaining: the size is checked **after** `await file.read()`, so an oversized request is still fully buffered in memory before rejection, and the planned per-request total cap (~200 MB) was not implemented — check `UploadFile.size` / stream, add a request-total limit. | M | Partly done |
| Secrets | `OPENAI_API_KEY` is loaded from a `.env` file inside the repo directory at startup; there is no secret manager, so anyone with repo-dir access reads the key. | `src/webapp/api.py:123` (`load_dotenv(REPO_ROOT / ".env")`) | Document env-only in production, never `.env` (see [Operations](operations.md)); move to a real secret manager before multi-host deployment. | S | Open |
| Health / readiness | No health endpoint existed (now added), and there is still no readiness check of the model server before accepting `analyze`: the job is queued and only fails on its first page when the server is down or the model is not loaded. | `src/webapp/api.py:153-167` (health, public via `src/webapp/auth.py:140`), `src/webapp/api.py:285-299` (analyze queues without probing), `src/webapp/analysis.py:244-247` (first-page abort when the model never answers) | Done: `GET /api/health` → status/model URL/projects-root writability/auth. Remaining: `analyze` returns 503 with the model error when a short `GET {base_url}/models` fails. | S | Partly done |
| Job durability | Jobs do not survive a restart: a `queued`/`running` job without a live task is rewritten to `error` ("Analysis interrupted (server restarted)"); the user must run the analysis again. Same for an interrupted module re-run. | `src/webapp/api.py:222-231` (`_job`), `src/webapp/api.py:386-393` (lost refine) | Persist job intent and resume on startup. | M | Open |

## P1 — needed before customer-facing use

| Area | Finding | Evidence | Fix | Effort | Status |
|---|---|---|---|---|---|
| Project lifecycle | No project deletion or rename endpoint; projects accumulate forever under `results/webapp/projects`. The only DELETE route resets a project's pricing override. | `src/webapp/api.py` and `src/webapp/reports.py` route lists (no `@app.delete` for projects; `src/webapp/reports.py:102` is `DELETE .../pricing`), `src/webapp/api.py:130` + `config/default.yaml:55-56` (store root) | `DELETE /api/projects/{id}` that moves the directory to `trash/`, audited, with a UI button. | S | Open |
| Authorization | Authentication exists, authorization does not: every logged-in user sees every project; no roles (viewer/editor/admin). | `src/webapp/users.py:12-17` (add/remove/list only), `src/webapp/auth.py:174-177` (user entries are salt + password hash, nothing else) | `role` field in the users file; project ACLs later. | M | Open |
| Auditability | The audit log and module versions are plain per-project files; nothing is tamper-evident or exported. | `src/webapp/store.py:233-247` (`audit.jsonl` append), `src/webapp/store.py:294-295` (version history, `history-archive-*`) | Hash-chain the lines or ship them to a central log. | M | Open |
| Cost control | No per-project token/cost accounting in the web app; a large multi-view project run takes ~9 min of model calls and the UI shows nothing about usage (the legacy pipeline has token reports; the web app does not reuse them). | `src/webapp/store.py:54-59` (`Job` has no usage fields), `src/webapp/analysis.py:203-262` (`run_analysis` records per-page status only) | Record usage from the agent results into `job.json` and show it per project. | S | Open |
| Observability | Stdlib logging only: no request ids, no structured logs; tracing (`FA_TRACING_ENABLED`) is wired into the legacy pipeline settings but not into the web app's agents. | `src/webapp/analysis.py:37` (`logging.getLogger`), `src/webapp/api.py:138` (sole middleware is auth), `config/settings.py:175` (env mapping exists, unused by `src/webapp`) | Request-id middleware + JSON logs; wire `FA_TRACING_ENABLED` into the webapp agents. | M | Open |
| Backups / retention | State is plain files with no backup job and no retention for `history-archive-*` and the `pages/` PNG cache (hundreds of MB per multi-page PDF). | `src/webapp/store.py:7-10` (layout, page-render cache), `src/webapp/store.py:294-295` (archives), store root under `results/` (`src/webapp/api.py:130`) | Documented backup of `results/webapp`; cleanup task for archives and page renders older than N days. | S | Open |
| Frontend structure | One ~1.3 MB JS chunk, no code splitting; `ModuleView.tsx` is 1142 lines and `Reports.tsx` 833; 3 `set-state-in-effect` lint warnings (re-verified with `npm run lint`). | `webapp/dist/assets/index-BaU_FseK.js` (1,322,409 bytes, single chunk), `webapp/src/ModuleView.tsx`, `webapp/src/Reports.tsx`; warnings at `webapp/src/ProjectView.tsx:129`, `webapp/src/Reports.tsx:61`, `webapp/src/Reports.tsx:481` | Route-level `React.lazy`; split `ModuleView` into Review/Editor/Header components; derive the effect-set state during render. | M | Open |
| Frontend resilience | A render error used to blank the whole page (no error boundary). | `webapp/src/ErrorBoundary.tsx`, wrapped around the router at `webapp/src/App.tsx:63-116` | Done: boundary shows the error and a "Reload" button. | S | Done |
| Type gate | Pre-existing `ty` diagnostics in `src/tools`, `src/pipeline` and the legacy tests block a full-repo type check, so CI scopes the gate to the new code. | `.github/workflows/ci.yml:25-26` ("legacy pipeline has pre-existing diagnostics" → `uvx ty check src/furniture src/webapp`) | Fix the diagnostics or exclude the legacy paths via `ty.toml`, then widen the CI gate. | M | Open |
| Quote comparison | Matches by cabinet code only; an unmatched line (for example a sanitary-block cabinet priced under a label with no code) cannot be mapped manually. | `src/furniture/quote.py:92-106` (code→module lookup; `app_amount` stays `None` when nothing matches) | Manual code assignment in the quote UI. | M | Open |

## P2 — quality and robustness

| Area | Finding | Evidence | Fix | Effort | Status |
|---|---|---|---|---|---|
| Kind inference | The keyword fallback misfires on cladding words inside desk texts (seen on modules ZM01-1 and ZM03-C of the multi-view sample before the model kind was trusted); the model-provided kind now dominates. | `src/furniture/resolve.py:287-301` (keyword table in `_infer_kind`), `src/furniture/resolve.py:424` (`kind = draft.kind or _infer_kind(...)`) | Per-project "kind corrections" memory applied before the fallback. | M | Open |
| Plausibility limits | Thresholds are global, so tall wardrobes above 3200 mm or deep reception desks above 1200 mm raise false positives. | `config/furniture.yaml:37-46` (`plausibility:` block, single `height_max_mm` / `depth_max_mm`) | Per-kind limit overrides. | S | Open |
| Legend keys | `L1..Ln` keys are assigned per parsed module, so the same decor in two modules gets different keys and material-summary rows split. | `src/furniture/legend.py:67` (`key=f"L{len(materials) + 1}"`), consumed at `src/furniture/resolve.py:423` | Deduplicate by normalised material name project-wide. | S | Open |
| Group titles | `_group_title` prefers a title containing the code; for groups whose every title is a plain view label the code is shown — acceptable, but the drawing's cabinet caption should win when present. | `src/furniture/pricing.py:95-98` | Prefer the caption when one exists. | S | Open |
| Labour in valuation | Process lines are priced only when `labour_rate_per_hour` is set; the model default `None` lists them as unpriced and excludes them from totals. The shipped workspace config carries a placeholder rate nobody has validated, so out-of-the-box valuations are placeholder-priced and the suggested-markup calibration over-suggests. | `src/furniture/pricing.py:29-30` (default), `src/furniture/pricing.py:239` (rate use), `src/furniture/pricing.py:289-295` (unpriced count and oversize note only in `notes`), `config/pricing.yaml:30` (placeholder 90/h) | Show the labour rate and unpriced-part count on the valuation page, not only in notes; mark placeholder rates as unconfirmed (see [Pricing](pricing.md)). | S | Open |
| E2E coverage | The suite covers happy paths only: analyse, review confirm, edit/history/restore, valuation with pricing, project review/activity, narrow screens. Missing: the 401 flow with auth enabled, a refine that is rejected when the fake model returns a worse draft, and re-analysis archiving history. | `webapp/e2e/main-flows.spec.ts:28-125` (6 tests), fake model `webapp/e2e/fake_llm.py` | Add the three scenarios to the suite. | M | Open |
| Sample data | The two committed sample sheets carry the original title blocks and logos — accepted by decision; revisit before the repository goes public. | `samples/GLADIOGROUP_P02_PW-ZM05_12.pdf`, `samples/06_A_METROPILITAN_NRF-zabudowy meblowe_widoki.pdf` | None now; re-evaluate at public launch. | S | Open (accepted) |

## Reasoning gaps

What the design process missed while building the app; each item states the concrete correct approach.

1. **Calibration against the wrong cost base.** The suggested markup is calibrated against client quotes that include margin, installation and labour, while the app prices materials, placeholder hardware and (when set at all) a placeholder labour rate — so "suggested markup" conflates missing cost lines with margin. Correct approach: fix real rates first (process minutes × labour rate, real hardware prices), then calibrate margin on the residual.
2. **No group-level 3D.** One module per wall/run keeps hardware counts right, but the 3D view never shows the whole cabinet; a group-level composite (walls placed at angles taken from the plan) was never planned. Correct approach: a group viewer that places the group's modules using the plan-sheet geometry.
3. **No confidence gate on model-read dimensions.** Dimensions the model reads on raster pages are trusted into geometry with only a warning; no confidence threshold stops the build, so one wrong raster read yields a confident-looking cut list. Correct approach: a threshold that blocks the build or forces review of the affected dimensions.
4. **Depth defaults instead of cross-file linking.** `depth` falls back to 600 mm when the page shows no section or plan, even though plan sheets exist elsewhere in the same package; cross-file plan linking was explicitly excluded, so every wardrobe depth on the ZM05-type sheets is a guess. Correct approach: link plan sheets to elevations within a file and take depth from there, flagging modules where it is still absent.
5. **Legend parsed per page.** Legends in these packages are often on a separate legend sheet; the parser never looks at other pages of the same file. Correct approach: parse legends file-wide and share them across pages.
6. **Audit identity collapses without auth.** Audit "who" is the session username; with auth disabled everything is recorded as `system`, so the activity page carries no information in the default configuration. Correct approach: treat the activity log as meaningful only with auth on, and require auth for any multi-user deployment.
7. **Localized totals pass the quote filter.** The quote parser filters totals rows by the `razem` prefix only; a localized total row (`SUMA`, `ŁĄCZNIE`) would be counted as a quote line and distort the comparison. Correct approach: recognise totals by row shape (no cabinet code, amount equal to the sum of the priced rows above), not by prefix.
8. **No prompt regression harness.** The fake-model e2e proves the UI, not the prompts; nothing catches a prompt change that degrades interpretation. Correct approach: golden drafts per sample page committed to the repo, compared on `gap_score` in CI.
9. **Session secret inside the repo tree.** `config/.session_secret` is auto-generated inside the repo; a `git add -A` after a `.gitignore` mistake leaks it. Correct approach: production requires `FA_SESSION_SECRET` from the environment and must not write the fallback file into the repo.
10. **Fixed web port (resolved).** `make web` hardcoded port 8000 while other services already used it; the port is now a variable (`WEB_PORT ?= 8000`, `make web WEB_PORT=8010`). Done.
