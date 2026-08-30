"""Desktop-entry and icon-cache integration.

Two rules govern this module:

1. We only ever write .desktop files that carry our managed marker.
2. We only ever delete .desktop files that carry our managed marker.

Rule 2 is what makes uninstall safe on a machine where launchers also come from
apt, flatpak, and the user's own hand.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .appimage import AppImageInfo
from .paths import (
    APP_ID_KEY,
    APPLICATIONS_DIR,
    ICONS_DIR,
    MANAGED_KEY,
    MANAGED_VALUE,
    SHA_KEY,
    VERSION_KEY,
)


def desktop_path_for(app_id: str) -> Path:
    return APPLICATIONS_DIR / f"{app_id}.desktop"


def is_managed(desktop_file: Path) -> bool:
    """True only when the file exists and declares our marker."""
    if not desktop_file.is_file():
        return False
    try:
        text = desktop_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return f"{MANAGED_KEY}={MANAGED_VALUE}" in text


def read_desktop_key(desktop_file: Path, key: str) -> str | None:
    if not desktop_file.is_file():
        return None
    for line in desktop_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


def render_desktop(
    info: AppImageInfo,
    app_id: str,
    target_binary: Path,
    *,
    extra_args: str = "--no-sandbox",
) -> str:
    """Build the .desktop text for an installed AppImage.

    The internal Exec= line reads `AppRun ...` which is only meaningful inside
    the mounted AppDir, so it is discarded and rebuilt against the absolute
    installed path.
    """
    name = info.desktop.name or info.path.stem
    comment = info.desktop.comment or f"{name} (AppImage)"
    categories = info.desktop.categories or "Utility;"
    if not categories.endswith(";"):
        categories += ";"

    exec_parts = [f'"{target_binary}"']
    if extra_args:
        exec_parts.extend(f'"{a}"' for a in extra_args.split())
    exec_parts.append("%U")
    exec_line = " ".join(exec_parts)

    lines = [
        "[Desktop Entry]",
        f"Name={name}",
        f"Comment={comment}",
        f"Exec={exec_line}",
        "Terminal=false",
        "Type=Application",
        f"Icon={app_id}",
        f"Categories={categories}",
    ]
    if info.desktop.mime_types:
        lines.append("MimeType=" + ";".join(info.desktop.mime_types) + ";")
    if info.desktop.startup_wm_class:
        lines.append(f"StartupWMClass={info.desktop.startup_wm_class}")
    # Provenance keys: these are what make uninstall safe and `list` honest.
    lines.append(f"{MANAGED_KEY}={MANAGED_VALUE}")
    lines.append(f"{APP_ID_KEY}={app_id}")
    if info.sha256:
        lines.append(f"{SHA_KEY}={info.sha256}")
    if info.desktop.version:
        lines.append(f"{VERSION_KEY}={info.desktop.version}")
    return "\n".join(lines) + "\n"


def install_icons(icons: dict[int, Path], app_id: str) -> list[Path]:
    """Copy extracted icons into the hicolor theme as <app_id>.png.

    Size 0 is the .DirIcon fallback; it goes to 256x256 because an unsized icon
    still has to live somewhere the theme spec will look.
    """
    written: list[Path] = []
    for size, src in sorted(icons.items()):
        eff = size or 256
        dest_dir = ICONS_DIR / f"{eff}x{eff}" / "apps"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{app_id}{src.suffix or '.png'}"
        shutil.copy2(src, dest)
        written.append(dest)
    return written


def write_desktop(text: str, app_id: str) -> Path:
    APPLICATIONS_DIR.mkdir(parents=True, exist_ok=True)
    target = desktop_path_for(app_id)
    tmp = target.with_suffix(".desktop.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(0o644)
    tmp.replace(target)
    return target


def validate_desktop(desktop_file: Path) -> tuple[bool, str]:
    """Run desktop-file-validate when available.

    Hints are not failures: a multi-category hint does not stop an app from
    launching, so only errors/warnings gate the install.
    """
    exe = shutil.which("desktop-file-validate")
    if not exe:
        return True, "desktop-file-validate not installed; skipped"
    proc = subprocess.run(
        [exe, str(desktop_file)], capture_output=True, text=True, check=False
    )
    out = (proc.stdout + proc.stderr).strip()
    if proc.returncode == 0 and not out:
        return True, "valid"
    hard = [ln for ln in out.splitlines() if ": hint:" not in ln]
    return (not hard and proc.returncode == 0), out or "unknown validator output"


def refresh_caches() -> list[str]:
    """Update desktop and icon caches. Returns human-readable notes."""
    notes: list[str] = []
    if exe := shutil.which("update-desktop-database"):
        proc = subprocess.run(
            [exe, str(APPLICATIONS_DIR)], capture_output=True, text=True, check=False
        )
        notes.append(
            "update-desktop-database: ok"
            if proc.returncode == 0
            else f"update-desktop-database failed: {proc.stderr.strip()}"
        )
    if exe := shutil.which("gtk-update-icon-cache"):
        proc = subprocess.run(
            [exe, "-f", "-t", str(ICONS_DIR)],
            capture_output=True,
            text=True,
            check=False,
        )
        # A theme dir with no index.theme is a normal, harmless failure here.
        notes.append(
            "gtk-update-icon-cache: ok"
            if proc.returncode == 0
            else "gtk-update-icon-cache: skipped (no index.theme)"
        )
    return notes


def registered_in_shell(app_id: str) -> bool | None:
    """Ask GIO whether the desktop entry is visible to the shell.

    Returns None when PyGObject is unavailable - unknown, not false.
    """
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio  # type: ignore[attr-defined]
    except (ImportError, ValueError):
        return None
    wanted = f"{app_id}.desktop"
    return any(a.get_id() == wanted for a in Gio.AppInfo.get_all())
