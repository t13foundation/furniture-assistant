# Design decisions

Short ADRs for the core trade-offs of the furniture domain and the web app. All have
status **Accepted**; their anchors in the code are named so they can be re-checked.

## Deterministic marks from the PDF text layer

**Context.** Vision models misread numbers on dense architectural drawings, and every
misread silently corrupts the geometry. Architectural PDFs print every dimension as a
text object with an exact position, so the numbers are already in the file.

**Decision.** `extract_marks` (`src/furniture/marks.py`) reads the numeric text objects
deterministically — values, units (cm/mm detection), orientation, chains — and the
interpretation agent (`src/furniture/interpret.py`) only assigns roles to the
numbered marks shown on an annotated page image.

**Consequence.** Values that the PDF states are exact and free of model error, prompts
stay small, and `CHAIN_MISMATCH`/`VIEW_MISMATCH` cross-checks compare printed numbers.
The model cannot introduce a wrong value, only a wrong role. Raster pages (scans, PNG,
DWG renders) have no text layer: their dimensions come from the model and are flagged
`UNVERIFIED_DIMENSIONS` (see [Data model](data-model.md#issues)).

## Overrides as the single feedback mechanism

**Context.** Review answers, editor edits and module-header changes all mutate modules,
and a later model re-run must not destroy that user work. Two write paths (direct edits
plus a separate answer store) would drift apart.

**Decision.** Every user input is an `Override` stored on `ModuleModel.overrides`; the
module itself is always recomputed as `resolve(draft)` with overrides applied
(`apply_overrides` / `assemble_module`, `src/furniture/resolve.py`). There is no
direct field edit anywhere.

**Consequence.** One mechanism to learn, audit and version; "Ask the model again"
merges the new draft under the user's latest overrides; overridden values carry
`user` provenance. The cost is that every change re-resolves and re-builds the module —
acceptable because building is pure rule-based computation.

## One module per wall/run with `group_id`

**Context.** One cabinet is usually drawn several times: closed and open fronts,
interior, side or section views; an L- or U-shaped built-in is drawn wall by wall.
Merging those views into a single module would mix scales and positions and break
hardware counts.

**Decision.** Each drawn run/wall is its own module. The shared cabinet code is stored
as `group_id` — provided by the model or derived from the module id/title
(`_group_id`, e.g. `ZM03-A/B/C` → `ZM03`) — and the sidebar groups modules by it,
valuation totals per cabinet.

**Consequence.** Hardware and cut lists stay correct per run, and repeated dimensions
are cross-checked between views (`VIEW_MISMATCH`) instead of being merged. The 3D view
shows one run at a time; there is no group-level composite view (listed as a known gap
in [Production readiness](production-readiness.md)).

## Fresh version per non-identical save, archive on re-analysis

**Context.** Editing needs who/when/what history and safe restore, but restoring a
version saved before a re-analysis would silently resurrect stale geometry from an old
drawing read.

**Decision.** `ProjectStore.save_module` (`src/webapp/store.py`) writes a monotonic
numbered version file per module whenever the content changes — an identical save adds
no version — and a re-analysis archives the whole history directory to
`modules/history-archive-<timestamp>/` (kept on disk, hidden from the UI) before the
new modules are written.

**Consequence.** Cheap, tamper-resistant history with a stable `index.json` per
module, and restores that never cross analysis runs. The archives and the page-render
cache grow on disk without automatic cleanup (backup/retention is covered in
[Operations](operations.md)).

## Generated TypeScript types from OpenAPI

**Context.** The frontend consumes the same Pydantic models the API serves;
hand-written TypeScript copies of those shapes drift on every backend change and the
breakage surfaces at runtime.

**Decision.** `make web-types` exports the backend's OpenAPI schema
(`src/webapp/openapi.py` → `webapp/openapi.json`) and generates
`webapp/src/api-types.ts` with openapi-typescript; `webapp/src/types.ts` only aliases
the generated shapes, and `tests/test_webapp_openapi.py` fails when the committed
schema is stale.

**Consequence.** One source of truth; type errors surface at `tsc` time and the
`types-drift` CI job makes regeneration explicit in every PR that touches the API. The
cost is one extra generation step per API change and a large generated file.

## Fake-model end-to-end tests

**Context.** End-to-end coverage of upload → analysis → review → edit → history →
pricing against a real vision model is slow, costs money and flakes with server state;
CI has no secrets.

**Decision.** The Playwright suite (`webapp/e2e/`, `make web-e2e`) runs the real
backend and the built UI against a deterministic OpenAI-compatible fake model
(`webapp/e2e/fake_llm.py`) that answers with a fixed page interpretation — no network,
no cost, no flakiness from model behaviour.

**Consequence.** The whole user journey is regression-tested in CI, including
refinement, restore and valuation. What it does not cover is the prompts themselves:
the fake answers fixed drafts, so prompt regressions need golden-draft comparisons
(listed as a gap in [Production readiness](production-readiness.md)).

## Auth optional until a user exists

**Context.** A single-workshop tool should start with zero configuration, but the same
deployment may later need accounts and an audit trail with real user names.

**Decision.** The auth middleware (`src/webapp/auth.py`) is a pass-through while the
users file has no entries (`auth_enabled()` is just `bool(load_users())`); adding the
first user with `uv run python -m src.webapp.users add <name>` switches every `/api`
route to require a signed `fa_session` cookie. Login, logout, `me` and `health` stay
public (`PUBLIC_API_PATHS`).

**Consequence.** No setup friction for local use and one flag-free switch to enforced
auth, with no route changes. Until that switch, audit entries and version authors are
`system`, which limits the value of the activity page — and the session cookie still
needs `FA_COOKIE_SECURE` behind TLS (see [Operations](operations.md)).
