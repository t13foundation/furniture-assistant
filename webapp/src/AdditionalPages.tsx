import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from './api'
import { isViewer, useAuth } from './auth'
import { ProjectSidebar } from './ProjectView'
import type {
  ActivityEntry,
  ModuleModel,
  ModuleVersion,
  Override,
  ProjectDetail,
  ProjectReviewItem,
  Question,
} from './types'

export function ProjectReviewPage() {
  const { id = '' } = useParams()
  const { auth } = useAuth()
  const viewer = isViewer(auth)
  const [detail, setDetail] = useState<ProjectDetail | null>(null)
  const [items, setItems] = useState<ProjectReviewItem[]>([])
  const [reason, setReason] = useState('all')
  const [error, setError] = useState('')
  const [collapsed, setCollapsed] = useState(true)
  const loadSequence = useRef(0)
  const load = useCallback(() => {
    const sequence = ++loadSequence.current
    return Promise.all([
      api<ProjectDetail>(`/api/projects/${encodeURIComponent(id)}`),
      api<ProjectReviewItem[]>(`/api/projects/${encodeURIComponent(id)}/review`),
    ])
      .then(([project, review]) => {
        if (sequence === loadSequence.current) {
          setDetail(project)
          setItems(review)
        }
      })
      .catch((e: unknown) => {
        if (sequence === loadSequence.current)
          setError(e instanceof Error ? e.message : 'Could not load review')
      })
  }, [id])
  useEffect(() => {
    void load()
    return () => {
      loadSequence.current += 1
    }
  }, [load])
  const [modules, setModules] = useState<Record<string, ModuleModel>>({})
  useEffect(() => {
    let active = true
    const missing = [...new Set(items.map((item) => item.module_id))]
    if (!missing.length) return
    void Promise.all(
      missing.map((moduleId) =>
        api<ModuleModel>(`/api/projects/${encodeURIComponent(id)}/modules/${encodeURIComponent(moduleId)}`),
      ),
    )
      .then((loaded) => {
        if (active)
          setModules((current) => ({
            ...current,
            ...Object.fromEntries(loaded.map((model) => [model.module.module_id, model])),
          }))
      })
      .catch(() => undefined)
    return () => {
      active = false
    }
  }, [id, items])
  const pending = items.flatMap((item) => item.questions.map((question) => ({ item, question })))
  const visible = pending.filter(
    ({ question }) =>
      (reason === 'all' || question.reason === reason) && (!collapsed || question.reason !== 'unverified'),
  )
  const groupedVisible = new Map<string, typeof visible>()
  for (const row of visible) {
    const group = row.item.group_id || row.item.module_id
    groupedVisible.set(group, [...(groupedVisible.get(group) ?? []), row])
  }
  const confirm = async (item: ProjectReviewItem, question: Question) => {
    const target = question.id
    let value: Override
    if (question.kind === 'dimension' && question.current_mm != null)
      value = { target, value_mm: question.current_mm }
    else if (question.kind === 'front' && question.current_value)
      value = { target, front: question.current_value as Override['front'] }
    else if (question.kind === 'shelves' && question.current_value != null)
      value = { target, shelves: Number(question.current_value) }
    else if (question.kind === 'material' && question.current_value)
      value = { target, material_key: question.current_value }
    else return
    try {
      await api(
        `/api/projects/${encodeURIComponent(id)}/modules/${encodeURIComponent(item.module_id)}/answers`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ overrides: [value] }),
        },
      )
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not confirm reading')
    }
  }
  return (
    <div className="project-layout">
      <ProjectSidebar id={id} detail={detail} />
      <main className="workspace report-page">
        <header className="report-page-heading">
          <div>
            <p>PROJECT / {detail?.project.name ?? 'Loading…'}</p>
            <h1>Review</h1>
          </div>
        </header>
        {error && (
          <p role="alert" className="error-banner">
            {error}
          </p>
        )}
        <label className="review-filter">
          Filter by reason
          <select value={reason} onChange={(event) => setReason(event.target.value)}>
            <option value="all">All reasons</option>
            {[
              'missing',
              'estimated',
              'assumed',
              'unknown_material',
              'conflict',
              'model_unsure',
              'unverified',
            ].map((value) => (
              <option key={value} value={value}>
                {value.replaceAll('_', ' ')}
              </option>
            ))}
          </select>
        </label>
        <section className="report-card">
          <h2>Open questions · {visible.length}</h2>
          {visible.length ? (
            [...groupedVisible.entries()].map(([group, rows]) => (
              <section className="review-group" key={group}>
                <h3>{group}</h3>
                {rows.map(({ item, question }) => (
                  <article
                    className="question-card overview-question"
                    key={`${item.module_id}:${question.id}`}
                  >
                    <div className="question-heading">
                      <strong>{item.title}</strong>
                      <span className={`reason-badge ${question.reason}`}>
                        {question.reason.replaceAll('_', ' ')}
                      </span>
                    </div>
                    <p>
                      {question.label}
                      {question.note ? ` · ${question.note}` : ''}
                    </p>
                    <QuestionCrop
                      projectId={id}
                      model={modules[item.module_id] ?? null}
                      fileIndex={
                        detail?.project.files.find(
                          (file) =>
                            file.name ===
                            detail?.modules.find((module) => module.module_id === item.module_id)?.file_name,
                        )?.index ?? 0
                      }
                      question={question}
                    />
                    <div className="header-actions">
                      {!viewer && (
                        <InlineConfirm question={question} onConfirm={() => void confirm(item, question)} />
                      )}
                      <Link
                        className="button secondary"
                        to={`/p/${encodeURIComponent(id)}/m/${encodeURIComponent(item.module_id)}?tab=review`}
                      >
                        Open module review
                      </Link>
                    </div>
                  </article>
                ))}
              </section>
            ))
          ) : (
            <p className="empty-report">No open questions match this filter.</p>
          )}
        </section>
        <section className="report-card">
          <button
            className="collapse-heading"
            aria-expanded={!collapsed}
            onClick={() => setCollapsed(!collapsed)}
          >
            <strong>Unverified readings</strong>
            <span>{collapsed ? 'Show' : 'Hide'}</span>
          </button>
          {!collapsed &&
            pending
              .filter(({ question }) => question.reason === 'unverified')
              .map(({ item, question }) => (
                <p key={`${item.module_id}:${question.id}`}>
                  {item.title} · {question.label} · {question.current_mm ?? question.current_value ?? '—'}
                </p>
              ))}
        </section>
      </main>
    </div>
  )
}
function InlineConfirm({ question, onConfirm }: { question: Question; onConfirm: () => void }) {
  const confirmable =
    question.kind === 'dimension' ? question.current_mm != null : question.current_value != null
  return (
    <button className="button secondary" disabled={!confirmable} onClick={onConfirm}>
      Confirm current value
    </button>
  )
}

export function QuestionCrop({
  projectId,
  model,
  fileIndex,
  question,
}: {
  projectId: string
  model: ModuleModel | null
  fileIndex: number
  question: Question
}) {
  // The parent owns the module (the review tab has it; the overview page fetches each module once).
  const module = model?.module ?? null
  const marks = module?.marks.filter((mark) => question.candidate_mark_ids.includes(mark.id)) ?? []
  const box =
    module?.view_bbox ??
    marks.reduce(
      (crop, mark) => ({
        x_min: Math.min(crop.x_min, mark.bbox.x_min),
        y_min: Math.min(crop.y_min, mark.bbox.y_min),
        x_max: Math.max(crop.x_max, mark.bbox.x_max),
        y_max: Math.max(crop.y_max, mark.bbox.y_max),
        label: '',
      }),
      { x_min: 0.2, y_min: 0.2, x_max: 0.8, y_max: 0.8, label: '' },
    )
  const width = Math.max(0.08, box.x_max - box.x_min)
  const height = Math.max(0.08, box.y_max - box.y_min)
  return (
    <div className="review-crop" aria-label="Drawing crop with candidate marks">
      <img
        src={`/api/projects/${encodeURIComponent(projectId)}/pages/${fileIndex}/${module?.page_index ?? 0}.png`}
        alt="Drawing detail"
        style={{
          width: `${100 / width}%`,
          height: `${100 / height}%`,
          left: `${(-100 * box.x_min) / width}%`,
          top: `${(-100 * box.y_min) / height}%`,
        }}
      />
      <svg
        viewBox={`${box.x_min} ${box.y_min} ${width} ${height}`}
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        {marks.map((mark) => (
          <rect
            key={mark.id}
            x={mark.bbox.x_min}
            y={mark.bbox.y_min}
            width={mark.bbox.x_max - mark.bbox.x_min}
            height={mark.bbox.y_max - mark.bbox.y_min}
          />
        ))}
      </svg>
    </div>
  )
}

export function ActivityPage() {
  const { id = '' } = useParams()
  const [detail, setDetail] = useState<ProjectDetail | null>(null)
  const [activity, setActivity] = useState<ActivityEntry[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    void Promise.all([
      api<ProjectDetail>(`/api/projects/${encodeURIComponent(id)}`),
      api<ActivityEntry[]>(`/api/projects/${encodeURIComponent(id)}/activity`),
    ])
      .then(([project, entries]) => {
        if (active) {
          setDetail(project)
          setActivity(entries)
        }
      })
      .catch((e: unknown) => {
        if (active) setError(e instanceof Error ? e.message : 'Could not load activity')
      })
    return () => {
      active = false
    }
  }, [id])
  return (
    <div className="project-layout">
      <ProjectSidebar id={id} detail={detail} />
      <main className="workspace report-page">
        <header className="report-page-heading">
          <div>
            <p>PROJECT / {detail?.project.name ?? 'Loading…'}</p>
            <h1>Activity</h1>
          </div>
        </header>
        {error && (
          <p role="alert" className="error-banner">
            {error}
          </p>
        )}
        <section className="report-card">
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>User</th>
                  <th>Action</th>
                  <th>Module</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {activity.map((entry, index) => (
                  <tr key={`${entry.at}-${index}`}>
                    <td>{new Date(entry.at).toLocaleString()}</td>
                    <td>{entry.user}</td>
                    <td>{entry.action}</td>
                    <td>{entry.module_id ?? '—'}</td>
                    <td>{entry.detail ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!activity.length && <p className="empty-report">No activity recorded.</p>}
        </section>
      </main>
    </div>
  )
}

export function HistoryPanel({
  projectId,
  moduleId,
  onRestore,
  canRestore,
}: {
  projectId: string
  moduleId: string
  onRestore: (model: ModuleModel) => void
  canRestore: boolean
}) {
  const [versions, setVersions] = useState<ModuleVersion[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<number | null>(null)
  // Bumped after a restore, which itself adds a version.
  const [revision, setRevision] = useState(0)
  const endpoint = `/api/projects/${encodeURIComponent(projectId)}/modules/${encodeURIComponent(moduleId)}/versions`
  useEffect(() => {
    let active = true
    void api<ModuleVersion[]>(endpoint)
      .then((value) => {
        if (active) setVersions(value)
      })
      .catch((e: unknown) => {
        if (active) setError(e instanceof Error ? e.message : 'Could not load history')
      })
    return () => {
      active = false
    }
  }, [endpoint, revision])
  const restore = async (version: number) => {
    setBusy(version)
    setError('')
    try {
      onRestore(await api<ModuleModel>(`${endpoint}/${version}/restore`, { method: 'POST' }))
      setRevision((value) => value + 1)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not restore version')
    } finally {
      setBusy(null)
    }
  }
  return (
    <section className="report-card">
      <h2>Module history</h2>
      {error && (
        <p role="alert" className="error-message">
          {error}
        </p>
      )}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Version</th>
              <th>When</th>
              <th>User</th>
              <th>Action</th>
              <th>Summary</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {versions.map((version) => (
              <tr key={version.version}>
                <td>{version.version}</td>
                <td>{new Date(version.at).toLocaleString()}</td>
                <td>{version.user ?? 'System'}</td>
                <td>{version.action}</td>
                <td>{version.summary}</td>
                <td>
                  {canRestore && (
                    <button
                      className="button secondary"
                      disabled={busy != null}
                      onClick={() => void restore(version.version)}
                    >
                      {busy === version.version ? 'Restoring…' : 'Restore'}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
