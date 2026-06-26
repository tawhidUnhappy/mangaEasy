import { useState } from 'react'
import { JobProvider } from './job-context'
import { EditorProvider } from './editor-context'
import { ProgressBar } from './components/ProgressBar'
import { Terminal } from './components/Terminal'
import { Setup } from './views/Setup'
import { Project } from './views/Project'
import { Workflow } from './views/Workflow'
import { Batch } from './views/Batch'
import { Editor } from './views/Editor'

const TABS = [
  ['setup', 'Setup'],
  ['project', 'Project'],
  ['workflow', 'Make a video'],
  ['batch', 'Batch videos'],
  ['editor', 'Editor']
] as const

type TabKey = (typeof TABS)[number][0]

function App(): React.JSX.Element {
  const [tab, setTab] = useState<TabKey>('setup')

  return (
    <JobProvider>
      <EditorProvider>
        <div className="app-shell">
          <div className="tab-bar">
            {TABS.map(([key, label]) => (
              <button key={key} className={tab === key ? 'active' : ''} onClick={() => setTab(key)}>
                {label}
              </button>
            ))}
          </div>
          <ProgressBar />

          <div style={{ flex: '1 1 62%', minHeight: 0, display: 'flex', flexDirection: 'column' }}>
            {/* Every view stays mounted, just hidden — a batch run in progress
                shouldn't be torn down by switching tabs and back. */}
            <Tab active={tab === 'setup'}>
              <Setup />
            </Tab>
            <Tab active={tab === 'project'}>
              <Project />
            </Tab>
            <Tab active={tab === 'workflow'}>
              <Workflow />
            </Tab>
            <Tab active={tab === 'batch'}>
              <Batch />
            </Tab>
            <Tab active={tab === 'editor'}>
              <Editor />
            </Tab>
          </div>

          <div style={{ flex: '1 1 38%', minHeight: 140, borderTop: '1px solid #3a3a3e' }}>
            <Terminal />
          </div>
        </div>
      </EditorProvider>
    </JobProvider>
  )
}

function Tab({ active, children }: { active: boolean; children: React.ReactNode }): React.JSX.Element {
  return <div style={{ display: active ? 'flex' : 'none', flex: 1, minHeight: 0 }}>{children}</div>
}

export default App
