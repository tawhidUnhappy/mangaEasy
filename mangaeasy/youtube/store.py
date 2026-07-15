"""Credential storage for one or more YouTube account profiles.

Everything lives in this install's own data folder
(``<data root>/.mangaeasy/youtube/``), never the system keyring:

* ``client_secret.json`` is the user's Google OAuth Desktop-app client.
* ``token.json`` is the granted access/refresh token.
* ``channel.json`` is the cached channel title/id for offline status.

Tokens are secrets: never print or log their contents, only paths/booleans.
The google-auth imports remain in :mod:`mangaeasy.youtube.auth`.

The ``default`` profile deliberately keeps the original files directly under
``youtube/``. Its ``client_secret.json`` is also the shared OAuth client used
by named profiles unless they provide an override. This preserves existing
single-account installs while every profile still owns an isolated token and
channel cache under ``youtube/profiles/<name>/``.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from tempfile import NamedTemporaryFile

from mangaeasy.tools.external import mangaeasy_home

# Full video management (youtube.force-ssl): upload, edit metadata, and delete
# the channel's videos. Tokens granted before this scope was added can be
# refreshed by running youtube-auth again for that profile.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

DEFAULT_PROFILE = "default"
PROFILE_PATTERN = r"[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?"
_PROFILE_RE = re.compile(rf"^{PROFILE_PATTERN}$")


def _is_link_or_reparse(path: Path) -> bool:
    """Return whether *path* is a symlink or Windows reparse point."""
    try:
        file_stat = path.lstat()
    except OSError:
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(
        reparse_flag and getattr(file_stat, "st_file_attributes", 0) & reparse_flag
    )


def _confined_child(parent: Path, name: str, label: str) -> Path:
    """Return one direct, non-link child that resolves below *parent*."""
    candidate = parent / name
    if _is_link_or_reparse(candidate):
        raise ValueError(f"{label} must not be a symlink or reparse point")
    if candidate.resolve(strict=False).parent != parent.resolve(strict=False):
        raise ValueError(f"{label} resolves outside the YouTube credential store")
    return candidate


def youtube_dir() -> Path:
    home = mangaeasy_home()
    return _confined_child(home, "youtube", "YouTube credential store")


def validate_profile(profile: str | None = None) -> str:
    """Return a safe, portable profile name or raise ``ValueError``."""
    value = DEFAULT_PROFILE if profile is None else str(profile)
    if not _PROFILE_RE.fullmatch(value):
        raise ValueError(
            "profile must be 1-64 lowercase letters, numbers, '-' or '_'; "
            "it must start and end with a letter or number"
        )
    return value


def profile_dir(profile: str | None = None) -> Path:
    name = validate_profile(profile)
    root = youtube_dir()
    if name == DEFAULT_PROFILE:
        # Legacy single-account layout. Do not migrate or duplicate secrets.
        return root
    profiles_root = _confined_child(root, "profiles", "YouTube profiles directory")
    return _confined_child(profiles_root, name, f"profile '{name}'")


def _profile_file(profile: str | None, filename: str) -> Path:
    """Return a confined credential/cache leaf, rejecting link aliases."""
    return _confined_child(profile_dir(profile), filename, f"YouTube file '{filename}'")


def client_secret_path(profile: str | None = None) -> Path:
    return _profile_file(profile, "client_secret.json")


def shared_client_secret_path() -> Path:
    """Predefined Desktop-app OAuth client shared by every profile."""
    return _profile_file(DEFAULT_PROFILE, "client_secret.json")


def effective_client_secret_path(profile: str | None = None) -> Path:
    """Profile override when present, otherwise the legacy/shared client."""
    name = validate_profile(profile)
    specific = client_secret_path(name)
    if name != DEFAULT_PROFILE and specific.is_file():
        return specific
    return shared_client_secret_path()


def token_path(profile: str | None = None) -> Path:
    return _profile_file(profile, "token.json")


def channel_cache_path(profile: str | None = None) -> Path:
    return _profile_file(profile, "channel.json")


def read_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path: Path, data: dict) -> None:
    """Atomically write owner-only JSON without ever exposing its contents."""
    root = youtube_dir()
    resolved_root = root.resolve(strict=False)
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise ValueError("OAuth JSON destination is outside the YouTube credential store") from None
    current = root
    for component in relative.parts:
        current = _confined_child(current, component, "OAuth JSON destination")
    if not path.resolve(strict=False).is_relative_to(resolved_root):
        raise ValueError("OAuth JSON destination resolves outside the YouTube credential store")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Recheck after creating parents so a pre-existing junction cannot be
    # introduced through a path that did not exist during the first check.
    current = root
    for component in relative.parts:
        current = _confined_child(current, component, "OAuth JSON destination")
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    try:
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def write_client_config(client_id: str, client_secret: str,
                        profile: str | None = None) -> None:
    """Persist a pasted client pair as a standard installed-app config."""
    write_json(client_secret_path(profile), {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    })


def looks_like_client_id(client_id: str) -> bool:
    return client_id.endswith(".apps.googleusercontent.com") and len(client_id) > 40


def status_snapshot(profile: str | None = None) -> dict:
    """Offline status for one profile (no network or google imports)."""
    name = validate_profile(profile)
    channel = read_json(channel_cache_path(name))
    token = read_json(token_path(name))
    shared_client = shared_client_secret_path()
    profile_client = client_secret_path(name)
    effective_client = effective_client_secret_path(name)
    if name != DEFAULT_PROFILE and profile_client.is_file():
        client_source = "profile"
    elif shared_client.is_file():
        client_source = "shared"
    else:
        client_source = None
    return {
        "profile": name,
        "connected": bool(token.get("refresh_token")),
        "client_secrets_present": effective_client.is_file(),
        "client_source": client_source,
        "effective_client_file": str(effective_client),
        "shared_client_file": str(shared_client),
        "shared_client_present": shared_client.is_file(),
        "profile_client_present": name != DEFAULT_PROFILE and profile_client.is_file(),
        "channel_title": channel.get("title"),
        "channel_id": channel.get("id"),
        "scopes": token.get("scopes") or [],
        "token_file": str(token_path(name)),
    }


def profile_names() -> list[str]:
    """Discover safe profile directories without reading their credentials.

    ``default`` is always present so a fresh install has a stable target.
    Empty named directories remain visible until explicitly removed.
    """
    names = {DEFAULT_PROFILE}
    root = _confined_child(youtube_dir(), "profiles", "YouTube profiles directory")
    try:
        children = list(root.iterdir())
    except OSError:
        children = []
    for child in children:
        if child.is_dir() and _PROFILE_RE.fullmatch(child.name):
            try:
                profile_dir(child.name)
            except ValueError:
                continue
            names.add(child.name)
    return sorted(names, key=lambda item: (item != DEFAULT_PROFILE, item))


def profiles_snapshot() -> list[dict]:
    """Machine-readable offline status for all profiles."""
    return [status_snapshot(name) for name in profile_names()]
