"""Read-only inspection of an AppImage file.

Everything here answers: *what is this file, and what does it claim about
itself?* No side effects on the system, no execution of the payload beyond the
AppImage runtime's own `--appimage-*` flags.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# Type-2 AppImages are ELF files with magic bytes 0x41 0x49 0x02 at offset 8.
# Type-1 uses 0x41 0x49 0x01. Anything else is not an AppImage.
_MAGIC_OFFSET = 8
_MAGIC_PREFIX = b"AI"


class NotAnAppImage(Exception):
    """Raised when the file is not an AppImage (by magic bytes)."""


@dataclass
class DesktopMeta:
    """The .desktop entry the AppImage ships internally."""

    name: str | None = None
    comment: str | None = None
    icon: str | None = None
    categories: str | None = None
    mime_types: list[str] = field(default_factory=list)
    startup_wm_class: str | None = None
    exec_line: str | None = None
    version: str | None = None  # X-AppImage-Version
    raw: dict[str, str] = field(default_factory=dict)


@dataclass
class AppImageInfo:
    path: Path
    size_bytes: int
    appimage_type: int
    sha256: str | None = None
    desktop: DesktopMeta = field(default_factory=DesktopMeta)
    icon_files: list[str] = field(default_factory=list)  # paths inside the AppDir
    update_information: str | None = None
    runtime_version: str | None = None
    is_executable: bool = False

    @property
    def display_name(self) -> str:
        return self.desktop.name or self.path.stem

    @property
    def slug(self) -> str:
        """Stable filesystem-safe id, derived from the internal Icon= name when
        available (that is what the icon files are named after), else the
        Name= field, else the filename."""
        base = self.desktop.icon or self.desktop.name or self.path.stem
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", base).strip("-._").lower()
        return slug or "appimage"


def read_magic(path: Path) -> int:
    """Return AppImage type (1 or 2). Raise NotAnAppImage otherwise."""
    with path.open("rb") as fh:
        head = fh.read(_MAGIC_OFFSET + 3)
    if len(head) < _MAGIC_OFFSET + 3:
        raise NotAnAppImage(f"{path}: too small to be an AppImage")
    if not head.startswith(b"\x7fELF"):
        raise NotAnAppImage(f"{path}: not an ELF binary")
    magic = head[_MAGIC_OFFSET : _MAGIC_OFFSET + 3]
    if magic[:2] != _MAGIC_PREFIX:
        raise NotAnAppImage(
            f"{path}: missing AppImage magic at offset 8 (found {magic!r})"
        )
    return magic[2]


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def _parse_desktop_text(text: str) -> DesktopMeta:
    """Parse the [Desktop Entry] group. Deliberately minimal: we only read, and
    we keep the raw dict so callers can carry through keys we don't model."""
    meta = DesktopMeta()
    in_group = False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            in_group = line == "[Desktop Entry]"
            continue
        if not in_group or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        meta.raw[key] = value
    r = meta.raw
    meta.name = r.get("Name")
    meta.comment = r.get("Comment")
    meta.icon = r.get("Icon")
    meta.categories = r.get("Categories")
    meta.startup_wm_class = r.get("StartupWMClass")
    meta.exec_line = r.get("Exec")
    meta.version = r.get("X-AppImage-Version")
    mt = r.get("MimeType", "")
    meta.mime_types = [m for m in (s.strip() for s in mt.split(";")) if m]
    return meta


def _read_updinfo(path: Path) -> str | None:
    """Read the .upd_info ELF section. Returns None when absent or blank.

    Electron-builder AppImages ship the section but leave it empty, so a
    present-but-empty section must report as *no* update information rather than
    an empty string that later code might treat as a usable URL.
    """
    if not shutil.which("readelf"):
        return None
    proc = subprocess.run(
        ["readelf", "-x", ".upd_info", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    chars: list[str] = []
    for line in proc.stdout.splitlines():
        # readelf -x rows look like:  0x0000 41490200 ... |ascii|
        if "|" not in line:
            continue
        ascii_col = line.rsplit("|", 2)
        if len(ascii_col) >= 2:
            chars.append(ascii_col[-2])
    blob = "".join(chars).replace(".", "").strip("\x00").strip()
    return blob or None


def _extract(path: Path, pattern: str, dest: Path) -> bool:
    """Run --appimage-extract for one glob into dest. True if anything landed.

    A file can carry valid AppImage magic and still not be executable by this
    kernel (wrong arch, truncated runtime), which surfaces as OSError rather
    than a non-zero exit code. Both mean "no payload", not a crash.
    """
    try:
        proc = subprocess.run(
            [str(path), "--appimage-extract", pattern],
            cwd=dest,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    root = dest / "squashfs-root"
    return proc.returncode == 0 and root.exists()


def _run_appimage_flag(path: Path, flag: str, timeout: int = 60) -> str | None:
    """Invoke one --appimage-* flag, returning trimmed output or None."""
    try:
        proc = subprocess.run(
            [str(path), flag],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout + proc.stderr).strip() or None


def inspect(path: Path, *, compute_sha: bool = True, deep: bool = True) -> AppImageInfo:
    """Inspect an AppImage.

    deep=False skips payload extraction (fast; magic + size + upd_info only).
    deep=True extracts only the .desktop file and icon paths - never the whole
    ~180 MB payload.
    """
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    info = AppImageInfo(
        path=path,
        size_bytes=path.stat().st_size,
        appimage_type=read_magic(path),
        is_executable=path.stat().st_mode & 0o111 != 0,
    )
    info.update_information = _read_updinfo(path)
    if compute_sha:
        info.sha256 = sha256_file(path)
    if not deep:
        return info

    # Extraction needs the exec bit; inspecting a freshly downloaded file that
    # is not yet chmod +x is a normal case, so copy to a temp file we own.
    with tempfile.TemporaryDirectory(prefix="appimagectl-") as td:
        tmp = Path(td)
        runner = path
        if not info.is_executable:
            runner = tmp / path.name
            shutil.copy2(path, runner)
            runner.chmod(0o755)

        workdir = tmp / "work"
        workdir.mkdir()
        if _extract(runner, "*.desktop", workdir):
            root = workdir / "squashfs-root"
            for entry in sorted(root.glob("*.desktop")):
                info.desktop = _parse_desktop_text(
                    entry.read_text(encoding="utf-8", errors="replace")
                )
                break
        shutil.rmtree(workdir / "squashfs-root", ignore_errors=True)
        if _extract(runner, "usr/share/icons/hicolor/*", workdir):
            root = workdir / "squashfs-root"
            info.icon_files = sorted(
                str(p.relative_to(root))
                for p in root.rglob("*")
                if p.is_file() and p.suffix in {".png", ".svg", ".xpm"}
            )

        out = _run_appimage_flag(runner, "--appimage-version")
        if out:
            info.runtime_version = out.split(":", 1)[-1].strip() or None

    return info


def extract_icons(path: Path, dest: Path) -> dict[int, Path]:
    """Extract hicolor PNG icons into dest. Returns {size: file}.

    Falls back to the top-level .DirIcon when the AppImage ships no hicolor
    tree, because a launcher with no icon is a visibly broken install.
    """
    dest.mkdir(parents=True, exist_ok=True)
    found: dict[int, Path] = {}
    with tempfile.TemporaryDirectory(prefix="appimagectl-icons-") as td:
        tmp = Path(td)
        runner = path
        if path.stat().st_mode & 0o111 == 0:
            runner = tmp / path.name
            shutil.copy2(path, runner)
            runner.chmod(0o755)
        work = tmp / "work"
        work.mkdir()
        if _extract(runner, "usr/share/icons/hicolor/*", work):
            root = work / "squashfs-root"
            for png in root.rglob("*.png"):
                m = re.search(r"/(\d+)x\1/", str(png))
                if not m:
                    continue
                size = int(m.group(1))
                target = dest / f"{size}-{png.name}"
                shutil.copy2(png, target)
                found[size] = target
        if not found:
            work2 = tmp / "work2"
            work2.mkdir()
            if _extract(runner, ".DirIcon", work2):
                diricon = work2 / "squashfs-root" / ".DirIcon"
                if diricon.is_file() and not diricon.is_symlink():
                    target = dest / "0-DirIcon.png"
                    shutil.copy2(diricon, target)
                    found[0] = target
    return found
