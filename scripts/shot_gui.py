#!/usr/bin/env python3
"""Capture real screenshots of the AppImage Control GUI under an Xvfb display.

Usage:
  DISPLAY=:77 python3 shot_gui.py <out.png>
  DISPLAY=:77 python3 shot_gui.py <out.png> --install-dialog

The install-dialog mode stubs op_inspect with demo data BEFORE the GUI starts,
so the dialog shows a realistic inspected app without touching real files.
Run inside the demo XDG env built by make_demo_env.py.
"""
import os
import subprocess
import sys
import time

os.environ.setdefault("GDK_BACKEND", "x11")

out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/appimagectl-main.png"
show_install = "--install-dialog" in sys.argv

if show_install:
    # Stub inspect before the GUI imports/uses it: thread worker will render
    # this demo payload in the dialog. No real file is opened.
    from appimagectl import core as _core

    _DEMO_APP = {
        "path": "/tmp/appimagectl-demo/Downloads/Timeline-3.14.2-x86_64.AppImage",
        "name": "Timeline",
        "app_id": "timeline",
        "size_bytes": 123456789,
        "size_human": "117.7 MiB",
        "appimage_type": 2,
        "sha256": "c" * 64,
        "executable": True,
        "runtime_version": "effcebc",
        "internal_version": "3.14.2",
        "comment": "Time-tracking dashboard",
        "categories": "Office;",
        "mime_types": [],
        "startup_wm_class": "Timeline",
        "icon_name": "timeline",
        "icon_files_in_payload": ["usr/share/icons/hicolor/256x256/apps/timeline.png"],
        "update_information": None,
        "updatable": False,
    }
    _core.op_inspect = lambda path, **kw: {"ok": True, "action": "inspect", "app": _DEMO_APP}

    import gi  # noqa: E402

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import GLib  # noqa: E402

    from appimagectl.gui.app import AppImageCtlApp, InstallDialog  # noqa: E402

    app = AppImageCtlApp()
    app.do_activate()
    win = app.props.active_window

    from pathlib import Path

    # Present the dialog from inside the loop so window mapping happens in
    # order; presenting before app.run() can leave it unmapped on Xvfb.
    def _show_dialog():
        dialog = InstallDialog(win, Path("Timeline-3.14.2-x86_64.AppImage"))
        dialog.present()
        return False

    GLib.timeout_add(500, _show_dialog)

    # Screenshot inside the GLib loop, then quit the app cleanly.
    def _shot_then_quit():
        import mss
        from PIL import Image

        with mss.mss() as sct:
            mon = sct.monitors[0]
            img = sct.grab(mon)
            im = Image.frombytes("RGB", img.size, img.rgb)
            im.save(out)
            print(f"saved {out} {im.size}")
        app.quit()
        return False

    GLib.timeout_add(6000, _shot_then_quit)
    app.run(None)
    sys.exit(0)
else:
    import shutil

    gui_bin = shutil.which("appimagectl")
    if not gui_bin:
        print("appimagectl not on PATH; use ./.venv/bin/appimagectl and retry")
        sys.exit(2)
    proc = subprocess.Popen([gui_bin, "gui"])
    time.sleep(6)  # window map + render + async reload

import mss  # noqa: E402
from PIL import Image  # noqa: E402

with mss.mss() as sct:
    mon = sct.monitors[0]
    img = sct.grab(mon)
    im = Image.frombytes("RGB", img.size, img.rgb)
    im.save(out)
    print(f"saved {out} {im.size}")

proc.terminate()
try:
    proc.wait(timeout=5)
except subprocess.TimeoutExpired:
    proc.kill()