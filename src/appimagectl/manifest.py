"""Install manifests: the record of every file we created for an app.

Uninstall reads the manifest and removes exactly what is listed - never a glob,
never a guess. If the manifest is missing, uninstall degrades to "adopt" mode,
which requires the .desktop file to carry our managed marker.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .paths import MANIFEST_DIR

SCHEMA_VERSION = 1


@dataclass
class Manifest:
    schema_version: int
    app_id: str  # slug, also the .desktop basename without suffix
    name: str
    appimage_path: str
    sha256: str
    version: str | None
    installed_at: str
    desktop_file: str
    icon_files: list[str] = field(default_factory=list)
    mime_types: list[str] = field(default_factory=list)
    source_path: str | None = None  # where it was installed from
    update_information: str | None = None

    @staticmethod
    def path_for(app_id: str) -> Path:
        return MANIFEST_DIR / f"{app_id}.json"

    def save(self) -> Path:
        MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
        target = self.path_for(self.app_id)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        tmp.replace(target)  # atomic within the same directory
        return target

    @classmethod
    def load(cls, app_id: str) -> Manifest | None:
        p = cls.path_for(app_id)
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def all(cls) -> list[Manifest]:
        if not MANIFEST_DIR.is_dir():
            return []
        out = []
        for p in sorted(MANIFEST_DIR.glob("*.json")):
            m = cls.load(p.stem)
            if m:
                out.append(m)
        return out

    def delete(self) -> None:
        self.path_for(self.app_id).unlink(missing_ok=True)

    def missing_files(self) -> list[str]:
        """Files this manifest claims exist but do not. A non-empty result means
        the install is damaged - reported, never silently repaired."""
        candidates = [self.appimage_path, self.desktop_file, *self.icon_files]
        return [c for c in candidates if not Path(c).exists()]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
