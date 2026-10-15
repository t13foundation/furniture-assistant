import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

type Props = { children: ReactNode }
type State = { error: Error | null }

/** Last-resort fallback: a render error shows a message and a reload button instead of a blank page. */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Unhandled render error', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <main className="auth-page">
        <h1>Something went wrong</h1>
        <p>{this.state.error.message}</p>
        <button className="button primary" type="button" onClick={() => location.reload()}>
          Reload
        </button>
      </main>
    )
  }
}
