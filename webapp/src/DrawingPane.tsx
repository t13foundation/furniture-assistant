import { useState } from 'react'
import type { BoundingBox, FurnitureModule } from './types'

const FOCUS_MARGIN = 0.06
const FULL_PAGE: BoundingBox = { x_min: 0, y_min: 0, x_max: 1, y_max: 1, label: '' }

function expandBox(box: BoundingBox, margin: number): BoundingBox {
  const x_min = Math.max(0, box.x_min - margin)
  const y_min = Math.max(0, box.y_min - margin)
  const x_max = Math.min(1, box.x_max + margin)
  const y_max = Math.min(1, box.y_max + margin)
  return x_max > x_min && y_max > y_min ? { x_min, y_min, x_max, y_max, label: box.label } : FULL_PAGE
}

interface Props {
  module: FurnitureModule
  fileIndex: number
  projectId: string
  highlightedMarkIds: string[]
  picking?: boolean
  onPickMark?: (markId: string) => void
  onHoverMark?: (markId: string | null) => void
}

export default function DrawingPane({
  module,
  fileIndex,
  projectId,
  highlightedMarkIds,
  picking = false,
  onPickMark,
  onHoverMark,
}: Props) {
  const [zoom, setZoom] = useState(1)
  const [showMarks, setShowMarks] = useState(true)
  const [showIssues, setShowIssues] = useState(false)
  const [focus, setFocus] = useState(true)
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null)
  const activeMarkIds = new Set(highlightedMarkIds)
  const errors = module.issues.filter((issue) => issue.severity === 'error')
  const sourceIds = new Set([
    ...Object.values(module.source_mark_ids).filter((id): id is string => Boolean(id)),
    ...module.columns.flatMap((column) => [
      ...column.source_mark_ids,
      ...column.cells.flatMap((cell) => cell.source_mark_ids),
    ]),
  ])
  const allViews = [{ bbox: module.view_bbox, kind: 'primary view', label: '' }, ...module.views]
  const bounds = {
    x_min: Math.min(...allViews.map((view) => view.bbox.x_min)),
    y_min: Math.min(...allViews.map((view) => view.bbox.y_min)),
    x_max: Math.max(...allViews.map((view) => view.bbox.x_max)),
    y_max: Math.max(...allViews.map((view) => view.bbox.y_max)),
    label: '',
  }
  const file = `/api/projects/${encodeURIComponent(projectId)}/pages/${fileIndex}/${module.page_index}.png`
  const crop = focus ? expandBox(bounds, FOCUS_MARGIN) : FULL_PAGE
  const cropW = crop.x_max - crop.x_min
  const cropH = crop.y_max - crop.y_min
  const fontSize = natural ? Math.max(10, natural.h / 110) : 12
  return (
    <section className="drawing-panel">
      <div className="pane-toolbar">
        <div>
          <strong>Drawing</strong>
          <span className="pane-subtitle">
            {module.file_name} · page {module.page_index + 1}
          </span>
        </div>
        <div className="drawing-tools">
          <button title="Zoom out" onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))}>
            −
          </button>
          <span>{Math.round(zoom * 100)}%</span>
          <button title="Zoom in" onClick={() => setZoom((value) => Math.min(3, value + 0.25))}>
            +
          </button>
          <button className={focus ? 'tool-active' : ''} onClick={() => setFocus((value) => !value)}>
            {focus ? 'Module' : 'Page'}
          </button>
          <button className={showMarks ? 'tool-active' : ''} onClick={() => setShowMarks((value) => !value)}>
            Marks
          </button>
        </div>
      </div>
      {picking && (
        <p className="pick-hint">Pick a dimension mark on the drawing to answer the selected question.</p>
      )}
      <div className="drawing-scroll">
        <div
          className="drawing-crop"
          style={{
            width: `${zoom * 100}%`,
            aspectRatio: natural ? `${cropW * natural.w} / ${cropH * natural.h}` : undefined,
          }}
        >
          <div
            className="drawing-page"
            style={{
              width: `${100 / cropW}%`,
              left: `${(-crop.x_min / cropW) * 100}%`,
              top: `${(-crop.y_min / cropH) * 100}%`,
            }}
          >
            <img
              src={file}
              alt={`Furniture drawing ${module.file_name} page ${module.page_index + 1}`}
              onLoad={(event) =>
                setNatural({ w: event.currentTarget.naturalWidth, h: event.currentTarget.naturalHeight })
              }
            />
            {natural && showMarks && (
              <svg
                className={`mark-overlay ${picking ? 'mark-overlay-picking' : ''}`}
                viewBox={`0 0 ${natural.w} ${natural.h}`}
                aria-label="Dimension marks and view outlines"
              >
                {allViews.map((view, index) => {
                  const x = view.bbox.x_min * natural.w
                  const y = view.bbox.y_min * natural.h
                  const width = (view.bbox.x_max - view.bbox.x_min) * natural.w
                  const height = (view.bbox.y_max - view.bbox.y_min) * natural.h
                  const label = index === 0 ? 'Primary view' : `${view.kind}: ${view.label}`
                  return (
                    <g key={`view-${index}`} className="drawing-view-outline">
                      <rect x={x} y={y} width={width} height={height} />
                      <text x={x + 3} y={y + fontSize}>
                        {label}
                      </text>
                    </g>
                  )
                })}
                {module.marks.map((mark) => {
                  const x = mark.bbox.x_min * natural.w
                  const y = mark.bbox.y_min * natural.h
                  const w = (mark.bbox.x_max - mark.bbox.x_min) * natural.w
                  const h = (mark.bbox.y_max - mark.bbox.y_min) * natural.h
                  const tagW = fontSize * (0.62 * mark.id.length + 0.5)
                  const tagH = fontSize * 1.25
                  const tone = errors.some((issue) => issue.mark_ids.includes(mark.id))
                    ? 'mark-error'
                    : activeMarkIds.has(mark.id)
                      ? 'mark-highlight'
                      : sourceIds.has(mark.id)
                        ? 'mark-source'
                        : 'mark-neutral'
                  return (
                    <g
                      key={mark.id}
                      className={`mark-label ${tone} ${picking ? 'mark-clickable' : ''}`}
                      onClick={picking ? () => onPickMark?.(mark.id) : undefined}
                      role={picking ? 'button' : undefined}
                      tabIndex={picking ? 0 : undefined}
                      onKeyDown={
                        picking ? (event) => event.key === 'Enter' && onPickMark?.(mark.id) : undefined
                      }
                    >
                      <rect className="mark-box" x={x - 2} y={y - 2} width={w + 4} height={h + 4} />
                      <rect
                        className="mark-tag"
                        x={x - tagW}
                        y={y - tagH}
                        width={tagW}
                        height={tagH}
                        rx={3}
                      />
                      <text x={x - tagW + fontSize * 0.25} y={y - tagH * 0.25} fontSize={fontSize}>
                        {mark.id}
                      </text>
                    </g>
                  )
                })}
              </svg>
            )}
          </div>
        </div>
      </div>
      <button
        className={`consistency-badge ${errors.length ? 'has-errors' : ''}`}
        onClick={() => setShowIssues((value) => !value)}
      >
        {errors.length
          ? `${errors.length} ${errors.length === 1 ? 'error' : 'errors'} in the drawing`
          : 'Drawing consistent'}{' '}
        <span>· Issues and cross-checks</span>
      </button>
      {showIssues && (
        <div className="issues-list">
          {module.issues.map((issue, index) => (
            <div className="issue-row" key={`${issue.code}-${index}`}>
              <span className={`issue-dot ${issue.severity}`} />
              <div>
                <strong>{issue.code}</strong>
                <p>{issue.message}</p>
                {issue.mark_ids.length > 0 && <small>Marks: {issue.mark_ids.join(', ')}</small>}
              </div>
            </div>
          ))}
          <h3>Cross-checks</h3>
          {module.view_checks.length ? (
            module.view_checks.map((check, index) => (
              <div
                className="issue-row cross-check-row"
                key={`${check.dimension}-${check.where}-${index}`}
                onMouseEnter={() => onHoverMark?.(check.mark_id)}
                onMouseLeave={() => onHoverMark?.(null)}
              >
                <span className={`cross-check-status ${check.status}`}>
                  {check.status === 'match' ? '✓' : check.status === 'mismatch' ? '✗' : '–'}
                </span>
                <div>
                  <strong>
                    {check.dimension}: {check.expected_mm} mm expected
                  </strong>
                  <p>
                    {check.found_mm == null ? 'No value found' : `${check.found_mm} mm found`} · {check.where}
                  </p>
                  {check.mark_id && <small>{check.mark_id}</small>}
                </div>
              </div>
            ))
          ) : (
            <p>No cross-checks available.</p>
          )}
        </div>
      )}
    </section>
  )
}
