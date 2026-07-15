"""mangaeasy.download.mangadex — polite MangaDex chapter downloader.

Follows MangaDex API etiquette:
  - Identifies itself with a proper User-Agent header.
  - Enforces a minimum gap between every API request.
  - Backs off exponentially on 429 (rate-limit) and 5xx responses.
  - Reports every image download result to the at-home CDN network as
    required by MangaDex's usage guidelines (best-effort, never fatal).
  - Adds ±20 % jitter to inter-image delays so traffic is not
    machine-exact.
  - Paginates the chapter feed so manga with > 100 chapters in a
    language work correctly.
  - Skips images that already exist and are non-empty (safe to re-run /
    resume interrupted downloads).
  - Caches chapter metadata locally so repeated runs skip the API feed
    lookup.  Pass --fresh to bypass the cache.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple
from urllib.parse import urlparse

import requests

from mangaeasy import __version__
from mangaeasy.brand import CLI_NAME
from mangaeasy.config import CONFIG_FILE, load_download_config, load_system_config
from mangaeasy.path_safety import portable_segment_arg, validate_portable_segment
from mangaeasy.paths import manga_dir
from mangaeasy.utils import emit_result

# ── Constants ─────────────────────────────────────────────────────────────────
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})

API_BASE   = "https://api.mangadex.org"
REPORT_URL = "https://api.mangadex.network/report"

_USER_AGENT = (
    f"MediaConductor/{__version__} "
    "(+https://github.com/tawhidUnhappy/MediaConductor)"
)

# Minimum seconds between consecutive MangaDex API calls.
# MangaDex asks clients to stay well below 5 req/s.
_MIN_API_INTERVAL = 0.4

_last_api_call: float = 0.0


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = _USER_AGENT
    return s


def _api_get(
    sess: requests.Session,
    url: str,
    params: dict | None = None,
    retries: int = 6,
) -> requests.Response:
    """GET with polite inter-call spacing and exponential back-off."""
    global _last_api_call
    gap = _MIN_API_INTERVAL - (time.monotonic() - _last_api_call)
    if gap > 0:
        time.sleep(gap)

    for attempt in range(retries):
        try:
            resp = sess.get(url, params=params, timeout=25)
            _last_api_call = time.monotonic()

            if resp.status_code == 429:
                wait = max(float(resp.headers.get("Retry-After", 60)),
                           30 * (2 ** attempt))
                print(f"[WARN] Rate-limited (429). Waiting {wait:.0f}s…", flush=True)
                time.sleep(wait)
                continue

            if resp.status_code >= 500:
                wait = min(120, 10 * (2 ** attempt))
                print(f"[WARN] Server error {resp.status_code}. Retry in {wait:.0f}s…",
                      flush=True)
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp

        except requests.exceptions.RequestException as exc:
            _last_api_call = time.monotonic()
            if attempt < retries - 1:
                wait = min(60, 5 * (2 ** attempt))
                print(f"[WARN] Network error: {exc}. Retry in {wait:.0f}s…", flush=True)
                time.sleep(wait)
            else:
                raise

    raise RuntimeError(f"All {retries} attempts failed for {url}")


def _report_image(
    sess: requests.Session,
    url: str,
    success: bool,
    cached: bool,
    bytes_dl: int,
    duration_ms: int,
) -> None:
    """Report an image download result to the MangaDex at-home network.

    This is a CDN health signal — required by MangaDex guidelines.
    Failures are silently swallowed so they never break the download.
    """
    try:
        sess.post(
            REPORT_URL,
            json={
                "url": url,
                "success": success,
                "cached": cached,
                "bytes": bytes_dl,
                "duration": duration_ms,
            },
            timeout=6,
        )
    except Exception:
        pass


# ── Metadata cache ────────────────────────────────────────────────────────────
# Stored at <chapter_dir>/.mdx_cache.json so it survives download/ deletion
# and lets subsequent runs skip the chapter-feed API lookup entirely.

_CACHE_FILE = ".mdx_cache.json"


def _load_cache(ch_dir: Path) -> dict | None:
    p = ch_dir / _CACHE_FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_cache(ch_dir: Path, data: dict) -> None:
    ch_dir.mkdir(parents=True, exist_ok=True)
    (ch_dir / _CACHE_FILE).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ── Manga metadata (library/<name>/manga.json) ───────────────────────────────
# One file per manga answering "where did this come from?": source site,
# title URL, MangaDex UUID, canonical title, and which chapters were
# downloaded when. Written/merged on every download; surfaced by
# `mangaeasy library-list`.

_MANGA_JSON = "manga.json"


def manga_url(manga_id: str) -> str:
    return f"https://mangadex.org/title/{manga_id}"


def load_manga_json(manga_root: Path) -> dict:
    p = manga_root / _MANGA_JSON
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def fetch_manga_info(sess: requests.Session, manga_id: str) -> dict:
    """Best-effort {title, original_language} from the MangaDex API — never fatal.

    ``originalLanguage`` drives automatic reading-direction resolution
    (ja/zh-hk -> right-to-left; ko/zh/en -> left-to-right), so paged Japanese
    manga and Korean/Chinese sources crop in the correct panel order without
    per-run flags. See mangaeasy/panels/direction.py.
    """
    try:
        resp = _api_get(sess, f"{API_BASE}/manga/{manga_id}", retries=2)
        attributes = resp.json().get("data", {}).get("attributes", {}) or {}
        titles = attributes.get("title") or {}
        return {
            "title": titles.get("en") or next(iter(titles.values()), None),
            "original_language": attributes.get("originalLanguage"),
        }
    except Exception:
        return {}


def fetch_manga_title(sess: requests.Session, manga_id: str) -> str | None:
    return fetch_manga_info(sess, manga_id).get("title")


def merge_manga_record(
    existing: dict,
    *,
    name: str,
    manga_id: str,
    lang: str,
    chapter_str: str,
    chapter_id: str,
    pages: int,
    source_url: str | None = None,
    title: str | None = None,
    original_language: str | None = None,
    when: str | None = None,
) -> dict:
    """Merge one downloaded chapter into a manga.json record (pure)."""
    record = dict(existing) if isinstance(existing, dict) else {}
    record["name"] = name
    record["source"] = "mangadex"
    record["manga_id"] = manga_id
    record["url"] = manga_url(manga_id)
    if title:
        record["title"] = title
    if original_language:
        record["original_language"] = original_language
    # Keep the user's original link only when it adds information
    # (e.g. the slugged URL they pasted) — not a bare UUID.
    if source_url and source_url.startswith(("http://", "https://")) \
            and source_url != record["url"]:
        record["source_url"] = source_url
    chapters = record.get("chapters")
    if not isinstance(chapters, dict):
        chapters = {}
    chapters[chapter_str] = {
        "chapter_id": chapter_id,
        "language": lang,
        "pages": pages,
        "downloaded_at": when or datetime.now(timezone.utc).isoformat(),
    }
    record["chapters"] = {k: chapters[k] for k in sorted(chapters)}
    return record


def update_manga_json(manga_root: Path, **kwargs) -> Path:
    """Read-merge-write library/<name>/manga.json; returns the file path."""
    record = merge_manga_record(load_manga_json(manga_root), **kwargs)
    manga_root.mkdir(parents=True, exist_ok=True)
    path = manga_root / _MANGA_JSON
    path.write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


# ── MangaDex API calls ────────────────────────────────────────────────────────

def normalize_manga_id(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith(("http://", "https://")):
        return raw
    uuid_re = re.compile(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
        r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    )
    m = uuid_re.search(urlparse(raw).path)
    if m:
        return m.group()
    print(f"[ERROR] Could not extract manga UUID from URL: {raw}")
    sys.exit(1)


def fetch_chapter_map(
    sess: requests.Session, manga_id: str, lang: str
) -> dict[str, dict]:
    """Return {chapter number string: {"id", "pages", "title"}} for the manga.

    Paginates the whole feed once. When the same chapter number exists in
    several scanlations, keeps the version with the most pages (partial
    uploads of a chapter are common on MangaDex) and logs the choice.
    """
    offset, limit, checked = 0, 100, 0
    best: dict[str, dict] = {}

    while True:
        print(f"[INFO] Fetching chapter feed (offset={offset})…", flush=True)
        resp = _api_get(
            sess,
            f"{API_BASE}/manga/{manga_id}/feed",
            params={
                "translatedLanguage[]": [lang],
                "order[chapter]": "asc",
                "limit": limit,
                "offset": offset,
            },
        )
        data  = resp.json()
        items = data.get("data", [])
        total = data.get("total", 0)

        for ch_obj in items:
            attrs = ch_obj.get("attributes", {})
            if attrs.get("externalUrl"):
                continue  # hosted off-site; images not downloadable via at-home
            # MangaDex stores chapter numbers as strings ("1", "1.5", …)
            ch_str = str(attrs.get("chapter") or "")
            if not ch_str:
                continue
            cand = {
                "id": ch_obj["id"],
                "pages": int(attrs.get("pages") or 0),
                "title": attrs.get("title") or "",
            }
            prev = best.get(ch_str)
            if prev is None:
                best[ch_str] = cand
            elif cand["pages"] > prev["pages"]:
                print(
                    f"[INFO] Chapter {ch_str}: preferring {cand['pages']}-page "
                    f"version over {prev['pages']}-page duplicate.",
                    flush=True,
                )
                best[ch_str] = cand

        checked += len(items)
        offset  += len(items)
        if not items or checked >= total or len(items) < limit:
            break

    return best


def find_chapter_id(
    sess: requests.Session, manga_id: str, chapter: str, lang: str
) -> str:
    """Return the chapter UUID (best duplicate), or exit if not found."""
    entry = fetch_chapter_map(sess, manga_id, lang).get(str(chapter))
    if entry is None:
        print(
            f"[ERROR] Chapter '{chapter}' not found for manga {manga_id} "
            f"in language '{lang}'.",
            flush=True,
        )
        sys.exit(1)
    title = entry["title"]
    print(f"[INFO] Found chapter {chapter}: {entry['id']}"
          + (f' "{title}"' if title else ""), flush=True)
    return entry["id"]


def fetch_at_home(sess: requests.Session, chapter_id: str) -> dict:
    print("[INFO] Fetching at-home CDN server…", flush=True)
    resp = _api_get(sess, f"{API_BASE}/at-home/server/{chapter_id}")
    data = resp.json()
    if "baseUrl" not in data or "chapter" not in data:
        raise RuntimeError(f"Unexpected at-home response: {data}")
    return data


def build_image_urls(
    at_home: dict, use_data_saver: bool
) -> Tuple[List[str], List[str]]:
    base       = at_home["baseUrl"]
    info       = at_home["chapter"]
    ch_hash    = info["hash"]
    key, qdir  = ("dataSaver", "data-saver") if use_data_saver else ("data", "data")
    files = info.get(key)
    if not files:
        raise RuntimeError(f"No images found under key '{key}' in at-home data")
    return [f"{base}/{qdir}/{ch_hash}/{f}" for f in files], files


# ── Image downloader ──────────────────────────────────────────────────────────

def download_images(
    sess: requests.Session,
    urls: List[str],
    filenames: List[str],
    output_dir: Path,
    delay: float,
    chapter_str: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    total     = len(urls)
    pad       = max(2, len(str(total)))
    skipped   = 0

    for idx, (url, fname) in enumerate(zip(urls, filenames, strict=False), start=1):
        ext      = os.path.splitext(fname)[1] or ".jpg"
        dest     = output_dir / f"{chapter_str}_{idx:0{pad}d}{ext}"

        if dest.exists() and dest.stat().st_size > 0:
            skipped += 1
            print(f"[{idx}/{total}] skip (exists): {dest.name}", flush=True)
            continue

        print(f"[{idx}/{total}] {dest.name}", flush=True)

        t0       = time.monotonic()
        success  = False
        bytes_dl = 0
        cached   = False

        for attempt in range(3):
            try:
                with sess.get(url, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    cached  = r.headers.get("X-Cache", "").upper().startswith("HIT")
                    content = b"".join(r.iter_content(chunk_size=65_536))
                    bytes_dl = len(content)
                    dest.write_bytes(content)
                success = True
                break
            except Exception as exc:
                if attempt < 2:
                    wait = 4 * (2 ** attempt)  # 4s, 8s
                    print(f"  ! attempt {attempt + 1}/3 failed: {exc}."
                          f" Retry in {wait}s…", flush=True)
                    time.sleep(wait)
                else:
                    print(f"  ! Gave up on {dest.name}: {exc}", flush=True)

        duration_ms = int((time.monotonic() - t0) * 1000)
        _report_image(sess, url, success, cached, bytes_dl, duration_ms)

        # Polite inter-image pause with ±20 % jitter.
        if idx < total:
            jitter  = random.uniform(-0.2, 0.2) * delay
            time.sleep(max(0.5, delay + jitter))

    downloaded = total - skipped
    if skipped:
        print(f"\n[INFO] {downloaded} new + {skipped} already existed → {output_dir.resolve()}", flush=True)
    else:
        print(f"\n[INFO] {total} pages saved → {output_dir.resolve()}", flush=True)


# ── Entry point ───────────────────────────────────────────────────────────────

def _parse_chapter_tokens(tokens: List[str]) -> List[str]:
    """Expand chapter tokens ("0", "7-12", "3.5") into an ordered list.

    Ranges only expand over integers; decimal chapters must be named
    explicitly (MangaDex numbers them "3.5" etc.).
    """
    out: List[str] = []
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        m = re.fullmatch(r"(\d+)-(\d+)", tok)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            if hi < lo:
                lo, hi = hi, lo
            out.extend(str(n) for n in range(lo, hi + 1))
        else:
            out.append(tok)
    seen: set[str] = set()
    return [c for c in out if not (c in seen or seen.add(c))]


def _download_one_chapter(
    sess: requests.Session,
    dl_cfg: dict,
    manga_id: str,
    raw_id: str,
    chapter: str,
    *,
    fresh: bool,
    chapter_entry: dict | None,
) -> bool:
    """Download a single chapter. Returns True on success.

    ``chapter_entry`` is the pre-fetched feed entry ({"id", "pages", ...})
    or None when the caller wants this function to look it up itself.
    """
    chapter_str    = str(chapter).zfill(2) if "." not in str(chapter) else str(chapter)
    chapter_str = validate_portable_segment(chapter_str, label="MangaDex chapter folder")
    lang           = dl_cfg.get("translated_language", "en")
    manga_root     = manga_dir(str(dl_cfg.get("name")))
    output_dir     = manga_root / chapter_str / "download"
    ch_dir         = output_dir.parent   # <library>/<name>/<chapter_str>/
    use_data_saver = bool(dl_cfg.get("use_data_saver", False))
    delay          = float(dl_cfg.get("download_delay", 1.5))

    print("=== MangaDex downloader ===")
    print(f"  User-Agent : {_USER_AGENT}")
    print(f"  Manga      : {manga_id}")
    print(f"  Chapter    : {chapter_str}  Language: {lang}")
    print(f"  Output     : {output_dir}")
    print(f"  Quality    : {'data-saver' if use_data_saver else 'original'}")
    print(f"  Img delay  : {delay}s ± 20 %")

    # ── Load / clear cache ─────────────────────────────────────────────────
    cache: dict | None = None
    if fresh:
        cache_file = ch_dir / _CACHE_FILE
        if cache_file.exists():
            cache_file.unlink()
            print("  Cache      : cleared (--fresh)")
        else:
            print("  Cache      : --fresh (no cache to clear)")
    else:
        cache = _load_cache(ch_dir)
        if cache:
            when = cache.get("fetched_at", "?")
            print(f"  Cache      : hit (fetched {when[:19]})")
        else:
            print("  Cache      : miss — will fetch from MangaDex")
    print("===========================\n")

    # ── Fast skip: chapter already complete ────────────────────────────────
    # Politeness matters most on `--all` re-runs over a long series: without
    # this, every already-downloaded chapter still costs an at-home API call
    # just to discover there is nothing to do.
    if cache and cache.get("total") and output_dir.is_dir():
        present = sum(1 for p in output_dir.iterdir() if p.suffix.lower() in _IMAGE_EXTS)
        if present >= int(cache["total"]):
            print(f"[INFO] Chapter {chapter_str} already complete "
                  f"({present}/{cache['total']} pages) — skipped. Use --fresh to re-fetch.",
                  flush=True)
            return True

    # ── Chapter ID ─────────────────────────────────────────────────────────
    # Cache the UUID forever — it never changes for a given manga/chapter/lang.
    if cache and cache.get("chapter_id"):
        chapter_id = cache["chapter_id"]
        print(f"[INFO] Cached chapter ID: {chapter_id}", flush=True)
    elif chapter_entry is not None:
        chapter_id = chapter_entry["id"]
        title = chapter_entry.get("title") or ""
        print(f"[INFO] Found chapter {chapter}: {chapter_id}"
              + (f' "{title}"' if title else ""), flush=True)
    else:
        chapter_id = find_chapter_id(sess, manga_id, str(chapter), lang)

    # ── At-home CDN ────────────────────────────────────────────────────────
    # Always fetch a fresh CDN URL — these rotate frequently.
    at_home = fetch_at_home(sess, chapter_id)
    urls, fnames = build_image_urls(at_home, use_data_saver)
    total = len(urls)

    # ── Persist / refresh cache ────────────────────────────────────────────
    _save_cache(ch_dir, {
        "manga_id":   manga_id,
        "chapter":    chapter_str,
        "lang":       lang,
        "chapter_id": chapter_id,
        "image_hash": at_home["chapter"]["hash"],
        "filenames":  fnames,
        "total":      total,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    })

    # ── Record the manga's source link (library/<name>/manga.json) ────────
    existing = load_manga_json(manga_root)
    title = existing.get("title")
    original_language = existing.get("original_language")
    if not title or not original_language:
        info = fetch_manga_info(sess, manga_id)
        title = title or info.get("title")
        original_language = original_language or info.get("original_language")
    info_path = update_manga_json(
        manga_root,
        original_language=original_language,
        name=str(dl_cfg.get("name")),
        manga_id=manga_id,
        lang=lang,
        chapter_str=chapter_str,
        chapter_id=chapter_id,
        pages=total,
        source_url=raw_id,
        title=title,
    )
    print(f"[INFO] Manga info recorded → {info_path}", flush=True)

    print(f"[INFO] {total} page(s) to download.\n", flush=True)
    download_images(sess, urls, fnames, output_dir, delay, chapter_str)

    # ── Missing-page report ────────────────────────────────────────────────
    actual = (
        sum(1 for p in output_dir.iterdir() if p.suffix.lower() in _IMAGE_EXTS)
        if output_dir.is_dir() else 0
    )
    if actual < total:
        print(
            f"\n[WARN] {actual}/{total} pages present — "
            f"{total - actual} missing. Run again to resume.",
            flush=True,
        )
        return False
    print(f"\n[INFO] All {total} pages present ✓", flush=True)
    return True


def _chapter_sort_key(ch: str) -> tuple[float, str]:
    """Numeric-first ordering for MangaDex chapter strings ("1", "3.5", …)."""
    try:
        return (float(ch), ch)
    except ValueError:
        return (float("inf"), ch)


def _chapter_arg(value: str) -> str:
    if not re.fullmatch(r"\d+(?:\.\d+)?", value):
        raise argparse.ArgumentTypeError("chapter must be a non-negative number such as 3 or 3.5")
    return value


def _chapter_token_arg(value: str) -> str:
    if not re.fullmatch(r"\d+(?:\.\d+)?|\d+-\d+", value):
        raise argparse.ArgumentTypeError(
            "chapter token must be a number, decimal, or integer range such as 0-12"
        )
    return value


def _slugify_project_name(title: str) -> str:
    """Filesystem-safe library folder name derived from a manga title."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", title).strip("_.")
    return slug[:60] or "manga"


def _resolve_dl_cfg(args) -> dict:
    """Merged download settings; --url bypasses the config.json requirement."""
    if args.url:
        project = {}
        if CONFIG_FILE.exists():
            try:
                project = json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("download") or {}
            except Exception:
                project = {}
        merged = {**load_system_config().get("download_defaults", {}), **project}
        merged["manga_id"] = args.url
        if not args.name and str(merged.get("name") or "").strip():
            # A config-supplied name beating --url title derivation has
            # misnamed a real project before — make the override loud.
            print(f"[INFO] project name '{merged['name']}' comes from config.json, "
                  f"NOT from the manga title — pass --name to override", flush=True)
    else:
        merged = load_download_config()
    if args.name:
        merged["name"] = args.name
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(
        prog=f"{CLI_NAME} download",
        description="Download manga chapters from MangaDex — politely (API "
                    "spacing, 429 backoff, jittered image delays, resumable).",
    )
    parser.add_argument(
        "--url", metavar="URL_OR_UUID",
        help="MangaDex title URL (or bare manga UUID). Overrides config.json, "
             "so agents can download without editing any file.",
    )
    parser.add_argument(
        "--name", metavar="PROJECT", type=portable_segment_arg,
        help="Library folder name (library/<PROJECT>/). With --url and no "
             "--name, a safe name is derived from the manga's title.",
    )
    parser.add_argument(
        "--fresh", action="store_true",
        help="Bypass the local cache and re-fetch all metadata from MangaDex.",
    )
    parser.add_argument(
        "--chapter", metavar="N", type=_chapter_arg,
        help="Chapter to download (overrides config.json). "
             "Accepts decimals like 3.5.",
    )
    parser.add_argument(
        "--chapters", nargs="+", metavar="TOKEN", type=_chapter_token_arg,
        help="Several chapters: numbers and inclusive ranges, "
             "e.g. --chapters 0-12 14 20.5. Missing chapters are skipped "
             "with a warning instead of aborting the batch.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Download every chapter available in the configured language "
             "(start to end; best duplicate per chapter). Already-complete "
             "chapters are skipped, so re-running resumes.",
    )
    parser.add_argument(
        "--from", dest="from_chapter", metavar="N", type=float,
        help="With --all: skip chapters numbered below N.",
    )
    parser.add_argument(
        "--to", dest="to_chapter", metavar="N", type=float,
        help="With --all: skip chapters numbered above N.",
    )
    args = parser.parse_args()
    if sum(bool(x) for x in (args.chapter, args.chapters, args.all)) > 1:
        parser.error("use only one of --chapter / --chapters / --all")
    if (args.from_chapter is not None or args.to_chapter is not None) and not args.all:
        parser.error("--from/--to only apply with --all")

    dl_cfg = _resolve_dl_cfg(args)

    raw_id = str(dl_cfg.get("manga_id", "")).strip()
    if not raw_id:
        print("[ERROR] 'manga_id' is missing in config.json download section"
              " (or pass --url)")
        sys.exit(1)

    manga_id = normalize_manga_id(raw_id)
    lang     = dl_cfg.get("translated_language", "en")
    sess     = _session()

    # Derive the library folder name from the manga title when only a URL was
    # given — one API call, cached in manga.json afterwards.
    if not str(dl_cfg.get("name") or "").strip():
        title = fetch_manga_title(sess, manga_id)
        if not title:
            print("[ERROR] could not fetch the manga title to derive a project "
                  "name — pass --name explicitly")
            sys.exit(1)
        dl_cfg["name"] = _slugify_project_name(title)
        print(f"[INFO] Project name: {dl_cfg['name']} (derived from title; "
              f"override with --name)", flush=True)

    # Config-supplied names pass through the same containment rule as
    # ``--name``.  Validate before feed/image work so a bad config cannot turn
    # ``library / name`` into a write outside the library root.
    try:
        dl_cfg["name"] = validate_portable_segment(
            str(dl_cfg["name"]), label="MangaDex project name"
        )
    except ValueError as exc:
        parser.error(str(exc))

    # One feed fetch covers every chapter in a batch (and disambiguates
    # duplicate scanlations by page count).
    chapter_map: dict[str, dict] | None = None

    if args.all:
        chapter_map = fetch_chapter_map(sess, manga_id, lang)
        if not chapter_map:
            print(f"[ERROR] no chapters found for this manga in '{lang}'")
            sys.exit(1)
        chapters = sorted(chapter_map, key=_chapter_sort_key)
        lo, hi = args.from_chapter, args.to_chapter
        if lo is not None or hi is not None:
            def _in_bounds(ch: str) -> bool:
                try:
                    num = float(ch)
                except ValueError:
                    return False  # non-numeric specials excluded from ranges
                return (lo is None or num >= lo) and (hi is None or num <= hi)
            chapters = [c for c in chapters if _in_bounds(c)]
        print(f"[INFO] --all: {len(chapters)} chapter(s) in '{lang}' "
              f"({chapters[0]} … {chapters[-1]})" if chapters else
              "[INFO] --all: nothing in the requested range", flush=True)
        if not chapters:
            sys.exit(1)
    elif args.chapters:
        chapters = _parse_chapter_tokens(args.chapters)
    elif args.chapter is not None:
        chapters = [str(args.chapter)]
    else:
        chapter = dl_cfg.get("chapter")
        if chapter is None:
            print("[ERROR] 'chapter' is missing in config.json download section"
                  " (or pass --chapter / --chapters / --all)")
            sys.exit(1)
        chapters = [str(chapter)]

    if chapter_map is None and len(chapters) > 1:
        chapter_map = fetch_chapter_map(sess, manga_id, lang)

    ok, missing, failed = [], [], []
    for chapter in chapters:
        entry = chapter_map.get(chapter) if chapter_map is not None else None
        if chapter_map is not None and entry is None:
            print(f"[WARN] Chapter {chapter} not on MangaDex in '{lang}' — skipped.",
                  flush=True)
            missing.append(chapter)
            continue
        try:
            done = _download_one_chapter(
                sess, dl_cfg, manga_id, raw_id, chapter,
                fresh=args.fresh, chapter_entry=entry,
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"[ERROR] Chapter {chapter} failed: {exc}", flush=True)
            failed.append(chapter)
            continue
        (ok if done else failed).append(chapter)

    if len(chapters) > 1:
        print("\n=== Batch summary ===")
        print(f"  downloaded : {len(ok)} ({', '.join(ok) or '-'})")
        if missing:
            print(f"  not found  : {len(missing)} ({', '.join(missing)})")
        if failed:
            print(f"  incomplete : {len(failed)} ({', '.join(failed)}) — rerun to resume")
    if not failed:
        emit_result(
            project=manga_dir(str(dl_cfg.get("name"))),
            downloaded=ok,
            missing=missing,
            failed=failed,
        )
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
