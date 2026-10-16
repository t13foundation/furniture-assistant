import type { CabinetKind, FurnitureModule, ModuleModel, Override } from './types'

type Tab = 'drawing' | 'missing' | 'hardware' | 'processes' | 'history'

export default function ModuleHeader({
  module,
  model,
  tab,
  setTab,
  submitAnswers,
  exportGltf,
  exportPath,
  onEdit,
  canEdit,
}: {
  module: FurnitureModule
  model: ModuleModel
  tab: Tab
  setTab: (tab: Tab) => void
  submitAnswers: (overrides: Override[]) => Promise<boolean>
  exportGltf: () => void
  exportPath: (format: string) => string
  onEdit: () => void
  canEdit: boolean
}) {
  return (
    <>
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
          {canEdit && (
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
          )}
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
          {canEdit && (
            <button className="button primary" onClick={onEdit}>
              Edit
            </button>
          )}
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
    </>
  )
}
