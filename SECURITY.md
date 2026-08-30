# Security Policy

## Reporting a vulnerability

appimagectl writes to a user's desktop environment (`~/.local/share`,
`~/.config`, `~/Applications`, the icon theme, and the desktop database). A bug
in the deletion or overwrite logic can damage data, so security issues are
taken seriously.

Please do NOT open a public issue for a vulnerability. Report privately to the
maintainers via GitHub's private vulnerability reporting (Security →
"Report a vulnerability") or by opening a draft PR with the fix only.

Include:

- the affected version(s)
- a minimal reproduction (what AppImage, what commands)
- impact: what could be deleted/overwritten/executed and under what
  circumstances
- whether it affects the CLI, the GUI, or both

You should receive an acknowledgement within one week.

## What the project protects

The safety invariants are documented in `AGENTS.md` and enforced by tests in
`tests/test_core.py`:

1. Uninstall deletes only manifest-listed files.
2. Files without the `X-AppImageCtl-Managed=true` marker are treated as
   foreign; install and uninstall refuse to touch them.
3. Installed binaries are SHA-256 verified against the source before install
   completes.
4. Uninstall/clean move data to trash; only `trash empty --yes` permanently
   deletes.

A vulnerability is anything that lets an attacker violate one of these, for
example:

- a crafted AppImage whose embedded `.desktop` file injects `Exec=` lines,
  paths, or keys that bypass the managed-marker checks
- a crafted `.desktop`/manifest that tricks uninstall into deleting files
  outside the recorded list (path traversal, symlink swap)
- a manifest JSON parse quirk that loads a different app's file list

## Supported versions

| Version | Supported          |
|---------|--------------------|
| 0.2.x   | :white_check_mark: |
| < 0.2   | :x:                |

## Process

1. Maintainer triages the report and confirms the issue.
2. A fix lands on `main` behind the existing safety invariants, with a
   regression test.
3. The fix is released; a changelog entry names the issue class (not the
   reporter's identity unless they consent).