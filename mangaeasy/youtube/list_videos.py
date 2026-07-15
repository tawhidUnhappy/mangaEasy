"""`mangaeasy youtube-list` — list the connected channel's uploads.

The delete/replace-a-bad-take workflow needs video IDs, and until this
command existed the only way to get one was hand-rolling Google API calls
against the raw token. One page of the uploads playlist costs ~1 quota unit
per part; `--limit` bounds pagination.
"""

from __future__ import annotations

import argparse
import json
import sys

from mangaeasy.brand import CLI_NAME
from mangaeasy.youtube import store


def _get(session_headers: dict, url: str, params: dict) -> dict:
    import requests

    response = requests.get(url, params=params, headers=session_headers, timeout=30)
    response.raise_for_status()
    return response.json()


def list_uploads(creds, limit: int) -> list[dict]:
    headers = {"Authorization": f"Bearer {creds.token}"}
    channels = _get(headers, "https://www.googleapis.com/youtube/v3/channels",
                    {"part": "contentDetails", "mine": "true"})
    items = channels.get("items") or []
    if not items:
        return []
    uploads_playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    videos: list[dict] = []
    page_token = None
    while len(videos) < limit:
        params = {"part": "snippet,status", "playlistId": uploads_playlist,
                  "maxResults": min(50, limit - len(videos))}
        if page_token:
            params["pageToken"] = page_token
        page = _get(headers, "https://www.googleapis.com/youtube/v3/playlistItems", params)
        for entry in page.get("items", []):
            snippet = entry.get("snippet", {})
            videos.append({
                "video_id": snippet.get("resourceId", {}).get("videoId"),
                "title": snippet.get("title"),
                "published_at": snippet.get("publishedAt"),
                "privacy": entry.get("status", {}).get("privacyStatus"),
            })
        page_token = page.get("nextPageToken")
        if not page_token:
            break
    return videos


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List the connected channel's uploaded videos (id, title, privacy, date) — "
                    "the IDs youtube-delete/youtube-thumbnail need."
    )
    parser.add_argument("--profile", type=store.validate_profile, default=store.DEFAULT_PROFILE,
                        metavar="NAME", help="YouTube account profile (default: default).")
    parser.add_argument("--no-auto-auth", action="store_false", dest="auto_auth", default=True,
                        help="Do not open browser consent automatically if authorization is needed.")
    parser.add_argument("--limit", type=int, default=25, help="Maximum videos to list (default 25).")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit one JSON object on stdout.")
    args = parser.parse_args()

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
        print("ERROR: stored YouTube token is invalid or was revoked.\n"
              f"Run `{CLI_NAME} youtube-auth --profile {args.profile}` or remove --no-auto-auth.",
              file=sys.stderr)
        return 1
    if creds is None:
        print(f"ERROR: YouTube profile '{args.profile}' is not connected and automatic "
              "authorization is disabled. Run "
              f"`{CLI_NAME} youtube-auth --profile {args.profile}` or omit --no-auto-auth.",
              file=sys.stderr)
        return 1

    try:
        videos = list_uploads(creds, max(1, args.limit))
    except Exception as exc:  # noqa: BLE001 - retry only a recognized API 401
        try:
            replacement = reauthorize_after_api_error(
                args.profile, exc, auto_auth=args.auto_auth
            )
        except YouTubeAuthorizationError as auth_exc:
            print(f"ERROR: {auth_exc}", file=sys.stderr)
            return 1
        if replacement is None:
            print(f"ERROR: could not list uploads: {type(exc).__name__}", file=sys.stderr)
            return 1
        try:
            videos = list_uploads(replacement, max(1, args.limit))
        except Exception as retry_exc:  # noqa: BLE001 - sanitized
            print("ERROR: could not list uploads after reauthorization: "
                  f"{type(retry_exc).__name__}", file=sys.stderr)
            return 1

    snapshot = store.status_snapshot(args.profile)
    if args.as_json:
        print(json.dumps({
            "profile": args.profile,
            "channel_title": snapshot.get("channel_title"),
            "channel_id": snapshot.get("channel_id"),
            "videos": videos,
        }, ensure_ascii=False))
        return 0
    if not videos:
        print("No uploads found.")
        return 0
    channel = snapshot.get("channel_title") or "channel unknown"
    print(f"Profile '{args.profile}' ({channel})")
    for video in videos:
        date = (video["published_at"] or "")[:10]
        print(f"{video['video_id']}  {date}  [{video['privacy']}]  {video['title']}")
    return 0
