# Changelog

All notable changes to appimagectl are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Full open-source packaging: MIT `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`,
  generated GUI screenshots for the README.

## [0.2.0] - 2026-08-30

### Added

- `clean <app-id>`: move an app's user data (config/cache/data/state and
  dot-dirs like `~/.zcode`) to trash. Refuses without `--yes`; warns when the
  app is still running.
- `verify <app-id>`: full integrity check — re-hashes the installed binary
  against the manifest, checks launcher marker, icons, and shell registration.
- `trash list | restore <name> | empty`: manage trashed binaries and cleaned
  data. `restore` refuses on filename collision; `empty` is the only operation
  that permanently deletes, and requires `--yes`.
- `check-update <app-id>` / `update <app-id>`: query embedded update
  information (`gh-releases-zsync|owner|repo|...`) against the GitHub releases
  API, download and swap the newer binary. The old binary goes to trash; the
  download is magic-verified before swap.
- `scan [dirs...]`: find AppImage files in common locations (Downloads,
  Desktop, `~/.local/bin`) so nothing outside the store is silently missed.
- GUI: Verify, Check update and Clean data actions per app; Trash viewer in
  the app menu.

### Fixed

- `list` now finds AppImages in store subdirectories (was top-level glob).
- `adopt` no longer crashes on absolute-path `Icon=` values.
- `adopt` recovers orphaned installs (marker present, manifest missing) by
  re-adopting instead of refusing.

## [0.1.0] - 2026-08-30

### Added

- `install`: integrate an AppImage into the desktop — magic-byte validation,
  payload `.desktop`/icon extraction, SHA-256 verified copy into the store,
  managed `.desktop` entry (provenance keys), validator gate with rollback,
  cache refresh, manifest record.
- `inspect`: read an AppImage's metadata without touching the system; reports
  upd_info honestly (Electron-built AppImages ship an empty one).
- `adopt`: claim an existing manual install into management without moving or
  rewriting its launcher beyond adding provenance keys.
- `uninstall`: remove exactly the manifest-listed files; move the binary to
  trash; refuse anything not proven ours.
- `list`, `doctor`, `run`, `gui`: inventory + health, environment doctor,
  detached launch, GTK4/libadwaita front end.
- `--json` on every command for machine-readable output; `--dry-run` on every
  destructive command.