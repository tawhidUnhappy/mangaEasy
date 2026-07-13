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
    parser.add_argument("--limit", type=int, default=25, help="Maximum videos to list (default 25).")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit one JSON object on stdout.")
    args = parser.parse_args()

    from mangaeasy.youtube.auth import load_credentials

    try:
        creds = load_credentials()
    except Exception as exc:  # noqa: BLE001 — same actionable message as upload
        print(f"ERROR: stored YouTube token is invalid or was revoked ({exc}).\n"
              "Re-run `mangaeasy youtube-auth`.", file=sys.stderr)
        return 1
    if creds is None:
        print("ERROR: no YouTube account connected. Run `mangaeasy youtube-auth` first.",
              file=sys.stderr)
        return 1

    try:
        videos = list_uploads(creds, max(1, args.limit))
    except Exception as exc:  # noqa: BLE001 — network/API errors become one clean line
        print(f"ERROR: could not list uploads: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps({"videos": videos}, ensure_ascii=False))
        return 0
    if not videos:
        print("No uploads found.")
        return 0
    for video in videos:
        date = (video["published_at"] or "")[:10]
        print(f"{video['video_id']}  {date}  [{video['privacy']}]  {video['title']}")
    return 0
