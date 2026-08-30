"""Core operations. Every function returns a plain dict so the CLI can emit it
as JSON and the GUI can render it without a second code path.

Design rules:
- Nothing destructive happens without an explicit manifest or managed marker.
- Uninstall moves the AppImage to our trash dir; it never hard-deletes payload.
- Every operation supports dry_run and reports the exact planned file changes.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from . import desktop as dsk
from .appimage import (
    AppImageInfo,
    NotAnAppImage,
    extract_icons,
    inspect,
    read_magic,
    sha256_file,
)
from .manifest import SCHEMA_VERSION, Manifest, now_iso
from .paths import ICONS_DIR, STORE_DIR, TRASH_DIR, ensure_dirs


class OperationError(Exception):
    """A refusal or failure with a message meant for the user."""


def _info_dict(info: AppImageInfo) -> dict:
    return {
        "path": str(info.path),
        "name": info.display_name,
        "app_id": info.slug,
        "size_bytes": info.size_bytes,
        "size_human": human_size(info.size_bytes),
        "appimage_type": info.appimage_type,
        "sha256": info.sha256,
        "executable": info.is_executable,
        "runtime_version": info.runtime_version,
        "internal_version": info.desktop.version,
        "comment": info.desktop.comment,
        "categories": info.desktop.categories,
        "mime_types": info.desktop.mime_types,
        "startup_wm_class": info.desktop.startup_wm_class,
        "icon_name": info.desktop.icon,
        "icon_files_in_payload": info.icon_files,
        "update_information": info.update_information,
        "updatable": bool(info.update_information),
    }


def human_size(n: int) -> str:
    step = 1024.0
    val = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if val < step or unit == "GiB":
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= step
    return f"{val:.1f} GiB"


def op_inspect(path: str | Path, *, deep: bool = True) -> dict:
    try:
        info = inspect(Path(path), deep=deep)
    except NotAnAppImage as exc:
        raise OperationError(str(exc)) from exc
    except FileNotFoundError as exc:
        raise OperationError(f"file not found: {path}") from exc
    return {"ok": True, "action": "inspect", "app": _info_dict(info)}


def op_install(
    path: str | Path,
    *,
    app_id: str | None = None,
    extra_args: str = "--no-sandbox",
    keep_source: bool = True,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Integrate an AppImage: copy into the store, install icons, write and
    validate a managed .desktop entry, record a manifest."""
    src = Path(path).expanduser().resolve()
    try:
        info = inspect(src, deep=True)
    except NotAnAppImage as exc:
        raise OperationError(str(exc)) from exc
    except FileNotFoundError as exc:
        raise OperationError(f"file not found: {src}") from exc

    aid = app_id or info.slug
    target = STORE_DIR / src.name
    desktop_file = dsk.desktop_path_for(aid)

    existing = Manifest.load(aid)
    conflict = None
    if existing and not force:
        conflict = f"app_id '{aid}' already installed from {existing.appimage_path}"
    elif desktop_file.exists() and not dsk.is_managed(desktop_file) and not force:
        conflict = (
            f"{desktop_file} exists and is NOT managed by appimagectl; "
            "refusing to overwrite a foreign launcher (use --force to override)"
        )
    if conflict:
        raise OperationError(conflict)

    plan = {
        "copy": {"from": str(src), "to": str(target)},
        "desktop_file": str(desktop_file),
        "icons": [],
        "manifest": str(Manifest.path_for(aid)),
    }
    if dry_run:
        return {
            "ok": True,
            "action": "install",
            "dry_run": True,
            "app_id": aid,
            "app": _info_dict(info),
            "plan": plan,
        }

    ensure_dirs()

    # Copy (or reuse in place if the source already IS the store target).
    if target.resolve() != src:
        shutil.copy2(src, target)
    target.chmod(target.stat().st_mode | 0o111)

    installed_sha = sha256_file(target)
    if info.sha256 and installed_sha != info.sha256:
        target.unlink(missing_ok=True)
        raise OperationError(
            f"copy verification failed: source sha256 {info.sha256} != "
            f"installed {installed_sha}; removed the bad copy"
        )

    icon_targets: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="appimagectl-inst-") as td:
        icons = extract_icons(target, Path(td))
        if icons:
            icon_targets = dsk.install_icons(icons, aid)
    plan["icons"] = [str(p) for p in icon_targets]

    info.sha256 = installed_sha
    text = dsk.render_desktop(info, aid, target, extra_args=extra_args)
    dsk.write_desktop(text, aid)

    valid, validator_note = dsk.validate_desktop(desktop_file)
    if not valid:
        desktop_file.unlink(missing_ok=True)
        for p in icon_targets:
            p.unlink(missing_ok=True)
        raise OperationError(
            f"generated .desktop failed validation, rolled back: {validator_note}"
        )

    notes = dsk.refresh_caches()

    manifest = Manifest(
        schema_version=SCHEMA_VERSION,
        app_id=aid,
        name=info.display_name,
        appimage_path=str(target),
        sha256=installed_sha,
        version=info.desktop.version,
        installed_at=now_iso(),
        desktop_file=str(desktop_file),
        icon_files=[str(p) for p in icon_targets],
        mime_types=info.desktop.mime_types,
        source_path=str(src),
        update_information=info.update_information,
    )
    manifest.save()

    if not keep_source and src != target:
        src.unlink(missing_ok=True)
        notes.append(f"removed source {src}")

    return {
        "ok": True,
        "action": "install",
        "app_id": aid,
        "app": _info_dict(info),
        "installed": {
            "binary": str(target),
            "desktop_file": str(desktop_file),
            "icons": [str(p) for p in icon_targets],
            "icon_count": len(icon_targets),
            "manifest": str(Manifest.path_for(aid)),
            "sha256_verified": True,
        },
        "validator": validator_note,
        "registered_in_shell": dsk.registered_in_shell(aid),
        "notes": notes,
        "warnings": (
            [] if icon_targets else ["no icons found in payload; launcher will be blank"]
        ),
    }


def op_uninstall(
    app_id: str,
    *,
    dry_run: bool = False,
) -> dict:
    """Remove an app we installed. Refuses anything we cannot prove is ours."""
    manifest = Manifest.load(app_id)
    desktop_file = dsk.desktop_path_for(app_id)

    if manifest is None:
        if not dsk.is_managed(desktop_file):
            raise OperationError(
                f"no manifest for '{app_id}' and {desktop_file} is not managed by "
                "appimagectl; refusing to guess which files to delete"
            )
        # Adopt mode: the launcher is ours but the manifest is gone.
        binary = dsk.read_desktop_key(desktop_file, "Exec")
        raise OperationError(
            f"manifest missing for '{app_id}'. The launcher is managed "
            f"(Exec={binary}) but the file list is unknown. Reinstall to restore "
            "the manifest, then uninstall."
        )

    to_remove = [Path(manifest.desktop_file), *(Path(p) for p in manifest.icon_files)]
    binary = Path(manifest.appimage_path)
    trash_target = TRASH_DIR / binary.name

    plan = {
        "delete": [str(p) for p in to_remove],
        "trash": {"from": str(binary), "to": str(trash_target)},
        "manifest": str(Manifest.path_for(app_id)),
    }
    if dry_run:
        return {
            "ok": True,
            "action": "uninstall",
            "dry_run": True,
            "app_id": app_id,
            "plan": plan,
        }

    if Path(manifest.desktop_file).exists() and not dsk.is_managed(
        Path(manifest.desktop_file)
    ):
        raise OperationError(
            f"{manifest.desktop_file} lost its managed marker; refusing to delete"
        )

    removed, skipped = [], []
    for p in to_remove:
        if p.exists():
            p.unlink()
            removed.append(str(p))
        else:
            skipped.append(str(p))

    trashed = None
    if binary.exists():
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        if trash_target.exists():
            trash_target.unlink()
        shutil.move(str(binary), str(trash_target))
        trashed = str(trash_target)

    manifest.delete()
    notes = dsk.refresh_caches()

    return {
        "ok": True,
        "action": "uninstall",
        "app_id": app_id,
        "removed": removed,
        "already_absent": skipped,
        "trashed_binary": trashed,
        "still_registered_in_shell": dsk.registered_in_shell(app_id),
        "notes": notes,
    }


def op_adopt(
    app_id: str,
    binary: str | Path | None = None,
    *,
    dry_run: bool = False,
) -> dict:
    """Claim an existing, manually-installed AppImage into management.

    Adopt does not move or rewrite anything except adding our provenance keys to
    the .desktop file. It refuses when the desktop entry does not exist, when it
    is already managed, or when its Exec= does not point at the given (or
    discovered) AppImage - otherwise we would be claiming files we cannot vouch
    for.
    """
    desktop_file = dsk.desktop_path_for(app_id)
    if not desktop_file.is_file():
        raise OperationError(
            f"no desktop entry at {desktop_file}; use 'install' instead of 'adopt'"
        )
    managed_already = dsk.is_managed(desktop_file)
    manifest_already = Manifest.load(app_id)
    if managed_already and manifest_already is not None:
        raise OperationError(f"'{app_id}' is already managed by appimagectl")
    # Orphan state: the launcher carries our marker but the manifest is missing
    # (e.g. a previous adopt crashed between writing the marker and saving the
    # manifest, or the manifest was deleted). Re-adopting is the correct
    # recovery - adopt only ever writes the same keys and the manifest.

    exec_line = dsk.read_desktop_key(desktop_file, "Exec") or ""
    if not exec_line:
        raise OperationError(f"{desktop_file} has no Exec= line; refusing")
    exec_binary = Path(exec_line.split('"')[1] if '"' in exec_line else exec_line.split()[0])
    exec_binary = exec_binary.expanduser().resolve()

    if binary is not None:
        wanted = Path(binary).expanduser().resolve()
        if exec_binary != wanted:
            raise OperationError(
                f"Exec= points at {exec_binary}, not {wanted}; refusing to adopt"
            )

    if not exec_binary.is_file():
        raise OperationError(f"Exec= binary missing: {exec_binary}")

    try:
        size = exec_binary.stat().st_size
        appimage_type = read_magic(exec_binary)
    except NotAnAppImage as exc:
        raise OperationError(f"Exec= target is not an AppImage: {exc}") from exc

    sha = sha256_file(exec_binary)
    plan = {
        "desktop_file": str(desktop_file),
        "binary": str(exec_binary),
        "add_keys": [
            f"{dsk.MANAGED_KEY}=true",
            f"{dsk.APP_ID_KEY}={app_id}",
            f"{dsk.SHA_KEY}={sha}",
        ],
    }
    if dry_run:
        return {"ok": True, "action": "adopt", "dry_run": True, "app_id": app_id, "plan": plan}

    text = desktop_file.read_text(encoding="utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    for key in (dsk.MANAGED_KEY, dsk.APP_ID_KEY, dsk.SHA_KEY):
        lines = [ln for ln in lines if not ln.startswith(key + "=")]
    lines.extend(plan["add_keys"])
    desktop_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    info = inspect(exec_binary, compute_sha=False, deep=False)
    entry_name = dsk.read_desktop_key(desktop_file, "Name") or info.display_name
    entry_version = dsk.read_desktop_key(desktop_file, "X-AppImage-Version")
    # Adopted apps may already have icons in the theme (installed by hand or by
    # a prior tool). Record exactly those named after this app so uninstall can
    # clean them too; a scoped glob can never match another app's icons. An
    # absolute Icon= path (e.g. ~/Applications/orca/icon.png) deliberately does
    # NOT count: that icon lives outside the theme and belongs to the app's own
    # directory, not to launcher management.
    icon_name = dsk.read_desktop_key(desktop_file, "Icon") or app_id
    icon_files: list[str] = []
    if icon_name and not Path(icon_name).is_absolute():
        icon_files = sorted(str(p) for p in ICONS_DIR.rglob(f"{icon_name}.*"))
    manifest = Manifest(
        schema_version=SCHEMA_VERSION,
        app_id=app_id,
        name=entry_name,
        appimage_path=str(exec_binary),
        sha256=sha,
        version=entry_version,
        installed_at=now_iso(),
        desktop_file=str(desktop_file),
        icon_files=icon_files,
        mime_types=info.desktop.mime_types,
        source_path=None,
        update_information=info.update_information,
    )
    manifest.save()

    return {
        "ok": True,
        "action": "adopt",
        "app_id": app_id,
        "binary": str(exec_binary),
        "size_bytes": size,
        "size_human": human_size(size),
        "appimage_type": appimage_type,
        "sha256": sha,
        "desktop_file": str(desktop_file),
        "registered_in_shell": dsk.registered_in_shell(app_id),
    }


def op_list(*, check: bool = True) -> dict:
    """List managed apps, plus any AppImage in the store we did NOT install."""
    apps = []
    known_paths = set()
    for m in Manifest.all():
        known_paths.add(m.appimage_path)
        entry = {
            "app_id": m.app_id,
            "name": m.name,
            "version": m.version,
            "binary": m.appimage_path,
            "desktop_file": m.desktop_file,
            "icon_count": len(m.icon_files),
            "installed_at": m.installed_at,
            "updatable": bool(m.update_information),
        }
        if check:
            missing = m.missing_files()
            entry["healthy"] = not missing
            entry["missing_files"] = missing
            entry["desktop_managed"] = dsk.is_managed(Path(m.desktop_file))
            entry["registered_in_shell"] = dsk.registered_in_shell(m.app_id)
        apps.append(entry)

    unmanaged = []
    if STORE_DIR.is_dir():
        # rglob so AppImages inside subdirectories (e.g. ~/Applications/orca/)
        # are found, not only the flat top level.
        for p in sorted(STORE_DIR.rglob("*.AppImage")) + sorted(STORE_DIR.rglob("*.appimage")):
            if p.is_file() and str(p) not in known_paths:
                unmanaged.append(
                    {"path": str(p), "size_human": human_size(p.stat().st_size)}
                )

    return {
        "ok": True,
        "action": "list",
        "schema_version": SCHEMA_VERSION,
        "store_dir": str(STORE_DIR),
        "count": len(apps),
        "apps": apps,
        "unmanaged_in_store": unmanaged,
    }


def op_doctor() -> dict:
    """Report on the environment and on every managed app. Read-only."""
    tools = {
        name: (shutil.which(name) or None)
        for name in (
            "desktop-file-validate",
            "update-desktop-database",
            "gtk-update-icon-cache",
            "readelf",
            "xdg-mime",
        )
    }
    try:
        import gi  # noqa: F401

        pygobject = True
    except ImportError:
        pygobject = False

    listing = op_list(check=True)
    problems: list[str] = []
    for app in listing["apps"]:
        if not app.get("healthy"):
            problems.append(f"{app['app_id']}: missing {app['missing_files']}")
        if app.get("desktop_managed") is False:
            problems.append(f"{app['app_id']}: .desktop lost its managed marker")
        if app.get("registered_in_shell") is False:
            problems.append(f"{app['app_id']}: not visible to the desktop shell")
    for name, p in tools.items():
        if p is None:
            problems.append(f"missing tool: {name} (some checks are skipped)")

    return {
        "ok": not problems,
        "action": "doctor",
        "tools": tools,
        "pygobject": pygobject,
        "store_dir": str(STORE_DIR),
        "managed_apps": listing["count"],
        "unmanaged_in_store": listing["unmanaged_in_store"],
        "problems": problems,
    }


def op_run(app_id: str) -> dict:
    """Launch a managed app detached from this process."""
    import subprocess

    manifest = Manifest.load(app_id)
    if manifest is None:
        raise OperationError(f"unknown app_id '{app_id}'")
    binary = Path(manifest.appimage_path)
    if not binary.is_file():
        raise OperationError(f"binary missing: {binary}")
    proc = subprocess.Popen(
        [str(binary), "--no-sandbox"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {
        "ok": True,
        "action": "run",
        "app_id": app_id,
        "pid": proc.pid,
        "binary": str(binary),
    }
