import type { Panel } from './types'
interface Props {
  rows: Panel[]
  solidsCount: number
  onHover: (row: Panel | null) => void
  onSelect: (partId: string) => void
  activePartIds: string[]
  reviewPartIds: string[]
  sourceByPart: Record<string, string>
}
export default function PartsList({
  rows,
  solidsCount,
  onHover,
  onSelect,
  activePartIds,
  reviewPartIds,
  sourceByPart,
}: Props) {
  return (
    <section className="parts-panel">
      <div className="parts-heading">
        <div>
          <strong>Parts list</strong>
          <span className="pane-subtitle">
            {rows.length} items · {solidsCount} solids
          </span>
        </div>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>PART</th>
              <th>QTY</th>
              <th>MATERIAL</th>
              <th>L × W × T</th>
              <th>EDGE</th>
              <th>m²</th>
              <th>SOURCE</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => {
              const ids = row.notes?.split(',').filter(Boolean) ?? [row.part_id]
              const active = ids.some((id) => activePartIds.includes(id))
              const edgeCount = Object.values(row.edge_banding).filter(Boolean).length
              const source = [
                ...new Set(ids.map((id) => sourceByPart[id] ?? 'rule').filter((value) => value !== 'rule')),
              ].join(', ')
              return (
                <tr
                  key={`${row.part_id}-${index}`}
                  className={`${active ? 'row-highlighted' : ''} ${ids.some((id) => reviewPartIds.includes(id)) ? 'row-review' : ''}`}
                  onMouseEnter={() => onHover(row)}
                  onMouseLeave={() => onHover(null)}
                  onClick={() => onSelect(ids[0] ?? row.part_id)}
                >
                  <td>
                    <button
                      className="part-select"
                      onClick={(event) => {
                        event.stopPropagation()
                        onSelect(ids[0] ?? row.part_id)
                      }}
                    >
                      {row.name}
                    </button>
                    {ids.some((id) => reviewPartIds.includes(id)) && (
                      <span className="review-row-badge">Review</span>
                    )}
                    <small>{row.part_id}</small>
                  </td>
                  <td>{row.qty}</td>
                  <td>{row.board_type ?? row.material_code ?? '—'}</td>
                  <td>
                    {row.length_mm} × {row.width_mm} × {row.thickness_mm}
                  </td>
                  <td>{edgeCount ? `${edgeCount} sides` : '—'}</td>
                  <td>{(((row.length_mm ?? 0) * (row.width_mm ?? 0) * row.qty) / 1_000_000).toFixed(2)}</td>
                  <td>{source || 'rule'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}
