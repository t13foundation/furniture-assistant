import { useEffect, useMemo, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { GLTFExporter } from 'three/examples/jsm/exporters/GLTFExporter.js'
import type { Scene } from 'three'
import { api } from './api'
import { HistoryPanel, QuestionCrop } from './AdditionalPages'
import DrawingPane from './DrawingPane'
import ModelPane from './ModelPane'
import ModuleEditor from './ModuleEditor'
import PartsList from './PartsList'
import { materialKinds } from './types'
import type {
  CabinetKind,
  FurnitureModule,
  MaterialKind,
  ModuleModel,
  Override,
  Panel,
  ProcessKind,
} from './types'

interface Props {
  id: string
  model: ModuleModel
  files: { index: number; name: string }[]
  onUpdate: (model: ModuleModel) => void
}
type Tab = 'drawing' | 'missing' | 'hardware' | 'processes' | 'history'
const reasonLabels = {
  conflict: 'Contradiction',
  model_unsure: 'Model unsure',
  unverified: 'Not confirmed by a dimension',
  estimated: 'Estimated',
  assumed: 'Default',
  missing: 'Missing',
  unknown_material: 'Unknown material',
} as const
const frontLabels = {
  door_left: 'Door left',
  door_right: 'Door right',
  door_double: 'Double door',
  drawer: 'Drawer',
  open: 'Open',
  niche_appliance: 'Appliance niche',
  panel_fixed: 'Fixed panel',
} as const
const processLabels: Record<ProcessKind, string> = {
  cutting: 'Rozkrój',
  edge_banding: 'Oklejanie',
  drilling: 'Wiercenie',
  assembly: 'Montaż korpusu',
  hardware_mounting: 'Montaż okuć',
  installation: 'Montaż',
}
export default function ModuleView({ id, model, files, onUpdate }: Props) {
  const location = useLocation()
  const [editing, setEditing] = useState(false)
  const [tab, setTab] = useState<Tab>(location.search.includes('tab=review') ? 'missing' : 'drawing')
  const [hoveredIds, setHoveredIds] = useState<string[]>([])
  const [hoveredRow, setHoveredRow] = useState<Panel | null>(null)
  const [hoveredCellIds, setHoveredCellIds] = useState<string[]>([])
  const [scene, setScene] = useState<Scene | null>(null)
  const [selectedCellId, setSelectedCellId] = useState<string | null>(null)
  const [selectedModule, setSelectedModule] = useState(false)
  const [elementDraft, setElementDraft] = useState<Record<string, number | string | null>>({})
  const [answers, setAnswers] = useState<Record<string, Override>>({})
  const [pickedQuestion, setPickedQuestion] = useState<string | null>(null)
  const [answerError, setAnswerError] = useState('')
  const [refining, setRefining] = useState(false)
  const [refineTimedOut, setRefineTimedOut] = useState(false)
  const [hoveredMarkId, setHoveredMarkId] = useState<string | null>(null)
  const module = model.module
  const fileIndex = files.find((file) => file.name === module.file_name)?.index ?? 0
  const partIds =
    hoveredRow?.notes?.split(',').filter(Boolean) ??
    (hoveredIds.length
      ? hoveredIds
      : model.solids
          .filter((solid) => solid.cell_id && hoveredCellIds.includes(solid.cell_id))
          .map((solid) => solid.part_id))
  const sourceByPart = useMemo(
    () =>
      Object.fromEntries(
        model.solids.map((solid) => [
          solid.part_id,
          solid.cell_id
            ? module.columns
                .flatMap((column) => column.cells)
                .find((cell) => cell.id === solid.cell_id)
                ?.source_mark_ids.join(', ') || solid.cell_id
            : 'rule',
        ]),
      ),
    [model.solids, module.columns],
  )
  const reviewPartIds = useMemo(() => {
    const cellIds = module.questions
      .map((question) => question.cell_id ?? question.id.match(/^cell:(.+?):/)?.[1])
      .filter((cellId): cellId is string => Boolean(cellId))
    const columns = module.questions
      .map((question) => question.id.match(/^col:(\d+):width$/)?.[1])
      .filter((index): index is string => index !== undefined)
    const moduleWide = module.questions.some((question) =>
      /^(width|height|depth|plinth|material:carcass|material:front)$/.test(question.id),
    )
    return model.solids
      .filter((solid) => {
        if (solid.cell_id && cellIds.includes(solid.cell_id)) return true
        if (solid.cell_id && columns.some((column) => solid.cell_id?.startsWith(`c${column}r`))) return true
        return moduleWide && ['carcass', 'back', 'plinth'].includes(solid.role)
      })
      .map((solid) => solid.part_id)
  }, [module.questions, model.solids])
  const selectedCell = selectedCellId
    ? module.columns.flatMap((column) => column.cells).find((cell) => cell.id === selectedCellId)
    : undefined
  const selectedColumn = selectedCell
    ? module.columns.find((column) => column.index === selectedCell.col)
    : undefined
  const highlightedMarks = useMemo(() => {
    const ids = new Set<string>()
    for (const solid of model.solids)
      if (partIds.includes(solid.part_id) && solid.cell_id)
        module.columns
          .flatMap((column) => column.cells)
          .find((cell) => cell.id === solid.cell_id)
          ?.source_mark_ids.forEach((markId) => ids.add(markId))
    if (hoveredMarkId) ids.add(hoveredMarkId)
    return [...ids]
  }, [module.columns, model.solids, partIds, hoveredMarkId])
  const exportGltf = () => {
    if (!scene) return
    new GLTFExporter().parse(
      scene,
      (result) => {
        const blob = new Blob([result as ArrayBuffer], { type: 'model/gltf-binary' })
        const link = document.createElement('a')
        link.href = URL.createObjectURL(blob)
        link.download = `${module.module_id}.glb`
        link.click()
        URL.revokeObjectURL(link.href)
      },
      (error) => console.error('glTF export failed', error),
      { binary: true },
    )
  }
  const modulePath = `/api/projects/${encodeURIComponent(id)}/modules/${encodeURIComponent(module.module_id)}`
  const saveModule = (updated: FurnitureModule) =>
    api<ModuleModel>(modulePath, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updated),
    })
  const submitAnswers = async (overrides: Override[]) => {
    setAnswerError('')
    try {
      const updated = await api<ModuleModel>(`${modulePath}/answers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ overrides }),
      })
      onUpdate(updated)
      setAnswers((current) =>
        Object.fromEntries(
          Object.entries(current).filter(
            ([target]) => !overrides.some((override) => override.target === target),
          ),
        ),
      )
      return true
    } catch (error) {
      setAnswerError(error instanceof Error ? error.message : 'Could not apply answers')
      return false
    }
  }
  const selectSolid = (solid: (typeof model.solids)[number]) => {
    setSelectedCellId(solid.cell_id)
    setSelectedModule(!solid.cell_id)
    setElementDraft({})
  }
  const saveElement = async () => {
    const overrides: Override[] = []
    if (selectedCell && selectedColumn) {
      for (const field of ['front', 'shelves', 'height_mm', 'material_key'] as const) {
        const original =
          field === 'height_mm'
            ? selectedCell.height_mm
            : field === 'material_key'
              ? selectedCell.material_key
              : selectedCell[field]
        if (
          field === 'material_key'
            ? Boolean(elementDraft.new_material_name) ||
              (elementDraft[field] !== undefined && elementDraft[field] !== original)
            : elementDraft[field] !== undefined && elementDraft[field] !== original
        ) {
          const target =
            field === 'height_mm'
              ? `cell:${selectedCell.id}:height`
              : `cell:${selectedCell.id}:${field === 'material_key' ? 'material' : field}`
          overrides.push(
            field === 'front'
              ? { target, front: elementDraft[field] as Override['front'] }
              : field === 'shelves'
                ? { target, shelves: Number(elementDraft[field]) }
                : field === 'height_mm'
                  ? { target, value_mm: Number(elementDraft[field]) }
                  : elementDraft.new_material_name
                    ? {
                        target,
                        material: {
                          key: `M${module.materials.length + 1}`,
                          name: String(elementDraft.new_material_name),
                          kind: (elementDraft.new_material_kind as MaterialKind) ?? 'laminated_board',
                          thickness_mm: null,
                        },
                      }
                    : { target, material_key: (elementDraft[field] as string | null) ?? '' },
          )
        }
      }
      if (elementDraft.column_width !== undefined && elementDraft.column_width !== selectedColumn.width_mm)
        overrides.push({
          target: `col:${selectedColumn.index}:width`,
          value_mm: Number(elementDraft.column_width),
        })
    } else if (selectedModule) {
      for (const field of ['width_mm', 'height_mm', 'depth_mm', 'plinth_mm'] as const) {
        if (elementDraft[field] !== undefined && elementDraft[field] !== module[field])
          overrides.push({ target: field.replace('_mm', ''), value_mm: Number(elementDraft[field]) })
      }
      for (const field of ['carcass_material_key', 'front_material_key'] as const) {
        if (elementDraft[field] !== undefined && elementDraft[field] !== module[field])
          overrides.push({
            target: `material:${field.startsWith('carcass') ? 'carcass' : 'front'}`,
            material_key: elementDraft[field] as string | null,
          })
      }
    }
    if (overrides.length && !(await submitAnswers(overrides))) return
    setSelectedCellId(null)
    setSelectedModule(false)
  }
  const refine = async () => {
    setAnswerError('')
    setRefineTimedOut(false)
    try {
      await api(`${modulePath}/refine`, { method: 'POST' })
      setRefining(true)
    } catch (error) {
      setAnswerError(error instanceof Error ? error.message : 'Could not refine module')
    }
  }
  useEffect(() => {
    if (refineTimedOut || (!refining && model.refine_status !== 'running')) return
    const startedAt = Date.now()
    let active = true
    const poll = async () => {
      try {
        const updated = await api<ModuleModel>(modulePath)
        if (!active) return
        onUpdate(updated)
        if (updated.refine_status !== 'running') setRefining(false)
      } catch {
        if (active && Date.now() - startedAt >= 5 * 60 * 1000) {
          setAnswerError(
            'Could not refresh refinement status after 5 minutes. Refresh the page to check again.',
          )
          setRefineTimedOut(true)
          setRefining(false)
        }
      }
    }
    const interval = window.setInterval(() => void poll(), 2000)
    const timeout = window.setTimeout(
      () => {
        if (active) {
          setAnswerError('Refinement is taking longer than 5 minutes. Refresh the page to check its status.')
          setRefineTimedOut(true)
          setRefining(false)
        }
      },
      5 * 60 * 1000,
    )
    return () => {
      active = false
      window.clearInterval(interval)
      window.clearTimeout(timeout)
    }
  }, [modulePath, model.refine_status, refining, refineTimedOut, onUpdate])
  const exportPath = (format: string) => `${modulePath}/export/${format}`
  const processKinds = [...new Set(model.processes.map((line) => line.process))]
  const hardwareGroups = [...new Set(model.hardware.map((item) => item.category))].map((category) => ({
    category,
    items: model.hardware.filter((item) => item.category === category),
  }))
  return (
    <div className="module-workspace">
      <header className="module-header">
        <div>
          <div className="eyebrow">DRAWING AND 3D</div>
          <h1>
            {module.title} <span className="module-id">{module.module_id}</span>
          </h1>
          <p>
            {module.file_name} · page {module.page_index + 1}
          </p>
          <div className="module-identity-chips" aria-label="Cabinet identity">
            <span className="module-identity-chip">{module.kind.replaceAll('_', ' ')}</span>
            <span className="module-identity-chip">{module.group_id || module.module_id}</span>
          </div>
          <div className="module-meta-controls">
            <label>
              Cabinet kind
              <select
                aria-label="Cabinet kind"
                value={module.kind}
                onChange={(event) =>
                  void submitAnswers([{ target: 'kind', kind: event.target.value as CabinetKind }])
                }
              >
                {(
                  [
                    'cabinet',
                    'wardrobe',
                    'kitchen',
                    'shelving',
                    'bench',
                    'reception_desk',
                    'wall_cladding',
                    'other',
                  ] as CabinetKind[]
                ).map((kind) => (
                  <option key={kind} value={kind}>
                    {kind.replaceAll('_', ' ')}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Cabinet group
              <input
                aria-label="Cabinet group code"
                defaultValue={module.group_id}
                key={`${module.module_id}:${module.group_id}`}
                onBlur={(event) => {
                  const group = event.currentTarget.value.trim()
                  if (group !== module.group_id) void submitAnswers([{ target: 'group', group_id: group }])
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') event.currentTarget.blur()
                }}
              />
            </label>
          </div>
        </div>
        <div className="header-actions">
          <button className="button secondary" onClick={exportGltf}>
            Download glTF
          </button>
          {(['cutlist.csv', 'cutlist.xlsx', 'sheets.svg', 'panels.dxf'] as const).map((format, index) => (
            <a className="button secondary" key={format} href={exportPath(format)}>
              {['CSV', 'XLSX', 'SVG', 'DXF'][index]}
            </a>
          ))}
          <button className="button primary" onClick={() => setEditing(true)}>
            Edit
          </button>
        </div>
      </header>
      <div className="tab-bar module-tabs">
        {(
          [
            ['drawing', 'Drawing and 3D'],
            ['missing', `Review ${module.questions.length}`],
            ['hardware', `Hardware ${model.hardware.length}`],
            ['processes', `Processes ${model.processes.length}`],
            ['history', 'History'],
          ] as [Tab, string][]
        ).map(([key, label]) => (
          <button className={tab === key ? 'tab-active' : ''} key={key} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </div>
      {tab === 'drawing' && (
        <>
          <div className="module-grid">
            <DrawingPane
              module={module}
              fileIndex={fileIndex}
              projectId={id}
              highlightedMarkIds={highlightedMarks}
              onHoverMark={setHoveredMarkId}
            />
            <ModelPane
              module={module}
              solids={model.solids}
              hoveredPartIds={partIds}
              reviewPartIds={reviewPartIds}
              onHover={setHoveredIds}
              onSelect={selectSolid}
              onScene={setScene}
            />
          </div>
          {(selectedCell || selectedModule) && (
            <section className="element-editor report-card">
              <header className="report-heading">
                <h3>{selectedCell ? selectedCell.label || selectedCell.id : 'Module elements'}</h3>
                <button
                  className="button secondary"
                  onClick={() => {
                    setSelectedCellId(null)
                    setSelectedModule(false)
                  }}
                >
                  Close
                </button>
              </header>
              {selectedCell && selectedColumn ? (
                <>
                  <div className="dimension-fields">
                    <label>
                      Front
                      <select
                        value={elementDraft.front ?? selectedCell.front}
                        onChange={(event) =>
                          setElementDraft((current) => ({ ...current, front: event.target.value }))
                        }
                      >
                        {Object.entries(frontLabels).map(([value, label]) => (
                          <option value={value} key={value}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Shelves
                      <input
                        type="number"
                        min="0"
                        value={elementDraft.shelves ?? selectedCell.shelves}
                        onChange={(event) =>
                          setElementDraft((current) => ({ ...current, shelves: Number(event.target.value) }))
                        }
                      />
                    </label>
                    <label>
                      Cell height (mm)
                      <input
                        type="number"
                        value={elementDraft.height_mm ?? selectedCell.height_mm}
                        onChange={(event) =>
                          setElementDraft((current) => ({
                            ...current,
                            height_mm: Number(event.target.value),
                          }))
                        }
                      />
                    </label>
                    <label>
                      Column width (mm)
                      <input
                        type="number"
                        value={elementDraft.column_width ?? selectedColumn.width_mm}
                        onChange={(event) =>
                          setElementDraft((current) => ({
                            ...current,
                            column_width: Number(event.target.value),
                          }))
                        }
                      />
                    </label>
                    <label>
                      Cell material
                      <select
                        value={
                          ('material_key' in elementDraft
                            ? elementDraft.material_key
                            : selectedCell.material_key) ?? ''
                        }
                        onChange={(event) =>
                          setElementDraft((current) => ({
                            ...current,
                            material_key: event.target.value || null,
                            new_material_name: '',
                          }))
                        }
                      >
                        <option value="">Module default</option>
                        {module.materials.map((material) => (
                          <option key={material.key} value={material.key}>
                            {material.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Or new material
                      <input
                        value={String(elementDraft.new_material_name ?? '')}
                        onChange={(event) =>
                          setElementDraft((current) => ({
                            ...current,
                            new_material_name: event.target.value,
                          }))
                        }
                      />
                    </label>
                    <label>
                      New material kind
                      <select
                        value={String(elementDraft.new_material_kind ?? 'laminated_board')}
                        onChange={(event) =>
                          setElementDraft((current) => ({
                            ...current,
                            new_material_kind: event.target.value,
                          }))
                        }
                      >
                        {materialKinds.map((kind) => (
                          <option key={kind} value={kind}>
                            {kind.replaceAll('_', ' ')}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  {module.questions
                    .filter(
                      (question) =>
                        question.cell_id === selectedCell.id ||
                        question.id.startsWith(`cell:${selectedCell.id}:`) ||
                        question.id.startsWith(`col:${selectedColumn.index}:`),
                    )
                    .map((question) => (
                      <p className="question-note" key={question.id}>
                        {question.label} · {reasonLabels[question.reason]}
                        {question.note ? ` · ${question.note}` : ''}
                      </p>
                    ))}
                </>
              ) : (
                <div className="dimension-fields">
                  {(['width_mm', 'height_mm', 'depth_mm', 'plinth_mm'] as const).map((field) => (
                    <label key={field}>
                      {field.replace('_mm', '')} (mm)
                      <input
                        type="number"
                        value={elementDraft[field] ?? module[field]}
                        onChange={(event) =>
                          setElementDraft((current) => ({ ...current, [field]: Number(event.target.value) }))
                        }
                      />
                    </label>
                  ))}
                  {(['carcass_material_key', 'front_material_key'] as const).map((field) => (
                    <label key={field}>
                      {field.startsWith('carcass') ? 'Carcass' : 'Front'} material
                      <select
                        value={elementDraft[field] ?? module[field] ?? ''}
                        onChange={(event) =>
                          setElementDraft((current) => ({ ...current, [field]: event.target.value || null }))
                        }
                      >
                        <option value="">Unknown</option>
                        {module.materials.map((material) => (
                          <option key={material.key} value={material.key}>
                            {material.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  ))}
                </div>
              )}
              {answerError && <p className="error-message">{answerError}</p>}
              <div className="modal-actions">
                <button className="button primary" onClick={() => void saveElement()}>
                  Save
                </button>
              </div>
            </section>
          )}
          <PartsList
            rows={model.cut_list}
            solidsCount={model.solids.length}
            activePartIds={partIds}
            reviewPartIds={reviewPartIds}
            sourceByPart={sourceByPart}
            onHover={setHoveredRow}
            onSelect={(partId) => {
              const solid = model.solids.find((item) => item.part_id === partId)
              if (solid) selectSolid(solid)
            }}
          />
        </>
      )}
      {tab === 'missing' && (
        <section className="report-card">
          <header className="report-heading">
            <div>
              <h2>Review</h2>
              <p>Resolve missing information and check uncertain values.</p>
            </div>
            <button
              className="button secondary"
              onClick={() => void refine()}
              disabled={model.refine_status === 'running' || refining}
            >
              <span>
                {model.refine_status === 'running' || refining ? 'Refining…' : 'Ask the model again'}
              </span>
            </button>
          </header>
          {(model.refine_status === 'running' || refining) && (
            <p className="notice">
              <span className="spinner" /> Model is reviewing open questions · attempt {model.refine_attempts}
            </p>
          )}
          {model.refine_status === 'error' && (
            <p className="error-message">Refinement failed: {model.refine_error || 'Unknown error'}</p>
          )}
          {answerError && <p className="error-message">{answerError}</p>}
          {module.questions.some((question) => question.reason === 'unverified') && (
            <button
              className="button primary"
              onClick={() => {
                const overrides = module.questions
                  .filter((question) => question.reason === 'unverified')
                  .flatMap((question): Override[] => {
                    if (question.kind === 'dimension' && question.current_mm != null)
                      return [{ target: question.id, value_mm: question.current_mm }]
                    if (question.kind === 'front' && question.current_value)
                      return [{ target: question.id, front: question.current_value as Override['front'] }]
                    if (question.kind === 'shelves' && question.current_value != null)
                      return [{ target: question.id, shelves: Number(question.current_value) }]
                    if (question.kind === 'material' && question.current_value)
                      return [{ target: question.id, material_key: question.current_value }]
                    return []
                  })
                if (overrides.length) void submitAnswers(overrides)
              }}
            >
              Confirm all readings
            </button>
          )}
          {module.questions.length ? (
            <>
              {(['Missing', 'Please check'] as const).map((group) => {
                const questions = module.questions.filter((question) =>
                  group === 'Missing'
                    ? question.reason === 'missing' || question.reason === 'unknown_material'
                    : !['missing', 'unknown_material', 'unverified'].includes(question.reason),
                )
                return questions.length ? (
                  <section className="review-group" key={group}>
                    <h3>{group}</h3>
                    {questions.map((question) => {
                      const answer = answers[question.id] ?? { target: question.id }
                      const update = (patch: Partial<Override>) =>
                        setAnswers((current) => ({
                          ...current,
                          [question.id]: { ...answer, ...patch },
                        }))
                      const currentDisplay =
                        question.kind === 'dimension'
                          ? `${question.current_mm ?? '—'} mm`
                          : question.kind === 'front'
                            ? (frontLabels[question.current_value as keyof typeof frontLabels] ?? '—')
                            : question.kind === 'material'
                              ? (module.materials.find((material) => material.key === question.current_value)
                                  ?.name ??
                                question.current_value ??
                                '—')
                              : (question.current_value ?? '—')
                      const canConfirm =
                        question.kind === 'dimension'
                          ? answer.mark_id != null || answer.value_mm != null || question.current_mm != null
                          : question.kind === 'front'
                            ? Boolean(answer.front ?? question.current_value)
                            : question.kind === 'shelves'
                              ? answer.shelves != null || question.current_value != null
                              : Boolean(answer.material || answer.material_key || question.current_value)
                      return (
                        <article className="question-card" key={question.id}>
                          <div className="question-heading">
                            <strong>{question.label}</strong>
                            <span className={`reason-badge ${question.reason}`}>
                              {reasonLabels[question.reason]}
                            </span>
                          </div>
                          {question.note && <p className="question-note">{question.note}</p>}
                          <QuestionCrop
                            projectId={id}
                            model={model}
                            fileIndex={fileIndex}
                            question={question}
                          />
                          <p className="current-value">Current value: {currentDisplay}</p>
                          {question.kind === 'dimension' ? (
                            <>
                              <div className="answer-row">
                                <label>
                                  Value (mm)
                                  <input
                                    type="number"
                                    value={answer.value_mm ?? question.current_mm ?? ''}
                                    onChange={(event) =>
                                      update({
                                        value_mm:
                                          event.target.value === '' ? null : Number(event.target.value),
                                        mark_id: null,
                                      })
                                    }
                                  />
                                </label>
                                <span>Units in drawing: {module.units}</span>
                              </div>
                              {!!question.candidate_mark_ids.length && (
                                <div className="candidate-marks">
                                  <span>Candidate marks</span>
                                  {question.candidate_mark_ids.map((markId) => {
                                    const mark = module.marks.find((item) => item.id === markId)
                                    return (
                                      <button
                                        key={markId}
                                        className={`chip ${answer.mark_id === markId ? 'selected' : ''}`}
                                        onClick={() => update({ mark_id: markId, value_mm: null })}
                                      >
                                        {markId} · {mark ? `${mark.value} ${mark.unit}` : 'mark'}
                                      </button>
                                    )
                                  })}
                                </div>
                              )}
                              <button
                                className={`button secondary ${pickedQuestion === question.id ? 'selected' : ''}`}
                                onClick={() =>
                                  setPickedQuestion(pickedQuestion === question.id ? null : question.id)
                                }
                              >
                                {pickedQuestion === question.id ? 'Cancel drawing pick' : 'Pick on drawing'}
                              </button>
                            </>
                          ) : question.kind === 'material' ? (
                            <MaterialAnswer
                              module={module}
                              value={
                                answer.material
                                  ? answer
                                  : {
                                      ...answer,
                                      material_key: answer.material_key ?? question.current_value ?? null,
                                    }
                              }
                              candidateValues={question.candidate_values}
                              onChange={update}
                            />
                          ) : question.kind === 'front' ? (
                            <label className="review-control">
                              Front
                              <select
                                value={answer.front ?? question.current_value ?? ''}
                                onChange={(event) =>
                                  update({ front: event.target.value as Override['front'] })
                                }
                              >
                                {Object.entries(frontLabels).map(([value, label]) => (
                                  <option value={value} key={value}>
                                    {label}
                                  </option>
                                ))}
                              </select>
                            </label>
                          ) : (
                            <label className="review-control">
                              Shelves
                              <input
                                type="number"
                                min="0"
                                value={answer.shelves ?? Number(question.current_value ?? 0)}
                                onChange={(event) => update({ shelves: Number(event.target.value) })}
                              />
                            </label>
                          )}
                          <button
                            className="button secondary confirm-button"
                            title="The current value is correct"
                            disabled={!canConfirm}
                            onClick={() =>
                              void submitAnswers([
                                {
                                  target: question.id,
                                  ...(question.kind === 'dimension'
                                    ? answer.mark_id
                                      ? { mark_id: answer.mark_id }
                                      : { value_mm: answer.value_mm ?? question.current_mm }
                                    : question.kind === 'front'
                                      ? {
                                          front:
                                            answer.front ?? (question.current_value as Override['front']),
                                        }
                                      : question.kind === 'shelves'
                                        ? { shelves: answer.shelves ?? Number(question.current_value ?? 0) }
                                        : answer.material
                                          ? { material: answer.material }
                                          : {
                                              material_key: answer.material_key ?? question.current_value,
                                            }),
                                },
                              ])
                            }
                          >
                            Confirm
                          </button>
                        </article>
                      )
                    })}
                  </section>
                ) : null
              })}
              {!!module.questions.filter((question) => question.reason === 'unverified').length && (
                <details className="review-group">
                  <summary>
                    Unverified readings (
                    {module.questions.filter((question) => question.reason === 'unverified').length})
                  </summary>
                  {module.questions
                    .filter((question) => question.reason === 'unverified')
                    .map((question) => (
                      <article className="question-card" key={question.id}>
                        <strong>{question.label}</strong>
                        <p>Current value: {question.current_mm ?? question.current_value ?? '—'}</p>
                      </article>
                    ))}
                </details>
              )}
            </>
          ) : (
            <p className="empty-report">Everything is reviewed.</p>
          )}
          {!!model.overrides.length && (
            <section>
              <h3>Applied overrides</h3>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Target</th>
                      <th>Value</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {model.overrides.map((override, index) => (
                      <tr key={`${override.target}-${index}`}>
                        <td>{override.target}</td>
                        <td>
                          {override.value_mm != null
                            ? `${override.value_mm} mm`
                            : (override.mark_id ??
                              override.material?.name ??
                              override.material_key ??
                              override.front ??
                              override.shelves ??
                              '—')}
                        </td>
                        <td>
                          <button
                            className="button secondary"
                            onClick={() => void submitAnswers([{ target: override.target }])}
                          >
                            Remove
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
          <div className="modal-actions">
            <button
              className="button primary"
              disabled={!Object.keys(answers).length}
              onClick={() => void submitAnswers(Object.values(answers))}
            >
              Apply answers
            </button>
          </div>
        </section>
      )}
      {tab === 'hardware' && (
        <section className="report-card">
          <h2>Hardware and fittings</h2>
          {model.hardware.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Category</th>
                    <th>Name</th>
                    <th>Specification</th>
                    <th>Qty</th>
                    <th>Cells</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {hardwareGroups.flatMap((group) => [
                    <tr className="hardware-category-row" key={`${group.category}-heading`}>
                      <th colSpan={6}>{group.category}</th>
                    </tr>,
                    ...group.items.map((item) => (
                      <tr
                        key={item.key}
                        title={item.source_text || undefined}
                        onMouseEnter={() => setHoveredCellIds(item.cell_ids)}
                        onMouseLeave={() => setHoveredCellIds([])}
                      >
                        <td>{item.category}</td>
                        <td>{item.name}</td>
                        <td>{item.spec || '—'}</td>
                        <td>
                          {item.qty} {item.unit}
                        </td>
                        <td>{item.cell_ids.join(', ') || '—'}</td>
                        <td>
                          {item.source}
                          {item.source_text ? ` · ${item.source_text}` : ''}
                        </td>
                      </tr>
                    )),
                  ])}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="empty-report">No hardware recorded.</p>
          )}
        </section>
      )}
      {tab === 'processes' && (
        <div className="process-layout">
          <ModelPane
            module={module}
            solids={model.solids}
            hoveredPartIds={partIds}
            reviewPartIds={reviewPartIds}
            onHover={setHoveredIds}
            onSelect={selectSolid}
            onScene={setScene}
          />
          <section className="report-card process-tables">
            <h2>Production and mounting processes</h2>
            {processKinds.length ? (
              processKinds.map((kind) => {
                const lines = model.processes.filter((line) => line.process === kind)
                const minutes = lines.reduce((sum, line) => sum + line.minutes, 0)
                return (
                  <section className="process-group" key={kind}>
                    <h3>{processLabels[kind]}</h3>
                    <div className="table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>Operation</th>
                            <th>Qty</th>
                            <th>Material</th>
                            <th>Minutes</th>
                          </tr>
                        </thead>
                        <tbody>
                          {lines.map((line, index) => (
                            <tr
                              key={`${line.operation}-${index}`}
                              onMouseEnter={() => setHoveredIds(line.part_ids)}
                              onMouseLeave={() => setHoveredIds([])}
                            >
                              <td>{line.operation}</td>
                              <td>
                                {line.qty} {line.unit}
                              </td>
                              <td>{line.material || '—'}</td>
                              <td>{line.minutes.toFixed(1)}</td>
                            </tr>
                          ))}
                        </tbody>
                        <tfoot>
                          <tr>
                            <th colSpan={3}>Subtotal</th>
                            <th>{minutes.toFixed(1)} min</th>
                          </tr>
                        </tfoot>
                      </table>
                    </div>
                  </section>
                )
              })
            ) : (
              <p className="empty-report">No process estimates available.</p>
            )}
            <strong>
              Total: {(model.processes.reduce((sum, line) => sum + line.minutes, 0) / 60).toFixed(2)} h
            </strong>
          </section>
        </div>
      )}
      {tab === 'history' && (
        <HistoryPanel
          projectId={id}
          moduleId={module.module_id}
          onRestore={(restored) => onUpdate(restored)}
        />
      )}
      {editing && (
        <ModuleEditor
          module={module}
          saveModule={saveModule}
          onCancel={() => setEditing(false)}
          onSave={(result) => {
            onUpdate(result)
            setEditing(false)
          }}
        />
      )}
      {tab === 'missing' && (
        <DrawingPane
          module={module}
          fileIndex={fileIndex}
          projectId={id}
          highlightedMarkIds={[
            ...module.marks
              .filter((mark) => Object.values(answers).some((answer) => answer.mark_id === mark.id))
              .map((mark) => mark.id),
            ...(hoveredMarkId ? [hoveredMarkId] : []),
          ]}
          picking={pickedQuestion !== null}
          onHoverMark={setHoveredMarkId}
          onPickMark={(markId) => {
            if (pickedQuestion) {
              setAnswers((current) => ({
                ...current,
                [pickedQuestion]: { target: pickedQuestion, mark_id: markId },
              }))
              setPickedQuestion(null)
            }
          }}
        />
      )}
    </div>
  )
}

function MaterialAnswer({
  module,
  value,
  candidateValues,
  onChange,
}: {
  module: FurnitureModule
  value: Override
  candidateValues: string[]
  onChange: (patch: Partial<Override>) => void
}) {
  const [name, setName] = useState('')
  const [kind, setKind] = useState<MaterialKind>('laminated_board')
  return (
    <div className="material-answer">
      <label>
        Existing material
        <select
          value={value.material_key ?? ''}
          onChange={(event) => onChange({ material_key: event.target.value || null, material: null })}
        >
          <option value="">Choose material…</option>
          {[
            ...new Map(
              [...module.materials, ...module.legend_materials].map((material) => [material.key, material]),
            ).values(),
          ].map((material) => (
            <option key={material.key} value={material.key}>
              {material.name} ({material.key})
            </option>
          ))}
        </select>
      </label>
      {!!candidateValues.length && (
        <div className="candidate-marks" aria-label="Suggested legend materials">
          <span>Suggested materials</span>
          {candidateValues.map((key) => {
            const material = [...module.legend_materials, ...module.materials].find(
              (item) => item.key === key,
            )
            return (
              <button
                type="button"
                className={`chip ${value.material_key === key ? 'selected' : ''}`}
                key={key}
                onClick={() => onChange({ material_key: key, material: null })}
              >
                {material?.name ?? key}
              </button>
            )
          })}
        </div>
      )}
      <div className="answer-row">
        <label>
          Or new material
          <input value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        <label>
          Kind
          <select value={kind} onChange={(event) => setKind(event.target.value as MaterialKind)}>
            {materialKinds.map((item) => (
              <option key={item} value={item}>
                {item.replaceAll('_', ' ')}
              </option>
            ))}
          </select>
        </label>
      </div>
      {name && (
        <button
          className="button secondary"
          onClick={() =>
            onChange({
              material_key: null,
              material: { key: `M${module.materials.length + 1}`, name, kind, thickness_mm: null },
            })
          }
        >
          Use “{name}”
        </button>
      )}
    </div>
  )
}
