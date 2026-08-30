"""Desktop-entry rendering and the managed-marker safety rule."""

from __future__ import annotations

from pathlib import Path

from appimagectl.appimage import AppImageInfo, DesktopMeta
from appimagectl.paths import MANAGED_KEY


def _info(**kw) -> AppImageInfo:
    meta = DesktopMeta(
        name=kw.pop("name", "Agent Orchestrator"),
        comment=kw.pop("comment", "Electron frontend"),
        icon=kw.pop("icon", "agent-orchestrator"),
        categories=kw.pop("categories", "Utility;"),
        mime_types=kw.pop("mime_types", ["x-scheme-handler/ao-app"]),
        startup_wm_class=kw.pop("wm", "Agent Orchestrator"),
        exec_line="AppRun --no-sandbox %U",
        version=kw.pop("version", "0.12.9"),
    )
    return AppImageInfo(
        path=Path(kw.pop("path", "/tmp/agent-orchestrator-linux-x64.AppImage")),
        size_bytes=1234,
        appimage_type=2,
        sha256=kw.pop("sha256", "a" * 64),
        desktop=meta,
    )


def test_slug_prefers_internal_icon_name():
    assert _info().slug == "agent-orchestrator"


def test_slug_sanitises_and_lowercases():
    info = _info(icon=None, name="My Weird App!! v2")
    assert info.slug == "my-weird-app-v2"


def test_slug_falls_back_to_filename():
    info = _info(icon=None, name=None, path="/tmp/ZCode-3.10.1-linux-x64.AppImage")
    assert info.slug == "zcode-3.10.1-linux-x64"


def test_render_replaces_apprun_with_absolute_path(sandbox):
    dsk = sandbox["desktop"]
    target = Path("/home/u/Applications/agent-orchestrator-linux-x64.AppImage")
    text = dsk.render_desktop(_info(), "agent-orchestrator", target)
    assert f'Exec="{target}" "--no-sandbox" %U' in text
    assert "AppRun" not in text


def test_render_honours_empty_extra_args(sandbox):
    dsk = sandbox["desktop"]
    target = Path("/home/u/Applications/x.AppImage")
    text = dsk.render_desktop(_info(), "x", target, extra_args="")
    assert f'Exec="{target}" %U' in text


def test_render_carries_provenance_keys(sandbox):
    dsk = sandbox["desktop"]
    text = dsk.render_desktop(_info(), "agent-orchestrator", Path("/x.AppImage"))
    assert f"{MANAGED_KEY}=true" in text
    assert "X-AppImageCtl-Id=agent-orchestrator" in text
    assert f"X-AppImageCtl-Sha256={'a' * 64}" in text
    assert "X-AppImageCtl-Version=0.12.9" in text


def test_render_appends_missing_category_semicolon(sandbox):
    dsk = sandbox["desktop"]
    text = dsk.render_desktop(_info(categories="Development"), "x", Path("/x.AppImage"))
    assert "Categories=Development;" in text


def test_render_omits_mimetype_when_none(sandbox):
    dsk = sandbox["desktop"]
    text = dsk.render_desktop(_info(mime_types=[]), "x", Path("/x.AppImage"))
    assert "MimeType=" not in text


def test_is_managed_true_for_our_file(sandbox):
    dsk = sandbox["desktop"]
    text = dsk.render_desktop(_info(), "aid", Path("/x.AppImage"))
    written = dsk.write_desktop(text, "aid")
    assert dsk.is_managed(written) is True


def test_is_managed_false_for_foreign_launcher(sandbox):
    dsk = sandbox["desktop"]
    foreign = sandbox["paths"].APPLICATIONS_DIR / "foreign.desktop"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text("[Desktop Entry]\nName=Foreign\nExec=/usr/bin/foreign\n")
    assert dsk.is_managed(foreign) is False


def test_is_managed_false_for_missing_file(sandbox):
    dsk = sandbox["desktop"]
    assert dsk.is_managed(sandbox["paths"].APPLICATIONS_DIR / "nope.desktop") is False


def test_generated_desktop_passes_validator(sandbox):
    """The real desktop-file-validate must accept what we generate."""
    dsk = sandbox["desktop"]
    text = dsk.render_desktop(_info(), "agent-orchestrator", Path("/x.AppImage"))
    written = dsk.write_desktop(text, "agent-orchestrator")
    ok, note = dsk.validate_desktop(written)
    assert ok, note


def test_install_icons_maps_diricon_fallback_to_256(sandbox, tmp_path):
    dsk = sandbox["desktop"]
    src = tmp_path / "0-DirIcon.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    written = dsk.install_icons({0: src}, "aid")
    assert len(written) == 1
    assert "256x256" in str(written[0])
    assert written[0].name == "aid.png"
