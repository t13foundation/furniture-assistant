import { useRef, useState, type DragEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from './api'

export default function NewProject() {
  const [files, setFiles] = useState<File[]>([])
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const navigate = useNavigate()
  const add = (incoming: FileList | File[]) =>
    setFiles((current) => [
      ...current,
      ...Array.from(incoming).filter(
        (file) => !current.some((existing) => existing.name === file.name && existing.size === file.size),
      ),
    ])
  const drop = (event: DragEvent) => {
    event.preventDefault()
    add(event.dataTransfer.files)
  }
  const submit = async () => {
    if (!files.length) return
    setBusy(true)
    setError('')
    try {
      const form = new FormData()
      form.set('name', name)
      files.forEach((file) => form.append('files', file))
      const created = await api<{ id: string }>('/api/projects', { method: 'POST', body: form })
      try {
        await api(`/api/projects/${encodeURIComponent(created.id)}/analyze`, { method: 'POST' })
      } catch (err) {
        navigate(`/p/${encodeURIComponent(created.id)}`, {
          state: { analysisError: err instanceof Error ? err.message : 'Could not start analysis' },
        })
        return
      }
      navigate(`/p/${encodeURIComponent(created.id)}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create project')
    } finally {
      setBusy(false)
    }
  }
  return (
    <main className="upload-page">
      <header className="topbar">
        <Link to="/" className="brand">
          Furniture Assistant
        </Link>
        <span>Furniture drawing workspace</span>
      </header>
      <section className="upload-card">
        <Link to="/" className="back-link">
          ← All projects
        </Link>
        <h1>New project</h1>
        <p>Upload drawings and analyze furniture modules.</p>
        <label className="field-label">
          Project name{' '}
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="e.g. Apartment 04"
          />
        </label>
        <div className="dropzone" onDragOver={(event) => event.preventDefault()} onDrop={drop}>
          <div className="upload-icon">↑</div>
          <strong>Drop drawing files here</strong>
          <span>PDF, images, CAD and supported drawing files</span>
          <button className="button secondary" type="button" onClick={() => input.current?.click()}>
            Select files
          </button>
          <input
            ref={input}
            type="file"
            multiple
            hidden
            onChange={(event) => {
              // Copy before clearing: the FileList is live and empties when value is reset.
              if (event.target.files) add(Array.from(event.target.files))
              event.target.value = ''
            }}
          />
        </div>
        {files.length > 0 && (
          <>
            <div className="file-summary">
              {files.length} {files.length === 1 ? 'file' : 'files'} ready
            </div>
            <ul className="file-list">
              {files.map((file, index) => (
                <li key={`${file.name}-${file.size}-${index}`}>
                  <span>▧ {file.name}</span>
                  <button
                    aria-label={`Remove ${file.name}`}
                    onClick={() => setFiles((current) => current.filter((_, i) => i !== index))}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
        {error && <p className="error-message">{error}</p>}
        <button className="button primary full" disabled={!files.length || busy} onClick={submit}>
          {busy ? 'Starting analysis…' : 'Run analysis'}
        </button>
      </section>
    </main>
  )
}
