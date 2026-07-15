# YouTube account profiles

MediaConductor can connect multiple YouTube channels at the same time. Give
each connection a short profile name, then select it explicitly for status,
upload, listing, deletion, and thumbnail changes.

Typical layouts are:

- `manga` -> a manga recap channel
- `song` -> a music/lyrics channel
- `ai-story` -> an AI story channel
- one shared profile (for example `main`) -> all three modes

Profile names are lowercase machine identifiers: 1-64 letters, numbers,
hyphens, or underscores, starting and ending with a letter or number. Paths,
dots, spaces, and uppercase names are rejected.

## See what is connected

```bash
mediaconductor youtube-profiles
mediaconductor youtube-profiles --json
```

`youtube-profiles --json` is the preferred discovery command for an AI agent.
It returns profile names, connection booleans, and cached channel names/IDs,
never access tokens, refresh tokens, or OAuth client contents. It also returns
the exact `shared_client_file` path where the one downloaded Desktop-app JSON
belongs. This listing is offline and never opens a browser.

The profile defaults to `default` when `--profile` is omitted. Existing
single-account installations remain valid without migration.

## Create a Google OAuth client

Each user supplies their own free Google OAuth Desktop-app client:

1. Open <https://console.cloud.google.com/> with the channel owner's account.
2. Create/select a project and enable **YouTube Data API v3**.
3. Configure the OAuth consent screen. For a personal app, moving it from
   Testing to In production avoids seven-day test-token expiry.
4. Create **Credentials -> OAuth client ID -> Desktop app**.
5. Click **Download JSON** for that Desktop-app client.

One Google Cloud project and one downloaded client JSON can authorize any
number of MediaConductor profiles. The OAuth client identifies the software;
the separate browser grants identify the YouTube accounts/channels.

## Put the one client JSON at the shared path

First ask MediaConductor for the exact platform-specific location:

```bash
mediaconductor youtube-profiles --json
# Read shared_client_file, for example:
# D:\MediaConductor\.mangaeasy\youtube\client_secret.json
```

Create the parent `youtube` directory if needed, then copy/move the downloaded
JSON to that exact `shared_client_file`. Do not paste its contents into a
prompt or commit it. Existing installations already using
`youtube/client_secret.json` need no migration.

## Connect distinct channels automatically

Once the shared file exists, an LLM or user can call a live command directly:

```bash
mediaconductor youtube-status --profile manga --verify --json
mediaconductor youtube-status --profile song --verify --json
mediaconductor youtube-status --profile ai-story --verify --json
```

The first call for each new profile opens Google's consent page automatically.
Choose the intended Google/YouTube account, approve once, and MediaConductor
saves a distinct token and channel cache for that profile. The command then
continues and returns the verified channel. Missing, expired, revoked, or
API-rejected authorization triggers the same browser reauthorization and one
retry. Interactive progress goes to stderr so the final JSON remains clean.

If all modes publish to one channel, connect only one profile and reuse that
same name everywhere.

For a headless worker, add `--no-auto-auth`; it fails actionably instead of
opening a browser. Explicit setup remains available with
`mediaconductor youtube-auth --profile NAME`, including `--no-browser` to print
the loopback consent URL. A named profile may optionally import its own client
with `--client-secrets`, which overrides the shared client only for that
profile.

New AI Story and Song Video manifests contain `youtube.profile: "default"`.
Set that field to the verified profile name before the explicit publish stage;
the high-level builder passes it to `youtube-upload` and records the returned
profile/channel identity in `publish.json`. Manga uploads select the same
value directly with `--profile`.

## Storage and backwards compatibility

The original single-account files remain the `default` profile:

```text
<application-data>/.mangaeasy/youtube/
  client_secret.json
  token.json
  channel.json
```

Named profiles are isolated:

```text
<application-data>/.mangaeasy/youtube/profiles/
  manga/{token.json,channel.json}
  song/{token.json,channel.json}
  ai-story/{token.json,channel.json}
```

The root `client_secret.json` is shared. A named folder contains its own
`client_secret.json` only when an optional profile-specific override was
imported. Tokens and channel caches are never shared between profile names.

Credential JSON is written atomically with owner-only permissions where the
platform supports them. Never copy these files into a project, prompt, log,
issue, or Git repository.

## Publish with an explicit profile

```bash
mediaconductor youtube-upload \
  --profile manga \
  --video /absolute/output/recap.mp4 \
  --title "My Manga Recap - Chapters 1-24" \
  --description-file description.txt \
  --tags "manga,recap" \
  --privacy private \
  --json
```

The machine result includes `profile`, cached/live `channel_title` and
`channel_id` where available, plus the video id, URL, and actual privacy.
Uploads are resumable and verify the selected token/channel before sending the
large file unless `--skip-verify` is explicitly used.
If that profile has no usable token, the browser consent flow runs first and
the upload resumes automatically. Use `--no-auto-auth` in noninteractive
production and authorize profiles before starting the worker.

The same selection applies to maintenance commands:

```bash
mediaconductor youtube-list --profile manga --limit 25 --json
mediaconductor youtube-thumbnail --profile manga --video-id VIDEO_ID --image thumb.png --json
mediaconductor youtube-delete --profile manga --video-id VIDEO_ID        # preview
mediaconductor youtube-delete --profile manga --video-id VIDEO_ID --confirm --json
mediaconductor youtube-logout --profile manga
mediaconductor youtube-logout --profile manga --forget-client
```

Logout removes only the selected profile's token/cache. On a named profile,
`--forget-client` removes only its optional override. On `default`, it removes
the shared root client, so other profiles without overrides cannot reauthorize
until that shared JSON is restored.

## MCP and agent use

The router exposes `youtube_profiles` and `youtube_status`. Every selected mode
also exposes profile-aware `youtube_upload`, `youtube_list`, `youtube_delete`,
and `youtube_thumbnail` tools. The safe sequence is:

1. Call `youtube_profiles`.
2. Ask the user when the intended profile/channel is ambiguous.
3. Call `youtube_status` with that profile and `verify=true`. If it has no
   token but the shared client exists, the user's browser opens once and the
   tool continues after consent. Set `auto_auth=false` only for headless use.
4. Publish once with the same explicit profile after all rights/review gates.
5. Record the returned profile, channel id, and video id in publish metadata.

OAuth consent still needs the channel owner in a browser, but the agent does
not handle credentials: after the shared client JSON is placed once, it can
call status/upload and wait for the user to approve Google's page.

## Permissions and YouTube policy

MediaConductor requests video-management scopes so it can upload, edit, and
delete the authenticated channel's videos. It does not request comment, live
chat, or account-settings access.

- YouTube can force uploads from unaudited API projects to private regardless
  of requested privacy. Change visibility in Studio or complete Google's API
  compliance audit.
- One upload currently consumes a large portion of the default daily API
  quota, so avoid duplicate uploads.
- Custom thumbnails require a YouTube account eligible for that feature.
- A token created by an older upload-only release may need browser re-consent
  for delete/update operations.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Profile is not listed | Run `youtube-auth --profile NAME` with a safe lowercase name. |
| Wrong channel appears | Stop; logout that profile and reconnect while signed into the intended Google account. |
| No OAuth client is available | Run `youtube-profiles --json` and place the downloaded Desktop-app JSON at `shared_client_file`. |
| Stored token is revoked/expired | Retry the live command; auto-auth opens consent. In headless mode run `youtube-auth --profile NAME` first. |
| Upload arrives private | Expected for many unaudited API projects; use Studio or complete the audit. |
| Delete/update returns insufficient scopes | Reconnect that profile once to grant the current video-management scopes. |
| Token expires after seven days | Move the personal consent screen out of Testing, then reconnect. |
