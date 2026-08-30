"""Magic-byte detection, hashing, and .desktop parsing."""

from __future__ import annotations

import hashlib

import pytest

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
