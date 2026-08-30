"""XDG paths and constants. Every path is resolved once, here."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "appimagectl"

# Marker keys written into managed .desktop files. Uninstall refuses to touch
# any .desktop file that does not carry MANAGED_KEY, so we can never delete a
# launcher created by a distro package or by the user.
MANAGED_KEY = "X-AppImageCtl-Managed"
MANAGED_VALUE = "true"
APP_ID_KEY = "X-AppImageCtl-Id"
SHA_KEY = "X-AppImageCtl-Sha256"
VERSION_KEY = "X-AppImageCtl-Version"


def _env_path(var: str, default: Path) -> Path:
    raw = os.environ.get(var)
    return Path(raw).expanduser() if raw else default


HOME = Path.home()
XDG_DATA_HOME = _env_path("XDG_DATA_HOME", HOME / ".local" / "share")
XDG_CONFIG_HOME = _env_path("XDG_CONFIG_HOME", HOME / ".config")
XDG_CACHE_HOME = _env_path("XDG_CACHE_HOME", HOME / ".cache")
XDG_STATE_HOME = _env_path("XDG_STATE_HOME", HOME / ".local" / "state")

APPLICATIONS_DIR = XDG_DATA_HOME / "applications"
ICONS_DIR = XDG_DATA_HOME / "icons" / "hicolor"
MIMEAPPS = XDG_CONFIG_HOME / "mimeapps.list"

# Where integrated AppImage binaries live.
STORE_DIR = _env_path("APPIMAGECTL_STORE", HOME / "Applications")

# Our own state: one manifest per installed app, listing every file we created.
STATE_DIR = XDG_DATA_HOME / APP_NAME
MANIFEST_DIR = STATE_DIR / "apps"
TRASH_DIR = STATE_DIR / "trash"

ICON_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512, 1024)


def ensure_dirs() -> None:
    for d in (APPLICATIONS_DIR, ICONS_DIR, STORE_DIR, MANIFEST_DIR):
        d.mkdir(parents=True, exist_ok=True)
