"""YouTube integration: storage snapshot, upload building blocks, CLI exit
codes, and MCP arg mapping — all offline (no Google account needed)."""

import json
import subprocess
import sys

import pytest

from mangaeasy.mcp_server import _build_args
from mangaeasy.youtube import store
from mangaeasy.youtube.upload import build_metadata, content_range, friendly_api_error, parse_tags


def run_cli(env_home, *args: str) -> subprocess.CompletedProcess:
    import os

    env = os.environ.copy()
    env["MANGAEASY_HOME"] = str(env_home)
    return subprocess.run(
        [sys.executable, "-m", "mangaeasy.cli", *args],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=120,
    )


def test_status_snapshot_disconnected(tmp_path, monkeypatch):
    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path))
    snapshot = store.status_snapshot()
    assert snapshot["connected"] is False
    assert snapshot["client_secrets_present"] is False
    assert snapshot["channel_title"] is None
    assert snapshot["profile"] == "default"


def test_status_snapshot_connected(tmp_path, monkeypatch):
    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path))
    store.write_json(store.token_path(), {"refresh_token": "r", "scopes": store.SCOPES})
    store.write_json(store.channel_cache_path(), {"id": "UC123", "title": "My Channel"})
    snapshot = store.status_snapshot()
    assert snapshot["connected"] is True
    assert snapshot["channel_title"] == "My Channel"
    assert snapshot["scopes"] == store.SCOPES


def test_status_json_cli(tmp_path):
    proc = run_cli(tmp_path, "youtube-status", "--json")
    assert proc.returncode == 0
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["connected"] is False


def test_upload_without_auth_fails_actionably(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    proc = run_cli(tmp_path, "youtube-upload", "--video", str(video), "--title", "t")
    assert proc.returncode == 1
    assert "youtube-auth" in proc.stderr


def test_auth_without_client_secrets_fails_actionably(tmp_path):
    proc = run_cli(tmp_path, "youtube-auth")
    assert proc.returncode == 1
    assert "client_secret.json" in proc.stderr


def test_logout_is_safe_when_disconnected(tmp_path):
    proc = run_cli(tmp_path, "youtube-logout")
    assert proc.returncode == 0
    assert "Nothing to disconnect" in proc.stdout


def test_build_metadata_shape():
    meta = build_metadata("Title", "Desc", ["a", "b"], "24", "unlisted", False)
    assert meta == {
        "snippet": {"title": "Title", "description": "Desc", "categoryId": "24", "tags": ["a", "b"]},
        "status": {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False},
    }
    assert "tags" not in build_metadata("T", "", [], "1", "private", False)["snippet"]


def test_parse_tags():
    assert parse_tags("manga, recap ,, x") == ["manga", "recap", "x"]
    assert parse_tags("") == []


def test_content_range():
    assert content_range(0, 100, 1000) == "bytes 0-99/1000"
    assert content_range(900, 100, 1000) == "bytes 900-999/1000"


def test_friendly_api_error_quota():
    body = json.dumps({"error": {"message": "Quota exceeded",
                                 "errors": [{"reason": "quotaExceeded"}]}})
    text = friendly_api_error(403, body)
    assert "quotaExceeded" in text
    assert "1,600" in text


def test_friendly_api_error_non_json():
    assert "YouTube API error 500" in friendly_api_error(500, "<html>oops</html>")


def test_mcp_upload_args():
    args = _build_args("youtube_upload", {
        "video": "/v.mp4", "title": "T", "tags": "a,b", "privacy": "unlisted",
    })
    assert args == ["--video", "/v.mp4", "--title", "T", "--tags", "a,b",
                    "--privacy", "unlisted", "--json"]
    with pytest.raises(ValueError):
        _build_args("youtube_upload", {"video": "/v.mp4"})  # title missing


def test_mcp_status_args():
    assert _build_args("youtube_status", {}) == ["--json"]
    assert _build_args("youtube_status", {"verify": True}) == ["--verify", "--json"]


def test_named_profile_paths_are_isolated_and_default_is_legacy_compatible(tmp_path, monkeypatch):
    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path))

    assert store.profile_dir() == tmp_path / "youtube"
    assert store.token_path() == tmp_path / "youtube" / "token.json"
    assert store.client_secret_path() == tmp_path / "youtube" / "client_secret.json"
    assert store.channel_cache_path() == tmp_path / "youtube" / "channel.json"

    manga = tmp_path / "youtube" / "profiles" / "manga"
    song = tmp_path / "youtube" / "profiles" / "song"
    assert store.token_path("manga") == manga / "token.json"
    assert store.client_secret_path("manga") == manga / "client_secret.json"
    assert store.channel_cache_path("manga") == manga / "channel.json"
    assert store.token_path("song") == song / "token.json"
    assert store.token_path("manga") != store.token_path("song")


def test_named_profile_prefers_override_then_falls_back_to_shared_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path))
    shared = store.shared_client_secret_path()
    store.write_json(shared, {"installed": {"client_id": "shared"}})

    assert store.effective_client_secret_path("manga") == shared
    fallback = store.status_snapshot("manga")
    assert fallback["client_secrets_present"] is True
    assert fallback["client_source"] == "shared"
    assert fallback["shared_client_file"] == str(shared)
    assert fallback["effective_client_file"] == str(shared)

    override = store.client_secret_path("manga")
    store.write_json(override, {"installed": {"client_id": "override"}})
    assert store.effective_client_secret_path("manga") == override
    specific = store.status_snapshot("manga")
    assert specific["client_source"] == "profile"
    assert specific["profile_client_present"] is True


@pytest.mark.parametrize("profile", ["../escape", "..", "a/b", r"a\b", ".hidden",
                                      "UPPER", "has space", "-start", "end-"])
def test_profile_traversal_and_unsafe_names_are_rejected(profile):
    with pytest.raises(ValueError):
        store.validate_profile(profile)


def test_cli_rejects_profile_traversal_before_file_access(tmp_path):
    proc = run_cli(tmp_path, "youtube-status", "--profile", "../escape", "--json")
    assert proc.returncode == 2
    assert "profile" in proc.stderr
    assert not (tmp_path.parent / "escape").exists()


def test_named_profile_directory_rejects_symlink_alias(tmp_path, monkeypatch):
    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path))
    profiles = tmp_path / "youtube" / "profiles"
    real = profiles / "real"
    real.mkdir(parents=True)
    alias = profiles / "alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are not available on this host")

    with pytest.raises(ValueError, match="symlink or reparse"):
        store.profile_dir("alias")
    assert "alias" not in store.profile_names()


def test_youtube_store_root_rejects_symlink_or_reparse_escape(tmp_path, monkeypatch):
    home = tmp_path / "home"
    outside = tmp_path / "outside"
    home.mkdir()
    outside.mkdir()
    root = home / "youtube"
    try:
        root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are not available on this host")
    monkeypatch.setenv("MANGAEASY_HOME", str(home))

    with pytest.raises(ValueError, match="credential store.*symlink or reparse"):
        store.profile_dir()
    with pytest.raises(ValueError, match="credential store.*symlink or reparse"):
        store.token_path("song")


def test_named_profiles_root_rejects_symlink_or_reparse_escape(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "youtube"
    outside = tmp_path / "outside"
    root.mkdir(parents=True)
    outside.mkdir()
    profiles = root / "profiles"
    try:
        profiles.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are not available on this host")
    monkeypatch.setenv("MANGAEASY_HOME", str(home))

    with pytest.raises(ValueError, match="profiles directory.*symlink or reparse"):
        store.profile_dir("song")
    with pytest.raises(ValueError, match="profiles directory.*symlink or reparse"):
        store.profile_names()


def test_credential_leaf_rejects_symlink_and_write_json_rejects_outside_path(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path / "home"))
    root = store.youtube_dir()
    root.mkdir(parents=True)
    outside = tmp_path / "outside-token.json"
    outside.write_text('{"refresh_token":"outside"}', encoding="utf-8")
    token = root / "token.json"
    try:
        token.symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks are not available on this host")

    with pytest.raises(ValueError, match="token.json.*symlink or reparse"):
        store.token_path()
    with pytest.raises(ValueError, match="outside the YouTube credential store"):
        store.write_json(tmp_path / "escaped.json", {"refresh_token": "secret"})


def test_profile_listing_is_machine_readable_and_does_not_leak_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path))
    store.write_json(store.token_path("manga"), {"refresh_token": "manga-secret"})
    store.write_json(store.channel_cache_path("manga"), {"id": "UCM", "title": "Manga Channel"})
    store.write_client_config(
        "123456789012-abcdefghijklmnop.apps.googleusercontent.com", "client-secret", "song"
    )

    proc = run_cli(tmp_path, "youtube-profiles", "--json")
    assert proc.returncode == 0
    assert "manga-secret" not in proc.stdout
    assert "client-secret" not in proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["shared_client_file"] == str(store.shared_client_secret_path())
    profiles = {item["profile"]: item for item in payload["profiles"]}
    assert list(profiles) == ["default", "manga", "song"]
    assert profiles["manga"]["connected"] is True
    assert profiles["manga"]["channel_title"] == "Manga Channel"
    assert profiles["song"]["client_secrets_present"] is True
    assert profiles["song"]["connected"] is False


def test_status_selects_named_profile_without_touching_default(tmp_path, monkeypatch):
    # Populate only a named account. The legacy default must remain disconnected.
    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path))
    store.write_json(store.token_path("ai-story"), {"refresh_token": "r"})
    store.write_json(store.channel_cache_path("ai-story"),
                     {"id": "UCAI", "title": "Story Channel"})

    named = run_cli(tmp_path, "youtube-status", "--profile", "ai-story", "--json")
    default = run_cli(tmp_path, "youtube-status", "--json")
    assert json.loads(named.stdout)["channel_id"] == "UCAI"
    assert json.loads(named.stdout)["profile"] == "ai-story"
    assert json.loads(default.stdout)["connected"] is False


@pytest.mark.parametrize("command", [
    "youtube-auth", "youtube-status", "youtube-logout", "youtube-upload",
    "youtube-list", "youtube-delete", "youtube-thumbnail",
])
def test_every_youtube_account_command_accepts_profile(tmp_path, command):
    proc = run_cli(tmp_path, command, "--help")
    assert proc.returncode == 0
    assert "--profile" in proc.stdout


@pytest.mark.parametrize("command", [
    "youtube-status", "youtube-upload", "youtube-list", "youtube-delete", "youtube-thumbnail",
])
def test_every_live_youtube_command_can_disable_auto_auth(tmp_path, command):
    proc = run_cli(tmp_path, command, "--help")
    assert proc.returncode == 0
    assert "--no-auto-auth" in proc.stdout


def test_mcp_profile_mappings_and_validation():
    assert _build_args("youtube_profiles", {}) == ["--json"]
    assert _build_args("youtube_status", {"profile": "manga", "verify": True}) == [
        "--profile", "manga", "--verify", "--json",
    ]
    assert _build_args("youtube_upload", {
        "profile": "song", "video": "/v.mp4", "title": "T",
    }) == ["--profile", "song", "--video", "/v.mp4", "--title", "T", "--json"]
    assert _build_args("youtube_list", {"profile": "ai-story", "limit": 5}) == [
        "--profile", "ai-story", "--limit", "5", "--json",
    ]
    assert _build_args("youtube_delete", {
        "profile": "manga", "video_id": "abc123", "confirm": True,
    }) == ["--profile", "manga", "--video-id", "abc123", "--confirm", "--json"]
    assert _build_args("youtube_thumbnail", {
        "profile": "song", "video_id": "abc123", "image": "/thumb.png",
    }) == ["--profile", "song", "--video-id", "abc123", "--image", "/thumb.png", "--json"]
    assert _build_args("youtube_status", {
        "profile": "song", "auto_auth": False, "verify": True,
    }) == ["--profile", "song", "--no-auto-auth", "--verify", "--json"]
    assert _build_args("youtube_upload", {
        "profile": "song", "auto_auth": False, "video": "/v.mp4", "title": "T",
    }) == ["--profile", "song", "--no-auto-auth", "--video", "/v.mp4",
           "--title", "T", "--json"]
    assert "--no-auto-auth" in _build_args(
        "youtube_list", {"profile": "song", "auto_auth": False}
    )
    assert "--no-auto-auth" in _build_args(
        "youtube_delete", {"profile": "song", "auto_auth": False, "video_id": "abc123"}
    )
    assert "--no-auto-auth" in _build_args(
        "youtube_thumbnail",
        {"profile": "song", "auto_auth": False, "video_id": "abc123", "image": "/t.png"},
    )
    with pytest.raises(ValueError, match="exactly one"):
        _build_args("youtube_delete", {"profile": "manga", "confirm": True})
    with pytest.raises(ValueError, match="safe format"):
        _build_args("youtube_status", {"profile": "../escape"})


def test_upload_json_identifies_selected_profile_and_channel(tmp_path, monkeypatch, capsys):
    from mangaeasy.youtube import auth, upload

    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(auth, "load_credentials", lambda profile: object())
    monkeypatch.setattr(auth, "_fetch_channel",
                        lambda _creds: {"id": "UCSONG", "title": "Song Channel"})
    monkeypatch.setattr(upload, "_session", lambda _creds: object())
    monkeypatch.setattr(upload, "_start_session", lambda *_args: "upload-url")
    monkeypatch.setattr(upload, "_upload_file", lambda *_args: {
        "id": "video123", "status": {"privacyStatus": "private"},
    })
    monkeypatch.setattr(sys, "argv", [
        "mediaconductor youtube-upload", "--profile", "song", "--video", str(video),
        "--title", "Title", "--json",
    ])

    assert upload.main() == 0
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert report["profile"] == "song"
    assert report["channel_title"] == "Song Channel"
    assert report["channel_id"] == "UCSONG"


def test_upload_thumbnail_reauthorizes_and_retries_once_on_401(
    tmp_path, monkeypatch, capsys,
):
    from mangaeasy.youtube import auth, upload

    video = tmp_path / "video.mp4"
    thumbnail = tmp_path / "thumbnail.png"
    video.write_bytes(b"video")
    thumbnail.write_bytes(b"image")
    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path / "home"))
    initial_credentials = object()
    replacement_credentials = object()
    browser_calls = []
    sessions = []
    thumbnail_calls = []

    monkeypatch.setattr(auth, "load_credentials", lambda _profile: initial_credentials)
    monkeypatch.setattr(
        auth,
        "authorize_profile",
        lambda profile: browser_calls.append(profile) or replacement_credentials,
    )

    def fake_session(credentials):
        sessions.append(credentials)
        return credentials

    def fake_thumbnail(session, _video_id, _thumbnail):
        thumbnail_calls.append(session)
        if len(thumbnail_calls) == 1:
            raise upload.YouTubeAPIError(
                401,
                json.dumps({"error": {"errors": [{"reason": "authError"}]}}),
            )

    monkeypatch.setattr(upload, "_session", fake_session)
    monkeypatch.setattr(upload, "_start_session", lambda *_args: "upload-url")
    monkeypatch.setattr(upload, "_upload_file", lambda *_args: {
        "id": "video123", "status": {"privacyStatus": "private"},
    })
    monkeypatch.setattr(upload, "_set_thumbnail", fake_thumbnail)
    monkeypatch.setattr(sys, "argv", [
        "mediaconductor youtube-upload", "--profile", "manga",
        "--video", str(video), "--title", "Title",
        "--thumbnail", str(thumbnail), "--skip-verify", "--json",
    ])

    assert upload.main() == 0
    assert browser_calls == ["manga"]
    assert sessions == [initial_credentials, replacement_credentials]
    assert thumbnail_calls == [initial_credentials, replacement_credentials]
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert report["ok"] is True
    assert report["profile"] == "manga"


def test_live_verify_drops_stale_channel_identity(tmp_path, monkeypatch):
    from mangaeasy.youtube import auth

    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path))
    store.write_json(store.token_path("song"), {"refresh_token": "r"})
    store.write_json(store.channel_cache_path("song"), {"id": "OLD", "title": "Old Channel"})
    monkeypatch.setattr(auth, "load_credentials", lambda _profile: object())
    monkeypatch.setattr(auth, "_fetch_channel", lambda _creds: {})

    result = auth._verify_live(store.status_snapshot("song"))
    assert result["verified"] is False
    assert result["channel_id"] is None
    assert not store.channel_cache_path("song").exists()


def test_ensure_credentials_auto_authorizes_missing_and_failed_refresh(tmp_path, monkeypatch):
    from mangaeasy.youtube import auth

    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path))
    sentinel = object()
    calls = []
    monkeypatch.setattr(auth, "authorize_profile",
                        lambda profile: calls.append(profile) or sentinel)
    monkeypatch.setattr(auth, "load_credentials", lambda _profile: None)
    assert auth.ensure_credentials("manga") is sentinel

    def failed_refresh(_profile):
        raise RuntimeError("refresh-token-must-not-be-printed")

    monkeypatch.setattr(auth, "load_credentials", failed_refresh)
    assert auth.ensure_credentials("song") is sentinel
    assert calls == ["manga", "song"]


def test_no_auto_auth_never_calls_browser_helper(tmp_path, monkeypatch):
    from mangaeasy.youtube import auth

    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path))
    monkeypatch.setattr(auth, "load_credentials", lambda _profile: None)
    monkeypatch.setattr(
        auth, "authorize_profile",
        lambda _profile: pytest.fail("browser helper must not run"),
    )
    store.write_json(store.channel_cache_path("song"), {"id": "STALE"})
    assert auth.ensure_credentials("song", auto_auth=False) is None
    assert not store.channel_cache_path("song").exists()


def test_authorize_profile_reuses_shared_client_but_writes_isolated_tokens(
        tmp_path, monkeypatch, capsys):
    from google_auth_oauthlib.flow import InstalledAppFlow
    from mangaeasy.youtube import auth

    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path))
    shared = store.shared_client_secret_path()
    store.write_json(shared, {"installed": {"client_id": "shared-client"}})
    opened_with = []
    counter = iter(("manga-refresh", "song-refresh"))

    class FakeCredentials:
        def __init__(self, refresh_token):
            self.refresh_token = refresh_token

        def to_json(self):
            return json.dumps({"token": "access", "refresh_token": self.refresh_token})

    class FakeFlow:
        def run_local_server(self, **_kwargs):
            print("FAKE_CONSENT_PROGRESS")
            return FakeCredentials(next(counter))

    def fake_from_file(path, _scopes):
        opened_with.append(path)
        return FakeFlow()

    monkeypatch.setattr(InstalledAppFlow, "from_client_secrets_file", staticmethod(fake_from_file))
    monkeypatch.setattr(
        auth, "_fetch_channel",
        lambda creds: {"id": creds.refresh_token, "title": creds.refresh_token},
    )

    auth.authorize_profile("manga")
    auth.authorize_profile("song")

    assert opened_with == [str(shared), str(shared)]
    assert store.read_json(store.token_path("manga"))["refresh_token"] == "manga-refresh"
    assert store.read_json(store.token_path("song"))["refresh_token"] == "song-refresh"
    assert store.token_path("manga") != store.token_path("song")
    captured = capsys.readouterr()
    assert "FAKE_CONSENT_PROGRESS" not in captured.out
    assert "FAKE_CONSENT_PROGRESS" in captured.err


def test_api_auth_rejection_triggers_one_browser_reauthorization(tmp_path, monkeypatch):
    from mangaeasy.youtube import auth

    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path))
    class Unauthorized:
        status_code = 401

    sentinel = object()
    monkeypatch.setattr(auth, "authorize_profile", lambda _profile: sentinel)
    assert auth.reauthorize_after_api_error(
        "manga", Unauthorized(), auto_auth=True
    ) is sentinel
    assert auth.reauthorize_after_api_error(
        "manga", Unauthorized(), auto_auth=False
    ) is None

    class AuthForbidden:
        status_code = 403
        reason = "authError"

    assert auth.reauthorize_after_api_error(
        "song", AuthForbidden(), auto_auth=True
    ) is sentinel


def test_live_verify_continues_after_automatic_authorization(tmp_path, monkeypatch):
    from mangaeasy.youtube import auth

    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path))
    store.write_json(store.shared_client_secret_path(), {"installed": {"client_id": "shared"}})
    credentials = object()
    monkeypatch.setattr(auth, "ensure_credentials", lambda _profile, auto_auth: credentials)
    monkeypatch.setattr(auth, "_fetch_channel",
                        lambda _creds: {"id": "UCNEW", "title": "New Channel"})

    report = auth._verify_live(store.status_snapshot("ai-story"), auto_auth=True)
    assert report["verified"] is True
    assert report["channel_id"] == "UCNEW"


def test_write_client_config_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("MANGAEASY_HOME", str(tmp_path))
    store.write_client_config("abc.apps.googleusercontent.com", "GOCSPX-xyz")
    data = store.read_json(store.client_secret_path())
    installed = data["installed"]
    assert installed["client_id"] == "abc.apps.googleusercontent.com"
    assert installed["client_secret"] == "GOCSPX-xyz"
    assert installed["token_uri"] == "https://oauth2.googleapis.com/token"


def test_looks_like_client_id():
    assert store.looks_like_client_id(
        "123456789012-abcdefghijklmnop.apps.googleusercontent.com"
    )
    assert not store.looks_like_client_id("not-a-client-id")
    assert not store.looks_like_client_id("x.apps.googleusercontent.com")  # too short


def test_auth_paste_requires_both_values(tmp_path):
    proc = run_cli(tmp_path, "youtube-auth", "--client-id", "only-half")
    assert proc.returncode == 1
    assert "together" in proc.stderr


def test_auth_paste_rejects_bad_client_id(tmp_path):
    proc = run_cli(tmp_path, "youtube-auth", "--client-id", "garbage", "--client-secret", "s")
    assert proc.returncode == 1
    assert "apps.googleusercontent.com" in proc.stderr


def test_auth_rejects_file_and_paste_together(tmp_path):
    secrets = tmp_path / "cs.json"
    secrets.write_text("{}", encoding="utf-8")
    proc = run_cli(
        tmp_path, "youtube-auth", "--client-secrets", str(secrets),
        "--client-id", "123456789012-abcdefghijklmnop.apps.googleusercontent.com",
        "--client-secret", "s",
    )
    assert proc.returncode == 1
    assert "not both" in proc.stderr


def test_status_verify_when_disconnected(tmp_path):
    proc = run_cli(tmp_path, "youtube-status", "--verify", "--json")
    assert proc.returncode == 0
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["verified"] is False
    assert "shared path" in data["verify_error"]


def test_status_no_auto_auth_is_offline_safe(tmp_path):
    proc = run_cli(tmp_path, "youtube-status", "--profile", "song", "--verify",
                   "--no-auto-auth", "--json")
    assert proc.returncode == 0
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["verified"] is False
    assert "automatic authorization is disabled" in data["verify_error"]
