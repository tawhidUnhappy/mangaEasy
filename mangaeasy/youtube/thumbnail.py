"""mangaeasy.youtube.thumbnail — set the thumbnail of an already-uploaded video.

``mangaeasy youtube-thumbnail`` replaces a live video's thumbnail through the
thumbnails.set API, so iterating on thumbnail art/markup never requires
re-uploading (or deleting) the video. Needs the same auth as youtube-upload;
custom thumbnails additionally require a verified YouTube account.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mangaeasy.brand import CLI_NAME
from mangaeasy.utils import emit_result
from mangaeasy.youtube import store
from mangaeasy.youtube.upload import THUMBNAIL_ENDPOINT, _session, friendly_api_error


def video_id_from_url(url: str) -> str | None:
    import re

    m = re.search(r"(?:youtu\.be/|[?&]v=)([A-Za-z0-9_-]{6,})", url)
    return m.group(1) if m else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} youtube-thumbnail",
        description="Set/replace the thumbnail of an already-uploaded video.",
    )
    parser.add_argument("--profile", type=store.validate_profile, default=store.DEFAULT_PROFILE,
                        metavar="NAME", help="YouTube account profile (default: default).")
    parser.add_argument("--no-auto-auth", action="store_false", dest="auto_auth", default=True,
                        help="Do not open browser consent automatically if authorization is needed.")
    parser.add_argument("--video-id", default=None, help="Video id, e.g. dQw4w9WgXcQ.")
    parser.add_argument("--url", default=None,
                        help="Video URL (youtu.be/... or youtube.com/watch?v=...).")
    parser.add_argument("--image", type=Path, required=True,
                        help="PNG/JPG to set as the thumbnail (max 2 MB).")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="Print one JSON object as the last stdout line.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    video_id = args.video_id or (video_id_from_url(args.url) if args.url else None)
    if not video_id:
        print("ERROR: pass --video-id or a recognizable --url", file=sys.stderr)
        return 2
    image = args.image.expanduser().resolve()
    if not image.is_file():
        print(f"ERROR: image not found: {image}", file=sys.stderr)
        return 1

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
    except Exception:  # expired/revoked token; never echo credential details
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
    mime = "image/png" if image.suffix.lower() == ".png" else "image/jpeg"
    response = session.post(
        THUMBNAIL_ENDPOINT,
        params={"videoId": video_id},
        data=image.read_bytes(),
        headers={"Content-Type": mime},
        timeout=120,
    )
    if response.status_code != 200:
        try:
            replacement = reauthorize_after_api_error(
                args.profile, response, auto_auth=args.auto_auth
            )
        except YouTubeAuthorizationError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        if replacement is not None:
            response = _session(replacement).post(
                THUMBNAIL_ENDPOINT,
                params={"videoId": video_id},
                data=image.read_bytes(),
                headers={"Content-Type": mime},
                timeout=120,
            )
    if response.status_code != 200:
        print(f"ERROR: {friendly_api_error(response.status_code, response.text)}",
              file=sys.stderr)
        return 1

    print(f"Thumbnail set on https://youtu.be/{video_id}")
    snapshot = store.status_snapshot(args.profile)
    result = {
        "command": "youtube-thumbnail",
        "video_id": video_id,
        "image": str(image),
        "profile": args.profile,
        "channel_title": snapshot.get("channel_title"),
        "channel_id": snapshot.get("channel_id"),
    }
    emit_result(**result)
    if args.as_json:
        print(json.dumps({**result, "thumbnail": str(image), "ok": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
