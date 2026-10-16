import { useState } from 'react'
import type { Valuation, ValuationGroup } from './types'
import { DataTable, Stat } from './ReportShared'

export default function ValuationReport({ data }: { data: Valuation }) {
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
