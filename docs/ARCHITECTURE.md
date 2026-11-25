# Architecture

How EpiQ Convert is put together. Read this before changing the code.

## The one idea

The app does no processing of its own. It finds the files, shows the scans,
writes the plain-text config that EpiQ-Map's `auto_rsm` expects, and runs

```
python -u -m epiq_map.hkl_convert.auto_rsm <config>
```

as a child process. The combine runs `combine_rsm.py` the same way. Child
processes keep the window responsive, make Stop a simple `kill`, and guarantee
the output matches what `rsm_monitor` would produce.

## Files

```
src/epiq_convert/
  beamtime.py      everything learned from the file system; no Qt
  app.py           the window, the table and the job queue
  combine_rsm.py   combine script; runs top to bottom when executed
  cli.py           console entry points
  __main__.py      python -m epiq_convert
pyproject.toml     packaging
setup.sh           optional macOS Desktop icon
```

## beamtime.py

Pure functions, testable without a display.

| Function | Job |
|---|---|
| `read_spec(path)` | One pass over the SPEC file → `{number: info}` |
| `detect_layout(spec_path)` | Scan root, calibration and mask candidates, output folder, sibling SPEC files |
| `aux_folder(folder)` | `.../id4b/...` → `.../id4baux/...` |
| `find_scan_folders(root, material)` | Folders named `<material>_<NNN>` up to three levels down |
| `load_scans(spec, root)` | The table rows: scans in the SPEC file that have a folder |
| `output_state(output, rows)` | Which maps and combined files exist |
| `inspect_frames(scan_dir)` | Image count and `._*.cbf` files |
| `build_config(...)` | Text of the `auto_rsm` config |
| `converted_name(row)`, `combined_name(paths)` | Output file names |

### Layout rules

Given SPEC file `<beam>/<name>`:

- **Scan root**: `<beam>/raw6M/<name>` if it exists, else `<beam>`.
- **Aux tree**: walking up the path, the first component `X` for which the
  same path with `Xaux` exists. On the server that turns `id4b` into
  `id4baux`.
- **Calibrations**: `<aux>/calibrations`, else `<beam>/calibrations`. `.poni`
  and `.edf` files are listed newest first; masks with "mask" in the name go
  before other `.edf` files. The first of each is the default.
- **Output**: `<aux>/processed/output-epiq`, else `<beam>/output`.
- **Sibling SPEC files**: files in `<beam>` that have a `raw6M/<file>` folder.

### SPEC parsing

`read_spec` reads `#S` (number, macro, motor, start, end), `#D`, `#O` (motor
names, split on two or more spaces because names can contain one space), `#P`
(positions) and counts data lines as `points`. If a scan number repeats, the
first is kept, as EpiQ-Map's `make_log_files` does.

It was checked against `spec2nexus` (which `auto_rsm` uses) on two real SPEC
files, 80 scans: `th`, `chi`, `phi`, `tth` and the point counts all match, and
the scanned motor matches `make_log_files.get_scan_motor`.

### What counts as a scan

A folder named `<name>_<NNN>` whose number is in the SPEC file. Alignment
scans have no folder and do not appear. `motor == "th"` makes a theta scan;
anything else is processed as a phi scan. Both are EpiQ-Map's rules.

Sample and temperature come from the path when the folder is three levels
below the scan root (`<sample>/<T>/<scan>`). Only for a flat copy does the app
open the scan folder, to read them from the detector's `.log` file.

### Speed

Loading never opens a scan folder in the standard layout, so it costs one
read of the SPEC file, a few directory listings, and one listing of
`transformed_objects`. Images are counted only when a conversion is
requested.

## app.py

- **`PathPicker`**: editable combo box plus **Choose…**. `changed` fires on
  any change, `edited` only for a change made by the user. `path()` is the
  committed value; `commit()` takes pending typed text.
- **`MainWindow`**: form, table, action bar, and the queue.

### Rows

Each row is the dict from `load_scans` plus GUI state:

```python
state      ""  | "queued" | "running" | "failed"
note       failure text
percent    progress while running
converted  bool, from output_state
combined   list of scan-number tuples, from output_state
```

`_paint_row` turns that into status text, colour and the row button's label.
`reload` rebuilds the rows; `check_outputs` only refreshes `converted` and
`combined`. Both are refused while anything is running, because jobs hold
references to row dicts.

### The queue

`self.jobs` is a list of dicts; `self.job` is the running one; `_pump` starts
the next when no process is running. One process at a time, because each
conversion needs many gigabytes.

- Convert job: `{"kind": "convert", "row", "redo"}`
- Combine job: `{"kind": "combine", "paths", "target", "label"}`

`request_convert(rows, single)` does the checks at click time: files present,
images exist, images vs SPEC points, existing output. With `single=True` it
may ask a question; in a batch it skips instead.

### Things `auto_rsm` does that the app depends on

Behaviours of EpiQ-Map 0.1.x. If they change, the app breaks.

1. **Image folder is derived.** `auto_rsm` ignores `Image Directory` and opens
   `<Temperature Directory>/<Material>_<scan:03d>/`. `Temperature Directory`
   is therefore the scan folder's parent.
2. **It never overwrites.** If the output exists it appends `_more`. The app
   reads the real path from the `Output file:` and `Saved` lines.
3. **Progress is a tqdm bar** refreshed with `\r`; matched by `TQDM_RE`.
4. **Config parsing is strict**: unknown keys raise, `Temperature` must be an
   integer (it is omitted when the folder name is not a number).

### Redo and cleanup

- Redo: `auto_rsm` writes `..._more.nxs`; on success it is `os.replace`d over
  the old file.
- Convert failure or Stop: the file named in `Output file:` is deleted unless
  it is the redo target. `auto_rsm` only picks unused names, so that file can
  only be this run's.
- Combine: written to `<target>.partial`, renamed on success, deleted
  otherwise.

### Configs

Written to `<output>/logs/epiq-convert/<name>_<sample>_<NNN>.txt`. They are in
a subfolder on purpose: `rsm_monitor` treats every `.txt` directly inside
`<output>/logs` as one of its own datasets.

### Combine rules

Enabled for two or three converted scans of the same sample. Order is by scan
number. Non-consecutive numbers, non-phi scans and different temperature
folders produce a question, not a block.

## combine_rsm.py

A script that does its work at import time. It is the owner's analysis script
with inputs taken from the command line and an optional third file. Run it
(`runpy` or by path); do not import it.

For inputs A, B and optional C on the same grid:

1. Max-projections of A and B along axis 1, zeros treated as missing.
2. Cross-correlate the mean-subtracted projections (`fftconvolve`); the peak
   gives an integer shift along axes 0 and 2.
3. Per slab: `nanmean` of A and shifted B, NaN → 0. If C is given, voxels
   still 0 take C's value. C is not shifted.
4. Write `entry/data/counts` (float32, gzip 1) and A's axes.

## Settings

`QSettings("EpiQ", "Convert")`.

| Key | Meaning |
|---|---|
| `spec` | Last SPEC file; reopened at start if it exists |
| `max_intensity` | Last "skip images brighter than" |
| `manual` | JSON `{beamtime folder: {poni, mask, output}}` for hand-chosen paths |
