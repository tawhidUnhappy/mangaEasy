# YouTube profile selection and publishing

Treat the YouTube profile as part of publish identity, not a global default.
A user may connect separate `manga`, `song`, and `ai-story` channels or reuse
one profile for several modes.

Before any publish operation:

1. Run `<mc> youtube-profiles --json` (or
   call MCP `youtube_profiles`). This is offline, never returns secrets, and
   reports the exact `shared_client_file` path. If the shared client is absent,
   tell the user to place their downloaded Google Desktop-app JSON there; do
   not read the file into context.
2. Match the requested output to a profile's cached channel title/id. If more
   than one profile is plausible, ask the user which one; never guess based
   only on profile name.
3. Run `youtube-status --profile <name> --verify --json` (or MCP
   `youtube_status`) and confirm the returned channel. With a shared client but
   no usable token, the browser opens automatically; wait for the user to
   approve Google consent and let the same call continue. Set
   `auto_auth=false`/`--no-auto-auth` only for a headless worker.
4. Carry the exact profile name through the mode manifest when it has a
   `youtube.profile` field and through every direct `youtube-* --profile`
   command. Do not silently fall back to `default` during publishing.
5. Publish only after the mode's rights, QA, disclosure, and explicit-user-
   approval gates. Record profile, channel id, and returned video id.

One shared Google Cloud client authorizes all profiles, but each profile owns
its distinct token/channel. Explicit authorization remains available:

```bash
<mc> youtube-auth --profile <name>
```

Do not create per-profile client copies unless the user explicitly needs a
different Google Cloud project; `--client-secrets` on a named profile is an
optional override, not the normal multi-account path.

If `shared_client_present` is false, the channel owner must do this one-time
Google Cloud setup (an agent must not invent or download their secret):

1. In **Google Cloud Console**, create or select one Google Cloud project,
   enable **YouTube Data API v3**,
   configure the OAuth consent screen, and add the owner's Google accounts as
   test users while the app remains in testing.
2. Create an OAuth client with application type **Desktop app** and download
   its JSON file.
3. Move that file, without pasting its contents into chat, to the exact
   `shared_client_file` reported by `youtube-profiles --json`. Its predefined
   name is `youtube/client_secret.json` below MediaConductor's data root.
4. Run live status/auth once per named profile. The browser's account chooser
   authorizes a different YouTube channel while the token stays isolated under
   that profile.

Never ask a user to paste a token, read token/client JSON into model context,
print its contents, or commit it. Source-checkout users can consult
`docs/youtube.md`; this reference is self-contained for wheel/frozen installs.
