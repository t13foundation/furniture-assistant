import { useState } from 'react'
import type { FurnitureModule, ModuleModel } from './types'
interface Props {
  module: FurnitureModule
  onSave: (module: ModuleModel) => void
  onCancel: () => void
  saveModule: (module: FurnitureModule) => Promise<ModuleModel>
}
export default function ModuleEditor({ module, onSave, onCancel, saveModule }: Props) {
  const [draft, setDraft] = useState(() => structuredClone(module))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const save = async () => {
    setSaving(true)
    setError('')
    try {
      onSave(await saveModule(draft))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save module')
    } finally {
      setSaving(false)
    }
  }
  return (
    <div className="modal-backdrop">
      <section className="editor-modal">
        <div className="modal-heading">
          <div>
            <div className="eyebrow">MODULE SETTINGS</div>
            <h2>Edit {module.title}</h2>
          </div>
          <button className="icon-button" onClick={onCancel} aria-label="Close">
            ×
          </button>
        </div>
        <div className="dimension-fields">
          {(['width_mm', 'height_mm', 'depth_mm', 'plinth_mm'] as const).map((key) => (
            <label key={key}>
              {key.replace('_mm', '').replace('_', ' ')} (mm)
              <input
                type="number"
                min="0"
                value={draft[key]}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, [key]: Number(event.target.value) }))
                }
              />
            </label>
          ))}
        </div>
        <h3>Columns and cells</h3>
        {draft.columns.map((column, colIndex) => (
          <div className="edit-column" key={column.index}>
            <label>
              Column {colIndex + 1} width (mm)
              <input
                type="number"
                min="0"
                value={column.width_mm}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    columns: current.columns.map((item, index) =>
                      index === colIndex ? { ...item, width_mm: Number(event.target.value) } : item,
                    ),
                  }))
                }
              />
            </label>
            {column.cells.map((cell, cellIndex) => (
              <div className="edit-cell" key={cell.id}>
                <strong>{cell.label || cell.id}</strong>
                <label>
                  Front
                  <select
                    value={cell.front}
                    onChange={(event) =>
                      setDraft((current) => ({
                        ...current,
                        columns: current.columns.map((item, index) =>
                          index === colIndex
                            ? {
                                ...item,
                                cells: item.cells.map((entry, cellNo) =>
                                  cellNo === cellIndex
                                    ? { ...entry, front: event.target.value as typeof entry.front }
                                    : entry,
                                ),
                              }
                            : item,
                        ),
                      }))
                    }
                  >
                    {[
                      'door_left',
                      'door_right',
                      'door_double',
                      'drawer',
                      'open',
                      'niche_appliance',
                      'panel_fixed',
                    ].map((front) => (
                      <option key={front} value={front}>
                        {front.replaceAll('_', ' ')}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Shelves
                  <input
                    type="number"
                    min="0"
                    value={cell.shelves}
                    onChange={(event) =>
                      setDraft((current) => ({
                        ...current,
                        columns: current.columns.map((item, index) =>
                          index === colIndex
                            ? {
                                ...item,
                                cells: item.cells.map((entry, cellNo) =>
                                  cellNo === cellIndex
                                    ? { ...entry, shelves: Number(event.target.value) }
                                    : entry,
                                ),
                              }
                            : item,
                        ),
                      }))
                    }
                  />
                </label>
                <label>
                  Front material
                  <select
                    value={cell.material_key ?? ''}
                    onChange={(event) =>
                      setDraft((current) => ({
                        ...current,
                        columns: current.columns.map((item, index) =>
                          index === colIndex
                            ? {
                                ...item,
                                cells: item.cells.map((entry, cellNo) =>
                                  cellNo === cellIndex
                                    ? { ...entry, material_key: event.target.value || null }
                                    : entry,
                                ),
                              }
                            : item,
                        ),
                      }))
                    }
                  >
                    <option value="">Module default</option>
                    {draft.materials.map((material) => (
                      <option key={material.key} value={material.key}>
                        {material.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            ))}
          </div>
        ))}
        <div className="dimension-fields">
          <label>
            Carcass material
            <select
              value={draft.carcass_material_key ?? ''}
              onChange={(event) =>
                setDraft((current) => ({ ...current, carcass_material_key: event.target.value || null }))
              }
            >
              <option value="">Unknown</option>
              {draft.materials.map((material) => (
                <option key={material.key} value={material.key}>
                  {material.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Front material
            <select
              value={draft.front_material_key ?? ''}
              onChange={(event) =>
                setDraft((current) => ({ ...current, front_material_key: event.target.value || null }))
              }
            >
              <option value="">Unknown</option>
              {draft.materials.map((material) => (
                <option key={material.key} value={material.key}>
                  {material.name}
                </option>
              ))}
            </select>
          </label>
        </div>
        {error && <p className="error-message">{error}</p>}
        <div className="modal-actions">
          <button className="button secondary" onClick={onCancel}>
            Cancel
          </button>
          <button className="button primary" disabled={saving} onClick={() => void save()}>
            {saving ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      </section>
    </div>
  )
}
