"""clean / verify / trash / scan behaviour.

The sandbox rules from test_core apply: catch errors as
sandbox["core"].OperationError, never via a module-level import. maintenance and
updates modules import core, so they must be reloaded by the sandbox before use
- fetch them from sandbox_ctx below.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _reload_mods(sandbox):
    import importlib

    for mod in ("appimagectl.maintenance", "appimagectl.updates"):
        importlib.reload(importlib.import_module(mod))
    return importlib.import_module("appimagectl.maintenance"), importlib.import_module(
        "appimagectl.updates"
    )


@pytest.fixture
def maint(sandbox):
    m, _u = _reload_mods(sandbox)
    return m


def _install_fake(sandbox, fake_appimage) -> str:
    return sandbox["core"].op_install(fake_appimage)["app_id"]


def _seed_user_data(sandbox, app_id: str, name: str) -> list[Path]:
    """Create plausible config/cache/dotdirs for a fake install."""
    cfg = sandbox["paths"].XDG_CONFIG_HOME / name
    cache = sandbox["paths"].XDG_CACHE_HOME / name.lower()
    dot = sandbox["paths"].HOME / f".{name.lower()}"
    for d in (cfg, cache, dot):
        d.mkdir(parents=True, exist_ok=True)
        (d / "data.bin").write_bytes(b"\x00" * 64)
    return [cfg, cache, dot]


def test_clean_lists_candidates_without_deleting(maint, sandbox, fake_appimage):
    app_id = _install_fake(sandbox, fake_appimage)
    m = sandbox["manifest"].Manifest.load(app_id)
    dirs = _seed_user_data(sandbox, app_id, m.name)

    res = maint.op_clean(app_id)  # no --yes -> dry plan
    assert res["dry_run"] is True
    assert res["found"] == 3
    kinds = sorted(e["kind"] for e in res["plan"])
    assert kinds == ["cache", "config", "dot"]
    for d in dirs:
        assert d.exists()  # nothing moved


def test_clean_refuses_without_yes_and_keeps_data(maint, sandbox, fake_appimage):
    app_id = _install_fake(sandbox, fake_appimage)
    m = sandbox["manifest"].Manifest.load(app_id)
    dirs = _seed_user_data(sandbox, app_id, m.name)
    maint.op_clean(app_id)  # no --yes: must not move anything
    assert all(d.exists() for d in dirs)


def test_clean_moves_data_to_trash_with_yes(maint, sandbox, fake_appimage):
    app_id = _install_fake(sandbox, fake_appimage)
    m = sandbox["manifest"].Manifest.load(app_id)
    dirs = _seed_user_data(sandbox, app_id, m.name)

    res = maint.op_clean(app_id, yes=True)
    assert res["ok"]
    assert len(res["moved_to_trash"]) == 3
    assert all(not d.exists() for d in dirs)

    trash_data = sandbox["paths"].TRASH_DIR / "data" / app_id
    assert trash_data.is_dir()
    assert len(list(trash_data.iterdir())) == 3


def test_clean_unknown_app_raises(maint, sandbox):
    with pytest.raises(sandbox["core"].OperationError, match="unknown app_id"):
        maint.op_clean("ghost")


def test_verify_ok_on_healthy_install(maint, sandbox, fake_appimage):
    app_id = _install_fake(sandbox, fake_appimage)
    res = maint.op_verify(app_id)
    assert res["ok"] is True
    assert all(c["ok"] for c in res["checks"])


def test_verify_catches_modified_binary(maint, sandbox, fake_appimage):
    app_id = _install_fake(sandbox, fake_appimage)
    m = sandbox["manifest"].Manifest.load(app_id)
    binary = Path(m.appimage_path)
    binary.write_bytes(binary.read_bytes() + b"tamper")

    res = maint.op_verify(app_id)
    assert res["ok"] is False
    sha_check = [c for c in res["checks"] if c["check"] == "binary_sha256"][0]
    assert sha_check["ok"] is False


def test_verify_catches_missing_binary(maint, sandbox, fake_appimage):
    app_id = _install_fake(sandbox, fake_appimage)
    Path(sandbox["manifest"].Manifest.load(app_id).appimage_path).unlink()
    res = maint.op_verify(app_id)
    assert res["ok"] is False
    assert any(c["check"] == "binary_sha256" and not c["ok"] for c in res["checks"])


def test_trash_restore_roundtrip(maint, sandbox, fake_appimage):
    core = sandbox["core"]
    app_id = _install_fake(sandbox, fake_appimage)
    name = Path(core.op_list()["apps"][0]["binary"]).name

    core.op_uninstall(app_id)  # binary -> trash
    listing = maint.op_trash_list()
    assert listing["binaries_total"] == 1

    res = maint.op_trash_restore(name)
    assert res["ok"]
    assert Path(res["restored_to"]).is_file()
    assert listing["binaries"][0]["name"] == name


def test_trash_restore_refuses_collision(maint, sandbox, fake_appimage):
    core = sandbox["core"]
    app_id = _install_fake(sandbox, fake_appimage)
    name = Path(core.op_list()["apps"][0]["binary"]).name

    core.op_uninstall(app_id)
    # recreate the same binary at the store location -> restore must refuse
    (sandbox["store"] / name).write_bytes(fake_appimage.read_bytes())
    with pytest.raises(sandbox["core"].OperationError, match="refusing to overwrite"):
        maint.op_trash_restore(name)


def test_trash_empty_requires_yes(maint, sandbox, fake_appimage):
    core = sandbox["core"]
    app_id = _install_fake(sandbox, fake_appimage)
    core.op_uninstall(app_id)

    res = maint.op_trash_empty()  # no --yes
    assert res["dry_run"] is True
    assert maint.op_trash_list()["binaries_total"] == 1  # still there

    res = maint.op_trash_empty(yes=True)
    assert res["ok"]
    assert maint.op_trash_list()["binaries_total"] == 0


def test_scan_finds_appimages_and_skips_non(sandbox, fake_appimage, tmp_path):
    maint, _ = _reload_mods(sandbox)
    d = tmp_path / "scanme"
    d.mkdir()
    (d / "Real.AppImage").write_bytes(fake_appimage.read_bytes())
    (d / "Fake.appimage").write_text("not an appimage")

    res = maint.op_scan([str(d)])
    assert len(res["found"]) == 1
    assert res["found"][0]["name"] == "Real.AppImage"
    assert len(res["skipped"]) == 1


def test_scan_ignores_missing_dir(sandbox, tmp_path):
    maint, _ = _reload_mods(sandbox)
    res = maint.op_scan([str(tmp_path / "nope")])
    assert res["ok"] and res["found"] == []


def test_scan_default_roots_cover_common_places(sandbox, fake_appimage):
    """The no-argument scan must reach the places an AppImage can hide
    (downloads, .local/bin), not just the store."""
    maint, _ = _reload_mods(sandbox)
    dl = sandbox["paths"].HOME / "Downloads"
    dl.mkdir()
    (dl / "Downloaded.AppImage").write_bytes(fake_appimage.read_bytes())
    bin_dir = sandbox["paths"].HOME / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "cli.appimage").write_bytes(fake_appimage.read_bytes())

    # HOME is redirected in sandbox, so the default roots resolve inside it.
    res = maint.op_scan(None)
    names = {f["name"] for f in res["found"]}
    assert "Downloaded.AppImage" in names
    assert "cli.appimage" in names


# ---------- updates (no network) ----------


def test_check_update_reports_not_updatable_without_upd_info(sandbox, fake_appimage):
    maint, updates = _reload_mods(sandbox)
    app_id = _install_fake(sandbox, fake_appimage)
    res = updates.op_check_update(app_id)
    assert res["ok"] and res["updatable"] is False
    assert "Electron" in res["reason"] or "update information" in res["reason"]


def test_update_without_source_does_nothing(sandbox, fake_appimage):
    maint, updates = _reload_mods(sandbox)
    app_id = _install_fake(sandbox, fake_appimage)
    res = updates.op_update(app_id)
    assert res["downloaded"] is False
    # the binary is untouched
    binary = Path(sandbox["manifest"].Manifest.load(app_id).appimage_path)
    assert binary.exists()