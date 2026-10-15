import type { Dispatch, SetStateAction } from 'react'
import { materialKinds } from './types'
import type { FurnitureModule } from './types'

type Draft = Record<string, number | string | null>
type Cell = FurnitureModule['columns'][number]['cells'][number]
type Column = FurnitureModule['columns'][number]
type Reason = FurnitureModule['questions'][number]['reason']

export default function ModuleElementEditor({
  module,
  selectedCell,
  selectedColumn,
  elementDraft,
  setElementDraft,
  reasonLabels,
  answerError,
  onSave,
  onClose,
}: {
  module: FurnitureModule
  selectedCell: Cell | undefined
  selectedColumn: Column | undefined
  elementDraft: Draft
  setElementDraft: Dispatch<SetStateAction<Draft>>
  reasonLabels: Record<Reason, string>
  answerError: string
  onSave: () => void
  onClose: () => void
}) {
  return (
    <section className="element-editor report-card">
      <header className="report-heading">
        <h3>{selectedCell ? selectedCell.label || selectedCell.id : 'Module elements'}</h3>
        <button className="button secondary" onClick={onClose}>
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
                {Object.entries({
                  door_left: 'Door left',
                  door_right: 'Door right',
                  door_double: 'Double door',
                  drawer: 'Drawer',
                  open: 'Open',
                  niche_appliance: 'Appliance niche',
                  panel_fixed: 'Fixed panel',
                }).map(([value, label]) => (
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
                  setElementDraft((current) => ({ ...current, height_mm: Number(event.target.value) }))
                }
              />
            </label>
            <label>
              Column width (mm)
              <input
                type="number"
                value={elementDraft.column_width ?? selectedColumn.width_mm}
                onChange={(event) =>
                  setElementDraft((current) => ({ ...current, column_width: Number(event.target.value) }))
                }
              />
            </label>
            <label>
              Cell material
              <select
                value={
                  ('material_key' in elementDraft ? elementDraft.material_key : selectedCell.material_key) ??
                  ''
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
                  setElementDraft((current) => ({ ...current, new_material_name: event.target.value }))
                }
              />
            </label>
            <label>
              New material kind
              <select
                value={String(elementDraft.new_material_kind ?? 'laminated_board')}
                onChange={(event) =>
                  setElementDraft((current) => ({ ...current, new_material_kind: event.target.value }))
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
        <button className="button primary" onClick={onSave}>
          Save
        </button>
      </div>
    </section>
  )
}
