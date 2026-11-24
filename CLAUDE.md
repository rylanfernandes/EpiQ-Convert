# CLAUDE.md

Brief for AI coding agents working in this repository. Humans: see
[README.md](README.md).

## What this is

A PyQt6 desktop app (`epiq-convert`) with two tabs:

- **Convert a scan**: one folder of `.cbf` images + SPEC file + `.poni` +
  mask → a lab-frame Q map (`.nxs`), by running EpiQ-Map's `auto_rsm` as a
  child process.
- **Combine scans**: two or three `.nxs` maps → one, by running
  `combine_rsm.py` as a child process.

The owner is a scientist, not a software developer. They asked for the
simplest possible UI: no config files, one action button per tab, plain
messages.

## Read first

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how it works, and the
  EpiQ-Map behaviours the app relies on.
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md): how to test without a display,
  and what has and has not been verified.

## Layout

```
src/epiq_convert/app.py          all GUI code and helpers (~900 lines)
src/epiq_convert/combine_rsm.py  combine script (executes on run)
src/epiq_convert/cli.py          entry points
pyproject.toml                   packaging
setup.sh                         optional macOS Desktop icon
```

## Rules

1. **Do not reimplement the reconstruction.** Conversion must stay a call to
   `python -m epiq_map.hkl_convert.auto_rsm <config>`. The owner's
   requirement is "exactly the same processing" as EpiQ-Map.
2. **Do not change the numerics in `combine_rsm.py`** (alignment, averaging,
   filling) unless the owner asks. It is their analysis script. Interface
   changes are fine; say clearly when a change would alter results.
3. **Never leave a damaged or partial output file.** Conversions rely on
   `auto_rsm` choosing a fresh name and are swapped in with `os.replace`;
   combines write `<target>.partial` first. Keep both.
4. **Never overwrite an existing result without asking.** Both tabs go
   through `confirm_replace`.
5. **Keep the UI minimal.** No U matrices, indexing, HKL ranges, folder
   watching or batch queues were wanted. Ask before adding controls.
6. **Do not touch user data** beyond deleting `._*.cbf` AppleDouble files in
   the scan folder before a run.
7. **No personal or machine-specific paths** in the repository.

## Things that will bite you

- `auto_rsm` ignores `Image Directory`. It opens
  `<Temperature Directory>/<Material>_<scan:03d>/`, so the scan folder name
  is load-bearing.
- If the output exists, `auto_rsm` writes `..._more.nxs` instead. Read the
  real path from its `Output file:` and `Saved` lines; do not compute it.
- `auto_rsm`'s config parser rejects unknown keys and needs an integer
  `Temperature`.
- `combine_rsm.py` runs at import. Execute it (`runpy` or by path), never
  `import` it.
- `scan_motor` reaches into `epiq_map.make_log_files._spec_cache`, a private
  cache, to force a re-read of the SPEC file.
- `PathPicker.path()` is the committed value, not the text in the box. Call
  `_typed()` to commit pending text, as the action handlers do.
- `_refresh` methods return early while a process is running.
- A conversion allocates a 1000³ float32 grid several times over. Do not run
  full conversions casually on a small machine.
- On macOS, EpiQ-Map's kernel is single-threaded.

## Testing

No test suite. Drive the real window with `QT_QPA_PLATFORM=offscreen`, pump
`app.processEvents()` while `w.process` is set, and monkeypatch
`confirm_replace`. Recipes are in docs/DEVELOPMENT.md. Use a symlinked subset
of images for conversions and small synthetic `.nxs` files for combines.

State what you actually ran. Do not describe a full-scan conversion as
tested unless one was run.

## Commands

```bash
pip install -e .          # develop
epiq-convert              # open the app
epiq-combine A B [C] -o out.nxs
python -m py_compile src/epiq_convert/*.py
```

## When you finish a change

Update `CHANGELOG.md`, and the README or docs if behaviour changed. Bump the
version in `pyproject.toml` and `src/epiq_convert/__init__.py` together.
