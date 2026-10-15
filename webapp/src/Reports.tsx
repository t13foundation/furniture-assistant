import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, ApiError } from './api'
import { ProjectSidebar, WorkspaceSidebar } from './ProjectView'
import { materialKinds } from './types'
import type {
  QuoteComparison,
  HardwareCategory,
  MaterialKind,
  MaterialSummary,
  Pricing,
  ProcessKind,
  ProcessSummary,
  ProjectDetail,
  ProjectPricing,
  Valuation,
  ValuationGroup,
} from './types'

const processLabels: Record<ProcessKind, string> = {
  cutting: 'Rozkrój',
  edge_banding: 'Oklejanie',
  drilling: 'Wiercenie',
  assembly: 'Montaż korpusu',
  hardware_mounting: 'Montaż okuć',
  installation: 'Montaż',
}
// Keyed by the generated union, so a category added in the backend fails the type check until listed here.
const hardwareCategories = Object.keys({
  hinge: 1,
  runner: 1,
  handle: 1,
  push_open: 1,
  shelf_support: 1,
  hanging_rail: 1,
  rail_holder: 1,
  leg: 1,
  connector: 1,
  back_fixing: 1,
  lighting: 1,
  mounting: 1,
  other: 1,
} satisfies Record<HardwareCategory, 1>) as HardwareCategory[]
type Page = 'files' | 'materials' | 'processes' | 'valuation' | 'pricing'
export default function ProjectReport({ page }: { page: Exclude<Page, 'pricing'> }) {
  const { id = '' } = useParams()
  const [detail, setDetail] = useState<{ id: string; value: ProjectDetail } | null>(null)
  const [error, setError] = useState('')
  // Tagged with its page: the component stays mounted when navigating between report pages, so
  // one page's summary must never be rendered by another page's view.
  const [report, setReport] = useState<{
    id: string
    page: Page
    data: MaterialSummary | ProcessSummary | Valuation
  } | null>(null)
  const currentDetail = detail?.id === id ? detail.value : null
  useEffect(() => {
    let active = true
    let polling = false
    let timer = 0
    setError('')
    setDetail(null)
    const endpoint =
      page === 'materials' ? 'summary/materials' : page === 'processes' ? 'summary/processes' : 'valuation'
    let reportRequest = 0
    const loadReport = () => {
      if (page === 'files') return
      const request = ++reportRequest
      void api<MaterialSummary | ProcessSummary | Valuation>(
        `/api/projects/${encodeURIComponent(id)}/${endpoint}`,
      )
        .then((data) => {
          if (active && request === reportRequest) setReport({ id, page, data })
        })
        .catch((e: unknown) => {
          if (active && request === reportRequest)
            setError(e instanceof Error ? e.message : 'Could not load report')
        })
    }
    const loadDetail = async () => {
      try {
        const result = await api<ProjectDetail>(`/api/projects/${encodeURIComponent(id)}`)
        if (!active) return
        setDetail({ id, value: result })
        const running = result.job?.status === 'queued' || result.job?.status === 'running'
        if (polling && !running) {
          polling = false
          window.clearInterval(timer)
          loadReport()
        }
        return running
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : 'Could not load project')
        return false
      }
    }
    const poll = async () => {
      if (!active || !polling) return
      await loadDetail()
    }
    timer = window.setInterval(() => void poll(), 2000)
    void loadDetail().then((running) => {
      if (!active) return
      polling = Boolean(running)
      if (!polling) window.clearInterval(timer)
    })
    loadReport()
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [id, page])
  const data = report?.id === id && report.page === page ? report.data : null
  const title = (
    {
      files: 'Uploaded files',
      materials: 'Material summary',
      processes: 'Production processes',
      valuation: 'Valuation',
    } as const
  )[page]
  return (
    <div className="project-layout">
      <ProjectSidebar id={id} detail={currentDetail} />
      <main className="workspace report-page">
        <header className="report-page-heading">
          <div>
            <p>PROJECT / {currentDetail?.project.name || 'Loading…'}</p>
            <h1>{title}</h1>
          </div>
          {page === 'valuation' && (
            <>
              <Link className="button secondary" to={`/p/${encodeURIComponent(id)}/pricing`}>
                Pricing in settings
              </Link>
              <a
                className="button primary"
                href={`/api/projects/${encodeURIComponent(id)}/export/valuation.xlsx`}
              >
                Download
              </a>
            </>
          )}
        </header>
        {error && <p className="error-banner">{error}</p>}
        {page === 'files' && currentDetail && <FilesReport detail={currentDetail} />}
        {page === 'materials' && data && <MaterialsReport data={data as MaterialSummary} id={id} />}
        {page === 'processes' && data && <ProcessesReport data={data as ProcessSummary} />}
        {page === 'valuation' && data && <ValuationReport data={data as Valuation} />}
      </main>
    </div>
  )
}

function FilesReport({ detail }: { detail: ProjectDetail }) {
  const items = detail.job?.items ?? []
  return (
    <section className="report-card">
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>File</th>
              <th>Drawing type</th>
              <th>Page</th>
              <th>Status</th>
              <th>Modules</th>
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {detail.project.files.flatMap((file) => {
              const fileItems = items.filter((item) => item.file_index === file.index)
              return (fileItems.length ? fileItems : [null]).map((item, index) => (
                <tr key={`${file.index}-${index}`}>
                  <td>{file.name}</td>
                  <td>{file.drawing_type}</td>
                  <td>{item ? item.page_index + 1 : '—'}</td>
                  <td>{item?.status ?? 'pending'}</td>
                  <td>{item?.module_ids.join(', ') || '—'}</td>
                  <td>{item?.error || (item?.status === 'skipped' ? 'Skipped by file type' : '—')}</td>
                </tr>
              ))
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}
function MaterialsReport({ data, id }: { data: MaterialSummary; id: string }) {
  return (
    <>
      <div className="stat-strip">
        <Stat label="Modules" value={data.module_count} />
        <Stat label="Board types" value={data.boards.length} />
        <Stat label="Hardware items" value={data.hardware.length} />
      </div>
      {!!data.incomplete_module_ids.length && (
        <p className="notice">Incomplete modules: {data.incomplete_module_ids.join(', ')}</p>
      )}
      {!!data.boards.some((row) => row.unplaced.length) && (
        <p className="error-message">
          Parts longer than available sheets: {data.boards.flatMap((row) => row.unplaced).join(', ')}
        </p>
      )}
      <section className="report-card">
        <h2>Boards</h2>
        <DataTable
          headers={['Material', 'Thickness', 'Parts', 'Area m²', 'Sheets', 'Utilization']}
          rows={data.boards.map((row) => [
            row.material || '—',
            `${row.thickness_mm} mm`,
            row.parts,
            row.area_m2.toFixed(2),
            row.sheets,
            `${(row.utilization * 100).toFixed(1)}%`,
          ])}
        />
      </section>
      <section className="report-card">
        <h2>Edge banding</h2>
        <DataTable
          headers={['Material', 'Length', 'Modules']}
          rows={data.edges.map((row) => [
            row.material,
            `${row.length_m.toFixed(2)} m`,
            row.module_ids.join(', '),
          ])}
        />
      </section>
      <section className="report-card">
        <h2>Hardware</h2>
        <DataTable
          headers={['Category', 'Item', 'Specification', 'Qty', 'Modules']}
          rows={data.hardware.map((row) => [
            row.category,
            row.name,
            row.spec || '—',
            `${row.qty} ${row.unit}`,
            row.module_ids.join(', '),
          ])}
        />
      </section>
      <div className="header-actions report-exports">
        {(['cutlist.csv', 'cutlist.xlsx', 'sheets.svg', 'panels.dxf'] as const).map((format) => (
          <a
            className="button secondary"
            key={format}
            href={`/api/projects/${encodeURIComponent(id)}/export/${format}`}
          >
            {format.toUpperCase()}
          </a>
        ))}
      </div>
    </>
  )
}
function ProcessesReport({ data }: { data: ProcessSummary }) {
  const [tab, setTab] = useState<'recognition' | 'summary' | ProcessKind>('recognition')
  const kinds = data.totals.map((total) => total.process)
  return (
    <>
      <div className="stat-strip">
        <Stat label="Modules" value={data.rows.length} />
        <Stat label="Process kinds" value={kinds.length} />
        <Stat label="Total hours" value={(data.total_minutes / 60).toFixed(1)} />
      </div>
      <div className="tab-bar report-tabs">
        {(['recognition', 'summary', ...kinds] as const).map((key) => (
          <button className={tab === key ? 'tab-active' : ''} key={key} onClick={() => setTab(key)}>
            {key === 'recognition'
              ? 'Recognition'
              : key === 'summary'
                ? 'Summary of processes'
                : processLabels[key]}
          </button>
        ))}
      </div>
      {tab === 'recognition' ? (
        <section className="report-card">
          <h2>Which processes are on which module</h2>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Module</th>
                  {kinds.map((kind) => (
                    <th key={kind}>{processLabels[kind]}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => (
                  <tr key={row.module_id}>
                    <td>
                      <strong>{row.title}</strong>
                      <small>
                        {row.file_name} · page {row.page_index + 1}
                      </small>
                    </td>
                    {kinds.map((kind) => {
                      const cell = row.processes[kind]
                      return (
                        <td key={kind}>
                          {cell
                            ? cell.qty == null || cell.unit == null
                              ? `✓ ${cell.minutes.toFixed(1)} min`
                              : `✓ ${cell.qty} ${cell.unit} · ${cell.minutes.toFixed(1)} min`
                            : '—'}
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : tab === 'summary' ? (
        <section className="report-card">
          <h2>Summary of processes</h2>
          <DataTable
            headers={['Process', 'Lines', 'Minutes', 'Hours']}
            rows={data.totals.map((total) => [
              processLabels[total.process],
              total.lines,
              total.minutes.toFixed(1),
              (total.minutes / 60).toFixed(2),
            ])}
          />
          <p>Total: {(data.total_minutes / 60).toFixed(2)} h</p>
        </section>
      ) : (
        <section className="report-card">
          <h2>{processLabels[tab]}</h2>
          <DataTable
            headers={['Module', 'Operation', 'Qty', 'Minutes']}
            rows={data.lines
              .filter((line) => line.process === tab)
              .map((line) => [
                line.module_id,
                line.operation,
                `${line.qty} ${line.unit}`,
                line.minutes.toFixed(1),
              ])}
          />
        </section>
      )}
    </>
  )
}
function ValuationReport({ data }: { data: Valuation }) {
  const [open, setOpen] = useState<Record<ValuationGroup, boolean>>({
    boards: false,
    edges: false,
    hardware: false,
    processes: false,
  })
  const groups: ValuationGroup[] = ['boards', 'edges', 'hardware', 'processes']
  const labels: Record<ValuationGroup, string> = {
    boards: 'Materials · Boards',
    edges: 'Edges',
    hardware: 'Hardware',
    processes: 'Processes',
  }
  const currency = (amount: number) => {
    try {
      return new Intl.NumberFormat(undefined, { style: 'currency', currency: data.currency }).format(amount)
    } catch {
      return `${amount.toFixed(2)} ${data.currency}`
    }
  }
  return (
    <>
      <div className="stat-strip valuation-stats">
        {groups.map((group) => (
          <Stat key={group} label={labels[group]} value={currency(data.totals[group])} />
        ))}
        <Stat label="Total net" value={currency(data.totals.total_net)} />
        <Stat label="Markup" value={currency(data.totals.markup)} />
        <Stat label="Total with markup" value={currency(data.totals.total_with_markup)} />
      </div>
      {data.notes.map((note, index) => (
        <p className="notice" key={index}>
          {note}
        </p>
      ))}
      {groups.map((group) => (
        <section className="report-card collapsible" key={group}>
          <button
            className="collapse-heading"
            onClick={() => setOpen((current) => ({ ...current, [group]: !current[group] }))}
          >
            <strong>{labels[group]}</strong>
            <span>
              {currency(data.totals[group])} {open[group] ? '−' : '+'}
            </span>
          </button>
          {open[group] && (
            <DataTable
              headers={['Item', 'Qty', 'Unit price', 'Amount', 'Modules']}
              rows={data.lines
                .filter((line) => line.group === group)
                .map((line) => [
                  line.name,
                  `${line.qty} ${line.unit}`,
                  line.unit_price == null ? 'no price' : currency(line.unit_price),
                  line.amount == null ? '—' : currency(line.amount),
                  line.module_ids.join(', '),
                ])}
            />
          )}
        </section>
      ))}
      <section className="report-card">
        <h2>Per module</h2>
        <DataTable
          headers={['Module', 'Amount']}
          rows={data.per_module.map((row) => [`${row.title} (${row.module_id})`, currency(row.amount)])}
        />
      </section>
      <section className="report-card">
        <h2>Per cabinet</h2>
        <DataTable
          headers={['Cabinet', 'Net', 'Markup', 'Total', 'Modules']}
          rows={data.per_group.map((row) => [
            row.title || row.group_id,
            currency(row.amount),
            currency(row.markup),
            currency(row.total_with_markup),
            row.module_ids.join(', '),
          ])}
        />
      </section>
    </>
  )
}
/** Workspace-wide pricing; inside a project (``/p/:id/pricing``) it keeps that project's sidebar. */
export function PricingSettings() {
  const { id } = useParams()
  const [detail, setDetail] = useState<ProjectDetail | null>(null)
  const [pricing, setPricing] = useState<Pricing | null>(null)
  const [inherited, setInherited] = useState(false)
  // Tagged with its project so another project's comparison is never shown while the next one loads.
  const [quoteState, setQuoteState] = useState<{ projectId: string; value: QuoteComparison } | null>(null)
  const quote = quoteState && quoteState.projectId === id ? quoteState.value : null
  const [quoteError, setQuoteError] = useState('')
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const [quoteFile, setQuoteFile] = useState<File | null>(null)
  useEffect(() => {
    const endpoint = id ? `/api/projects/${encodeURIComponent(id)}/pricing` : '/api/pricing'
    void api<Pricing | ProjectPricing>(endpoint)
      .then((result) => {
        if (id) {
          const projectPricing = result as ProjectPricing
          setPricing(projectPricing.pricing)
          setInherited(projectPricing.inherited)
        } else setPricing(result as Pricing)
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Could not load pricing'))
  }, [id])
  useEffect(() => {
    if (!id) return
    let active = true
    void api<QuoteComparison>(`/api/projects/${encodeURIComponent(id)}/quote`)
      .then((value) => {
        if (active) setQuoteState({ projectId: id, value })
      })
      .catch((e: unknown) => {
        // 404: no client quote uploaded yet.
        if (active && !(e instanceof ApiError && e.status === 404))
          setQuoteError(e instanceof Error ? e.message : 'Could not load the client quote')
      })
    return () => {
      active = false
    }
  }, [id])
  useEffect(() => {
    setDetail(null)
    if (id)
      void api<ProjectDetail>(`/api/projects/${encodeURIComponent(id)}`)
        .then(setDetail)
        .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Could not load project'))
  }, [id])
  const sidebar = id ? <ProjectSidebar id={id} detail={detail} /> : <WorkspaceSidebar />
  if (!pricing)
    return (
      <div className="project-layout">
        {sidebar}
        <main className="workspace pricing-page">
          <h1>Pricing settings</h1>
          {error || 'Loading pricing…'}
        </main>
      </div>
    )
  const updateBoard = (index: number, patch: Partial<Pricing['boards'][number]>) =>
    setPricing(
      (current) =>
        current && {
          ...current,
          boards: current.boards.map((row, i) => (i === index ? { ...row, ...patch } : row)),
        },
    )
  const save = async () => {
    setError('')
    setSaved(false)
    if (!/^[A-Z]{3}$/.test(pricing.currency)) {
      setError('Currency must be a 3-letter uppercase ISO code (for example, EUR).')
      return
    }
    try {
      const result = await api<Pricing | ProjectPricing>(
        id ? `/api/projects/${encodeURIComponent(id)}/pricing` : '/api/pricing',
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(pricing),
        },
      )
      if (id) {
        const projectPricing = result as ProjectPricing
        setPricing(projectPricing.pricing)
        setInherited(projectPricing.inherited)
      } else setPricing(result as Pricing)
      setSaved(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save pricing')
    }
  }
  const uploadQuote = async () => {
    if (!id || !quoteFile) return
    setQuoteError('')
    try {
      const body = new FormData()
      body.append('file', quoteFile)
      const value = await api<QuoteComparison>(`/api/projects/${encodeURIComponent(id)}/quote`, {
        method: 'POST',
        body,
      })
      setQuoteState({ projectId: id, value })
    } catch (e) {
      setQuoteError(e instanceof Error ? e.message : 'Could not compare quote')
    }
  }
  const saveQuoteAssignment = async (
    lineIndex: number,
    label: string,
    moduleIds: string[],
    ignored: boolean,
  ) => {
    if (!id) return
    setQuoteError('')
    try {
      const value = await api<QuoteComparison>(`/api/projects/${encodeURIComponent(id)}/quote/assignments`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ line_index: lineIndex, label, module_ids: moduleIds, ignored }),
      })
      setQuoteState({ projectId: id, value })
    } catch (e) {
      setQuoteError(e instanceof Error ? e.message : 'Could not save quote assignment')
    }
  }
  const revertPricing = async () => {
    if (!id) return
    setError('')
    try {
      const result = await api<ProjectPricing>(`/api/projects/${encodeURIComponent(id)}/pricing`, {
        method: 'DELETE',
      })
      setPricing(result.pricing)
      setInherited(result.inherited)
      setSaved(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not revert pricing')
    }
  }
  return (
    <div className="project-layout">
      {sidebar}
      <main className="workspace pricing-page">
        <header className="report-page-heading">
          <div>
            <p>{detail ? `PROJECT / ${detail.project.name} / SETTINGS` : 'SETTINGS'}</p>
            <h1>Pricing</h1>
          </div>
          {id && (
            <Link to={`/p/${encodeURIComponent(id)}/valuation`} className="button secondary">
              Back to valuation
            </Link>
          )}
        </header>
        {id && inherited && (
          <p className="notice">Using workspace pricing. Save to customize pricing for this project.</p>
        )}
        {id && !inherited && (
          <p className="notice">
            Customized pricing for this project.{' '}
            <button className="button secondary" onClick={() => void revertPricing()}>
              Revert to workspace
            </button>
          </p>
        )}
        {id && (
          <section className="report-card">
            <label>
              Markup percent
              <input
                type="number"
                min="0"
                step="0.1"
                value={pricing.markup_percent}
                onChange={(event) => setPricing({ ...pricing, markup_percent: Number(event.target.value) })}
              />
            </label>
          </section>
        )}
        <section className="report-card">
          <label>
            Currency
            <input
              value={pricing.currency}
              onChange={(event) => setPricing({ ...pricing, currency: event.target.value })}
            />
          </label>
          <h2>Board prices per m²</h2>
          {pricing.boards.map((row, index) => (
            <div className="pricing-row" key={index}>
              <label>
                Match
                <input
                  value={row.match}
                  onChange={(event) => updateBoard(index, { match: event.target.value })}
                />
              </label>
              <label>
                Material kind
                <select
                  value={row.kind ?? ''}
                  onChange={(event) =>
                    updateBoard(index, {
                      kind: event.target.value ? (event.target.value as MaterialKind) : null,
                    })
                  }
                >
                  <option value="">Any</option>
                  {materialKinds.map((kind) => (
                    <option key={kind}>{kind}</option>
                  ))}
                </select>
              </label>
              <label>
                Thickness mm
                <input
                  type="number"
                  value={row.thickness_mm ?? ''}
                  onChange={(event) =>
                    updateBoard(index, {
                      thickness_mm: event.target.value === '' ? null : Number(event.target.value),
                    })
                  }
                />
              </label>
              <label>
                Price / m²
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  value={row.price_per_m2}
                  onChange={(event) => updateBoard(index, { price_per_m2: Number(event.target.value) })}
                />
              </label>
              <button
                className="button secondary"
                onClick={() =>
                  setPricing({ ...pricing, boards: pricing.boards.filter((_, i) => i !== index) })
                }
              >
                Remove
              </button>
            </div>
          ))}
          <button
            className="button secondary"
            onClick={() =>
              setPricing({
                ...pricing,
                boards: [...pricing.boards, { match: '', kind: null, thickness_mm: null, price_per_m2: 0 }],
              })
            }
          >
            Add board price
          </button>
        </section>
        <section className="report-card">
          <h2>Edge banding</h2>
          <label>
            Price per metre
            <input
              type="number"
              min="0"
              step="0.01"
              value={pricing.edge_banding_per_m ?? ''}
              onChange={(event) =>
                setPricing({
                  ...pricing,
                  edge_banding_per_m: event.target.value === '' ? null : Number(event.target.value),
                })
              }
            />
          </label>
        </section>
        <section className="report-card">
          <h2>Hardware price per unit</h2>
          {hardwareCategories.map((category) => (
            <label className="inline-field" key={category}>
              {category}
              <input
                type="number"
                min="0"
                step="0.01"
                value={pricing.hardware[category] ?? ''}
                onChange={(event) => {
                  const { [category]: _previous, ...hardware } = pricing.hardware
                  setPricing({
                    ...pricing,
                    hardware:
                      event.target.value === ''
                        ? hardware
                        : { ...hardware, [category]: Number(event.target.value) },
                  })
                }}
              />
            </label>
          ))}
        </section>
        <section className="report-card">
          <h2>Labour</h2>
          <label>
            Rate per hour
            <input
              type="number"
              min="0"
              step="0.01"
              value={pricing.labour_rate_per_hour ?? ''}
              onChange={(event) =>
                setPricing({
                  ...pricing,
                  labour_rate_per_hour: event.target.value === '' ? null : Number(event.target.value),
                })
              }
            />
          </label>
        </section>
        {error && <p className="error-message">{error}</p>}
        {saved && <p className="notice">Pricing saved.</p>}
        <button className="button primary" onClick={() => void save()}>
          Save pricing
        </button>
        {id && (
          <section className="report-card">
            <h2>Compare with client quote</h2>
            <label>
              Quote workbook (.xlsx)
              <input
                type="file"
                accept=".xlsx"
                onChange={(event) => setQuoteFile(event.target.files?.[0] ?? null)}
              />
            </label>
            <button className="button secondary" disabled={!quoteFile} onClick={() => void uploadQuote()}>
              Compare quote
            </button>
            {quoteError && (
              <p role="alert" className="error-message">
                {quoteError}
              </p>
            )}
            {quote && (
              <>
                <p>
                  Quote total {quote.total_quote.toFixed(2)} · App total {quote.total_app.toFixed(2)} ·
                  Suggested markup {quote.suggested_markup_percent.toFixed(1)}% ({quote.suggestion_basis})
                </p>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        {['Code', 'Quote item', 'Quote', 'App', 'Ratio', 'Modules', 'Assign modules', 'Ignore'].map(
                          (header) => <th key={header}>{header}</th>,
                        )}
                      </tr>
                    </thead>
                    <tbody>
                      {quote.comparison.map((row) => {
                        const ignored = row.match === 'ignored'
                        const selected = row.match === 'manual' ? row.module_ids : []
                        return (
                          <tr key={row.line_index}>
                            <td>{row.codes.join(', ') || '—'}</td>
                            <td>{row.label}</td>
                            <td>{row.quote_amount.toFixed(2)}</td>
                            <td>{row.app_amount?.toFixed(2) ?? '—'}</td>
                            <td>{row.ratio == null ? '—' : `×${row.ratio.toFixed(2)}`}</td>
                            <td>
                              {row.module_ids.length
                                ? `${row.module_ids.join(', ')}${row.complete ? '' : ' (incomplete or partly unpriced)'}`
                                : row.match === 'ignored' ? 'ignored' : 'no matching module'}
                            </td>
                            <td>
                              <select
                                aria-label={`Modules for ${row.label}`}
                                multiple
                                value={selected}
                                disabled={ignored || !detail?.modules.length}
                                onChange={(event) =>
                                  void saveQuoteAssignment(
                                    row.line_index,
                                    row.label,
                                    Array.from(event.currentTarget.selectedOptions, (option) => option.value),
                                    false,
                                  )
                                }
                              >
                                {(detail?.modules ?? []).map((module) => (
                                  <option key={module.module_id} value={module.module_id}>
                                    {module.group_id} · {module.title || module.module_id}
                                  </option>
                                ))}
                              </select>
                            </td>
                            <td>
                              <input
                                type="checkbox"
                                aria-label={`Ignore ${row.label}`}
                                checked={ignored}
                                onChange={(event) =>
                                  void saveQuoteAssignment(
                                    row.line_index,
                                    row.label,
                                    event.currentTarget.checked ? [] : selected,
                                    event.currentTarget.checked,
                                  )
                                }
                              />
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
                <button
                  className="button secondary"
                  onClick={() => setPricing({ ...pricing, markup_percent: quote.suggested_markup_percent })}
                >
                  Apply suggested markup
                </button>
              </>
            )}
          </section>
        )}
      </main>
    </div>
  )
}
function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}
function DataTable({ headers, rows }: { headers: string[]; rows: (string | number)[][] }) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {headers.map((header) => (
              <th key={header}>{header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map((row, i) => (
              <tr key={i}>
                {row.map((value, j) => (
                  <td key={j}>{value}</td>
                ))}
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={headers.length}>No data</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
