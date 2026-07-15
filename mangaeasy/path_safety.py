"""Portable validation for names that are joined below application roots.

These helpers validate *names*, not arbitrary user-selected paths.  They keep
``root / user_value`` operations from accepting absolute paths, traversal, or
Windows-only escape/alias forms while still allowing ordinary Unicode and
spaces.
"""

from __future__ import annotations

import argparse
import re
from pathlib import PureWindowsPath


class UnsafePathComponentError(ValueError):
    """A purported child name or relative subpath is unsafe or non-portable."""


_WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)


def _validate_part(part: str, label: str) -> None:
    if part in {"", ".", ".."}:
        raise UnsafePathComponentError(f"{label} must not contain empty or traversal segments")
    if part.endswith((".", " ")):
        raise UnsafePathComponentError(f"{label} must not end a segment with a dot or space")
    if any(character in '<>:"|?*' or ord(character) < 32 for character in part):
        raise UnsafePathComponentError(f"{label} contains non-portable filename characters")
    if part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES:
        raise UnsafePathComponentError(f"{label} contains a reserved portable filename")


def validate_relative_subpath(value: str, *, label: str = "folder path") -> str:
    """Return a normalized safe relative subpath, allowing multiple segments.

    Both slash forms are accepted for cross-platform command files and the
    returned spelling uses ``/``.  Leading/trailing whitespace is rejected so
    a visually identical but surprising directory is never selected.
    """
    if not isinstance(value, str) or not value or value != value.strip():
        raise UnsafePathComponentError(f"{label} must be a non-empty relative path")
    if "\x00" in value:
        raise UnsafePathComponentError(f"{label} contains a null byte")

    portable = value.replace("\\", "/")
    windows_path = PureWindowsPath(value)
    if portable.startswith("/") or windows_path.drive or windows_path.root:
        raise UnsafePathComponentError(f"{label} must be relative to its documented parent")
    # Guard drive syntax even on hosts where pathlib would parse it as a plain
    # filename (for example, validating ``C:folder`` on POSIX).
    if re.match(r"^[A-Za-z]:", portable):
        raise UnsafePathComponentError(f"{label} must not contain a drive prefix")

    parts = portable.split("/")
    for part in parts:
        _validate_part(part, label)
    return "/".join(parts)


def validate_portable_segment(value: str, *, label: str = "folder name") -> str:
    """Return one safe, cross-platform child name (Unicode and spaces allowed)."""
    normalized = validate_relative_subpath(value, label=label)
    if "/" in normalized:
        raise UnsafePathComponentError(f"{label} must be one portable path segment")
    return normalized


def relative_subpath_arg(value: str) -> str:
    """``argparse`` type adapter for a safe relative folder path."""
    try:
        return validate_relative_subpath(value)
    except UnsafePathComponentError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def portable_segment_arg(value: str) -> str:
    """``argparse`` type adapter for one safe portable folder name."""
    try:
        return validate_portable_segment(value)
    except UnsafePathComponentError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def portable_prefix_template_arg(value: str) -> str:
    """Argparse adapter for a filename prefix with only the documented token."""
    try:
        rendered = value.replace("{item}", "item")
        if "{" in rendered or "}" in rendered:
            raise UnsafePathComponentError(
                "filename prefix may contain only the documented {item} placeholder"
            )
        validate_portable_segment(rendered, label="filename prefix")
        return value
    except UnsafePathComponentError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None
