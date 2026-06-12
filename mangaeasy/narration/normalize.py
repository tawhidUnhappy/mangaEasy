"""mangaeasy.narration.normalize — normalize narration text for safe index-tts2 playback.

Two passes per narration string:
  1. Character-level: smart quotes, em/en dashes, ellipsis, accented vowels
  2. Word-level: exact substitutions for words TTS mispronounces

Default target: chapter defined in config.json (download.name / download.chapter)
Use --all to process every chapter under the manga folder.

Usage:
  mangaeasy normalize-narration           # dry-run current chapter
  mangaeasy normalize-narration --write   # apply to current chapter
  mangaeasy normalize-narration --all     # dry-run all chapters
  mangaeasy normalize-narration --all --write
"""

import argparse
import re
from pathlib import Path

from mangaeasy.config import load_download_config
from mangaeasy.narration import load_narration, save_narration
from mangaeasy.paths import manga_dir, narration_json

# ---------------------------------------------------------------------------
# Character-level replacements (applied first, in order)
# ---------------------------------------------------------------------------
CHAR_MAP: list[tuple[str, str]] = [
    # Smart / curly quotes → straight
    ("“", '"'),  # " left double
    ("”", '"'),  # " right double
    ("‘", "'"),  # ' left single
    ("’", "'"),  # ' right single
    # Dashes
    ("—", " - "),  # — em dash
    ("–", "-"),    # – en dash
    # Ellipsis
    ("…", "..."),  # …
    # Accented vowels
    ("é", "e"), ("è", "e"), ("ê", "e"), ("ë", "e"),
    ("à", "a"), ("â", "a"), ("ä", "a"),
    ("î", "i"), ("ï", "i"),
    ("ô", "o"), ("ö", "o"),
    ("ù", "u"), ("û", "u"), ("ü", "u"),
    ("ç", "s"),
    # Upper-case equivalents
    ("É", "E"), ("À", "A"), ("Â", "A"),
    ("Î", "I"), ("Ô", "O"), ("Û", "U"), ("Ç", "S"),
]

# ---------------------------------------------------------------------------
# Word-level substitutions (whole-word, case-insensitive)
# ---------------------------------------------------------------------------
WORD_MAP: dict[str, str] = {
    # French loanwords (accent already stripped by CHAR_MAP, kept as safety net)
    "fiancee": "fi-on-say",
    "fiance":  "fi-on-say",
    "cafe":    "ka-fay",
    "resume":  "reh-zoo-may",
    "naive":   "nah-eve",
    "facade":  "fah-sahd",
    "cliche":  "klee-shay",
    "passe":   "pa-say",
    # Onomatopoeia
    "hmph": "hmm",
    "tsk":  "tsk tsk",
    # Abbreviations
    "vs":   "versus",
    "vs.":  "versus",
    "ft.":  "featuring",
    "etc.": "et cetera",
    "i.e.": "that is",
    "e.g.": "for example",
}

_WORD_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in WORD_MAP) + r")\b",
    flags=re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Core normalization
# ---------------------------------------------------------------------------

def _apply_char_map(text: str) -> str:
    for src, dst in CHAR_MAP:
        text = text.replace(src, dst)
    return text


def _apply_word_map(text: str) -> str:
    def replacer(m: re.Match) -> str:
        orig = m.group(0)
        sub  = WORD_MAP[orig.lower()]
        if orig.isupper():
            return sub.upper()
        if orig.istitle():
            return sub.capitalize()
        return sub

    return _WORD_PATTERN.sub(replacer, text)


def normalize(text: str) -> str:
    text = _apply_char_map(text)
    text = _apply_word_map(text)
    return text


# ---------------------------------------------------------------------------
# File processing
# ---------------------------------------------------------------------------

def process_file(path: Path, write: bool, label: str) -> int:
    entries = load_narration(path)
    changes = 0
    for entry in entries:
        original = entry.get("narration", "")
        fixed    = normalize(original)
        if fixed != original:
            if not write:
                print(f"\n  [{label}]")
                print(f"  BEFORE: {original}")
                print(f"  AFTER:  {fixed}")
            entry["narration"] = fixed
            changes += 1

    if write and changes:
        save_narration(entries, path)
        print(f"  [WROTE] {label}  ({changes} change(s))")

    return changes


# ---------------------------------------------------------------------------
# Chapter discovery
# ---------------------------------------------------------------------------

def _all_narration_files(name: str) -> list[tuple[Path, str]]:
    title_dir = manga_dir(name)
    files = []
    for ch_dir in sorted(title_dir.iterdir()):
        try:
            chapter = int(ch_dir.name)
        except ValueError:
            continue
        f = ch_dir / f"narration_{chapter:02d}.json"
        if f.exists():
            files.append((f, f"{name} / {ch_dir.name}"))
    return files


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--write", action="store_true",
                        help="Apply changes in-place (default: dry-run only).")
    parser.add_argument("--all",   action="store_true",
                        help="Process all chapters, not just the one in config.json.")
    args = parser.parse_args()

    dl      = load_download_config()
    name    = str(dl["name"])
    chapter = int(dl["chapter"])

    if args.all:
        files = _all_narration_files(name)
        scope = "all chapters"
    else:
        path = narration_json()
        if not path.exists():
            print(f"[ERROR] Narration file not found: {path}")
            return
        files = [(path, f"{name} / ch{chapter:02d}")]
        scope = f"chapter {chapter:02d}"

    if not files:
        print(f"[ERROR] No narration.json files found for {name!r}")
        return

    mode = "APPLYING" if args.write else "DRY-RUN"
    print(f"[{mode}] {name}  —  {scope}")

    total = 0
    for path, label in files:
        total += process_file(path, write=args.write, label=label)

    if not args.write:
        if total:
            print(f"\n[DRY-RUN] {total} narration(s) would change.  Add --write to apply.")
        else:
            print("\n[DRY-RUN] Clean — nothing to change.")
    else:
        print(f"\n[DONE] {total} narration(s) updated in {len(files)} file(s).")


if __name__ == "__main__":
    main()
