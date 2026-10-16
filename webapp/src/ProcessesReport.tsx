import { useState } from 'react'
import type { ProcessKind, ProcessSummary } from './types'
import { DataTable, Stat } from './ReportShared'

const processLabels: Record<ProcessKind, string> = {
  cutting: 'Rozkrój',
  edge_banding: 'Oklejanie',
  drilling: 'Wiercenie',
  assembly: 'Montaż korpusu',
  hardware_mounting: 'Montaż okuć',
  installation: 'Montaż',
}

export default function ProcessesReport({ data }: { data: ProcessSummary }) {
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
