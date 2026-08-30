"""GTK4 + libadwaita front end for appimagectl.

Layout is a two-pane split: the left list is the inventory, the right pane is
the evidence for the selected row. Every value shown comes from core; the GUI
computes nothing on its own, so what you read here is what the CLI reports.

Long operations (inspect, install, uninstall) run on worker threads and post
results back with GLib.idle_add, because they hash and extract from ~180 MB
files and would otherwise freeze the frame.
"""

from __future__ import annotations

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # type: ignore[attr-defined]  # noqa: E402

from .. import __version__  # noqa: E402
from ..core import (  # noqa: E402
    OperationError,
    op_doctor,
    op_inspect,
    op_install,
    op_list,
    op_run,
    op_uninstall,
)
from ..maintenance import op_clean, op_trash_list, op_verify  # noqa: E402
from ..updates import op_check_update  # noqa: E402

APP_ID = "io.github.appimagectl.appimagectl"


def _row(group: Adw.PreferencesGroup, title: str, value: str | None) -> None:
    """One label/value line. Values are selectable because the whole point is
    copying a hash or a path out of here."""
    row = Adw.ActionRow(title=title, subtitle=value or "-")
    row.set_subtitle_selectable(True)
    row.add_css_class("property")
    group.add(row)


class DetailPane(Gtk.Box):
    """Right-hand pane: evidence for one app, plus its actions."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.window = window
        self.app_entry: dict | None = None

        self.scroller = Gtk.ScrolledWindow(vexpand=True)
        self.content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            margin_top=18,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
        )
        self.scroller.set_child(self.content)
        self.append(self.scroller)
        self.show_placeholder()

    def _clear(self) -> None:
        while child := self.content.get_first_child():
            self.content.remove(child)

    def show_placeholder(self, text: str = "Select an app") -> None:
        self._clear()
        status = Adw.StatusPage(title=text, icon_name="application-x-executable-symbolic")
        status.set_vexpand(True)
        self.content.append(status)

    def show_app(self, entry: dict) -> None:
        """Render a row from `op_list`."""
        self.app_entry = entry
        self._clear()

        header = Adw.PreferencesGroup(title=entry["name"], description=entry["app_id"])
        _row(header, "Version", entry.get("version"))
        _row(header, "Binary", entry["binary"])
        _row(header, "Desktop entry", entry["desktop_file"])
        _row(header, "Installed", entry.get("installed_at"))
        self.content.append(header)

        state = Adw.PreferencesGroup(title="State")
        healthy = entry.get("healthy")
        _row(state, "Files on disk", "all present" if healthy else "MISSING FILES")
        if entry.get("missing_files"):
            _row(state, "Missing", "\n".join(entry["missing_files"]))
        managed = entry.get("desktop_managed")
        _row(
            state,
            "Launcher ownership",
            "managed by appimagectl" if managed else "NOT managed - will not be removed",
        )
        reg = entry.get("registered_in_shell")
        _row(
            state,
            "Visible to shell",
            "yes" if reg else ("unknown" if reg is None else "no"),
        )
        _row(state, "Icon sizes installed", str(entry.get("icon_count", 0)))
        _row(
            state,
            "Self-update metadata",
            "present" if entry.get("updatable") else "absent (Electron-built AppImages omit it)",
        )
        self.content.append(state)

        actions = Adw.PreferencesGroup(title="Actions")

        launch = Adw.ActionRow(title="Launch", subtitle="Start the app detached")
        btn_launch = Gtk.Button(label="Launch", valign=Gtk.Align.CENTER)
        btn_launch.connect("clicked", self._on_launch)
        launch.add_suffix(btn_launch)
        actions.add(launch)

        verify = Adw.ActionRow(
            title="Verify",
            subtitle="Re-hash the binary and check every installed file",
        )
        btn_verify = Gtk.Button(label="Verify", valign=Gtk.Align.CENTER)
        btn_verify.connect("clicked", self._on_verify)
        verify.add_suffix(btn_verify)
        actions.add(verify)

        upd = Adw.ActionRow(
            title="Check update",
            subtitle="Query the embedded update source (GitHub releases)",
        )
        btn_upd = Gtk.Button(label="Check", valign=Gtk.Align.CENTER)
        btn_upd.connect("clicked", self._on_check_update)
        upd.add_suffix(btn_upd)
        actions.add(upd)

        clean = Adw.ActionRow(
            title="Clean user data",
            subtitle="Move config/cache/data/dotdirs to trash",
        )
        btn_clean = Gtk.Button(label="Clean", valign=Gtk.Align.CENTER)
        btn_clean.add_css_class("destructive-action")
        btn_clean.connect("clicked", self._on_clean)
        clean.add_suffix(btn_clean)
        actions.add(clean)

        rm = Adw.ActionRow(
            title="Uninstall",
            subtitle="Delete launcher and icons; move the binary to trash",
        )
        btn_rm = Gtk.Button(label="Uninstall", valign=Gtk.Align.CENTER)
        btn_rm.add_css_class("destructive-action")
        btn_rm.connect("clicked", self._on_uninstall)
        rm.add_suffix(btn_rm)
        actions.add(rm)

        self.content.append(actions)

    def _on_launch(self, _btn: Gtk.Button) -> None:
        assert self.app_entry
        try:
            res = op_run(self.app_entry["app_id"])
            self.window.toast(f"Launched {res['app_id']} (pid {res['pid']})")
        except OperationError as exc:
            self.window.toast(str(exc))

    def _on_verify(self, _btn: Gtk.Button) -> None:
        assert self.app_entry
        app_id = self.app_entry["app_id"]
        self.window.begin_operation(f"Verifying {app_id}")

        def work() -> None:
            try:
                res = op_verify(app_id)
                bad = [c["check"] for c in res["checks"] if not c["ok"]]
                msg = f"{app_id}: verify OK" if res["ok"] else (
                    f"{app_id}: BROKEN - {', '.join(bad)}"
                )
            except OperationError as exc:
                msg = str(exc)
            GLib.idle_add(self.window.finish_operation, msg)

        threading.Thread(target=work, daemon=True).start()

    def _on_check_update(self, _btn: Gtk.Button) -> None:
        assert self.app_entry
        app_id = self.app_entry["app_id"]
        self.window.begin_operation(f"Checking updates for {app_id}")

        def work() -> None:
            try:
                res = op_check_update(app_id)
                if not res.get("updatable"):
                    msg = f"{app_id}: not updatable ({res['reason'][:60]}...)"
                elif not res.get("found"):
                    msg = f"{app_id}: no matching release asset"
                elif res.get("available"):
                    msg = (
                        f"{app_id}: update available "
                        f"{res['current_version']} -> {res['latest_version']} "
                        f"({res['asset_name']})"
                    )
                else:
                    msg = f"{app_id}: up to date ({res['latest_version']})"
            except OperationError as exc:
                msg = str(exc)
            GLib.idle_add(self.window.finish_operation, msg)

        threading.Thread(target=work, daemon=True).start()

    def _on_clean(self, _btn: Gtk.Button) -> None:
        assert self.app_entry
        entry = self.app_entry
        app_id = entry["app_id"]
        try:
            plan = op_clean(app_id)  # no --yes: dry plan
        except OperationError as exc:
            self.window.toast(str(exc))
            return
        if not plan["plan"]:
            self.window.toast(f"No user data for {app_id}")
            return
        body_lines = [f"Will move to trash ({plan['found']} location(s)):"]
        body_lines += [
            f"  {e['kind']:<6} {e['path']}  ({e['size_human']})" for e in plan["plan"]
        ]
        dialog = Adw.MessageDialog(
            transient_for=self.window,
            heading=f"Clean data for {entry['name']}?",
            body="\n".join(body_lines) + "\n\nData is moved to the appimagectl trash, not deleted.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clean", "Clean")
        dialog.set_response_appearance("clean", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._on_clean_response, app_id)
        dialog.present()

    def _on_clean_response(self, dialog: Adw.MessageDialog, response: str, app_id: str) -> None:
        dialog.close()
        if response != "clean":
            return

        def work() -> None:
            try:
                res = op_clean(app_id, yes=True)
                msg = f"Cleaned {app_id}: {len(res['moved_to_trash'])} location(s) -> trash"
            except OperationError as exc:
                msg = str(exc)
            GLib.idle_add(self.window.finish_operation, msg)

        self.window.begin_operation(f"Cleaning {app_id}")
        threading.Thread(target=work, daemon=True).start()

    def _on_uninstall(self, _btn: Gtk.Button) -> None:
        assert self.app_entry
        entry = self.app_entry
        app_id = entry["app_id"]
        try:
            plan = op_uninstall(app_id, dry_run=True)
        except OperationError as exc:
            self.window.toast(str(exc))
            return

        body_lines = ["These files will be deleted:"]
        body_lines += [f"  {p}" for p in plan["plan"]["delete"]]
        body_lines.append("")
        body_lines.append(f"Binary moved to trash:\n  {plan['plan']['trash']['to']}")

        dialog = Adw.MessageDialog(
            transient_for=self.window,
            heading=f"Uninstall {entry['name']}?",
            body="\n".join(body_lines),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Uninstall")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._on_uninstall_response, app_id)
        dialog.present()

    def _on_uninstall_response(self, dialog: Adw.MessageDialog, response: str, app_id: str) -> None:
        dialog.close()
        if response != "remove":
            return

        def work() -> None:
            try:
                res = op_uninstall(app_id)
                msg = f"Removed {app_id}: {len(res['removed'])} file(s)"
            except OperationError as exc:
                msg = str(exc)
            GLib.idle_add(self.window.finish_operation, msg)

        self.window.begin_operation(f"Uninstalling {app_id}")
        threading.Thread(target=work, daemon=True).start()


class InstallDialog(Adw.Window):
    """Inspect-then-confirm install flow. Nothing is written until Install is
    pressed, and the dialog shows exactly what will be written."""

    def __init__(self, parent: MainWindow, path: Path) -> None:
        super().__init__(
            transient_for=parent,
            modal=True,
            title="Install AppImage",
            default_width=620,
            default_height=560,
        )
        self.parent_window = parent
        self.path = path
        self.info: dict | None = None

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_end_title_buttons=False)
        self.btn_cancel = Gtk.Button(label="Cancel")
        self.btn_cancel.connect("clicked", lambda *_: self.close())
        self.btn_install = Gtk.Button(label="Install")
        self.btn_install.add_css_class("suggested-action")
        self.btn_install.set_sensitive(False)
        self.btn_install.connect("clicked", self._on_install)
        header.pack_start(self.btn_cancel)
        header.pack_end(self.btn_install)
        toolbar.add_top_bar(header)

        self.body = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            margin_top=18,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
        )
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(self.body)
        toolbar.set_content(scroller)
        self.set_content(toolbar)

        self.spinner_page = Adw.StatusPage(
            title="Reading AppImage",
            description=f"Hashing and extracting metadata from {path.name}",
        )
        spinner = Gtk.Spinner(spinning=True, width_request=32, height_request=32)
        self.spinner_page.set_child(spinner)
        self.body.append(self.spinner_page)

        threading.Thread(target=self._inspect_worker, daemon=True).start()

    def _inspect_worker(self) -> None:
        try:
            res = op_inspect(self.path)
            GLib.idle_add(self._show_info, res["app"])
        except OperationError as exc:
            GLib.idle_add(self._show_error, str(exc))

    def _clear_body(self) -> None:
        while child := self.body.get_first_child():
            self.body.remove(child)

    def _show_error(self, message: str) -> None:
        self._clear_body()
        self.body.append(
            Adw.StatusPage(
                title="Cannot install",
                description=message,
                icon_name="dialog-warning-symbolic",
            )
        )

    def _show_info(self, app: dict) -> None:
        self.info = app
        self._clear_body()

        g = Adw.PreferencesGroup(title=app["name"], description=f"app id: {app['app_id']}")
        _row(g, "Source", app["path"])
        _row(g, "Size", f"{app['size_human']}  (type-{app['appimage_type']} AppImage)")
        _row(g, "SHA-256", app["sha256"])
        _row(g, "Internal version", app["internal_version"])
        _row(g, "Runtime", app["runtime_version"])
        self.body.append(g)

        d = Adw.PreferencesGroup(title="Declared by the AppImage")
        _row(d, "Comment", app["comment"])
        _row(d, "Categories", app["categories"])
        _row(d, "MIME handlers", ", ".join(app["mime_types"]) or None)
        _row(d, "Window class", app["startup_wm_class"])
        _row(d, "Icons in payload", str(len(app["icon_files_in_payload"])))
        self.body.append(d)

        opts = Adw.PreferencesGroup(
            title="Install options",
            description="Exec arguments are written verbatim into the .desktop entry.",
        )
        self.args_row = Adw.EntryRow(title="Exec arguments")
        self.args_row.set_text("--no-sandbox")
        opts.add(self.args_row)
        self.move_row = Adw.SwitchRow(
            title="Delete source after install",
            subtitle=f"Remove {self.path.name} from its current folder",
        )
        opts.add(self.move_row)
        self.body.append(opts)

        if not app["icon_files_in_payload"]:
            warn = Adw.PreferencesGroup(title="Warning")
            _row(warn, "No icons", "The launcher will show a generic icon.")
            self.body.append(warn)

        self.btn_install.set_sensitive(True)

    def _on_install(self, _btn: Gtk.Button) -> None:
        args = self.args_row.get_text().strip()
        move = self.move_row.get_active()
        self.btn_install.set_sensitive(False)
        self.btn_cancel.set_sensitive(False)
        self._clear_body()
        page = Adw.StatusPage(title="Installing", description=self.path.name)
        page.set_child(Gtk.Spinner(spinning=True, width_request=32, height_request=32))
        self.body.append(page)

        def work() -> None:
            try:
                res = op_install(self.path, extra_args=args, keep_source=not move)
                msg = (
                    f"Installed {res['app']['name']}: "
                    f"{res['installed']['icon_count']} icon size(s), "
                    f"validator {res['validator']}"
                )
            except OperationError as exc:
                msg = f"Install failed: {exc}"
            GLib.idle_add(self._finish, msg)

        threading.Thread(target=work, daemon=True).start()

    def _finish(self, message: str) -> None:
        self.close()
        self.parent_window.finish_operation(message)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(
            application=app,
            title="AppImage Control",
            default_width=1040,
            default_height=680,
        )

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        split = Adw.NavigationSplitView()
        self.toast_overlay.set_child(split)

        # ---- sidebar: inventory
        side_toolbar = Adw.ToolbarView()
        side_header = Adw.HeaderBar()
        btn_add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Install an AppImage")
        btn_add.connect("clicked", self._on_add)
        side_header.pack_start(btn_add)
        btn_refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh")
        btn_refresh.connect("clicked", lambda *_: self.reload())
        side_header.pack_end(btn_refresh)

        menu = Gio.Menu()
        menu.append("Trash", "win.trash")
        menu.append("Doctor", "win.doctor")
        menu.append("About", "win.about")
        btn_menu = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu)
        side_header.pack_end(btn_menu)
        side_toolbar.add_top_bar(side_header)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("navigation-sidebar")
        self.listbox.connect("row-selected", self._on_row_selected)
        side_scroller = Gtk.ScrolledWindow(vexpand=True)
        side_scroller.set_child(self.listbox)
        side_toolbar.set_content(side_scroller)

        self.status_label = Gtk.Label(
            label="", xalign=0.0, margin_start=12, margin_end=12, margin_top=6, margin_bottom=6
        )
        self.status_label.add_css_class("dim-label")
        self.status_label.add_css_class("caption")
        side_toolbar.add_bottom_bar(self.status_label)

        split.set_sidebar(Adw.NavigationPage(title="Installed", child=side_toolbar))

        # ---- content: detail
        content_toolbar = Adw.ToolbarView()
        content_toolbar.add_top_bar(Adw.HeaderBar())
        self.detail = DetailPane(self)
        content_toolbar.set_content(self.detail)
        split.set_content(Adw.NavigationPage(title="Details", child=content_toolbar))

        for name, cb in (
            ("doctor", self._on_doctor),
            ("trash", self._on_trash),
            ("about", self._on_about),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", cb)
            self.add_action(action)

        self.rows: list[dict] = []
        self.reload()

    # ---- helpers

    def toast(self, message: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=message, timeout=4))

    def begin_operation(self, label: str) -> None:
        self.status_label.set_label(label)

    def finish_operation(self, message: str) -> bool:
        self.status_label.set_label("")
        self.toast(message)
        self.reload()
        return False  # so it can be used directly with GLib.idle_add

    def reload(self) -> None:
        while child := self.listbox.get_first_child():
            self.listbox.remove(child)

        data = op_list(check=True)
        self.rows = data["apps"]

        for entry in self.rows:
            row = Adw.ActionRow(title=entry["name"], subtitle=entry["app_id"])
            if not entry.get("healthy", True):
                badge = Gtk.Label(label="damaged")
                badge.add_css_class("error")
                badge.add_css_class("caption")
                row.add_suffix(badge)
            elif entry.get("registered_in_shell") is False:
                badge = Gtk.Label(label="unlisted")
                badge.add_css_class("warning")
                badge.add_css_class("caption")
                row.add_suffix(badge)
            self.listbox.append(row)

        unmanaged = data["unmanaged_in_store"]
        parts = [f"{data['count']} managed"]
        if unmanaged:
            parts.append(f"{len(unmanaged)} unmanaged in store")
        self.status_label.set_label(" · ".join(parts))

        if self.rows:
            first = self.listbox.get_row_at_index(0)
            if first:
                self.listbox.select_row(first)
        else:
            self.detail.show_placeholder("No AppImages installed")

    # ---- callbacks

    def _on_row_selected(self, _lb: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None:
            return
        idx = row.get_index()
        if 0 <= idx < len(self.rows):
            self.detail.show_app(self.rows[idx])

    def _on_add(self, _btn: Gtk.Button) -> None:
        dialog = Gtk.FileDialog(title="Choose an AppImage")
        filt = Gtk.FileFilter()
        filt.set_name("AppImage")
        filt.add_pattern("*.AppImage")
        filt.add_pattern("*.appimage")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)
        dialog.set_default_filter(filt)
        downloads = Path.home() / "Downloads"
        if downloads.is_dir():
            dialog.set_initial_folder(Gio.File.new_for_path(str(downloads)))
        dialog.open(self, None, self._on_file_chosen)

    def _on_file_chosen(self, dialog: Gtk.FileDialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return  # user cancelled
        if gfile is None:
            return
        InstallDialog(self, Path(gfile.get_path())).present()

    def _on_doctor(self, *_args) -> None:
        report = op_doctor()
        lines = [
            f"store dir: {report['store_dir']}",
            f"managed apps: {report['managed_apps']}",
            f"PyGObject: {'yes' if report['pygobject'] else 'no'}",
            "",
        ]
        lines += [f"{name}: {path or 'MISSING'}" for name, path in report["tools"].items()]
        if report["unmanaged_in_store"]:
            lines.append("")
            lines.append("unmanaged AppImages in store:")
            lines += [f"  {u['path']} ({u['size_human']})" for u in report["unmanaged_in_store"]]
        if report["problems"]:
            lines.append("")
            lines.append("problems:")
            lines += [f"  - {p}" for p in report["problems"]]

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Doctor" + ("" if report["ok"] else " - problems found"),
            body="\n".join(lines),
        )
        dialog.add_response("close", "Close")
        dialog.present()

    def _on_trash(self, *_args) -> None:
        listing = op_trash_list()
        lines = [f"Binaries ({listing['binaries_total']}):"]
        lines += [
            f"  {b['name']}  ({b['size_human']})" for b in listing["binaries"]
        ] or ["  (none)"]
        lines.append(f"\nCleaned data ({listing['data_total']}):")
        lines += [
            f"  {d['app_id']}  {d['path']}  ({d['size_human']})" for d in listing["data_dirs"]
        ] or ["  (none)"]
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Trash",
            body="\n".join(lines)
            + '\n\nRestore binaries with "appimagectl trash restore <name>". '
            + 'Empty permanently with "appimagectl trash empty --yes".',
        )
        dialog.add_response("close", "Close")
        dialog.present()

    def _on_about(self, *_args) -> None:
        about = Adw.AboutWindow(
            transient_for=self,
            application_name="AppImage Control",
            application_icon="application-x-executable",
            version=__version__,
            developer_name="appimagectl contributors",
            comments=(
                "Installs, inspects and removes AppImages. Only launchers it "
                "created are ever deleted."
            ),
            license_type=Gtk.License.MIT_X11,
        )
        about.present()


class AppImageCtlApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.connect("open", self._on_open)

    def do_activate(self) -> None:  # noqa: N802 - GObject naming
        win = self.props.active_window or MainWindow(self)
        win.present()

    def _on_open(self, _app, files, _n_files, _hint) -> None:
        self.do_activate()
        win = self.props.active_window
        for gfile in files:
            path = gfile.get_path()
            if path and path.lower().endswith(".appimage"):
                InstallDialog(win, Path(path)).present()
                break


def run_gui() -> int:
    return AppImageCtlApp().run(None)
