"""Shared fixtures.

The important one is `fake_appimage`: a real ELF file with AppImage magic whose
payload we can control. Tests that only need magic/metadata parsing use it
directly; tests that need extraction are marked and skipped unless a real
AppImage is available, because only the genuine runtime can self-extract.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect every XDG path into tmp_path, then reimport the path-consuming
    modules so they pick it up. Nothing in these tests may touch the real
    ~/.local/share.
    """
    home = tmp_path / "home"
    (home / ".local" / "share").mkdir(parents=True)
    (home / ".config").mkdir(parents=True)
    store = home / "Applications"
    store.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    monkeypatch.setenv("APPIMAGECTL_STORE", str(store))

    import importlib

    from appimagectl import cli, core, desktop, manifest, paths

    for mod in (paths, manifest, desktop, core, cli):
        importlib.reload(mod)

    # GLib caches the user data dir at first Gio call per process; with each
    # test redirecting XDG_HOME to a new tmp dir, `registered_in_shell` would
    # compare against the FIRST sandbox's dir and report False forever after.
    # Under test we treat shell registration as unknown (None) - it is a
    # desktop-shell concern, not a filesystem fact.
    monkeypatch.setattr(desktop, "registered_in_shell", lambda app_id: None)

    return {
        "home": home,
        "store": store,
        "paths": paths,
        "core": core,
        "desktop": desktop,
        "manifest": manifest,
    }


def _minimal_elf_with_magic(appimage_type: int = 2) -> bytes:
    """Build a 64-byte ELF header carrying AppImage magic at offset 8.

    Byte 8 onward is the ELF ident padding, which is exactly where the AppImage
    spec puts 'AI\\x02'. This is enough for magic detection without needing a
    real 180 MB runtime.
    """
    header = bytearray(64)
    header[0:4] = b"\x7fELF"
    header[4] = 2  # 64-bit
    header[5] = 1  # little endian
    header[6] = 1  # ELF version
    header[7] = 0  # System V ABI
    header[8:11] = b"AI" + bytes([appimage_type])
    struct.pack_into("<H", header, 16, 2)  # e_type = ET_EXEC
    struct.pack_into("<H", header, 18, 0x3E)  # e_machine = x86-64
    return bytes(header)


@pytest.fixture
def fake_appimage(tmp_path):
    """A file that passes magic detection but cannot self-extract."""
    p = tmp_path / "FakeApp-1.2.3-x86_64.AppImage"
    p.write_bytes(_minimal_elf_with_magic(2) + b"\x00" * 4096)
    p.chmod(0o755)
    return p


@pytest.fixture
def not_an_appimage(tmp_path):
    p = tmp_path / "notanapp.AppImage"
    p.write_bytes(b"#!/bin/sh\necho hello\n")
    p.chmod(0o755)
    return p


def _find_real_appimage() -> Path | None:
    """A genuine AppImage on this machine, for extraction-dependent tests."""
    if env := os.environ.get("APPIMAGECTL_TEST_APPIMAGE"):
        p = Path(env)
        return p if p.is_file() else None
    for d in (Path.home() / "Applications", Path.home() / "Downloads"):
        if d.is_dir():
            for p in sorted(d.glob("*.AppImage")):
                return p
    return None


@pytest.fixture
def real_appimage():
    p = _find_real_appimage()
    if p is None:
        pytest.skip("no real AppImage available for extraction test")
    return p
