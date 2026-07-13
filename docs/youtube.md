# Uploading to YouTube from mangaEasy

mangaEasy can upload your finished videos straight to your YouTube channel —
from the CLI (`mangaeasy youtube-upload`) or an AI assistant (MCP tool
`youtube_upload`). You connect your account once (`mangaeasy youtube-auth`);
the login token lives in mangaEasy's own data folder and can be removed any
time with `mangaeasy youtube-logout`.

Uploading requires a one-time ~10 minute Google setup, because YouTube's
API rules make every user bring their **own** (free) API credentials —
explained at the bottom. Follow the steps in order.

---

## Part 1 — Create your own Google OAuth client (one time, free)

1. **Open the Google Cloud console**: https://console.cloud.google.com/
   (sign in with the Google account that owns your YouTube channel).
2. **Create a project**: top bar → project picker → **New project** → name
   it anything (e.g. `mangaeasy-uploads`) → Create → make sure it's
   selected.
3. **Enable the YouTube Data API v3**: menu → *APIs & Services* →
   *Library* → search "YouTube Data API v3" → **Enable**.
4. **Configure the consent screen**: *APIs & Services* → *OAuth consent
   screen*:
   - User type: **External** → Create.
   - App name (anything, e.g. `mangaEasy`), your email in both email
     fields → Save through the remaining screens (scopes/test users can be
     left empty).
   - **Important:** on the consent screen page, set **Publishing status to
     "In production"** (button: *Publish app*). Leaving it in "Testing"
     makes Google expire your login every 7 days. "In production" without
     Google's verification is fine for personal use — you'll just see an
     "unverified app" warning once, during your own consent (click
     *Advanced → Go to mangaEasy (unsafe)* — it's your own app asking for
     your own permission).
5. **Create the client credentials**: *APIs & Services* → *Credentials* →
   **+ Create credentials** → *OAuth client ID* → Application type:
   **Desktop app** → Create. Google now shows the **Client ID** (ends with
   `.apps.googleusercontent.com`) and **Client secret** (starts with
   `GOCSPX-`) — copy both. (You can also *Download JSON* if you prefer a
   file; both work.)

## Part 2 — Attach the project & connect mangaEasy

Connect with `youtube-auth` (this opens Google's consent page in your
browser; approve it once):

```bash
# paste the two values...
mangaeasy youtube-auth --client-id 1234-abc.apps.googleusercontent.com --client-secret GOCSPX-xyz
# ...or use the downloaded file
mangaeasy youtube-auth --client-secrets /path/to/client_secret_1234.json

mangaeasy youtube-status --verify   # → Connected as <channel> / verified: yes
```

The client file and token are stored in `<data folder>/.mangaeasy/youtube/`
— nothing outside mangaEasy's own folder. Disconnect any time with
`mangaeasy youtube-logout`; you can also revoke access at
https://myaccount.google.com/permissions.

**What you're granting:** full management of your channel's *videos* —
upload, edit metadata, and delete. Delete matters in practice: when you
re-upload a fixed take, the bad one can be removed through the API instead
of a manual YouTube Studio trip. mangaEasy never touches comments,
playlists, live chat, or account settings. If you connected with an older
mangaEasy version, your token is upload-only — video management will fail
with a 403 "insufficient authentication scopes" until you reconnect once
(`mangaeasy youtube-auth`).

## Part 3 — Upload

```bash
mangaeasy youtube-upload \
  --video /path/to/output/myproject/myproject_full_20260703.mp4 \
  --title "My Manga Recap — Chapters 1-24" \
  --description-file description.txt \
  --tags "manga,recap" \
  --privacy private
# → MANGAEASY_RESULT {"video_id": "...", "url": "https://youtu.be/...", "privacy": "private"}
```

Uploads are resumable — network hiccups retry and continue where they
stopped, and large files upload in chunks with progress.

## The three YouTube-policy rules to know

1. **Your videos arrive as _private_** no matter what privacy you request.
   YouTube locks uploads from API projects that haven't passed its
   compliance audit to private. Publishing is one click in
   [YouTube Studio](https://studio.youtube.com) (Visibility → Public).
   If you want direct public uploads, you can apply for the audit from the
   Cloud console (YouTube API Services — Audit and Quota Extension form).
2. **~6 uploads per day.** One upload costs 1,600 of your project's default
   10,000 daily quota units. The quota resets at midnight Pacific time.
   Since the project is your own, nobody else shares your quota.
3. **"Testing" consent screens expire tokens every 7 days** — that's why
   Part 1 step 4 says set the app to "In production".

## Troubleshooting

| Symptom | Fix |
|---|---|
| "no OAuth client configured yet" | Run Part 1, then `youtube-auth --client-secrets <file>` |
| "no YouTube account connected" | Run `mangaeasy youtube-auth` (Setup tab → Connect) |
| Browser shows "unverified app" warning | Expected (it's your own app) — Advanced → continue |
| Upload works but video is private | Expected (rule 1) — publish in YouTube Studio |
| `quotaExceeded` | Rule 2 — wait for the daily reset (midnight Pacific) |
| Disconnected after a week | Consent screen left in "Testing" — set it to "In production" and reconnect |
| Thumbnail warning after upload | Custom thumbnails need a phone-verified YouTube account (youtube.com/verify) |
| 403 "insufficient authentication scopes" on delete/update | Token predates full video management — reconnect once: `mangaeasy youtube-auth` |

## Listing uploads / finding a video id

`mangaeasy youtube-list [--limit N] [--json]` prints the channel's uploads
(id, date, privacy, title) — the ids that `youtube-delete` and
`youtube-thumbnail` need when replacing a bad take.

`youtube-upload` now verifies the stored token with a 1-quota-unit channel
probe *before* starting the (multi-hundred-MB) upload, so an expired/revoked
token fails in seconds with the fix in hand instead of mid-transfer. Pass
`--skip-verify` to bypass the probe.
