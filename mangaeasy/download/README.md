# mangaeasy/download — acquire source chapters

The **acquire** stage: fetch raw manga pages from a source into
`library/<Project>/<item>/download/`, and record where they came from.

## Files

- [`mangadex.py`](mangadex.py) (`mangaeasy download`) — download a chapter, a
  batch (`--chapters 0-12 14 20.5`), or the whole series (`--all`, optional
  `--from`/`--to` bounds) from MangaDex. `--url <title url>` works with no
  config.json (project name derived from the fetched title; override with
  `--name`); already-complete chapters are fast-skipped so `--all` re-runs
  resume without extra API calls. Writes pages to the item's `download/`
  folder and **maintains `library/<Project>/manga.json`** — the per-project
  source record (source site, canonical MangaDex title URL, the original
  pasted URL, fetched title, and a per-chapter map of
  `chapter_id`/`language`/`pages`/`downloaded_at`).

## Public entry points

- `main()` — the CLI command.
- `update_manga_json(manga_root, **kwargs)` / `merge_manga_record(...)` /
  `load_manga_json(manga_root)` — read/update the source record. `library-list`
  surfaces it as each project's `manga` field. Use these rather than writing
  `manga.json` by hand.
- `manga_url(manga_id)` — canonical `https://mangadex.org/title/<uuid>`.

## Gotchas

- `manga.json` is the **only** place the manga's origin link is kept —
  `config.json` holds just the *current* download target, so without this file
  the link to a previously downloaded manga is lost. Always route writes
  through `update_manga_json()`.
- Downloads are cached per chapter dir (`_load_cache`/`_save_cache`) so re-runs
  don't refetch unchanged pages.

## Tests

[tests/test_manga_json.py](../../tests/test_manga_json.py).
