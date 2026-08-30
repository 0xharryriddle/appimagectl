#!/usr/bin/env python3
"""Build a sanitized demo environment for README screenshots.

Creates a scratch XDG tree with two fictional-but-realistic managed apps
(rendered through the same desktop/manifest writers the tool uses in
production), so screenshots never leak the operator's real home paths, app
inventory, timestamps, or versions.

Usage:
  python3 scripts/make_demo_env.py <dest>          # build env
  DISPLAY=:77 XDG_*_HOME/<dest> ... shot_gui.py    # screenshot with env set
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

DEST = Path(sys.argv[1])
# Set XDG paths BEFORE importing appimagectl modules: they resolve once at
# import time and must point at the demo tree, not the operator's real home.
os.environ["HOME"] = str(DEST / "home")
os.environ["XDG_DATA_HOME"] = str(DEST / "data")
os.environ["XDG_CONFIG_HOME"] = str(DEST / "config")
os.environ["XDG_CACHE_HOME"] = str(DEST / "cache")
os.environ["APPIMAGECTL_STORE"] = str(DEST / "Applications")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from appimagectl import desktop as dsk  # noqa: E402
from appimagectl.appimage import AppImageInfo, DesktopMeta  # noqa: E402
from appimagectl.manifest import SCHEMA_VERSION, Manifest, now_iso  # noqa: E402

DEMO_APPS = [
    {
        "app_id": "timeline",
        "name": "Timeline",
        "comment": "Time-tracking dashboard",
        "icon": "timeline",
        "categories": "Office;",
        "version": "3.14.2",
        "binary": "Timeline-3.14.2-x86_64.AppImage",
        "sha": "a" * 64,
        "wm": "Timeline",
        "mime": [],
    },
    {
        "app_id": "notesync",
        "name": "Notesync",
        "comment": "Offline-first markdown notes",
        "icon": "notesync",
        "categories": "Utility;",
        "version": "1.8.0",
        "binary": "notesync-1.8.0-x86_64.AppImage",
        "sha": "b" * 64,
        "wm": "notesync",
        "mime": ["x-scheme-handler/notesync"],
    },
]


def build(dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    data_home = dest / "data"
    config_home = dest / "config"
    store = dest / "Applications"
    (dest / "home").mkdir(parents=True, exist_ok=True)
    for d in (
        data_home / "applications",
        data_home / "icons" / "hicolor" / "256x256" / "apps",
        config_home,
        store,
    ):
        d.mkdir(parents=True, exist_ok=True)

    for app in DEMO_APPS:
        binary = store / app["binary"]
        binary.write_bytes(b"\x7fELF" + b"\x00" * 8192)  # placeholder payload
        binary.chmod(0o755)

        meta = DesktopMeta(
            name=app["name"],
            comment=app["comment"],
            icon=app["icon"],
            categories=app["categories"],
            mime_types=app["mime"],
            startup_wm_class=app["wm"],
            version=app["version"],
        )
        info = AppImageInfo(
            path=binary,
            size_bytes=binary.stat().st_size,
            appimage_type=2,
            sha256=app["sha"],
            desktop=meta,
        )
        text = dsk.render_desktop(info, app["app_id"], binary)
        desktop_file = dsk.write_desktop(text, app["app_id"])

        # a real PNG so the launcher row has an icon
        icon = data_home / "icons" / "hicolor" / "256x256" / "apps" / f"{app['icon']}.png"
        color = (220, 120, 90) if app["app_id"] == "timeline" else (90, 140, 220)
        icon.write_bytes(_solid_png(color))

        m = Manifest(
            schema_version=SCHEMA_VERSION,
            app_id=app["app_id"],
            name=app["name"],
            appimage_path=str(binary),
            sha256=app["sha"],
            version=app["version"],
            installed_at=now_iso(),
            desktop_file=str(desktop_file),
            icon_files=[str(icon)],
            mime_types=app["mime"],
            source_path="/tmp/demo-download/",
            update_information=None,
        )
        # write manifest directly to the demo data home (not the real state dir)
        manifest_path = data_home / "appimagectl" / "apps" / f"{app['app_id']}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(m.__dict__, indent=2) + "\n")

    print(f"demo env at {dest}")


def _solid_png(rgb):
    """A minimal valid PNG (1x1 solid color) built without PIL."""
    import struct
    import zlib

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00" + bytes(rgb)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


if __name__ == "__main__":
    build(Path(sys.argv[1]))