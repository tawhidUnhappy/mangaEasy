"""`mangaeasy youtube-delete` — delete a video from the connected channel.

Plain `requests` against the videos endpoint, same as upload.py. This is the
"bad take" path the broadened OAuth scope exists for (store.SCOPES): replace a
flawed upload with a fixed one without a manual YouTube Studio trip. Tokens
granted before the scope change get 403 insufficientPermissions here — the fix
is re-running `mangaeasy youtube-auth`, not code.

Deletion is irreversible, so the command is two-step by design: without
--confirm it only looks the video up and prints what would be deleted (title,
privacy), exiting with the usage code. Costs ~51 quota units (list 1 + delete
50) out of the default 10,000/day.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

from mangaeasy.brand import CLI_NAME
from mangaeasy.utils import emit_result
from mangaeasy.youtube import store
from mangaeasy.youtube.upload import YouTubeAPIError, _session

VIDEOS_ENDPOINT = "https://www.googleapis.com/youtube/v3/videos"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Delete a video from the connected YouTube channel.")
    parser.add_argument("--profile", type=store.validate_profile, default=store.DEFAULT_PROFILE,
                        metavar="NAME", help="YouTube account profile (default: default).")
    parser.add_argument("--no-auto-auth", action="store_false", dest="auto_auth", default=True,
                        help="Do not open browser consent automatically if authorization is needed.")
    parser.add_argument("--video-id", default=None, help="Video id, e.g. dQw4w9WgXcQ.")
    parser.add_argument("--url", default=None,
                        help="Video URL (youtu.be/... or youtube.com/watch?v=...); alternative to --video-id.")
    parser.add_argument("--confirm", action="store_true",
                        help="Actually delete. Without this flag the command only shows what would be deleted.")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Also print one final JSON object on stdout.")
    return parser.parse_args()


def extract_video_id(video_id: str | None, url: str | None) -> str | None:
    if video_id:
        return video_id.strip()
    if url:
        match = re.search(r"(?:youtu\.be/|[?&]v=|/shorts/)([A-Za-z0-9_-]{5,})", url)
        if match:
            return match.group(1)
    return None


def lookup_video(session, video_id: str) -> dict | None:
    response = session.get(
        VIDEOS_ENDPOINT,
        params={"part": "snippet,status", "id": video_id},
        timeout=60,
    )
    if response.status_code != 200:
        raise YouTubeAPIError(response.status_code, response.text)
    items = response.json().get("items") or []
    return items[0] if items else None


def delete_video(session, video_id: str) -> None:
    response = session.delete(VIDEOS_ENDPOINT, params={"id": video_id}, timeout=60)
    if response.status_code != 204:
        raise YouTubeAPIError(response.status_code, response.text)


def main() -> int:
    args = parse_args()
    video_id = extract_video_id(args.video_id, args.url)
    if not video_id:
        print("ERROR: pass --video-id or --url.", file=sys.stderr)
        return 2

    from mangaeasy.youtube.auth import (
        YouTubeAuthorizationError,
        ensure_credentials,
        reauthorize_after_api_error,
    )

    try:
        creds = ensure_credentials(args.profile, auto_auth=args.auto_auth)
    except YouTubeAuthorizationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - never echo credential/refresh exception contents
        print("ERROR: stored YouTube token is unusable.\n"
              f"Run `{CLI_NAME} youtube-auth --profile {args.profile}` or remove --no-auto-auth.",
              file=sys.stderr)
        return 1
    if creds is None:
        print(
            f"ERROR: YouTube profile '{args.profile}' is not connected and automatic "
            "authorization is disabled.\n"
            f"Run `{CLI_NAME} youtube-auth --profile {args.profile}` or omit --no-auto-auth.",
            file=sys.stderr,
        )
        return 1

    session = _session(creds)
    try:
        video = lookup_video(session, video_id)
    except YouTubeAPIError as exc:
        try:
            replacement = reauthorize_after_api_error(
                args.profile, exc, auto_auth=args.auto_auth
            )
        except YouTubeAuthorizationError as auth_exc:
            print(f"ERROR: {auth_exc}", file=sys.stderr)
            return 1
        if replacement is None:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        session = _session(replacement)
        try:
            video = lookup_video(session, video_id)
        except RuntimeError as retry_exc:
            print(f"ERROR: {retry_exc}", file=sys.stderr)
            return 1
    if video is None:
        print(f"ERROR: video {video_id} not found (already deleted, or not visible to this account).",
              file=sys.stderr)
        return 1
    title = (video.get("snippet") or {}).get("title", "")
    privacy = (video.get("status") or {}).get("privacyStatus", "")
    if not args.confirm:
        print(f"Would delete: {video_id}  '{title}'  [{privacy}]")
        print("Re-run with --confirm to actually delete it (irreversible).")
        return 2
    try:
        delete_video(session, video_id)
    except YouTubeAPIError as exc:
        try:
            replacement = reauthorize_after_api_error(
                args.profile, exc, auto_auth=args.auto_auth
            )
        except YouTubeAuthorizationError as auth_exc:
            print(f"ERROR: {auth_exc}", file=sys.stderr)
            return 1
        if replacement is None:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        try:
            delete_video(_session(replacement), video_id)
        except RuntimeError as retry_exc:
            print(f"ERROR: {retry_exc}", file=sys.stderr)
            return 1

    print(f"Deleted: {video_id}  '{title}'")
    snapshot = store.status_snapshot(args.profile)
    result = {
        "video_id": video_id,
        "deleted": True,
        "title": title,
        "profile": args.profile,
        "channel_title": snapshot.get("channel_title"),
        "channel_id": snapshot.get("channel_id"),
    }
    emit_result(**result)
    if args.as_json:
        # Last line on purpose: JSON-mode consumers parse the final stdout line.
        print(json.dumps({**result, "ok": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
