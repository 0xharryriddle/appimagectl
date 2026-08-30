"""CLI entry point. Human-readable by default, --json for machines."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .core import (
    OperationError,
    op_adopt,
    op_doctor,
    op_inspect,
    op_install,
    op_list,
    op_run,
    op_uninstall,
)
from .maintenance import (
    op_clean,
    op_scan,
    op_trash_empty,
    op_trash_list,
    op_trash_restore,
    op_verify,
)
from .updates import op_check_update, op_update


def _emit(payload: dict, as_json: bool, renderer) -> int:
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        renderer(payload)
    return 0 if payload.get("ok") else 1


def _r_inspect(p: dict) -> None:
    a = p["app"]
    print(f"{a['name']}  ({a['app_id']})")
    print(f"  path            {a['path']}")
    print(f"  size            {a['size_human']}   type-{a['appimage_type']} AppImage")
    print(f"  sha256          {a['sha256']}")
    print(f"  executable      {a['executable']}")
    print(f"  internal ver    {a['internal_version'] or '-'}")
    print(f"  runtime         {a['runtime_version'] or '-'}")
    print(f"  categories      {a['categories'] or '-'}")
    print(f"  mime types      {', '.join(a['mime_types']) or '-'}")
    print(f"  wm class        {a['startup_wm_class'] or '-'}")
    print(f"  icons in payload {len(a['icon_files_in_payload'])}")
    print(f"  self-update     {a['update_information'] or 'not available'}")


def _r_install(p: dict) -> None:
    if p.get("dry_run"):
        print(f"DRY RUN - would install {p['app']['name']} as '{p['app_id']}'")
        pl = p["plan"]
        print(f"  copy    {pl['copy']['from']}")
        print(f"       -> {pl['copy']['to']}")
        print(f"  desktop {pl['desktop_file']}")
        print(f"  manifest {pl['manifest']}")
        return
    i = p["installed"]
    print(f"Installed {p['app']['name']}  (app_id: {p['app_id']})")
    print(f"  binary       {i['binary']}")
    print(f"  desktop      {i['desktop_file']}")
    print(f"  icons        {i['icon_count']} size(s)")
    print(f"  sha256       {p['app']['sha256']} (verified after copy)")
    print(f"  validator    {p['validator']}")
    reg = p["registered_in_shell"]
    print(f"  shell sees it {'yes' if reg else ('unknown' if reg is None else 'NO')}")
    for n in p.get("notes", []):
        print(f"  note         {n}")
    for w in p.get("warnings", []):
        print(f"  WARNING      {w}")


def _r_uninstall(p: dict) -> None:
    if p.get("dry_run"):
        print(f"DRY RUN - would uninstall '{p['app_id']}'")
        for f in p["plan"]["delete"]:
            print(f"  delete  {f}")
        print(f"  trash   {p['plan']['trash']['from']}")
        print(f"       -> {p['plan']['trash']['to']}")
        return
    print(f"Uninstalled '{p['app_id']}'")
    for f in p["removed"]:
        print(f"  removed  {f}")
    for f in p["already_absent"]:
        print(f"  absent   {f}")
    if p["trashed_binary"]:
        print(f"  trashed  {p['trashed_binary']}")
    still = p["still_registered_in_shell"]
    if still:
        print("  WARNING  shell still lists this app (cache may lag one session)")


def _r_adopt(p: dict) -> None:
    if p.get("dry_run"):
        print(f"DRY RUN - would adopt '{p['app_id']}'")
        print(f"  desktop {p['plan']['desktop_file']}")
        print(f"  binary  {p['plan']['binary']}")
        for k in p["plan"]["add_keys"]:
            print(f"  add     {k}")
        return
    print(f"Adopted '{p['app_id']}'")
    print(f"  binary       {p['binary']}  ({p['size_human']})")
    print(f"  sha256       {p['sha256']}")
    print(f"  desktop      {p['desktop_file']}")
    reg = p["registered_in_shell"]
    print(f"  shell sees it {'yes' if reg else ('unknown' if reg is None else 'NO')}")


def _r_list(p: dict) -> None:
    if not p["apps"]:
        print(f"No managed apps. Store: {p['store_dir']}")
    else:
        print(f"{'APP_ID':<24} {'NAME':<24} {'VER':<12} {'STATE':<10} BINARY")
        for a in p["apps"]:
            state = "ok" if a.get("healthy", True) else "DAMAGED"
            if a.get("registered_in_shell") is False:
                state = "unlisted"
            print(
                f"{a['app_id']:<24} {a['name'][:23]:<24} "
                f"{(a['version'] or '-')[:11]:<12} {state:<10} {a['binary']}"
            )
    if p["unmanaged_in_store"]:
        print(f"\nAppImages in {p['store_dir']} not managed by appimagectl:")
        for u in p["unmanaged_in_store"]:
            print(f"  {u['path']}  ({u['size_human']})")


def _r_doctor(p: dict) -> None:
    print(f"appimagectl doctor  ({'OK' if p['ok'] else 'PROBLEMS FOUND'})")
    print(f"  store dir     {p['store_dir']}")
    print(f"  managed apps  {p['managed_apps']}")
    print(f"  PyGObject     {'yes' if p['pygobject'] else 'no (GUI unavailable)'}")
    for name, path in p["tools"].items():
        print(f"  {name:<26} {path or 'MISSING'}")
    if p["problems"]:
        print("  problems:")
        for pr in p["problems"]:
            print(f"    - {pr}")


def _r_run(p: dict) -> None:
    print(f"Launched {p['app_id']} (pid {p['pid']})")


def _r_clean(p: dict) -> None:
    if p.get("dry_run"):
        print(f"Would clean data for '{p['app_id']}':")
        for e in p["plan"]:
            print(f"  {e['kind']:<7} {e['path']}  ({e['size_human']})")
        print(f"  {p['found']} dir(s) would move to trash")
        if p.get("running_pids"):
            print(f"  WARNING running pids: {p['running_pids']}")
        return
    if "moved_to_trash" in p:
        print(f"Cleaned '{p['app_id']}' - moved to trash:")
        for m in p["moved_to_trash"]:
            print(f"  {m['kind']:<7} {m['from']}  ({m['size_human']})")
        if p.get("running_pids"):
            print(f"  WARNING: {len(p['running_pids'])} process(es) still running")
    else:
        print(f"No user data found for '{p['app_id']}'")


def _r_trash_list(p: dict) -> None:
    if not p["binaries"] and not p["data_dirs"]:
        print("Trash is empty.")
        return
    print(f"Binaries ({p['binaries_total']}):")
    for b in p["binaries"]:
        print(f"  {b['name']}  ({b['size_human']})")
    print(f"\nData ({p['data_total']}):")
    for d in p["data_dirs"]:
        print(f"  {d['app_id']}  {d['path']}  ({d['size_human']})")


def _r_trash_restore(p: dict) -> None:
    print(f"Restored {p['name']} -> {p['restored_to']}")


def _r_trash_empty(p: dict) -> None:
    if p.get("dry_run"):
        print("Would permanently delete:")
        for b in p["binaries"]:
            print(f"  {b['path']}  ({b['size_human']})")
        for d in p["data_dirs"]:
            print(f"  {d['path']}  ({d['size_human']})")
        if not p["binaries"] and not p["data_dirs"]:
            print("  (trash is empty)")
        print("add --yes to execute")
        return
    for f in p["permanently_deleted"]:
        print(f"  deleted  {f}")


def _r_verify(p: dict) -> None:
    print(f"verify {p['app_id']}: {'OK' if p['ok'] else 'PROBLEMS'}")
    for c in p["checks"]:
        mark = "ok " if c["ok"] else "BAD"
        detail = ""
        if c.get("missing") and c["missing"]:
            detail = f"  missing: {c['missing']}"
        if c["check"] == "shell_registration" and c["ok"] is None:
            mark, detail = "?? ", " (PyGObject unavailable)"
        print(f"  [{mark}] {c['check']}{detail}")


def _r_check_update(p: dict) -> None:
    print(f"check-update {p['app_id']}:")
    if not p.get("updatable"):
        print(f"  not updatable - {p['reason']}")
        return
    if not p.get("found"):
        print(f"  {p['reason']}")
        return
    print(f"  current   {p['current_version']}")
    print(f"  latest    {p['latest_version']}")
    print(f"  status    {'UPDATE AVAILABLE' if p['available'] else 'up to date'}")
    print(f"  asset     {p['asset_name']}  ({p['asset_size_human']})")
    print(f"  release   {p['release_tag']}  {p['release_url']}")


def _r_update(p: dict) -> None:
    if not p.get("downloaded"):
        if p.get("dry_run"):
            print(f"DRY RUN - would update '{p['app_id']}':")
            print(f"  download {p['plan']['download']}")
            print(f"  replace  {p['plan']['replace']}")
            print(f"  trash    {p['plan']['trash_old']}")
        else:
            _r_check_update(p)
        return
    print(f"Updated '{p['app_id']}' {p['from_version']} -> {p['to_version']}")
    print(f"  asset        {p['asset']}  ({p['download_size_human']})")
    print(f"  sha256       {p['sha256_verified']}")
    print(f"  old binary   {p['old_binary_trashed']}")


def _r_scan(p: dict) -> None:
    for d in p["found"]:
        print(f"{d['path']}  ({d['size_human']})")
    if p.get("skipped"):
        print(f"  ({len(p['skipped'])} not AppImages)")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="appimagectl",
        description="Install, uninstall, and inspect AppImages with desktop integration.",
    )
    ap.add_argument("--version", action="version", version=f"appimagectl {__version__}")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ins = sub.add_parser("inspect", help="read an AppImage without touching the system")
    p_ins.add_argument("path")
    p_ins.add_argument("--shallow", action="store_true", help="skip payload extraction")

    p_i = sub.add_parser("install", help="integrate an AppImage into the desktop")
    p_i.add_argument("path")
    p_i.add_argument("--app-id", help="override the derived app id")
    p_i.add_argument(
        "--args",
        default="--no-sandbox",
        help="extra args in Exec= (default: --no-sandbox; '' for none)",
    )
    p_i.add_argument("--move", action="store_true", help="delete the source file after install")
    p_i.add_argument("--force", action="store_true", help="overwrite an existing entry")
    p_i.add_argument("--dry-run", action="store_true")

    p_u = sub.add_parser("uninstall", help="remove an app installed by appimagectl")
    p_u.add_argument("app_id")
    p_u.add_argument("--dry-run", action="store_true")

    p_a = sub.add_parser(
        "adopt",
        help="claim an existing manual AppImage install into management",
    )
    p_a.add_argument("app_id")
    p_a.add_argument("--binary", help="expected binary; must match Exec= in the desktop entry")
    p_a.add_argument("--dry-run", action="store_true")

    p_c = sub.add_parser(
        "clean",
        help="move an app's user data (config/cache/data/state/dotdirs) to trash",
    )
    p_c.add_argument("app_id")
    p_c.add_argument("--yes", action="store_true", help="execute; default is a dry plan")
    p_c.add_argument("--dry-run", action="store_true")

    p_v = sub.add_parser("verify", help="full integrity check of a managed app")
    p_v.add_argument("app_id")

    p_t = sub.add_parser("trash", help="manage trashed binaries and cleaned data")
    t_sub = p_t.add_subparsers(dest="trash_cmd", required=True)
    t_sub.add_parser("list", help="list trashed items")
    p_tr = t_sub.add_parser("restore", help="move a trashed binary back to the store")
    p_tr.add_argument("name")
    p_te = t_sub.add_parser("empty", help="permanently delete everything in trash")
    p_te.add_argument("--yes", action="store_true")

    p_cu = sub.add_parser(
        "check-update",
        help="query the embedded update source (GitHub releases) for a newer version",
    )
    p_cu.add_argument("app_id")

    p_up = sub.add_parser("update", help="download and swap in a newer version")
    p_up.add_argument("app_id")
    p_up.add_argument("--dry-run", action="store_true")

    p_sn = sub.add_parser(
        "scan",
        help="find AppImage files in a directory (default: ~/Downloads)",
    )
    p_sn.add_argument("dirs", nargs="*")

    p_l = sub.add_parser("list", help="list managed apps")
    p_l.add_argument("--no-check", action="store_true", help="skip health checks")

    sub.add_parser("doctor", help="check environment and installed apps")

    p_r = sub.add_parser("run", help="launch a managed app")
    p_r.add_argument("app_id")

    sub.add_parser("gui", help="open the GTK4 desktop app")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "inspect":
            return _emit(op_inspect(args.path, deep=not args.shallow), args.json, _r_inspect)
        if args.cmd == "install":
            return _emit(
                op_install(
                    args.path,
                    app_id=args.app_id,
                    extra_args=args.args,
                    keep_source=not args.move,
                    force=args.force,
                    dry_run=args.dry_run,
                ),
                args.json,
                _r_install,
            )
        if args.cmd == "uninstall":
            return _emit(
                op_uninstall(args.app_id, dry_run=args.dry_run), args.json, _r_uninstall
            )
        if args.cmd == "adopt":
            return _emit(
                op_adopt(args.app_id, binary=args.binary, dry_run=args.dry_run),
                args.json,
                _r_adopt,
            )
        if args.cmd == "clean":
            return _emit(
                op_clean(args.app_id, yes=args.yes, dry_run=args.dry_run),
                args.json,
                _r_clean,
            )
        if args.cmd == "verify":
            return _emit(op_verify(args.app_id), args.json, _r_verify)
        if args.cmd == "trash":
            if args.trash_cmd == "list":
                return _emit(op_trash_list(), args.json, _r_trash_list)
            if args.trash_cmd == "restore":
                return _emit(op_trash_restore(args.name), args.json, _r_trash_restore)
            if args.trash_cmd == "empty":
                return _emit(op_trash_empty(yes=args.yes), args.json, _r_trash_empty)
        if args.cmd == "check-update":
            return _emit(op_check_update(args.app_id), args.json, _r_check_update)
        if args.cmd == "update":
            return _emit(
                op_update(args.app_id, dry_run=args.dry_run), args.json, _r_update
            )
        if args.cmd == "scan":
            dirs = args.dirs or None
            return _emit(op_scan(dirs), args.json, _r_scan)
        if args.cmd == "list":
            return _emit(op_list(check=not args.no_check), args.json, _r_list)
        if args.cmd == "doctor":
            return _emit(op_doctor(), args.json, _r_doctor)
        if args.cmd == "run":
            return _emit(op_run(args.app_id), args.json, _r_run)
        if args.cmd == "gui":
            from .gui.app import run_gui

            return run_gui()
    except OperationError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 1


if __name__ == "__main__":
    sys.exit(main())
