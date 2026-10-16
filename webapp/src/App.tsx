import { lazy, Suspense, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { HashRouter, Route, Routes } from 'react-router-dom'
import { api } from './api'
import type { AuthState } from './types'
import { AuthContext } from './auth'
import ErrorBoundary from './ErrorBoundary'
import './styles.css'

const NewProject = lazy(() => import('./NewProject'))
const ProjectView = lazy(() => import('./ProjectView'))
const ProjectList = lazy(() => import('./ProjectView').then(({ ProjectList }) => ({ default: ProjectList })))
const ProjectReport = lazy(() => import('./Reports'))
const PricingSettings = lazy(() =>
  import('./Reports').then(({ PricingSettings }) => ({ default: PricingSettings })),
)
const ProjectReviewPage = lazy(() =>
  import('./AdditionalPages').then(({ ProjectReviewPage }) => ({ default: ProjectReviewPage })),
)
const ActivityPage = lazy(() =>
  import('./AdditionalPages').then(({ ActivityPage }) => ({ default: ActivityPage })),
)

export default function App() {
  const [auth, setAuth] = useState<AuthState | null>(null)
  const [error, setError] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [unauthorized, setUnauthorized] = useState(false)
  useEffect(() => {
    let active = true
    const onUnauthorized = () => setUnauthorized(true)
    window.addEventListener('fa-unauthorized', onUnauthorized)
    void api<AuthState>('/api/auth/me')
      .then((value) => {
        if (active) {
          setAuth(value)
          setUnauthorized(value.auth_enabled && !value.username)
        }
      })
      .catch((e: unknown) => {
        if (active) setError(e instanceof Error ? e.message : 'Could not check sign-in status')
      })
    return () => {
      active = false
      window.removeEventListener('fa-unauthorized', onUnauthorized)
    }
  }, [])
  const login = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError('')
    try {
      const result = await api<{ username: string; role: AuthState['role'] }>('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      setAuth((current) => ({
        auth_enabled: current?.auth_enabled ?? true,
        username: result.username,
        role: result.role,
      }))
      setUnauthorized(false)
      setPassword('')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not sign in')
    }
  }
  const logout = async () => {
    await api('/api/auth/logout', { method: 'POST' })
    setAuth((current) => (current ? { ...current, username: null, role: null } : current))
    setUnauthorized(true)
  }
  if (!auth) return <main className="auth-page">{error || 'Loading sign-in status…'}</main>
  return (
    <AuthContext.Provider value={{ auth, logout }}>
      <ErrorBoundary>
        <HashRouter>
          {unauthorized ? (
            <main className="auth-page">
              <form className="report-card auth-card" onSubmit={(event) => void login(event)}>
                <h1>Sign in</h1>
                <label>
                  Username
                  <input
                    autoComplete="username"
                    value={username}
                    onChange={(event) => setUsername(event.target.value)}
                    required
                  />
                </label>
                <label>
                  Password
                  <input
                    type="password"
                    autoComplete="current-password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    required
                  />
                </label>
                {error && (
                  <p role="alert" className="error-message">
                    {error}
                  </p>
                )}
                <button className="button primary" type="submit">
                  Sign in
                </button>
              </form>
            </main>
          ) : (
            <Suspense fallback={<main className="auth-page">Loading…</main>}>
              <Routes>
                <Route path="/" element={<ProjectList />} />
                <Route path="/new" element={<NewProject />} />
                <Route path="/settings/pricing" element={<PricingSettings />} />
                <Route path="/p/:id" element={<ProjectView />} />
                <Route path="/p/:id/m/:moduleId" element={<ProjectView />} />
                <Route path="/p/:id/files" element={<ProjectReport page="files" />} />
                <Route path="/p/:id/materials" element={<ProjectReport page="materials" />} />
                <Route path="/p/:id/processes" element={<ProjectReport page="processes" />} />
                <Route path="/p/:id/valuation" element={<ProjectReport page="valuation" />} />
                <Route path="/p/:id/pricing" element={<PricingSettings />} />
                <Route path="/p/:id/review" element={<ProjectReviewPage />} />
                <Route path="/p/:id/activity" element={<ActivityPage />} />
                <Route path="*" element={<ProjectList />} />
              </Routes>
            </Suspense>
          )}
        </HashRouter>
      </ErrorBoundary>
    </AuthContext.Provider>
  )
}
