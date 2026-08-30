# Contributing to appimagectl

Thanks for considering a contribution. This project is small on purpose; the
rules below keep it that way.

## Ground rules

1. **Safety invariants are not negotiable.** The four invariants in
   `AGENTS.md` (manifest-only deletion, managed-marker guard, SHA-verified
   copies, trash-not-delete) are load-bearing. A change that weakens one will
   be rejected even if the tests pass.
2. **Everything destructive supports dry-run.** New destructive operations
   must ship `dry_run` and refuse without `--yes` (or equivalent).
3. **Core returns dicts, not exceptions, for flow.** New operations should
   return plain dicts shaped like the existing ones so CLI and GUI can render
   without branching per-caller.

## Setup

```bash
python3 -m venv --system-site-packages .venv   # PyGObject comes from the system
./.venv/bin/pip install -e '.[dev]'
./verify.sh
```

The GUI needs `gir1.2-gtk-4.0 gir1.2-adw-1` and PyGObject. Everything
(including the GUI import check) must pass on a machine without a display:
`./verify.sh` runs the CLI smoke in a headless-friendly way.

## Tests

- Run `./verify.sh` before pushing anything; it is the CI mirror.
- The sandbox fixture redirects all XDG paths into `tmp_path` and reloads the
  package modules. Catch errors as `sandbox["core"].OperationError`, never via
  a module-level import (stale class across reloads).
- The sandbox patches `desktop.registered_in_shell` to `None` (GLib caches the
  user data dir per process). `verify` treats `None` as unknown, `False` as
  failure.
- New tests must not touch the real `~/.local/share`, `~/.config` or
  `~/Applications`.

## Code style

- `ruff` (line-length 100, select E,F,I,UP,B,SIM) must be clean.
- No global state; operations take explicit arguments and return dicts.
- Plain, dense UI. No emoji, no gradients, no decorative chrome.

## Screenshots

`docs/screenshots/*.png` are generated, not hand-edited. Regenerate them with:

```bash
Xvfb :77 -screen 0 1280x800x24 &
python3 scripts/make_demo_env.py /tmp/appimagectl-demo
DISPLAY=:77 XDG_DATA_HOME=/tmp/appimagectl-demo/data \
  XDG_CONFIG_HOME=/tmp/appimagectl-demo/config \
  APPIMAGECTL_STORE=/tmp/appimagectl-demo/Applications \
  ./.venv/bin/python scripts/shot_gui.py docs/screenshots/appimagectl-main.png
DISPLAY=:77 ... ./.venv/bin/python scripts/shot_gui.py \
  docs/screenshots/appimagectl-install.png --install-dialog
```

The demo env uses fictional apps and paths so no real machine data lands in a
commit. Never replace these with mocked-up images.

## Commits

- Write meaningful commit messages; keep unrelated changes out of one commit.
- Run `./verify.sh` and confirm it exits 0 before opening a PR.
- Screenshot/docs changes: if a screenshot changed, regenerate it through
  `scripts/`, not by hand.