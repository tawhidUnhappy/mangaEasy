import { EDITOR_LABELS, useEditor } from '../editor-context'

/** Editor tab — embeds whichever Flask sub-editor (cut-page, panel-editor,
 * narration-editor, …) was last launched from the Workflow tab, via
 * Electron's <webview> tag instead of nicegui's iframe + fetch-polling probe
 * (launch() already waits for the MANGAEASY_OPEN_URL signal before
 * resolving, so there's no "is it up yet?" polling needed here at all). */
export function Editor(): React.JSX.Element {
  const { name, url, reloadToken, launching, reload, close } = useEditor()

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      <div
        className="row"
        style={{
          padding: '6px 10px',
          borderBottom: '1px solid #3a3a3e',
          justifyContent: 'space-between'
        }}
      >
        <strong>{name ? (EDITOR_LABELS[name] ?? name) : 'Editor'}</strong>
        <div className="row">
          <button onClick={reload} disabled={!url}>
            Reload
          </button>
          <button className="negative" onClick={close} disabled={!url}>
            Close
          </button>
        </div>
      </div>
      <div style={{ flex: 1, minHeight: 0, position: 'relative', background: '#111' }}>
        {launching && (
          <div style={{ color: '#888', padding: 16 }}>
            Starting {EDITOR_LABELS[launching] ?? launching}…
          </div>
        )}
        {!launching && !url && (
          <div style={{ color: '#777', padding: 16 }}>
            Open an editor from the &quot;Make a video&quot; tab.
          </div>
        )}
        {url && <webview key={reloadToken} src={url} style={{ width: '100%', height: '100%' }} />}
      </div>
    </div>
  )
}
