/**
 * Tracks which Flask sub-editor (cut-page, panel-editor, narration-editor, …)
 * is currently embedded in the Editor tab — mirrors nicegui_app.py's
 * `current_editor` dict + `_set_editor_frame`.
 */
import { createContext, useCallback, useContext, useState } from 'react'

export const EDITOR_LABELS: Record<string, string> = {
  'cut-page': 'Crop',
  'panel-editor': 'Panel Editor',
  'narration-editor': 'Narration Writer',
  'narration-editor-all': 'Narration Writer (All)',
  'narration-review': 'Narration Review'
}

interface EditorContextValue {
  name: string | null
  url: string | null
  reloadToken: number
  launching: string | null
  launch: (name: string) => Promise<void>
  reload: () => void
  close: () => void
}

const EditorContext = createContext<EditorContextValue | null>(null)

export function EditorProvider({ children }: { children: React.ReactNode }): React.JSX.Element {
  const [name, setName] = useState<string | null>(null)
  const [url, setUrl] = useState<string | null>(null)
  const [reloadToken, setReloadToken] = useState(0)
  const [launching, setLaunching] = useState<string | null>(null)

  const launch = useCallback(
    async (editorName: string) => {
      setLaunching(editorName)
      try {
        const alreadyRunning = await window.api.isEditorRunning(editorName)
        const editorUrl = alreadyRunning ? url : await window.api.launchEditor(editorName)
        setName(editorName)
        setUrl(editorUrl)
        setReloadToken((t) => t + 1)
      } finally {
        setLaunching(null)
      }
    },
    [url]
  )

  const reload = useCallback(() => setReloadToken((t) => t + 1), [])
  const close = useCallback(() => {
    setName(null)
    setUrl(null)
  }, [])

  return (
    <EditorContext.Provider value={{ name, url, reloadToken, launching, launch, reload, close }}>
      {children}
    </EditorContext.Provider>
  )
}

export function useEditor(): EditorContextValue {
  const ctx = useContext(EditorContext)
  if (!ctx) throw new Error('useEditor() must be used inside <EditorProvider>')
  return ctx
}
