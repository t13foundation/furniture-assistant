# Furniture Assistant

Turns architectural furniture drawings into parametric 3D cabinets, cut lists, hardware and process
lists and a valuation for CNC manufacturing. Input is the package a furniture maker receives: elevation /
build-in PDFs (vector, with a text layer), plans, renders and scans (PNG/JPG), CAD (`.dwg`; `.p3d` needs an
export) and client quote spreadsheets (`.xlsx`).

**Demo:** [docs/app-demo.mp4](docs/app-demo.mp4) — a 52-second walkthrough of the two sample sheets in
[`samples/`](samples/) (1280×720, H.264).

## Quick start

```bash
make install          # uv sync (Python 3.13)
make web-build        # build the frontend (webapp/, needs Node 22+)
make web              # API + UI on http://127.0.0.1:8000 (make web WEB_PORT=8010 for another port)
```

Upload `samples/GLADIOGROUP_P02_PW-ZM05_12.pdf`, click **Run analysis** and open a module. The analysis needs
an OpenAI-compatible model server: LM Studio on `http://localhost:1234/v1` by default, or
`FA_API_BASE=https://api.openai.com/v1` with `OPENAI_API_KEY` in the environment.

```bash
make test             # pytest
make check            # ruff + ty
make web-ui           # frontend dev server on :5173 (proxies /api to :8000)
make web-types        # backend OpenAPI → webapp/openapi.json → webapp/src/api-types.ts (after API changes)
make web-e2e          # Playwright against the real backend, built UI and a fake model
make docs             # documentation site on :8001 (make docs-build = strict build)
```

## Web app in short

Every page of an uploaded drawing goes through: dimension marks and chains from the PDF text layer (no
model) → material legend → an interpretation agent that only assigns roles to marks (module size, columns,
cells, fronts, materials, cabinet kind and code) → resolution with per-dimension provenance, plausibility
and cross-view checks → open questions and one automatic model re-run → construction, hardware and
processes per cabinet kind → 3D view, parts list, exports (CSV, XLSX, SVG sheet layout, DXF, glTF).
Users answer open questions in a Review tab; answers and edits are stored as overrides, every save is a
version, and project pages summarise materials, processes and valuation (with project pricing, markup
and client-quote comparison). Authentication is off until the first user is added.

Documentation (`docs/`, served with `make docs`):
[architecture](docs/architecture.md) ·
[data model](docs/data-model.md) ·
[API](docs/api.md) ·
[review workflow](docs/review-workflow.md) ·
[pricing and quotes](docs/pricing.md) ·
[configuration](docs/configuration.md) ·
[operations](docs/operations.md) ·
[development and branching](docs/development.md) ·
[design decisions](docs/decisions.md) ·
[production readiness](docs/production-readiness.md).

## Legacy batch pipeline

`main.py` runs the earlier agentic extraction (explore → summarise → validate → geometry checks) over
drawing files and writes JSON/XLSX cutting lists to `results/`:

```bash
make single                       # first drawing under sample-data/
make run                          # every drawing under sample-data/
make run-project PROJECT="<dir>"  # every drawing of sample-data/<dir>
make benchmark                    # multi-model comparison
make evaluate                     # compare results/ with ground truth
```

## Data

- `samples/` — two committed sample sheets used by tests and the demo.
- `sample-data/<PROJECT>/` — further project packages; git-ignored, never committed.
- `results/` — pipeline output and web app projects (`results/webapp/projects/`); git-ignored.
- Configuration: `config/default.yaml` → `FA_*` environment variables → CLI arguments;
  construction rules in `config/furniture.yaml`, prices in `config/pricing.yaml`.

## Contributing

Branches: `feat/<topic>` / `fix/<topic>` → PR into `dev` → `stage` → `main` (tagged releases); CI runs
lint, types, tests, frontend build, OpenAPI drift and Playwright. See
[docs/development.md](docs/development.md).

## License

See [LICENSE](LICENSE).
