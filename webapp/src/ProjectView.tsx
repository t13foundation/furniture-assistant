import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Link, NavLink, useLocation, useParams } from 'react-router-dom'
import { api } from './api'
import ModuleView from './ModuleView'
import { useAuth } from './auth'
import type { Job, ModuleModel, ProjectDetail, ProjectSummary } from './types'

function UsageSummary({ title, usage }: { title: string; usage: Job['usage'] }) {
  return (
    <span className="job-usage">
      {title}: {usage.requests} requests · {usage.input_tokens} input / {usage.output_tokens} output tokens
      {usage.estimated_cost !== null && ` · estimated cost ${usage.estimated_cost.toFixed(4)}`}
    </span>
  )
}

export function ProjectList() {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const { auth, logout } = useAuth()
  const [error, setError] = useState('')
  const refresh = useCallback(
    () =>
      api<ProjectSummary[]>('/api/projects')
        .then(setProjects)
        .catch((err) => setError(err.message)),
    [],
  )
  useEffect(() => {
    void refresh()
  }, [refresh])
  return (
    <main className="projects-page">
      <header className="topbar">
        <Link to="/" className="brand">
          Furniture Assistant
        </Link>
        <span>Furniture drawing workspace</span>
        {auth.username && <span className="signed-in-user">{auth.username}</span>}
        {auth.auth_enabled && (
          <button className="button secondary" onClick={() => void logout()}>
            Sign out
          </button>
        )}
        <div className="topbar-actions">
          <Link to="/settings/pricing" className="button secondary">
            Pricing settings
          </Link>
          <Link to="/new" className="button primary">
            + New project
          </Link>
        </div>
      </header>
      <section className="projects-content">
        <div className="page-heading">
          <div>
            <div className="eyebrow">WORKSPACE</div>
            <h1>Projects</h1>
            <p>Your furniture drawings and analysis results.</p>
          </div>
          <Link to="/new" className="button primary">
            + New project
          </Link>
        </div>
        {error && <p className="error-message">{error}</p>}
        {projects.length ? (
          <div className="project-grid">
            {projects.map((project) => (
              <Link key={project.id} className="project-card" to={`/p/${encodeURIComponent(project.id)}`}>
                <div className="project-card-icon">▧</div>
                <h2>{project.name || 'Untitled project'}</h2>
                <span>
                  {project.files.length} files · {project.module_count} modules
                </span>
                <span
                  className={`status-pill ${project.job_status === 'done' ? 'done' : project.job_status === 'error' ? 'error' : 'running'}`}
                >
                  {project.job_status || 'Not analyzed'}
                </span>
              </Link>
            ))}
          </div>
        ) : (
          <div className="empty-state">
            <div className="empty-icon">▧</div>
            <h2>No projects yet</h2>
            <p>Start by uploading your furniture drawings.</p>
            <Link to="/new" className="button primary">
              Create your first project
            </Link>
          </div>
        )}
      </section>
    </main>
  )
}

export default function ProjectView() {
  const { id = '', moduleId } = useParams()
  const location = useLocation()
  const [detail, setDetail] = useState<ProjectDetail | null>(null)
  const [model, setModel] = useState<ModuleModel | null>(null)
  const [loadedKey, setLoadedKey] = useState('')
  const [error, setError] = useState(
    (location.state as { analysisError?: string } | null)?.analysisError ?? '',
  )
  const projectIdRef = useRef(id)
  const refresh = useCallback(async () => {
    const result = await api<ProjectDetail>(`/api/projects/${encodeURIComponent(id)}`)
    if (projectIdRef.current === id) setDetail(result)
  }, [id])
  useEffect(() => {
    let active = true
    const poll = async () => {
      try {
        const result = await api<ProjectDetail>(`/api/projects/${encodeURIComponent(id)}`)
        if (active) setDetail(result)
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : 'Could not load project')
      }
    }
    void poll()
    const timer = window.setInterval(() => {
      if (detail?.job?.status === 'running' || detail?.job?.status === 'queued') void poll()
    }, 2000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [id, detail?.job?.status])
  const selectedModuleId = moduleId ?? detail?.modules[0]?.module_id
  const selection = useRef({ id, moduleId: selectedModuleId })
  useLayoutEffect(() => {
    projectIdRef.current = id
    selection.current = { id, moduleId: selectedModuleId }
  }, [id, selectedModuleId])
  const projectLoaded = detail?.project.id === id
  useEffect(() => {
    setModel(null)
    setLoadedKey('')
    if (!projectLoaded || !selectedModuleId) return
    let active = true
    const key = `${id}:${selectedModuleId}`
    void api<ModuleModel>(
      `/api/projects/${encodeURIComponent(id)}/modules/${encodeURIComponent(selectedModuleId)}`,
    )
      .then((value) => {
        if (active) {
          setModel(value)
          setLoadedKey(key)
        }
      })
      .catch((err) => {
        if (active) setError(err.message)
      })
    return () => {
      active = false
    }
  }, [projectLoaded, id, selectedModuleId])
  const updateModel = useCallback(
    (value: ModuleModel) => {
      if (selection.current.id === id && selection.current.moduleId === value.module.module_id) {
        setModel(value)
        setLoadedKey(`${id}:${value.module.module_id}`)
      }
      if (selection.current.id === id) void refresh()
    },
    [id, refresh],
  )
  const runAnalysis = async () => {
    setError('')
    try {
      await api(`/api/projects/${encodeURIComponent(id)}/analyze`, { method: 'POST' })
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start analysis')
    }
  }
  const completed =
    detail?.job?.items.filter((item) => ['done', 'skipped', 'error'].includes(item.status)).length ?? 0
  const total = detail?.job?.items.length ?? 0
  const selectedModel =
    model?.module.module_id === selectedModuleId && loadedKey === `${id}:${selectedModuleId}` ? model : null
  return (
    <div className="project-layout">
      <ProjectSidebar id={id} detail={detail} moduleId={selectedModuleId} />
      <main className="workspace">
        {error && <div className="error-banner">{error}</div>}
        {detail?.job && ['running', 'queued'].includes(detail.job.status) && (
          <div className="job-banner">
            <span className="spinner" /> Analysis running · {completed} of {total} pages processed
          </div>
        )}
        {detail && (
          <div className="job-banner">
            <UsageSummary title="Project total" usage={detail.usage} />
            {detail.job && <UsageSummary title="This analysis" usage={detail.job.usage} />}
          </div>
        )}
        {detail?.job?.status === 'error' && (
          <div className="error-banner">Analysis failed: {detail.job.error ?? 'unknown error'}</div>
        )}
        {detail && (!detail.job || detail.job.status === 'error') && (
          <button className="button primary" onClick={() => void runAnalysis()}>
            Run analysis
          </button>
        )}
        {selectedModel ? (
          <ModuleView
            key={`${id}:${selectedModuleId}`}
            id={id}
            model={selectedModel}
            files={detail?.project.files ?? []}
            onUpdate={updateModel}
          />
        ) : (
          <div className="empty-state">
            <h2>
              {selectedModuleId
                ? 'Loading module…'
                : detail?.job?.status === 'done'
                  ? 'No furniture modules found'
                  : 'Preparing your drawings…'}
            </h2>
          </div>
        )}
      </main>
    </div>
  )
}

/** Project navigation shared by the module view and the project report pages. */
export function ProjectSidebar({
  id,
  detail,
  moduleId,
}: {
  id: string
  detail: ProjectDetail | null
  moduleId?: string
}) {
  const [open, setOpen] = useState(false)
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const { auth, logout } = useAuth()
  const completed =
    detail?.job?.items.filter((item) => ['done', 'skipped', 'error'].includes(item.status)).length ?? 0
  const total = detail?.job?.items.length ?? 0
  const groups = new Map<string, ProjectDetail['modules']>()
  for (const item of detail?.modules ?? []) {
    const group = item.group_id || item.module_id
    groups.set(group, [...(groups.get(group) ?? []), item])
  }
  return (
    <>
      <button
        className="mobile-menu-trigger"
        aria-label="Open project navigation"
        onClick={() => setOpen(true)}
      >
        ☰ Menu
      </button>
      {open && (
        <button className="sidebar-overlay" aria-label="Close navigation" onClick={() => setOpen(false)} />
      )}
      <aside className={`sidebar ${open ? 'sidebar-open' : ''}`}>
        <button className="mobile-menu-button" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? 'Close menu' : '☰ Menu'}
        </button>
        <Link to="/" className="brand">
          Furniture Assistant
        </Link>
        <Link to="/" className="back-link">
          ← Projects
        </Link>
        <div className="sidebar-project">
          <div className="eyebrow">PROJECT</div>
          <strong>{detail?.project.name || 'Loading…'}</strong>
        </div>
        {detail?.job && ['running', 'queued'].includes(detail.job.status) && (
          <div className="progress-block">
            <div className="progress-caption">
              Analysis progress{' '}
              <span>
                {completed}/{total}
              </span>
            </div>
            <div className="progress-track">
              <div style={{ width: `${total ? (completed / total) * 100 : 0}%` }} />
            </div>
          </div>
        )}
        <div className="sidebar-label">PROJECT</div>
        {(
          [
            ['files', 'Uploaded files'],
            ['review', 'Review'],
            ['activity', 'Activity'],
            ['materials', 'Material summary'],
            ['processes', 'Production processes'],
            ['valuation', 'Valuation'],
          ] as const
        ).map(([path, label]) => (
          <NavLink
            className="project-nav-link"
            key={path}
            to={`/p/${encodeURIComponent(id)}/${path}`}
            onClick={() => setOpen(false)}
          >
            {label}
          </NavLink>
        ))}
        <NavLink
          className="project-nav-link"
          to={`/p/${encodeURIComponent(id)}/pricing`}
          onClick={() => setOpen(false)}
        >
          Pricing settings
        </NavLink>
        <div className="sidebar-label">CABINETS / MODULES</div>
        {[...groups.entries()].map(([group, items]) => {
          const attention = items.reduce((sum, item) => sum + item.attention_count, 0)
          const groupOpen = !collapsed[group]
          return (
            <section className="module-group" key={group}>
              <button
                className="module-group-heading"
                aria-expanded={groupOpen}
                onClick={() => setCollapsed((current) => ({ ...current, [group]: !current[group] }))}
              >
                <span>{group}</span>
                <span>
                  {attention > 0 && <span className="question-count">{attention}</span>}{' '}
                  {groupOpen ? '−' : '+'}
                </span>
              </button>
              {groupOpen &&
                items.map((item) => {
                  const status = item.status === 'ok' ? 'done' : item.status
                  return (
                    <Link
                      key={item.module_id}
                      className={`module-link ${item.module_id === moduleId ? 'selected' : ''}`}
                      to={`/p/${encodeURIComponent(id)}/m/${encodeURIComponent(item.module_id)}`}
                      onClick={() => setOpen(false)}
                    >
                      <span className="module-link-label">
                        <span className="module-link-title">{item.title}</span>
                        <small className="module-link-id">{item.module_id}</small>
                      </span>
                      <span className={`status-pill ${status}`}>{status}</span>
                      {item.attention_count > 0 && (
                        <span
                          className="question-count"
                          title={`${item.attention_count} need attention, ${item.question_count} to review`}
                        >
                          {item.attention_count}
                        </span>
                      )}
                      {item.refine_status === 'running' && (
                        <span className="spinner" aria-label="Model refining" />
                      )}
                      {item.issue_counts.error > 0 && (
                        <span className="issue-count">{item.issue_counts.error}</span>
                      )}
                    </Link>
                  )
                })}
            </section>
          )
        })}
        {detail && detail.modules.length === 0 && <div className="sidebar-empty">No modules found yet.</div>}
        <Link to="/new" className="sidebar-new">
          + New project
        </Link>
        <footer className="sidebar-footer">
          {auth.username && <span>{auth.username}</span>}
          {auth.auth_enabled && (
            <button className="button secondary" onClick={() => void logout()}>
              Sign out
            </button>
          )}
        </footer>
      </aside>
    </>
  )
}

/** Navigation for pages outside a project (workspace pricing settings). */
export function WorkspaceSidebar() {
  const [open, setOpen] = useState(false)
  const { auth, logout } = useAuth()
  return (
    <>
      <button
        className="mobile-menu-trigger"
        aria-label="Open workspace navigation"
        onClick={() => setOpen(true)}
      >
        ☰ Menu
      </button>
      {open && (
        <button className="sidebar-overlay" aria-label="Close navigation" onClick={() => setOpen(false)} />
      )}
      <aside className={`sidebar ${open ? 'sidebar-open' : ''}`}>
        <button className="mobile-menu-button" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? 'Close menu' : '☰ Menu'}
        </button>
        <Link to="/" className="brand">
          Furniture Assistant
        </Link>
        <Link to="/" className="back-link">
          ← Projects
        </Link>
        <div className="sidebar-label">WORKSPACE</div>
        <NavLink className="project-nav-link" to="/" end>
          Projects
        </NavLink>
        <NavLink className="project-nav-link" to="/settings/pricing">
          Pricing settings
        </NavLink>
        <Link to="/new" className="sidebar-new">
          + New project
        </Link>
        <footer className="sidebar-footer">
          {auth.username && <span>{auth.username}</span>}
          {auth.auth_enabled && (
            <button className="button secondary" onClick={() => void logout()}>
              Sign out
            </button>
          )}
        </footer>
      </aside>
    </>
  )
}
