"""Update detection and application for AppImages with embedded update info.

The AppImage spec embeds an `update_information` string such as
`gh-releases-zsync|owner|repo|latest|*x86_64.AppImage`. We parse the GitHub
form and compare against the repo's releases via the public API (no token;
rate-limited but fine for one app at a time).

Electron-builder AppImages ship an EMPTY .upd_info section, so for them we
report honestly that no update source exists instead of inventing one.
"""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import desktop as dsk
from .appimage import NotAnAppImage, inspect, read_magic, sha256_file
from .core import OperationError, human_size
from .manifest import Manifest, now_iso
from .paths import STORE_DIR, TRASH_DIR


@dataclass
class GitHubUpdateSource:
    owner: str
    repo: str
    tag: str  # 'latest', 'latest-pre', or a specific tag
    pattern: str  # asset name glob, e.g. '*x86_64.AppImage'


GH_RE = re.compile(r"^gh-releases-zsync\|([^|]+)\|([^|]+)\|([^|]+)\|(.+)$")


def parse_update_info(update_information: str | None) -> GitHubUpdateSource | None:
    if not update_information:
        return None
    m = GH_RE.match(update_information.strip())
    if not m:
        return None  # zsync| or gl-releases-* exist but we do not support them yet
    return GitHubUpdateSource(m.group(1), m.group(2), m.group(3), m.group(4))


def _api_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "appimagectl/0.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise OperationError(f"github API {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise OperationError(f"network error: {exc.reason}") from exc


def _version_from_name(name: str) -> str | None:
    """Best-effort version extraction from an asset name: v1.2.3, 1.2.3, 2024.8."""
    m = re.search(r"v?(\d+\.\d+(?:\.\d+)?(?:[-+][\w.]+)?)", name)
    return m.group(1) if m else None


def op_check_update(app_id: str, *, timeout: int = 30) -> dict:
    """Query the update source for a managed app. Read-only, network."""
    manifest = Manifest.load(app_id)
    if manifest is None:
        raise OperationError(f"unknown app_id '{app_id}'")
    source = parse_update_info(manifest.update_information)
    if source is None:
        return {
            "ok": True,
            "action": "check-update",
            "app_id": app_id,
            "updatable": False,
            "current": manifest.version,
            "reason": (
                "no usable update information embedded (Electron-built AppImages "
                "ship an empty .upd_info)"
            ),
        }

    tag_part = "latest" if source.tag in ("latest", "latest-pre") else f"tags/{source.tag}"
    url = f"https://api.github.com/repos/{source.owner}/{source.repo}/releases/{tag_part}"
    data = _api_json(url, timeout=timeout)
    assets = [
        a
        for a in data.get("assets", [])
        if fnmatch.fnmatch(a.get("name", ""), source.pattern)
    ]
    if not assets:
        return {
            "ok": True,
            "action": "check-update",
            "app_id": app_id,
            "updatable": True,
            "found": False,
            "reason": (
                f"release {data.get('tag_name')} has no asset matching "
                f"'{source.pattern}'"
            ),
        }
    asset = assets[0]
    latest_version = _version_from_name(asset["name"])
    available = latest_version is not None and latest_version != manifest.version
    return {
        "ok": True,
        "action": "check-update",
        "app_id": app_id,
        "updatable": True,
        "found": True,
        "current_version": manifest.version,
        "latest_version": latest_version,
        "available": available,
        "release_tag": data.get("tag_name"),
        "release_url": data.get("html_url"),
        "asset_url": asset["browser_download_url"],
        "asset_name": asset["name"],
        "asset_size_bytes": asset.get("size"),
        "asset_size_human": human_size(asset.get("size") or 0),
        "published_at": data.get("published_at"),
    }


def op_update(app_id: str, *, dry_run: bool = False, timeout: int = 300) -> dict:
    """Download the newer AppImage from the update source, swap it in, and
    record a fresh manifest. The old binary goes to trash."""
    manifest = Manifest.load(app_id)
    if manifest is None:
        raise OperationError(f"unknown app_id '{app_id}'")

    check = op_check_update(app_id)
    if not check.get("updatable") or not check.get("found") or not check.get("available"):
        return {**check, "action": "update", "downloaded": False, "note": "nothing to update"}

    binary = Path(manifest.appimage_path)
    if dry_run:
        return {
            **check,
            "action": "update",
            "dry_run": True,
            "plan": {
                "download": check["asset_url"],
                "replace": str(binary),
                "trash_old": str(TRASH_DIR / binary.name),
            },
        }

    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="appimagectl-upd-", suffix=".AppImage", delete=False
    ) as tmp:
        tmp_name = tmp.name
    req = urllib.request.Request(check["asset_url"], headers={"User-Agent": "appimagectl/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp_name, "wb") as out:
            shutil.copyfileobj(resp, out)
    except Exception as exc:
        Path(tmp_name).unlink(missing_ok=True)
        raise OperationError(f"download failed: {exc}") from exc

    try:
        read_magic(Path(tmp_name))  # verify it is an AppImage before swapping
    except NotAnAppImage as exc:
        Path(tmp_name).unlink(missing_ok=True)
        raise OperationError(f"downloaded file is not an AppImage; aborted ({exc})") from exc

    new_sha = sha256_file(Path(tmp_name))

    # Keep the old binary recoverable, then put the new one in place.
    if binary.exists():
        trash_target = TRASH_DIR / binary.name
        if trash_target.exists():
            trash_target.unlink()
        shutil.move(str(binary), str(trash_target))
    final = Path(tmp_name)
    final.chmod(0o755)

    target = STORE_DIR / binary.name
    if binary != target:  # manifest path could theoretically live elsewhere
        shutil.move(str(final), str(target))
    else:
        shutil.move(str(final), str(binary))
    installed = target if binary != target else binary

    new_version = check.get("latest_version") or _version_from_name(installed.name)
    updated = Manifest(
        schema_version=manifest.schema_version,
        app_id=manifest.app_id,
        name=manifest.name,
        appimage_path=str(installed),
        sha256=new_sha,
        version=new_version,
        installed_at=now_iso(),
        desktop_file=manifest.desktop_file,
        icon_files=manifest.icon_files,
        mime_types=manifest.mime_types,
        source_path=check["asset_url"],
        update_information=manifest.update_information,
    )
    updated.save()

    # Refresh the .desktop entry's provenance version to match the new binary.
    # inspect(deep=False) cannot see the payload's Name=/Comment=, so carry the
    # existing entry's presentation fields forward instead of rebuilding them.
    old_desktop_text = Path(manifest.desktop_file).read_text(
        encoding="utf-8", errors="replace"
    )
    old_keys = dict(
        line.split("=", 1)
        for line in old_desktop_text.splitlines()
        if line.strip() and not line.startswith("#") and "=" in line
    )
    fresh = inspect(installed, compute_sha=False, deep=False)
    fresh.desktop.name = old_keys.get("Name") or fresh.display_name
    fresh.desktop.comment = old_keys.get("Comment") or fresh.desktop.comment
    fresh.desktop.categories = old_keys.get("Categories") or fresh.desktop.categories
    fresh.desktop.startup_wm_class = old_keys.get(
        "StartupWMClass"
    ) or fresh.desktop.startup_wm_class
    text = dsk.render_desktop(fresh, manifest.app_id, installed)
    dsk.write_desktop(text, manifest.app_id)

    return {
        "ok": True,
        "action": "update",
        "app_id": app_id,
        "downloaded": True,
        "from_version": manifest.version,
        "to_version": new_version,
        "asset": check["asset_name"],
        "download_size_human": check["asset_size_human"],
        "sha256_verified": new_sha,
        "old_binary_trashed": str(TRASH_DIR / binary.name),
        "notes": dsk.refresh_caches(),
    }