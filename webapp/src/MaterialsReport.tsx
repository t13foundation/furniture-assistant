import type { MaterialSummary } from './types'
import { DataTable, Stat } from './ReportShared'

export default function MaterialsReport({ data, id }: { data: MaterialSummary; id: string }) {
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
