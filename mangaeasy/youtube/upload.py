"""`mangaeasy youtube-upload` — resumable upload to the connected channel.

Plain `requests` against YouTube's resumable-upload HTTP protocol (no
Google discovery client): one POST to open an upload session, then chunked
PUTs with Content-Range headers; 308 means "keep going", 200/201 carries
the created video resource. Transient failures re-query the session offset
and resume instead of restarting.

Machine contract (docs/ai-guide.md): MANGAEASY_PROGRESS lines per chunk and
a final ``MANGAEASY_RESULT {"video_id": ..., "url": ...}`` on success.

Policy notes surfaced to users (docs/youtube.md has the full story):
uploads from unaudited API projects are locked to *private* by YouTube
regardless of the requested privacy — hence the private default — and one
upload costs 1,600 of the default 10,000/day quota units (~6 uploads/day).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from mangaeasy.utils import emit_result

UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMBNAIL_ENDPOINT = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
CHUNK_SIZE = 8 * 1024 * 1024  # multiple of 256 KiB, as the protocol requires
MAX_RETRIES = 8

PRIVACY_CHOICES = ("private", "unlisted", "public")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload a video to the connected YouTube channel.")
    parser.add_argument("--video", type=Path, required=True, help="Video file to upload (mp4 etc.).")
    parser.add_argument("--title", required=True, help="Video title (max 100 chars).")
    parser.add_argument("--description", default="", help="Video description text.")
    parser.add_argument("--description-file", type=Path, default=None,
                        help="Read the description from a UTF-8 text file (wins over --description).")
    parser.add_argument("--tags", default="", help="Comma-separated tags, e.g. 'manga,recap'.")
    parser.add_argument("--privacy", choices=PRIVACY_CHOICES, default="private",
                        help="Requested privacy (default private — YouTube forces private anyway "
                             "for API projects that haven't passed its audit; see docs/youtube.md).")
    parser.add_argument("--category", default="1",
                        help="YouTube category id (default 1 = Film & Animation; 24 = Entertainment).")
    parser.add_argument("--thumbnail", type=Path, default=None,
                        help="Optional image to set as the thumbnail after upload (needs a verified "
                             "YouTube account for custom thumbnails).")
    parser.add_argument("--made-for-kids", action="store_true",
                        help="Declare the video as made for kids (default: not made for kids).")
    parser.add_argument("--skip-verify", action="store_true",
                        help="Skip the pre-upload token/channel probe (1 quota unit).")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Also print one final JSON object on stdout.")
    return parser.parse_args()


def build_metadata(title: str, description: str, tags: list[str], category: str,
                   privacy: str, made_for_kids: bool) -> dict:
    snippet: dict = {"title": title, "description": description, "categoryId": category}
    if tags:
        snippet["tags"] = tags
    return {
        "snippet": snippet,
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": made_for_kids},
    }


def parse_tags(raw: str) -> list[str]:
    return [tag.strip() for tag in raw.split(",") if tag.strip()]


def content_range(offset: int, chunk_len: int, total: int) -> str:
    return f"bytes {offset}-{offset + chunk_len - 1}/{total}"


def friendly_api_error(status_code: int, body: str) -> str:
    """One actionable line out of a Google API error payload."""
    reason = ""
    message = ""
    try:
        error = json.loads(body).get("error") or {}
        message = error.get("message") or ""
        errors = error.get("errors") or []
        if errors:
            reason = errors[0].get("reason") or ""
    except ValueError:
        message = body[:200]
    hints = {
        "quotaExceeded": "Daily YouTube API quota exhausted (one upload costs 1,600 of the default "
                         "10,000 units — about 6 uploads/day). Try again after midnight Pacific time.",
        "uploadLimitExceeded": "This channel hit YouTube's upload limit for now. Try again later.",
        "authError": "Authorization expired or was revoked — run: mangaeasy youtube-auth",
        "forbidden": "YouTube refused the request — check the channel is active and the API is enabled.",
        "invalidTitle": "YouTube rejected the title (empty, too long, or invalid characters).",
    }
    hint = hints.get(reason, "")
    base = f"YouTube API error {status_code}" + (f" ({reason})" if reason else "")
    detail = f": {message}" if message else ""
    return f"{base}{detail}" + (f"\n  {hint}" if hint else "")


def _session(creds):
    import requests

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {creds.token}"
    return session


def _start_session(session, metadata: dict, total: int) -> str:
    response = session.post(
        UPLOAD_ENDPOINT,
        params={"uploadType": "resumable", "part": "snippet,status"},
        json=metadata,
        headers={
            "X-Upload-Content-Length": str(total),
            "X-Upload-Content-Type": "video/*",
        },
        timeout=60,
    )
    if response.status_code != 200:
        raise RuntimeError(friendly_api_error(response.status_code, response.text))
    location = response.headers.get("Location")
    if not location:
        raise RuntimeError("YouTube did not return an upload session URL.")
    return location


def _committed_offset(session, upload_url: str, total: int) -> int:
    """Ask the session how many bytes it already has (resume support)."""
    response = session.put(
        upload_url, headers={"Content-Range": f"bytes */{total}"}, timeout=60
    )
    if response.status_code == 308:
        range_header = response.headers.get("Range", "")
        if range_header.startswith("bytes=0-"):
            return int(range_header.split("-", 1)[1]) + 1
        return 0
    if response.status_code in (200, 201):
        return total
    raise RuntimeError(friendly_api_error(response.status_code, response.text))


def _upload_file(session, upload_url: str, video: Path) -> dict:
    total = video.stat().st_size
    offset = 0
    retries = 0
    with video.open("rb") as f:
        while offset < total:
            f.seek(offset)
            chunk = f.read(CHUNK_SIZE)
            try:
                response = session.put(
                    upload_url,
                    data=chunk,
                    headers={"Content-Range": content_range(offset, len(chunk), total)},
                    timeout=300,
                )
            except OSError as exc:
                retries += 1
                if retries > MAX_RETRIES:
                    raise RuntimeError(f"Upload failed after {MAX_RETRIES} retries: {exc}") from exc
                time.sleep(min(2 ** retries, 60))
                offset = _committed_offset(session, upload_url, total)
                continue

            if response.status_code == 308:
                offset += len(chunk)
                retries = 0
            elif response.status_code in (200, 201):
                print(f"MANGAEASY_PROGRESS {total}/{total} uploading", flush=True)
                return response.json()
            elif response.status_code in (500, 502, 503, 504):
                retries += 1
                if retries > MAX_RETRIES:
                    raise RuntimeError(friendly_api_error(response.status_code, response.text))
                time.sleep(min(2 ** retries, 60))
                offset = _committed_offset(session, upload_url, total)
                continue
            else:
                raise RuntimeError(friendly_api_error(response.status_code, response.text))

            print(f"MANGAEASY_PROGRESS {offset}/{total} uploading", flush=True)
    # Loop ended without a 200/201 — ask the session for the final state.
    response = session.put(upload_url, headers={"Content-Range": f"bytes */{total}"}, timeout=60)
    if response.status_code in (200, 201):
        return response.json()
    raise RuntimeError(friendly_api_error(response.status_code, response.text))


def _set_thumbnail(session, video_id: str, thumbnail: Path) -> None:
    suffix = thumbnail.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    response = session.post(
        THUMBNAIL_ENDPOINT,
        params={"videoId": video_id},
        data=thumbnail.read_bytes(),
        headers={"Content-Type": mime},
        timeout=120,
    )
    if response.status_code != 200:
        print(f"[warn] thumbnail not set: {friendly_api_error(response.status_code, response.text)}")


def main() -> int:
    args = parse_args()

    video = args.video.expanduser().resolve()
    if not video.is_file():
        print(f"ERROR: video file not found: {video}", file=sys.stderr)
        return 1
    if args.thumbnail is not None and not args.thumbnail.is_file():
        print(f"ERROR: thumbnail file not found: {args.thumbnail}", file=sys.stderr)
        return 1

    description = args.description
    if args.description_file is not None:
        description = args.description_file.read_text(encoding="utf-8-sig")

    from mangaeasy.youtube.auth import _fetch_channel, load_credentials

    # Fail here, in seconds, with the fix in hand — never minutes into a
    # multi-hundred-MB upload. Refreshing the token is what surfaces an
    # expired/revoked grant, so catch it instead of tracebacking.
    try:
        creds = load_credentials()
    except Exception as exc:  # noqa: BLE001 — any auth failure gets the same actionable message
        print(
            f"ERROR: stored YouTube token is invalid or was revoked ({exc}).\n"
            "Re-run `mangaeasy youtube-auth` (interactive browser consent), then retry this upload.",
            file=sys.stderr,
        )
        return 1
    if creds is None:
        print(
            "ERROR: no YouTube account connected.\n"
            "Run `mangaeasy youtube-auth` first (see docs/youtube.md for the one-time setup).",
            file=sys.stderr,
        )
        return 1
    if not args.skip_verify:
        try:
            channel = _fetch_channel(creds)
            if channel.get("title"):
                print(f"Connected as {channel['title']}.", flush=True)
        except Exception as exc:  # noqa: BLE001 — pre-flight only; --skip-verify bypasses
            print(
                f"ERROR: YouTube API not reachable with this token ({exc}).\n"
                "Check `mangaeasy youtube-status --verify`; pass --skip-verify to try anyway.",
                file=sys.stderr,
            )
            return 1

    metadata = build_metadata(
        args.title, description, parse_tags(args.tags), args.category,
        args.privacy, args.made_for_kids,
    )
    total_mb = video.stat().st_size / (1024 * 1024)
    print(f"Uploading {video.name} ({total_mb:.1f} MB) as '{args.title}' [{args.privacy}]...", flush=True)

    session = _session(creds)
    try:
        upload_url = _start_session(session, metadata, video.stat().st_size)
        resource = _upload_file(session, upload_url, video)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    video_id = resource.get("id", "")
    url = f"https://youtu.be/{video_id}" if video_id else ""
    actual_privacy = (resource.get("status") or {}).get("privacyStatus", args.privacy)

    if args.thumbnail is not None and video_id:
        _set_thumbnail(session, video_id, args.thumbnail)

    print(f"\nUploaded: {url}  (privacy: {actual_privacy})")
    if actual_privacy == "private" and args.privacy != "private":
        print("  NOTE: YouTube locked it to private — API projects that haven't passed "
              "YouTube's audit can't publish directly; flip it in YouTube Studio.")
    emit_result(video_id=video_id, url=url, privacy=actual_privacy)
    if args.as_json:
        # Last line on purpose: JSON-mode consumers (incl. the MCP server)
        # parse the final stdout line as the report object.
        print(json.dumps({"video_id": video_id, "url": url, "privacy": actual_privacy, "ok": True},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
