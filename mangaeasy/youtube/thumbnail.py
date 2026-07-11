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

from mangaeasy.utils import emit_result
from mangaeasy.youtube.upload import THUMBNAIL_ENDPOINT, _session, friendly_api_error


def video_id_from_url(url: str) -> str | None:
    import re

    m = re.search(r"(?:youtu\.be/|[?&]v=)([A-Za-z0-9_-]{6,})", url)
    return m.group(1) if m else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mangaeasy youtube-thumbnail",
        description="Set/replace the thumbnail of an already-uploaded video.",
    )
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

    from mangaeasy.youtube.auth import load_credentials

    try:
        creds = load_credentials()
    except Exception as exc:  # expired/revoked refresh token raises here
        print(f"ERROR: stored YouTube token is unusable ({exc}).\n"
              "Re-run `mangaeasy youtube-auth` (re-consent), then retry.",
              file=sys.stderr)
        return 1
    if creds is None:
        print(
            "ERROR: no YouTube account connected.\n"
            "Run `mangaeasy youtube-auth` first (see docs/youtube.md).",
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
        print(f"ERROR: {friendly_api_error(response.status_code, response.text)}",
              file=sys.stderr)
        return 1

    print(f"Thumbnail set on https://youtu.be/{video_id}")
    emit_result(command="youtube-thumbnail", video_id=video_id, image=image)
    if args.as_json:
        print(json.dumps({"video_id": video_id, "thumbnail": str(image), "ok": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
