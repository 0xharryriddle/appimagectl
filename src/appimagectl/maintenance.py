"""Maintenance operations: data cleanup, trash management, integrity verify.

Every destructive step obeys the project rule: nothing is hard-deleted while it
could be restored. `clean` MOVES app data into our trash dir; `trash empty` is
the only place real deletion happens, and even it requires --yes.

Data-dir discovery deliberately uses name variants (exact Name=, lowercased,
app_id) so both `~/.config/ZCode` and `~/.zcode` style apps are found, but only
under the user-writable XDG dirs - never system paths.
"""

from __future__ import annotations

import contextlib
import shutil
from pathlib import Path

from . import desktop as dsk
from .appimage import read_magic
from .core import OperationError, human_size
from .manifest import Manifest
from .paths import (
    HOME,
    TRASH_DIR,
    XDG_CACHE_HOME,
    XDG_CONFIG_HOME,
    XDG_DATA_HOME,
    XDG_STATE_HOME,
)

# Where clean() parks moved app data. Kept separate from binary trash so
# `trash list` never mixes 180 MB binaries with user data.
DATA_TRASH = TRASH_DIR / "data"

CANDIDATE_KINDS = ()  # kinds are derived from the dir tree, not this constant


def _name_variants(manifest: Manifest) -> set[str]:
    out = {manifest.app_id.replace("-", "").replace("_", "")}
    if manifest.name:
        out.add(manifest.name)
        out.add(manifest.name.lower())
    out.add(manifest.app_id)
    return {v for v in out if v}


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            with contextlib.suppress(OSError):
                total += p.stat().st_size
    return total


def data_candidates(app_id: str) -> list[dict]:
    """Enumerate plausible user-data locations for an app. Read-only.

    Returns entries with kind/path/exists/size so callers can show a plan and
    let the user (or --yes) decide. Nothing here is ever touched.
    """
    manifest = Manifest.load(app_id)
    if manifest is None:
        raise OperationError(f"unknown app_id '{app_id}'")

    roots = (
        (XDG_CONFIG_HOME, "config"),
        (XDG_CACHE_HOME, "cache"),
        (XDG_DATA_HOME, "data"),
        (XDG_STATE_HOME, "state"),
    )
    seen: set[Path] = set()
    candidates: list[dict] = []
    for variant in sorted(_name_variants(manifest), key=len, reverse=True):
        for root, kind in roots:
            p = root / variant
            if p in seen:
                continue
            seen.add(p)
            if p.exists():
                candidates.append(
                    {
                        "kind": kind,
                        "path": str(p),
                        "exists": True,
                        "size_bytes": _dir_size(p),
                        "size_human": human_size(_dir_size(p)),
                    }
                )
        d = HOME / f".{variant}"
        if d in seen or not d.exists():
            continue
        seen.add(d)
        candidates.append(
            {
                "kind": "dot",
                "path": str(d),
                "exists": True,
                "size_bytes": _dir_size(d),
                "size_human": human_size(_dir_size(d)),
            }
        )
    return candidates


def _running_pids(manifest: Manifest) -> list[int]:
    """Find processes whose command line contains the app binary's basename."""
    base = Path(manifest.appimage_path).stem.lower().split("-")[0]
    pids: list[int] = []
    try:
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                cmd = (proc / "cmdline").read_bytes().split(b"\x00")
            except OSError:
                continue
            text = " ".join(c.decode(errors="ignore") for c in cmd).lower()
            if base in text and "appimagectl" not in text:
                pids.append(int(proc.name))
    except OSError:
        pass
    return pids


def op_clean(app_id: str, *, yes: bool = False, dry_run: bool = False) -> dict:
    """Move an app's user data (config/cache/data/state/dotdirs) to our trash.

    Refuses without --yes (uninstall also refuses without confirmation). Data
    is MOVED, never unlinked, so a mistaken clean is recoverable via
    `trash list` + manual restore.
    """
    manifest = Manifest.load(app_id)
    if manifest is None:
        raise OperationError(f"unknown app_id '{app_id}'")

    candidates = data_candidates(app_id)
    plan = [
        {
            "kind": c["kind"],
            "path": c["path"],
            "size_bytes": c["size_bytes"],
            "size_human": c["size_human"],
        }
        for c in candidates
    ]
    if not plan:
        return {
            "ok": True,
            "action": "clean",
            "app_id": app_id,
            "found": 0,
            "plan": [],
            "note": "no user data found",
        }

    pid_list = _running_pids(manifest)
    if dry_run or not yes:
        return {
            "ok": True,
            "action": "clean",
            "app_id": app_id,
            "dry_run": not yes or dry_run,
            "found": len(plan),
            "plan": plan,
            "running_pids": pid_list,
            "note": (
                "add --yes to move these to trash"
                if not yes
                else "dry run; nothing moved"
            ),
        }

    moved: list[dict] = []
    for entry in plan:
        src = Path(entry["path"])
        dest_dir = DATA_TRASH / app_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        if dest.exists():
            dest = dest_dir / f"{src.name}-{len(list(dest_dir.iterdir()))}"
        shutil.move(str(src), str(dest))
        moved.append(
            {
                "from": str(src),
                "to": str(dest),
                "kind": entry["kind"],
                "size_human": entry["size_human"],
            }
        )

    return {
        "ok": True,
        "action": "clean",
        "app_id": app_id,
        "found": len(plan),
        "moved_to_trash": moved,
        "running_pids": pid_list,
        "note": (
            f"WARNING: {len(pid_list)} process(es) may still be running; the app "
            "should be closed before cleaning" if pid_list else None
        ),
    }


def op_verify(app_id: str) -> dict:
    """Full integrity check of one installed app: binary hash vs manifest,
    launcher marker, icons, shell registration."""
    manifest = Manifest.load(app_id)
    if manifest is None:
        raise OperationError(f"unknown app_id '{app_id}'")

    from .appimage import sha256_file

    checks: list[dict] = []
    ok = True

    binary = Path(manifest.appimage_path)
    if binary.is_file():
        actual = sha256_file(binary)
        match = actual == manifest.sha256
        ok &= match
        checks.append({"check": "binary_sha256", "ok": match})
    else:
        ok = False
        checks.append({"check": "binary_sha256", "ok": False, "missing": True})

    desktop = Path(manifest.desktop_file)
    managed = dsk.is_managed(desktop)
    ok &= managed
    checks.append({"check": "desktop_managed", "ok": managed})

    icons = [Path(p) for p in manifest.icon_files]
    missing_icons = [str(p) for p in icons if not p.exists()]
    ok &= not missing_icons
    checks.append(
        {
            "check": "icons",
            "ok": not missing_icons,
            "missing": missing_icons if missing_icons else None,
        }
    )

    registered = dsk.registered_in_shell(app_id)
    if registered is False:
        ok = False
    checks.append(
        {
            "check": "shell_registration",
            # None means "cannot determine" (e.g. no PyGObject): not a failure,
            # and the renderer can show it as unknown.
            "ok": True if registered is None else registered,
        }
    )

    return {
        "ok": ok,
        "action": "verify",
        "app_id": app_id,
        "checks": checks,
        "missing": [p for p in manifest.missing_files()],
    }


def op_scan(dirs: list[str] | None = None) -> dict:
    """Find AppImage files under the given directories (recursive, sizes only).

    Defaults to the *small, likely* locations so nothing outside ~/Applications
    is silently missed without paying for a full-home walk: Downloads, Desktop,
    ~/.local/bin, and the store itself (including subdirectories). Pass a
    directory explicitly (e.g. `appimagectl scan ~`) for a whole-tree sweep."""
    roots = dirs or [
        str(Path.home() / "Downloads"),
        str(Path.home() / "Desktop"),
        str(Path.home() / ".local" / "bin"),
    ]
    found: list[dict] = []
    skipped: list[str] = []
    for raw in roots:
        d = Path(raw).expanduser()
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*.AppImage")) + sorted(d.rglob("*.appimage")):
            if not p.is_file():
                continue
            try:
                read_magic(p)
            except Exception:
                skipped.append(str(p))
                continue
            found.append(
                {
                    "path": str(p),
                    "name": p.name,
                    "size_bytes": p.stat().st_size,
                    "size_human": human_size(p.stat().st_size),
                }
            )
    return {"ok": True, "action": "scan", "found": found, "skipped": skipped}


def op_trash_list() -> dict:
    binaries = []
    data = []
    if TRASH_DIR.is_dir():
        for p in sorted(TRASH_DIR.iterdir()):
            if p.is_file() and p.suffix == ".AppImage":
                binaries.append(
                    {"name": p.name, "path": str(p), "size_human": human_size(p.stat().st_size)}
                )
        data_dir = TRASH_DIR / "data"
        if data_dir.is_dir():
            for app in sorted(data_dir.iterdir()):
                if app.is_dir():
                    size = sum(
                        s.stat().st_size
                        for s in app.rglob("*")
                        if s.is_file()
                    )
                    data.append(
                        {
                            "app_id": app.name,
                            "path": str(app),
                            "size_human": human_size(size),
                        }
                    )
    return {
        "ok": True,
        "action": "trash-list",
        "binaries": binaries,
        "data_dirs": data,
        "binaries_total": len(binaries),
        "data_total": len(data),
    }


def op_trash_restore(name: str) -> dict:
    """Move a trashed binary back to the store. Refuses on name collision so a
    restore can never silently overwrite a newer install."""
    from .paths import STORE_DIR

    src = TRASH_DIR / name
    if not src.is_file():
        raise OperationError(f"no trashed binary named '{name}'")
    dest = STORE_DIR / name
    if dest.exists():
        raise OperationError(
            f"{dest} already exists; refusing to overwrite (uninstall it first)"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    dest.chmod(dest.stat().st_mode | 0o111)
    return {
        "ok": True,
        "action": "trash-restore",
        "name": name,
        "restored_to": str(dest),
        "size_human": human_size(dest.stat().st_size),
    }


def op_trash_empty(yes: bool = False) -> dict:
    """Permanently delete trashed binaries. The one operation that unlinks
    data, so it requires --yes and lists exactly what it will remove."""
    if not yes:
        listing = op_trash_list()
        return {
            "ok": True,
            "action": "trash-empty",
            "dry_run": True,
            "binaries": listing["binaries"],
            "data_dirs": listing["data_dirs"],
            "note": "add --yes to permanently delete",
        }
    removed: list[str] = []
    if TRASH_DIR.is_dir():
        for p in sorted(TRASH_DIR.iterdir()):
            if p.is_file() or p.is_dir():
                shutil.rmtree(p) if p.is_dir() else p.unlink()
                removed.append(str(p))
    return {
        "ok": True,
        "action": "trash-empty",
        "permanently_deleted": removed,
    }