# Furniture Assistant

Furniture Assistant turns a furniture drawing package into manufacturing data. Upload the
architectural PDFs of a project (elevations, plans, sections); the app splits them into
**modules** (one cabinet or wall run per drawing view), resolves every dimension to a
source, builds a **3D model** and a **cut list**, derives **hardware** and **production
processes**, and rolls everything up into a per-project **valuation** and a comparison
with the client's quote. Everything the drawing does not state — or states twice,
contradictorily — lands in a review workflow where you confirm, correct or override it,
and every change is versioned.

The heavy lifting is split deliberately: dimension marks and the material legend are read
**deterministically from the PDF text layer** (no model, exact values); a vision model
only assigns roles to the numbered marks (module width, column widths, cell heights,
fronts, cabinet kind and code). See [Architecture](architecture.md) and
[Design decisions](decisions.md).

## 60-second quick start

```bash
make install       # uv sync (Python deps)
make web-build     # build the frontend into webapp/dist (needs Node)
make web           # API + UI on http://127.0.0.1:8000
```

Open `http://127.0.0.1:8000`, create a project, upload a drawing package and press
"Run analysis". The analysis calls a vision model: start LM Studio with its
OpenAI-compatible server on `http://localhost:1234/v1` first (or point `FA_API_BASE` at
another OpenAI-compatible endpoint — see [Configuration](configuration.md)). Use
`make web WEB_PORT=8010` when 8000 is taken.

A 54-second walkthrough of the app is in **[app-demo.mp4](app-demo.mp4)**
(1280×720, H.264, ~11 MB).

## Sample drawings

Two committed sample sheets (both in centimetres) are used by the tests and the demo:

- `samples/GLADIOGROUP_P02_PW-ZM05_12.pdf` — the ZM05 sample sheet
- `samples/06_A_METROPILITAN_NRF-zabudowy meblowe_widoki.pdf` — the multi-view sample
  sheet (several elevations per cabinet)

Further project packages live in the gitignored `sample-data/` directory; see
[Development](development.md) for the policy.

## Where to go next

| Page | Content |
|------|---------|
| [Architecture](architecture.md) | Pipeline, module map, analysis-job sequence, project directory layout |
| [Data model](data-model.md) | `FurnitureModule`, marks and chains, issue codes, questions, overrides, provenance |
| [API](api.md) | Every endpoint with request/response models and auth |
| [Review workflow](review-workflow.md) | Attention badges, review items, confirm/edit, "Ask the model again", history |
| [Pricing and quotes](pricing.md) | Rates, valuation lines, client-quote import and markup calibration |
| [Configuration](configuration.md) | `config/*.yaml` and the `FA_*` environment variables |
| [Operations](operations.md) | Auth and users, sessions, reverse proxy, backups, model server options |
| [Development](development.md) | Repo layout, make targets, test layers, branching, release checklist |
| [Design decisions](decisions.md) | Short ADRs covering the core trade-offs |
| [Production readiness](production-readiness.md) | Review of what blocks or degrades real deployment |
