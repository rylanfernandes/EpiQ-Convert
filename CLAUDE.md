# CLAUDE.md

Brief for AI coding agents working in this repository. Humans: see
[README.md](README.md).

## What this is

A PyQt6 desktop app (`epiq-convert`) for CHESS QM2 beamtimes. The user picks a
SPEC file; the app finds the image folders, calibration, mask and output
folder from the standard layout, lists the scans in a table, and converts or
combines the ones selected.

- **Convert** runs EpiQ-Map's `auto_rsm` as a child process (lab-frame Q map,
  no indexing).
- **Combine** runs `combine_rsm.py` as a child process on two or three maps.

The owner is a scientist, not a software developer. They want it automatic,
simple, fast on a shared server, and correct.

## Read first

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): layout rules, the queue, and
  the EpiQ-Map behaviours the app relies on.
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md): how to test without a display,
  how to build a replica of the server tree, and what has been verified.

## Layout

```
src/epiq_convert/beamtime.py     file-system logic, no Qt (~300 lines)
src/epiq_convert/app.py          window, table, job queue (~650 lines)
src/epiq_convert/combine_rsm.py  combine script (executes on run)
src/epiq_convert/cli.py          entry points
```

## Rules

1. **Do not reimplement the reconstruction.** Conversion stays a call to
   `python -m epiq_map.hkl_convert.auto_rsm <config>`.
2. **Do not change the numerics in `combine_rsm.py`** unless the owner asks.
   It is their analysis script. Say clearly when a change would alter results.
3. **Never combine automatically.** The owner selects the scans.
4. **Never leave a partial output or overwrite without asking.** See "Redo and
   cleanup" in the architecture notes.
5. **Stay fast on a network disk.** Do not open scan folders (thousands of
   files each) at load time. Do not import the detector stack in the GUI
   process.
6. **Stay small.** No U matrices, indexing, HKL ranges or folder watching. Ask
   before adding controls.
7. **Do not touch raw data** beyond deleting `._*.cbf` AppleDouble files in a
   scan folder before converting it.
8. **No personal or machine-specific paths** in the repository. Example paths
   from the beamline (`/nfs/chess/id4b/...`) are fine.

## Things that will bite you

- You probably cannot reach `/nfs/chess`. Ask the owner to run read-only
  listing commands and paste the output, then test against a replica tree.
- `auto_rsm` ignores `Image Directory`; it opens
  `<Temperature Directory>/<Material>_<scan:03d>/`.
- If the output exists, `auto_rsm` writes `..._more.nxs`. Read the real path
  from its `Output file:` and `Saved` lines.
- `auto_rsm`'s config parser rejects unknown keys and needs an integer
  `Temperature`.
- Configs go in `<output>/logs/epiq-convert/`, not `<output>/logs/`, because
  `rsm_monitor` reads every `.txt` directly in `logs/` as its own.
- Output names must match EpiQ-Map's
  (`<name>_<sample>_scans_<N>_full.nxs`) or the Converted column is wrong.
- `combine_rsm.py` runs at import. Execute it, never `import` it.
- Jobs hold references to row dicts, so `reload` is refused while busy.
- `PathPicker.path()` is the committed value; call `commit()` for typed text.
- A conversion allocates a 1000³ float32 grid several times over.

## Testing

No test suite. Use `QT_QPA_PLATFORM=offscreen`, redirect `QSettings` to a
temporary folder, replace `w.ask` with a lambda, and pump
`app.processEvents()` while `w.busy()`. Recipes are in docs/DEVELOPMENT.md.

State what you actually ran. The server itself and a full-size conversion have
never been tested through this app; do not imply otherwise.

## Commands

```bash
pip install -e .
epiq-convert [SPEC file]
epiq-combine A B [C] -o out.nxs
python -m py_compile src/epiq_convert/*.py
```

## When you finish a change

Update `CHANGELOG.md`, and the README and docs if behaviour changed. Bump the
version in `pyproject.toml` and `src/epiq_convert/__init__.py` together.
