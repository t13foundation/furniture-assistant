import { useState } from 'react'
import { materialKinds } from './types'
import type { FurnitureModule, MaterialKind, Override } from './types'

export default function MaterialAnswer({
  module,
  value,
  candidateValues,
  onChange,
  disabled,
}: {
  module: FurnitureModule
  value: Override
  candidateValues: string[]
  onChange: (patch: Partial<Override>) => void
  disabled: boolean
}) {
  const [name, setName] = useState('')
  const [kind, setKind] = useState<MaterialKind>('laminated_board')
  return (
    <div className="material-answer">
      <label>
        Existing material
        <select
          disabled={disabled}
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
                disabled={disabled}
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
          <input disabled={disabled} value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        <label>
          Kind
          <select
            disabled={disabled}
            value={kind}
            onChange={(event) => setKind(event.target.value as MaterialKind)}
          >
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
          disabled={disabled}
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
