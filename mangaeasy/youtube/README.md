# mangaeasy/youtube — publish to YouTube

The **publish** stage: connect one or more named channel profiles, then upload
(and, if needed, delete) finished videos through an explicitly selected
profile. See [docs/youtube.md](../../docs/youtube.md).

## Files

| File | Command | Role |
|---|---|---|
| [`auth.py`](auth.py) | `youtube-profiles`, `youtube-auth`, `youtube-status`, `youtube-logout` | Profile discovery, OAuth connect flow, status, and disconnect; google-auth imports stay lazy |
| [`upload.py`](upload.py) | `youtube-upload` | resumable upload, hand-rolled `requests` against the resumable protocol |
| [`delete.py`](delete.py) | `youtube-delete` | delete a video (two-step: needs `--confirm`) |
| [`store.py`](store.py) | — | shared root OAuth client, safe names, legacy `default`, and isolated named token/channel storage |

## Gotchas (all load-bearing — see CLAUDE.md)

- **Tokens are secrets**: print paths/booleans, never contents.
- **Profile is publish identity**: all channel operations accept `--profile`.
  Never guess when multiple cached channels are plausible. The omitted value
  is the backwards-compatible `default`, not an instruction to choose any
  available account.
- **One client, separate grants**: `<home>/youtube/client_secret.json` is the
  predefined shared Desktop-app client. Named profiles prefer an optional own
  client but normally reuse the shared file while keeping distinct token and
  channel JSON.
- **Live commands auto-authorize**: missing/failed tokens and API 401 responses
  open browser consent and retry once. `--no-auto-auth` is the headless opt-out;
  offline status/profile listing never opens a browser.
- **Upload is hand-rolled `requests`** against the resumable protocol, not the
  Google discovery client — keeps the frozen build small and deps shallow.
  Keep it that way.
- **Default privacy stays `private`**: YouTube force-locks uploads from
  unaudited API projects to private regardless of the request — don't "fix" it.
- `store.SCOPES` requests **full video management** (`youtube.force-ssl`) so a
  bad take can be deleted/replaced via the API. Tokens minted before that
  upload fine but 403 on delete/update — the fix is re-running `youtube-auth`
  (re-consent), not code.
- `youtube-upload --json` prints its JSON object as the **last** stdout line
  (after `MANGAEASY_RESULT`) because the MCP server parses the final line.

## Tests

[tests/test_youtube.py](../../tests/test_youtube.py).
