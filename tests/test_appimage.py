"""Magic-byte detection, hashing, and .desktop parsing."""

from __future__ import annotations

import hashlib

import pytest

from appimagectl import appimage
from appimagectl.appimage import (
    NotAnAppImage,
    _parse_desktop_text,
    read_magic,
    sha256_file,
)


def test_detects_type2_magic(fake_appimage):
    assert read_magic(fake_appimage) == 2


def test_rejects_non_elf(not_an_appimage):
    with pytest.raises(NotAnAppImage, match="not an ELF"):
        read_magic(not_an_appimage)


def test_rejects_elf_without_appimage_magic(tmp_path):
    p = tmp_path / "plain.AppImage"
    header = bytearray(64)
    header[0:4] = b"\x7fELF"
    p.write_bytes(bytes(header))
    with pytest.raises(NotAnAppImage, match="missing AppImage magic"):
        read_magic(p)


def test_rejects_truncated_file(tmp_path):
    p = tmp_path / "tiny.AppImage"
    p.write_bytes(b"\x7fELF")
    with pytest.raises(NotAnAppImage, match="too small"):
        read_magic(p)


def test_sha256_matches_hashlib(fake_appimage):
    expected = hashlib.sha256(fake_appimage.read_bytes()).hexdigest()
    assert sha256_file(fake_appimage) == expected


def test_parse_desktop_reads_desktop_entry_group_only():
    text = """
[Desktop Entry]
Name=Agent Orchestrator
Comment=Electron frontend
Exec=AppRun --no-sandbox %U
Icon=agent-orchestrator
Categories=Utility;
MimeType=x-scheme-handler/ao-app;
StartupWMClass=Agent Orchestrator
X-AppImage-Version=0.12.9

[Desktop Action New]
Name=Should Be Ignored
"""
    meta = _parse_desktop_text(text)
    assert meta.name == "Agent Orchestrator"
    assert meta.icon == "agent-orchestrator"
    assert meta.version == "0.12.9"
    assert meta.mime_types == ["x-scheme-handler/ao-app"]
    assert meta.startup_wm_class == "Agent Orchestrator"
    # The second group must not leak in.
    assert meta.raw["Name"] == "Agent Orchestrator"


def test_parse_desktop_ignores_comments_and_blanks():
    meta = _parse_desktop_text("# comment\n\n[Desktop Entry]\nName=X\n# another\n")
    assert meta.name == "X"
    assert meta.comment is None


def test_parse_desktop_drops_empty_mime_segments():
    meta = _parse_desktop_text("[Desktop Entry]\nMimeType=a/b;;c/d;\n")
    assert meta.mime_types == ["a/b", "c/d"]


def test_inspect_reads_internal_target_of_desktop_symlink(fake_appimage, monkeypatch, tmp_path):
    target_relative = "usr/share/applications/SwiftDL.desktop"

    def extract(_path, pattern, dest):
        root = dest / "squashfs-root"
        root.mkdir(parents=True, exist_ok=True)
        if pattern == "*.desktop":
            (root / "SwiftDL.desktop").symlink_to(target_relative)
            return True
        if pattern == target_relative:
            target = root / target_relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "[Desktop Entry]\nName=SwiftDL\nExec=swiftdl-app\n",
                encoding="utf-8",
            )
            return True
        return False

    monkeypatch.setattr(appimage, "_extract", extract)
    monkeypatch.setattr(appimage, "_run_appimage_flag", lambda *_args, **_kwargs: None)

    result = appimage.inspect(fake_appimage, compute_sha=False)

    assert result.desktop.name == "SwiftDL"
    assert result.desktop.exec_line == "swiftdl-app"


def test_inspect_ignores_desktop_symlink_escaping_appdir(fake_appimage, monkeypatch, tmp_path):
    outside = tmp_path / "outside.desktop"
    outside.write_text("[Desktop Entry]\nName=Outside payload\n", encoding="utf-8")

    def extract(_path, pattern, dest):
        if pattern != "*.desktop":
            return False
        root = dest / "squashfs-root"
        root.mkdir(parents=True, exist_ok=True)
        (root / "malicious.desktop").symlink_to(outside)
        return True

    monkeypatch.setattr(appimage, "_extract", extract)
    monkeypatch.setattr(appimage, "_run_appimage_flag", lambda *_args, **_kwargs: None)

    result = appimage.inspect(fake_appimage, compute_sha=False)

    assert result.desktop.name is None


def test_inspect_skips_dangling_desktop_symlink(fake_appimage, monkeypatch):
    def extract(_path, pattern, dest):
        if pattern != "*.desktop":
            return False
        root = dest / "squashfs-root"
        root.mkdir(parents=True, exist_ok=True)
        (root / "missing.desktop").symlink_to("usr/share/applications/missing.desktop")
        return True

    monkeypatch.setattr(appimage, "_extract", extract)
    monkeypatch.setattr(appimage, "_run_appimage_flag", lambda *_args, **_kwargs: None)

    result = appimage.inspect(fake_appimage, compute_sha=False)

    assert result.desktop.name is None
    assert result.appimage_type == 2
