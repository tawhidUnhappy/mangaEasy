import { Component, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

/** Last-resort renderer error catch — a crash in one view must not take the
 * whole window down to a blank page with no explanation. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error): void {
    console.error('renderer crashed:', error)
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div style={{ padding: 24 }}>
          <h3>Something went wrong in the interface</h3>
          <p className="hint">
            The running job (if any) is unaffected. The error below may help when reporting a bug —
            logs are in the data folder&apos;s <span className="mono">.mangaeasy/logs/</span>.
          </p>
          <pre style={{ whiteSpace: 'pre-wrap', color: '#e57373' }}>
            {this.state.error.message}
            {'\n'}
            {this.state.error.stack}
          </pre>
          <button onClick={() => window.location.reload()}>Reload interface</button>
        </div>
      )
    }
    return this.props.children
  }
}
