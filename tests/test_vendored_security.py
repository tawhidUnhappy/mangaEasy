from __future__ import annotations

import hashlib
import io
import zipfile

import pytest

from mangaeasy.tools import vendored


class _Response:
    def __init__(self, payload: bytes, url: str = "https://example.invalid/asset"):
        self._stream = io.BytesIO(payload)
        self._url = url
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def geturl(self) -> str:
        return self._url


def test_download_is_https_hash_checked_and_atomic(tmp_path, monkeypatch):
    payload = b"verified archive"
    monkeypatch.setattr(
        vendored.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(payload),
    )
    destination = tmp_path / "tool.zip"

    vendored._download(
        "https://example.invalid/tool.zip",
        destination,
        lambda _message: None,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )

    assert destination.read_bytes() == payload
    assert not (tmp_path / ".tool.zip.part").exists()


def test_download_hash_failure_preserves_existing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        vendored.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(b"tampered"),
    )
    destination = tmp_path / "tool.zip"
    destination.write_bytes(b"known-good")

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        vendored._download(
            "https://example.invalid/tool.zip",
            destination,
            lambda _message: None,
            expected_sha256="0" * 64,
        )

    assert destination.read_bytes() == b"known-good"
    assert not (tmp_path / ".tool.zip.part").exists()


def test_download_rejects_plain_http(tmp_path):
    with pytest.raises(RuntimeError, match="non-HTTPS"):
        vendored._download(
            "http://example.invalid/tool.zip",
            tmp_path / "tool.zip",
            lambda _message: None,
        )


def test_checksum_manifest_requires_exact_asset_and_digest(monkeypatch):
    digest = "a" * 64
    payload = f"{digest} *wanted.zip\n{'b' * 64} other.zip\n".encode()
    monkeypatch.setattr(
        vendored.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response(payload),
    )
    assert vendored._remote_checksum("https://example.invalid/checksums", "wanted.zip") == digest
    with pytest.raises(RuntimeError, match="does not contain"):
        vendored._remote_checksum("https://example.invalid/checksums", "missing.zip")


def test_single_member_extraction_rejects_oversized_payload(tmp_path, monkeypatch):
    archive = tmp_path / "fixture.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("root/bin/tool", b"1234")
    monkeypatch.setattr(vendored, "MAX_MEMBER_BYTES", 3)

    with pytest.raises(RuntimeError, match="too large"):
        vendored._extract_member(archive, "bin/tool", tmp_path / "tool", lambda _message: None)


def test_bootstrap_asset_tables_are_versioned_and_hash_pinned():
    assert vendored.UV_VERSION == "0.11.16"
    assert vendored.GIT_LFS_VERSION == "3.7.1"
    for asset, digest in vendored.UV_ASSETS.values():
        assert "latest" not in asset
        assert len(digest) == 64 and int(digest, 16) >= 0
    for asset, _executable, digest in vendored.GIT_LFS_ASSETS.values():
        assert vendored.GIT_LFS_VERSION in asset
        assert len(digest) == 64 and int(digest, 16) >= 0


def test_unused_bulk_archive_extractor_is_not_exposed():
    assert not hasattr(vendored, "_extract_all")
