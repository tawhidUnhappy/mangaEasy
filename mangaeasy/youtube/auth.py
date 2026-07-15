"""YouTube OAuth connect, automatic authorization, status, and logout."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import TextIO

from mangaeasy.brand import CLI_NAME, PRODUCT_NAME
from mangaeasy.youtube import store


class YouTubeAuthorizationError(RuntimeError):
    """A sanitized, user-actionable OAuth failure safe to print."""


def _add_profile_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        type=store.validate_profile,
        default=store.DEFAULT_PROFILE,
        metavar="NAME",
        help="Isolated YouTube account profile (default: default; e.g. manga, song, ai-story).",
    )


def _add_auto_auth_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--no-auto-auth",
        action="store_false",
        dest="auto_auth",
        default=True,
        help="Do not open browser consent automatically when this profile needs authorization.",
    )


def _missing_client_message(profile: str) -> str:
    shared = store.shared_client_secret_path()
    return (
        f"no OAuth Desktop-app client is available for profile '{profile}'. "
        f"Download the JSON and place it at the shared path: {shared} "
        f"(also shown by `{CLI_NAME} youtube-profiles --json`), or run "
        f"`{CLI_NAME} youtube-auth --profile {profile} --client-secrets FILE`"
    )


def load_credentials(profile: str | None = None):
    """Load one profile's credentials and refresh them if stale.

    Returns ``None`` when no usable token exists. Refresh errors intentionally
    propagate so :func:`ensure_credentials` can trigger browser re-consent.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    name = store.validate_profile(profile)
    path = store.token_path(name)
    if not path.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(path), store.SCOPES)
    except ValueError:
        return None
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        store.write_json(path, json.loads(creds.to_json()))
    return creds if creds.valid else None


def _fetch_channel(creds) -> dict:
    """Return the authenticated channel title/id."""
    import requests

    response = requests.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "snippet", "mine": "true"},
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=30,
    )
    response.raise_for_status()
    items = response.json().get("items") or []
    if not items:
        return {}
    return {"id": items[0]["id"], "title": items[0]["snippet"]["title"]}


def authorize_profile(profile: str | None = None, *, no_browser: bool = False,
                      progress_stream: TextIO | None = None):
    """Run browser consent and persist this profile's isolated token/channel.

    A profile-specific OAuth client wins when present; otherwise all profiles
    reuse the predefined shared Desktop-app client. OAuth library stdout is
    redirected to stderr by default so JSON command stdout stays parseable.
    """
    name = store.validate_profile(profile)
    stream = progress_stream or sys.stderr
    client_path = store.effective_client_secret_path(name)
    if not client_path.is_file():
        raise YouTubeAuthorizationError(_missing_client_message(name))

    # Re-consent may select a different Google account. Clear cached identity
    # before opening the browser so an aborted/failed switch cannot leave a
    # misleading channel name attached to the profile.
    store.channel_cache_path(name).unlink(missing_ok=True)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(str(client_path), store.SCOPES)
        print(
            f"YouTube profile '{name}' needs authorization; opening Google consent...",
            file=stream,
            flush=True,
        )
        with redirect_stdout(stream):
            creds = flow.run_local_server(
                port=0,
                open_browser=not no_browser,
                prompt="consent select_account",
                authorization_prompt_message=(
                    f"Visit this URL to authorize {PRODUCT_NAME} profile '{name}':\n{{url}}"
                ),
                success_message=f"{PRODUCT_NAME} is connected - you can close this tab.",
            )
    except Exception as exc:
        raise YouTubeAuthorizationError(
            f"browser authorization failed for profile '{name}' ({type(exc).__name__})"
        ) from None

    try:
        store.write_json(store.token_path(name), json.loads(creds.to_json()))
    except (OSError, ValueError, TypeError) as exc:
        raise YouTubeAuthorizationError(
            f"could not store authorization for profile '{name}' ({type(exc).__name__})"
        ) from None
    try:
        channel = _fetch_channel(creds)
        if channel:
            store.write_json(store.channel_cache_path(name), channel)
            print(
                f"Authorized YouTube profile '{name}' as {channel['title']}.",
                file=stream,
                flush=True,
            )
        else:
            print(
                f"[warn] profile '{name}' authorized but has no accessible YouTube channel.",
                file=stream,
                flush=True,
            )
    except Exception as exc:  # channel identity is verified again by live commands
        print(
            f"[warn] profile '{name}' authorized; channel lookup failed "
            f"({type(exc).__name__}).",
            file=stream,
            flush=True,
        )
    return creds


def ensure_credentials(profile: str | None = None, *, auto_auth: bool = True):
    """Return usable credentials, automatically authorizing when requested."""
    name = store.validate_profile(profile)
    try:
        creds = load_credentials(name)
    except Exception:
        store.channel_cache_path(name).unlink(missing_ok=True)
        if not auto_auth:
            raise
        print(
            f"Stored authorization for YouTube profile '{name}' is no longer usable; "
            "starting browser reauthorization.",
            file=sys.stderr,
            flush=True,
        )
        return authorize_profile(name)
    if creds is not None:
        return creds
    store.channel_cache_path(name).unlink(missing_ok=True)
    if not auto_auth:
        return None
    return authorize_profile(name)


def reauthorize_after_api_error(profile: str, error_or_response, *, auto_auth: bool):
    """Re-consent once after a live auth rejection; otherwise return ``None``."""
    status_code = getattr(error_or_response, "status_code", None)
    reason = getattr(error_or_response, "reason", "")
    response = getattr(error_or_response, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    api_response = response if response is not None else error_or_response
    if not reason and status_code == 403:
        try:
            errors = (api_response.json().get("error") or {}).get("errors") or []
            if errors:
                reason = errors[0].get("reason") or ""
        except (AttributeError, TypeError, ValueError):
            pass
    if status_code != 401 and not (status_code == 403 and reason == "authError"):
        return None
    name = store.validate_profile(profile)
    store.channel_cache_path(name).unlink(missing_ok=True)
    if not auto_auth:
        return None
    print(
        f"YouTube rejected profile '{name}' authorization; starting browser reauthorization.",
        file=sys.stderr,
        flush=True,
    )
    return authorize_profile(name)


def auth_main() -> int:
    parser = argparse.ArgumentParser(
        description="Connect a YouTube account profile (opens Google's browser consent page)."
    )
    _add_profile_argument(parser)
    parser.add_argument(
        "--client-secrets",
        type=Path,
        default=None,
        help="Optional profile-specific Google OAuth Desktop-app JSON. For the shared client, "
             "use profile default or place it at the path from youtube-profiles --json.",
    )
    parser.add_argument(
        "--client-id",
        default=None,
        help="Alternative to --client-secrets: OAuth client ID. Use with --client-secret.",
    )
    parser.add_argument("--client-secret", default=None,
                        help="OAuth client secret paired with --client-id.")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the consent URL instead of opening a browser automatically.",
    )
    args = parser.parse_args()
    profile = args.profile

    if (args.client_id is None) != (args.client_secret is None):
        print("ERROR: --client-id and --client-secret must be provided together.", file=sys.stderr)
        return 1
    if args.client_id is not None and args.client_secrets is not None:
        print(
            "ERROR: use either --client-secrets or --client-id/--client-secret, not both.",
            file=sys.stderr,
        )
        return 1

    if args.client_id is not None:
        client_id = args.client_id.strip()
        client_secret = args.client_secret.strip()
        if not store.looks_like_client_id(client_id):
            print(
                "ERROR: that does not look like a Google OAuth client ID "
                "(expected .apps.googleusercontent.com).\n"
                "Copy it from Google Cloud console -> APIs & Services -> Credentials.",
                file=sys.stderr,
            )
            return 1
        if not client_secret:
            print("ERROR: the client secret is empty.", file=sys.stderr)
            return 1
        store.write_client_config(client_id, client_secret, profile)
        print(f"Saved OAuth client for profile '{profile}' at {store.client_secret_path(profile)}",
              file=sys.stderr)

    if args.client_secrets is not None:
        source = args.client_secrets.expanduser().resolve()
        if not source.is_file():
            print(f"ERROR: client secrets file not found: {source}", file=sys.stderr)
            return 1
        config = store.read_json(source)
        if not config.get("installed"):
            print("ERROR: OAuth client file must contain an 'installed' Desktop-app client.",
                  file=sys.stderr)
            return 1
        destination = store.client_secret_path(profile)
        store.write_json(destination, config)
        print(f"Imported OAuth client for profile '{profile}' at {destination}", file=sys.stderr)

    try:
        authorize_profile(profile, no_browser=args.no_browser)
    except YouTubeAuthorizationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    snapshot = store.status_snapshot(profile)
    who = f" as {snapshot['channel_title']}" if snapshot.get("channel_title") else ""
    print(f"Connected profile '{profile}'{who}.")
    print(f"Token stored at {snapshot['token_file']}")
    print(f"Try it: {CLI_NAME} youtube-status --profile {profile} --verify")
    return 0


def _verify_live(snapshot: dict, *, auto_auth: bool = True) -> dict:
    """Authorize/refresh one profile and verify its channel live."""
    profile = snapshot["profile"]
    try:
        creds = ensure_credentials(profile, auto_auth=auto_auth)
        if creds is None:
            return {
                **snapshot,
                "verified": False,
                "verify_error": "not connected; automatic authorization is disabled",
            }
        # Authorization may have changed every on-disk status field.
        snapshot = store.status_snapshot(profile)
        try:
            channel = _fetch_channel(creds)
        except Exception as api_error:
            replacement = reauthorize_after_api_error(
                profile, api_error, auto_auth=auto_auth
            )
            if replacement is None:
                raise
            creds = replacement
            snapshot = store.status_snapshot(profile)
            channel = _fetch_channel(creds)
        if not channel:
            store.channel_cache_path(profile).unlink(missing_ok=True)
            return {
                **snapshot,
                "channel_title": None,
                "channel_id": None,
                "verified": False,
                "verify_error": "authenticated account has no accessible YouTube channel",
            }
        store.write_json(store.channel_cache_path(profile), channel)
        snapshot = {
            **snapshot,
            "channel_title": channel["title"],
            "channel_id": channel["id"],
        }
        return {**snapshot, "verified": True, "verify_error": None}
    except YouTubeAuthorizationError as exc:
        return {**store.status_snapshot(profile), "verified": False, "verify_error": str(exc)}
    except Exception as exc:  # never echo refresh/request contents
        suffix = "; automatic authorization is disabled" if not auto_auth else ""
        return {
            **store.status_snapshot(profile),
            "verified": False,
            "verify_error": f"authorization/network check failed ({type(exc).__name__}){suffix}",
        }


def status_main() -> int:
    parser = argparse.ArgumentParser(description="Show one YouTube profile's connection status.")
    _add_profile_argument(parser)
    _add_auto_auth_argument(parser)
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit one JSON object on stdout.")
    parser.add_argument("--verify", action="store_true",
                        help="Verify live; browser-authorize automatically if needed.")
    args = parser.parse_args()

    snapshot = store.status_snapshot(args.profile)
    if args.verify:
        snapshot = _verify_live(snapshot, auto_auth=args.auto_auth)

    if args.as_json:
        print(json.dumps(snapshot, ensure_ascii=False))
        return 0

    print(f"Profile: {args.profile}")
    if not snapshot["connected"]:
        print("Not connected.")
        if not snapshot["client_secrets_present"]:
            print(f"Place the downloaded Desktop-app JSON at {snapshot['shared_client_file']}")
        elif args.verify and args.auto_auth:
            print("Browser authorization did not complete; see the error above.")
        else:
            print(f"Run: {CLI_NAME} youtube-auth --profile {args.profile}")
        return 0
    who = snapshot["channel_title"] or "(channel name unknown)"
    print(f"Connected as {who}")
    print(f"  token: {snapshot['token_file']}")
    if args.verify:
        if snapshot.get("verified"):
            print("  verified: yes - token refreshed and channel reachable.")
        else:
            print(f"  verified: NO - {snapshot.get('verify_error')}")
            return 1
    return 0


def profiles_main() -> int:
    parser = argparse.ArgumentParser(description="List isolated YouTube account profiles.")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="Emit one JSON object on stdout.")
    args = parser.parse_args()
    profiles = store.profiles_snapshot()
    payload = {
        "shared_client_file": str(store.shared_client_secret_path()),
        "shared_client_present": store.shared_client_secret_path().is_file(),
        "profiles": profiles,
    }
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    print(f"Shared OAuth client: {payload['shared_client_file']}")
    for snapshot in profiles:
        state = "connected" if snapshot["connected"] else "not connected"
        channel = f" - {snapshot['channel_title']}" if snapshot["channel_title"] else ""
        print(f"{snapshot['profile']}: {state}{channel}")
    return 0


def logout_main() -> int:
    parser = argparse.ArgumentParser(description="Disconnect one YouTube account profile.")
    _add_profile_argument(parser)
    parser.add_argument("--forget-client", action="store_true",
                        help="Delete this profile's override client; on default, delete the shared client.")
    args = parser.parse_args()
    profile = args.profile

    token = store.read_json(store.token_path(profile))
    if token.get("token") or token.get("refresh_token"):
        try:
            import requests

            requests.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": token.get("refresh_token") or token.get("token")},
                timeout=15,
            )
        except Exception:  # local deletion is authoritative
            pass

    removed = False
    for path in (store.token_path(profile), store.channel_cache_path(profile)):
        if path.exists():
            path.unlink()
            removed = True
    client_path = store.client_secret_path(profile)
    if args.forget_client and client_path.exists():
        client_path.unlink()
        print(f"Deleted the OAuth client for profile '{profile}' too.")

    directory = store.profile_dir(profile)
    if profile != store.DEFAULT_PROFILE:
        try:
            directory.rmdir()
        except OSError:
            pass
    message = "Disconnected." if removed else "Nothing to disconnect."
    print(f"Profile '{profile}': {message}")
    return 0
