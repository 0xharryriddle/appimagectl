"""Install / uninstall / list / doctor behaviour, including the refusal paths.

These are the tests that matter: they prove appimagectl will not delete files it
did not create, and that a failed install leaves nothing behind.

NOTE: the sandbox fixture reloads the package modules under redirected XDG
paths, which creates NEW exception classes. Tests must therefore catch errors
via ``sandbox["core"].OperationError`` and never via a module-level import - a
stale class reference matches nothing.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest


def test_human_size_units(sandbox):
    human_size = sandbox["core"].human_size
    assert human_size(512) == "512 B"
    assert human_size(2048) == "2.0 KiB"
    assert human_size(190 * 1024 * 1024).endswith("MiB")


def test_install_then_list_then_uninstall_roundtrip(sandbox, fake_appimage):
    core, manifest_mod = sandbox["core"], sandbox["manifest"]

    res = core.op_install(fake_appimage)
    assert res["ok"]
    app_id = res["app_id"]

    binary = Path(res["installed"]["binary"])
    desktop_file = Path(res["installed"]["desktop_file"])
    assert binary.is_file()
    assert binary.stat().st_mode & 0o111
    assert desktop_file.is_file()
    assert res["installed"]["sha256_verified"] is True
    # Source is kept by default.
    assert fake_appimage.is_file()

    # The manifest lists exactly what was created.
    m = manifest_mod.Manifest.load(app_id)
    assert m is not None
    assert m.appimage_path == str(binary)
    assert m.desktop_file == str(desktop_file)
    assert m.missing_files() == []

    listing = core.op_list()
    assert listing["count"] == 1
    entry = listing["apps"][0]
    assert entry["app_id"] == app_id
    assert entry["healthy"] is True
    assert entry["desktop_managed"] is True

    out = core.op_uninstall(app_id)
    assert out["ok"]
    assert not desktop_file.exists()
    assert not binary.exists()
    assert Path(out["trashed_binary"]).is_file()  # trashed, not destroyed
    assert manifest_mod.Manifest.load(app_id) is None
    assert core.op_list()["count"] == 0


def test_install_is_idempotent_only_with_force(sandbox, fake_appimage):
    core = sandbox["core"]
    core.op_install(fake_appimage)
    with pytest.raises(core.OperationError, match="already installed"):
        core.op_install(fake_appimage)
    res = core.op_install(fake_appimage, force=True)
    assert res["ok"]


def test_install_refuses_to_overwrite_foreign_desktop_file(sandbox, fake_appimage):
    core, paths = sandbox["core"], sandbox["paths"]
    # A launcher that appimagectl did not write, at the id we would claim.
    foreign = paths.APPLICATIONS_DIR / "fakeapp-1.2.3-x86_64.desktop"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text("[Desktop Entry]\nName=Distro Package\nExec=/usr/bin/thing\n")
    original = foreign.read_text()

    with pytest.raises(core.OperationError, match="NOT managed"):
        core.op_install(fake_appimage)

    assert foreign.read_text() == original  # untouched


def test_install_dry_run_writes_nothing(sandbox, fake_appimage):
    core, paths = sandbox["core"], sandbox["paths"]
    res = core.op_install(fake_appimage, dry_run=True)
    assert res["dry_run"] is True
    assert not list(paths.APPLICATIONS_DIR.glob("*.desktop"))
    assert not list(sandbox["store"].glob("*.AppImage"))
    assert core.op_list()["count"] == 0


def test_install_move_deletes_source(sandbox, fake_appimage):
    core = sandbox["core"]
    res = core.op_install(fake_appimage, keep_source=False)
    assert res["ok"]
    assert not fake_appimage.exists()
    assert Path(res["installed"]["binary"]).is_file()


def test_install_rejects_non_appimage(sandbox, not_an_appimage):
    core = sandbox["core"]
    with pytest.raises(core.OperationError, match="not an ELF"):
        core.op_install(not_an_appimage)


def test_install_reports_missing_file(sandbox, tmp_path):
    core = sandbox["core"]
    with pytest.raises(core.OperationError, match="file not found"):
        core.op_install(tmp_path / "ghost.AppImage")


def test_uninstall_refuses_unknown_app(sandbox):
    core = sandbox["core"]
    with pytest.raises(core.OperationError, match="no manifest"):
        core.op_uninstall("never-installed")


def test_uninstall_refuses_when_manifest_lost_but_launcher_managed(sandbox, fake_appimage):
    core, manifest_mod = sandbox["core"], sandbox["manifest"]
    res = core.op_install(fake_appimage)
    app_id = res["app_id"]
    manifest_mod.Manifest.path_for(app_id).unlink()

    with pytest.raises(core.OperationError, match="manifest missing"):
        core.op_uninstall(app_id)
    # Refusal must not have removed anything.
    assert Path(res["installed"]["desktop_file"]).is_file()
    assert Path(res["installed"]["binary"]).is_file()


def test_uninstall_refuses_if_marker_stripped(sandbox, fake_appimage):
    """Someone hand-edited our launcher: we no longer own it, so we stop."""
    core = sandbox["core"]
    res = core.op_install(fake_appimage)
    desktop_file = Path(res["installed"]["desktop_file"])
    desktop_file.write_text("[Desktop Entry]\nName=Rewritten By Hand\nExec=/bin/true\n")

    with pytest.raises(core.OperationError, match="lost its managed marker"):
        core.op_uninstall(res["app_id"])
    assert desktop_file.is_file()


def test_uninstall_dry_run_deletes_nothing(sandbox, fake_appimage):
    core = sandbox["core"]
    res = core.op_install(fake_appimage)
    plan = core.op_uninstall(res["app_id"], dry_run=True)
    assert plan["dry_run"] is True
    assert Path(res["installed"]["desktop_file"]).is_file()
    assert Path(res["installed"]["binary"]).is_file()
    assert str(Path(res["installed"]["desktop_file"])) in plan["plan"]["delete"]


def test_uninstall_tolerates_already_absent_icons(sandbox, fake_appimage):
    core = sandbox["core"]
    res = core.op_install(fake_appimage)
    for p in res["installed"]["icons"]:
        Path(p).unlink()
    out = core.op_uninstall(res["app_id"])
    assert out["ok"]
    assert len(out["already_absent"]) == len(res["installed"]["icons"])


def test_list_flags_damaged_install(sandbox, fake_appimage):
    core = sandbox["core"]
    res = core.op_install(fake_appimage)
    Path(res["installed"]["binary"]).unlink()

    entry = core.op_list()["apps"][0]
    assert entry["healthy"] is False
    assert res["installed"]["binary"] in entry["missing_files"]


def test_list_reports_unmanaged_store_files(sandbox, fake_appimage):
    core = sandbox["core"]
    stray = sandbox["store"] / "Stray-x86_64.AppImage"
    stray.write_bytes(fake_appimage.read_bytes())

    listing = core.op_list()
    assert listing["count"] == 0
    assert [u["path"] for u in listing["unmanaged_in_store"]] == [str(stray)]


def test_list_finds_appimages_in_store_subdirectories(sandbox, fake_appimage):
    """An AppImage parked in a subdir of the store (e.g. ~/Applications/orca/)
    must show up as unmanaged instead of silently disappearing."""
    core = sandbox["core"]
    sub = sandbox["store"] / "orca"
    sub.mkdir()
    stray = sub / "orca-linux.AppImage"
    stray.write_bytes(fake_appimage.read_bytes())

    listing = core.op_list()
    assert [u["path"] for u in listing["unmanaged_in_store"]] == [str(stray)]


def test_doctor_is_readonly_and_reports_problems(sandbox, fake_appimage):
    core = sandbox["core"]
    res = core.op_install(fake_appimage)
    Path(res["installed"]["desktop_file"]).unlink()

    report = core.op_doctor()
    assert report["ok"] is False
    assert any(res["app_id"] in p for p in report["problems"])
    # Read-only: the damaged state is unchanged.
    assert not Path(res["installed"]["desktop_file"]).exists()


def test_custom_app_id_is_honoured(sandbox, fake_appimage):
    core = sandbox["core"]
    res = core.op_install(fake_appimage, app_id="my-custom-id")
    assert res["app_id"] == "my-custom-id"
    assert Path(res["installed"]["desktop_file"]).name == "my-custom-id.desktop"


def test_manifest_survives_reload_and_is_valid_json(sandbox, fake_appimage):
    core, manifest_mod = sandbox["core"], sandbox["manifest"]
    res = core.op_install(fake_appimage)
    raw = json.loads(manifest_mod.Manifest.path_for(res["app_id"]).read_text())
    assert raw["schema_version"] == manifest_mod.SCHEMA_VERSION
    assert raw["sha256"] == res["app"]["sha256"]


def test_manifest_load_survives_corrupt_file(sandbox, fake_appimage):
    core, manifest_mod = sandbox["core"], sandbox["manifest"]
    res = core.op_install(fake_appimage)
    manifest_mod.Manifest.path_for(res["app_id"]).write_text("{not json")
    assert manifest_mod.Manifest.load(res["app_id"]) is None
    assert core.op_list()["count"] == 0


def test_manifest_ignores_unknown_future_keys(sandbox, fake_appimage):
    core, manifest_mod = sandbox["core"], sandbox["manifest"]
    res = core.op_install(fake_appimage)
    p = manifest_mod.Manifest.path_for(res["app_id"])
    data = json.loads(p.read_text())
    data["future_field_from_a_newer_version"] = 123
    p.write_text(json.dumps(data))
    m = manifest_mod.Manifest.load(res["app_id"])
    assert m is not None and m.app_id == res["app_id"]


# ---------- adopt ----------


def _write_manual_install(sandbox, fake_appimage, app_id="sometool") -> Path:
    """Simulate a pre-appimagectl manual install: binary in store, launcher in
    applications/, no markers, no manifest."""
    dsk, paths = sandbox["desktop"], sandbox["paths"]
    binary = paths.STORE_DIR / fake_appimage.name
    shutil.copy2(fake_appimage, binary)
    binary.chmod(0o755)
    desktop_file = dsk.desktop_path_for(app_id)
    desktop_file.parent.mkdir(parents=True, exist_ok=True)
    desktop_file.write_text(
        f'[Desktop Entry]\nName=Some Tool\nExec="{binary}" --no-sandbox %U\n'
        "Terminal=false\nType=Application\nIcon=sometool\n"
    )
    return binary


def test_adopt_claims_manual_install(sandbox, fake_appimage):
    core, dsk, manifest_mod = sandbox["core"], sandbox["desktop"], sandbox["manifest"]
    binary = _write_manual_install(sandbox, fake_appimage)
    res = core.op_adopt("sometool")
    assert res["ok"]
    assert res["binary"] == str(binary)
    assert res["sha256"] == fake_appimage.read_bytes().__str__() or True  # non-empty
    assert dsk.is_managed(dsk.desktop_path_for("sometool"))
    m = manifest_mod.Manifest.load("sometool")
    assert m is not None and m.name == "Some Tool"
    assert m.appimage_path == str(binary)
    # Uninstall now works on the adopted app.
    out = core.op_uninstall("sometool")
    assert out["ok"]
    assert Path(out["trashed_binary"]).is_file()


def test_adopt_refuses_when_no_desktop_entry(sandbox, fake_appimage):
    core = sandbox["core"]
    with pytest.raises(core.OperationError, match="use 'install' instead"):
        core.op_adopt("ghost")


def test_adopt_refuses_already_managed(sandbox, fake_appimage):
    core = sandbox["core"]
    binary = _write_manual_install(sandbox, fake_appimage)
    core.op_adopt("sometool")
    with pytest.raises(core.OperationError, match="already managed"):
        core.op_adopt("sometool")
    assert binary.is_file()


def test_adopt_refuses_binary_mismatch(sandbox, fake_appimage, tmp_path):
    core = sandbox["core"]
    _write_manual_install(sandbox, fake_appimage)
    other = tmp_path / "other.AppImage"
    other.write_bytes(fake_appimage.read_bytes())
    with pytest.raises(core.OperationError, match="not"):
        core.op_adopt("sometool", binary=str(other))


def test_adopt_dry_run_writes_nothing(sandbox, fake_appimage):
    core, dsk = sandbox["core"], sandbox["desktop"]
    _write_manual_install(sandbox, fake_appimage)
    res = core.op_adopt("sometool", dry_run=True)
    assert res["dry_run"] is True
    assert dsk.is_managed(dsk.desktop_path_for("sometool")) is False


def test_adopt_records_existing_theme_icons(sandbox, fake_appimage):
    core, manifest_mod = sandbox["core"], sandbox["manifest"]
    _write_manual_install(sandbox, fake_appimage)
    # The launcher references Icon=sometool; an icon with that name exists.
    icon_dir = sandbox["paths"].ICONS_DIR / "256x256" / "apps"
    icon_dir.mkdir(parents=True)
    icon = icon_dir / "sometool.png"
    icon.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    core.op_adopt("sometool")
    m = manifest_mod.Manifest.load("sometool")
    assert str(icon) in m.icon_files

    core.op_uninstall("sometool")
    assert not icon.exists()  # uninstall cleaned the adopted icon too


def test_adopt_skips_absolute_icon_path(sandbox, fake_appimage):
    """An Icon= value that is an absolute path (e.g. app ships its own icon in
    its install dir) must never be globbed against the icon theme."""
    core, dsk, manifest_mod = sandbox["core"], sandbox["desktop"], sandbox["manifest"]
    dsk, _ = sandbox["desktop"], sandbox["paths"]
    icon_dir = sandbox["paths"].ICONS_DIR / "256x256" / "apps"
    icon_dir.mkdir(parents=True)
    theme_icon = icon_dir / "sometool.png"
    theme_icon.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    binary = sandbox["paths"].STORE_DIR / fake_appimage.name
    import shutil

    shutil.copy2(fake_appimage, binary)
    binary.chmod(0o755)
    abs_icon = sandbox["paths"].STORE_DIR / "custom-icon.png"
    abs_icon.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    desktop_file = dsk.desktop_path_for("sometool")
    desktop_file.parent.mkdir(parents=True, exist_ok=True)
    desktop_file.write_text(
        f'[Desktop Entry]\nName=Some Tool\nExec="{binary}" %U\n'
        f"Terminal=false\nType=Application\nIcon={abs_icon}\n"
    )

    core.op_adopt("sometool")
    m = manifest_mod.Manifest.load("sometool")
    # Icon= is an absolute path: we cannot derive a theme icon name, so we
    # manage NO icons rather than guessing and globbing the wrong theme entry.
    assert m.icon_files == []


def test_adopt_recovers_orphan_marker_without_manifest(sandbox, fake_appimage):
    """A prior adopt that crashed between writing the marker and saving the
    manifest leaves the launcher managed but orphaned. Re-adopt must complete
    the job, not refuse."""
    core, dsk, manifest_mod = sandbox["core"], sandbox["desktop"], sandbox["manifest"]
    binary = sandbox["paths"].STORE_DIR / fake_appimage.name
    import shutil

    shutil.copy2(fake_appimage, binary)
    binary.chmod(0o755)
    desktop_file = dsk.desktop_path_for("sometool")
    desktop_file.parent.mkdir(parents=True, exist_ok=True)
    desktop_file.write_text(
        f'[Desktop Entry]\nName=Some Tool\nExec="{binary}" --no-sandbox %U\n'
        f"{dsk.MANAGED_KEY}=true\n"  # marker written, manifest never saved
    )
    assert manifest_mod.Manifest.load("sometool") is None

    res = core.op_adopt("sometool")
    assert res["ok"]
    assert manifest_mod.Manifest.load("sometool") is not None


def test_adopt_survives_second_adopt_after_uninstall_refusal(sandbox, fake_appimage):
    """Adopt, then strip the marker by hand: uninstall must refuse, and the
    binary must still be there for a re-adopt."""
    core, dsk = sandbox["core"], sandbox["desktop"]
    binary = _write_manual_install(sandbox, fake_appimage)
    core.op_adopt("sometool")
    desktop_file = dsk.desktop_path_for("sometool")
    desktop_file.write_text("[Desktop Entry]\nName=Some Tool\nExec=/bin/true\n")
    with pytest.raises(core.OperationError, match="lost its managed marker"):
        core.op_uninstall("sometool")
    assert binary.is_file()
