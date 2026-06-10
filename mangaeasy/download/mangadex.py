#!/usr/bin/env python3
"""mangaeasy.download.mangadex — download a chapter from MangaDex."""

import os
import sys
import time
from pathlib import Path
from typing import List, Tuple
from urllib.parse import urlparse
import re

import requests

from mangaeasy.config import PROJECT_ROOT, load_download_config

API_BASE = "https://api.mangadex.org"


def normalize_manga_id(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        parts = parsed.path.strip("/").split("/")
        uuid_re = re.compile(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
        )
        for part in parts:
            if uuid_re.match(part):
                return part
        if len(parts) >= 2:
            return parts[1]
        print(f"[ERROR] Could not extract manga UUID from URL: {raw}")
        sys.exit(1)
    return raw


def find_chapter_id(manga_id: str, chapter: str, lang: str) -> str:
    url = f"{API_BASE}/manga/{manga_id}/feed"
    params = {
        "translatedLanguage[]": [lang],
        "order[publishAt]": "desc",
        "limit": 500,
    }
    print(f"[INFO] Searching chapter via: {url}")
    resp = requests.get(url, params=params, timeout=20)
    if resp.status_code != 200:
        print(f"[ERROR] MangaDex API returned {resp.status_code}")
        resp.raise_for_status()
    data = resp.json()
    items = data.get("data", [])
    if not items:
        print(f"[ERROR] No chapters found for manga {manga_id} in language '{lang}'.")
        sys.exit(1)
    for chapter_obj in items:
        attrs = chapter_obj.get("attributes", {})
        if attrs.get("chapter") == chapter:
            cid = chapter_obj["id"]
            print(f"[INFO] Selected chapter ID: {cid} | title: {attrs.get('title')!r}")
            return cid
    print(
        f"[ERROR] No chapter '{chapter}' found for manga {manga_id} in language '{lang}' "
        f"after checking {len(items)} chapters."
    )
    sys.exit(1)


def fetch_at_home_info(chapter_id: str) -> dict:
    url = f"{API_BASE}/at-home/server/{chapter_id}"
    print(f"[INFO] Fetching at-home server info: {url}")
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if "baseUrl" not in data or "chapter" not in data:
        raise RuntimeError(f"Unexpected at-home response: {data}")
    return data


def build_image_urls(at_home_data: dict, use_data_saver: bool) -> Tuple[List[str], List[str]]:
    base_url = at_home_data["baseUrl"]
    chapter_info = at_home_data["chapter"]
    chapter_hash = chapter_info["hash"]
    if use_data_saver:
        key, quality_dir = "dataSaver", "data-saver"
    else:
        key, quality_dir = "data", "data"
    files = chapter_info.get(key)
    if not files:
        raise RuntimeError(f"No images found for key '{key}' in at-home data")
    urls = [f"{base_url}/{quality_dir}/{chapter_hash}/{fname}" for fname in files]
    return urls, files


def download_images(
    urls: List[str], filenames: List[str], output_dir: Path, delay: float, chapter_str: str
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    total = len(urls)
    pad_width = max(2, len(str(total)))
    for idx, (url, original_name) in enumerate(zip(urls, filenames), start=1):
        _, ext = os.path.splitext(original_name)
        ext = ext or ".jpg"
        numbered_name = f"{chapter_str}_{idx:0{pad_width}d}{ext}"
        dest_path = output_dir / numbered_name
        print(f"[{idx}/{total}] {url} -> {dest_path}")
        try:
            with requests.get(url, stream=True, timeout=30) as r:
                r.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
        except Exception as e:
            print(f"  ! Failed: {e}")
        if delay > 0:
            time.sleep(delay)
    print(f"\n[INFO] Done. Saved {total} pages in {output_dir.resolve()}")


def main() -> None:
    dl_cfg = load_download_config()

    raw_manga_id = str(dl_cfg.get("manga_id", "")).strip()
    if not raw_manga_id:
        print("[ERROR] 'manga_id' is missing in config.json download section")
        sys.exit(1)

    manga_id = normalize_manga_id(raw_manga_id)
    chapter = dl_cfg.get("chapter")
    if chapter is None:
        print("[ERROR] 'chapter' is missing in config.json download section")
        sys.exit(1)

    chapter_str = str(chapter).zfill(2)
    lang = dl_cfg.get("translated_language", "en")
    output_dir = PROJECT_ROOT / "manga" / str(dl_cfg.get("name")) / chapter_str / "download"
    use_data_saver = bool(dl_cfg.get("use_data_saver", False))
    delay = float(dl_cfg.get("download_delay", 0.5))

    print("=== MangaDex downloader ===")
    print(f"  Manga ID  : {manga_id}")
    print(f"  Chapter   : {chapter_str}")
    print(f"  Language  : {lang}")
    print(f"  Output    : {output_dir}")
    print(f"  Quality   : {'Data-saver' if use_data_saver else 'Original'}")
    print(f"  Delay     : {delay}s")
    print("===========================\n")

    chapter_id = find_chapter_id(manga_id, str(chapter), lang)
    at_home = fetch_at_home_info(chapter_id)
    urls, filenames = build_image_urls(at_home, use_data_saver)
    print(f"[INFO] Found {len(urls)} page(s). Starting download...\n")
    download_images(urls, filenames, output_dir, delay, chapter_str)


if __name__ == "__main__":
    main()
