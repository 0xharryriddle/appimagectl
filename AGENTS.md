# AGENTS.md — appimagectl

Rules for any agent working in this repository.

## Project identity
- appimagectl: install/uninstall/inspect AppImages with safe desktop integration.
  CLI core (JSON output) + thin GTK4/libadwaita GUI.
- Source lives in `src/appimagectl/`, tests in `tests/`, dev deps in `.venv`.

## Workflow
- Research → Plan → Implement → Verify. Verify means running `./verify.sh` green.
- Run commands before documenting them. Documentation that names a command
  must show real output from that command.
- No fabricated results, no "works in theory" claims. A feature is done when
  its test passes and the real CLI produced real output.

## Safety invariants (do not break)
1. Uninstall deletes ONLY files listed in the app's manifest.
2. Install/uninstall refuse to touch a `.desktop` file without
   `X-AppImageCtl-Managed=true` (foreign launchers).
3. The copied binary is SHA-verified against the source before install completes.
4. Uninstall MOVES the AppImage to trash (`~/.local/share/appimagectl/trash/`),
   never hard-deletes it. `clean` moves user data to trash too.
5. `trash empty` is the ONLY operation that permanently unlinks data, and it
   requires `--yes` and prints the list first.
6. Every destructive operation supports `--dry-run` and shows the exact plan.
- Any change touching these rules must update the tests that pin them
  (`tests/test_core.py` refusal tests).

## Tests
- Run: `./verify.sh` (ruff + pytest + CLI smoke).
- The sandbox fixture redirects all XDG paths into tmp_path and reloads the
  package modules. ALWAYS catch errors as `sandbox["core"].OperationError` —
  a module-level `from appimagectl.core import OperationError` captures a stale
  class across reloads and will never match.
- The sandbox also patches `desktop.registered_in_shell` to `None`, because
  GLib caches the user-data dir at first Gio call per process and would compare
  every later test against the first sandbox's tmp dir. `verify` treats None
  (unknown) as not-a-failure; `False` is a failure.
- New tests must not touch the real `~/.local/share`, `~/.config` or
  `~/Applications`.

## Style
- ruff (line-length 100, select E,F,I,UP,B,SIM) must be clean.
- Plain dicts across `core.py` → CLI/GUI boundary; no shared state.
- No emoji, no decorative UI. Dense, evidence-first interfaces.

## Verification
- `verify.sh` mirrors CI. Do not claim green until it exits 0.
- GUI changes: import-check `appimagectl.gui.app` (needs system PyGObject).
  Do not launch the GUI on the user's display without explicit approval.