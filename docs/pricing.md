# Pricing and quotes

Valuation turns the reviewed modules into a cost: material summary × rates (+ process labour) with a workspace-wide or per-project markup, and an optional client-quote comparison that suggests the markup. Rates live in `config/pricing.yaml` (see [Configuration](configuration.md)); the engine is `src/furniture/pricing.py`, the quote parser `src/furniture/quote.py`, the endpoints `src/webapp/reports.py` (see [API](api.md)).

!!! warning
    The shipped `config/pricing.yaml` contains **placeholder rates only** — replace them with current supplier and workshop prices before quoting. Valuation notes repeat this on every report.

## Workspace pricing

`Pricing` keys (workspace defaults in `config/pricing.yaml`, editable via *Pricing settings* or `PUT /api/pricing`):

| Key | Type | Meaning |
|---|---|---|
| `currency` | ISO 4217 code | default `PLN`; used for all amounts |
| `boards` | list | `match`, `kind`, `thickness_mm` (both optional filters), `price_per_m2` |
| `edge_banding_per_m` | float / null | per running metre of edge banding; null = unpriced |
| `hardware` | map | price per unit, keyed by hardware category (`hinge`, `runner`, `handle`, `push_open`, `shelf_support`, `hanging_rail`, `rail_holder`, `leg`, `connector`, `back_fixing`, `lighting`, `mounting`, `other`) |
| `labour_rate_per_hour` | float / null | process minutes × rate; **null = labour not priced** (process lines are listed as unpriced, not as free) |
| `markup_percent` | float | applied to net totals |
| `updated_at` | timestamp | managed by the app on save |

Board price for a material row is the first `boards` entry that matches: (1) `match` — a case-insensitive substring of the material name, constrained by `kind`/`thickness_mm` when set — then (2) `kind` + `thickness_mm`, then (3) `thickness_mm` alone. No match → the line is unpriced.

## Project pricing and markup

Each project can override the workspace rates (`GET/PUT/DELETE /api/projects/{id}/pricing`); the response's `inherited` flag tells whether the project uses its own `pricing.yaml` (stored in the project directory) or the workspace one, and `DELETE` reverts to the workspace. Both changes are audited as `pricing_change`. The markup applies uniformly: `markup = total_net × markup_percent / 100`, shown as Total net / Markup / Total with markup and per cabinet.

## Valuation

`GET /api/projects/{id}/valuation` (and the *Valuation* tab, exportable as `valuation.xlsx`) builds one line per material/process row, grouped `boards`, `edges`, `hardware`, `processes`:

| Group | Quantity priced | Unit price |
|---|---|---|
| boards | **bought area** = nested sheet count × full stock-sheet area (`sheet_length_mm × sheet_width_mm` from `config/furniture.yaml`) — offcuts are paid for | matched `boards` rate per m² |
| edges | total edge length in metres | `edge_banding_per_m` |
| hardware | quantity per item | `hardware[category]` |
| processes | minutes ÷ 60 | `labour_rate_per_hour` |

Unpriced rules — anything without a rate gets `amount: null`, is counted in `unpriced`, **excluded from all totals**, and called out in the notes; it is never silently free. The same applies to process lines when `labour_rate_per_hour` is null. Parts longer or wider than the stock sheet cannot be nested: they are excluded from the sheet count and **not billed**, and listed in the notes (the generated notes are in Polish, client-facing). Sheets that would contain only unplaceable parts do not count as sheets to buy.

Shared rows are split across modules by quantity share (cut area for boards, length for edges, unit count for hardware; processes belong to their module), producing `per_module` net amounts; `per_group` rolls them up by cabinet group.

## Client quote import

Upload the client's quote workbook (`.xlsx`) on the *Pricing settings* page (`POST /api/projects/{id}/quote`, stored in the project as `quote.xlsx` + parsed `quote.json`, re-compared on every view; not `.xlsx` → 400, unreadable → 422). Every sheet is scanned row by row. A workbook with no priced rows is still saved and returns an empty comparison.

- **Label** = the first text cell containing a cabinet code; **amount** = the last numeric cell in the row. Rows like `ZM 01.1 – zabudowa, 37650` are the expected shape.
- Rows whose label starts with `RAZEM` (case-insensitive) are skipped as totals — only that prefix is filtered.
- **Codes** are `2–4 letters + 2–3 digits + optional `.N``: `ZM05`, `ZM 01.1`, `ZK-04`. Matching ignores spacing and punctuation (`ZM 01.1` ≡ `ZM01.1`). A two-letter prefix with the letter `O` typed where a zero belongs is corrected: `ZKO2` → `ZK02`.
- **Multi-code rows** are supported: `ZM09, ZM10, ZM11, ZM12 – lamelles` prices those cabinets together; the app amount is the sum over the matched modules.
- Lines match modules by normalized `group_id` or `module_id`. An unmatched line remains in the quote total until assigned or ignored.

## Manual line assignments

The comparison table lets you assign any quote line to one or more project modules, or mark it **Ignore** (for totals and non-cabinet rows). A manual assignment replaces code matching; clearing the selection restores automatic matching. A module can be assigned to only one quote line; attempting to reuse it returns 409. Ignored lines are excluded from quote totals and suggested markup. Assignments are stored with the parsed lines in `quote.json`; re-import carries them over only when label and amount uniquely identify a row in both workbooks, so ambiguous duplicate rows lose their old decisions. Changes are audited.

## Comparison and suggested markup

Each quote line is compared against the app's `per_module` amounts: quote amount, app amount (**net, before markup**), ratio, and `complete` — true only when every matched module is fully dimensioned (`status: ok`) and none of its valuation lines is unpriced.

**Suggested markup** = (median quote/app ratio − 1) × 100 over the *complete and priced* cabinets; when there are none, over all priced matches ("median quote/app ratio of N complete cabinets" vs "…matched, incomplete" — the basis string says which). The median is deliberate: one badly read cabinet cannot set the markup. *Apply suggested markup* copies the value into `markup_percent`.

Calibration caveats:

- The client quote includes margin, labour, installation and delivery; the app prices materials, edge banding, hardware and (only if a rate is set) labour. Until labour is priced (`labour_rate_per_hour`), the suggested markup conflates "missing cost lines" with real margin — set the rate first, then calibrate.
- Comparison amounts are net of markup; boards are priced as whole bought sheets (offcuts included), so utilization losses sit on the app side of the ratio.
- Oversize parts are not billed at all (see above), which understates the app side when such parts exist.
