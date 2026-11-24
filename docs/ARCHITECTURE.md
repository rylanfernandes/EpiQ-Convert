# Architecture

How EpiQ Convert is put together. Read this before changing the code.

## The one idea

The app does no reconstruction of its own. It collects inputs, writes the
plain-text config that EpiQ-Map's `auto_rsm` expects, and runs

```
python -u -m epiq_map.hkl_convert.auto_rsm <config>
```

as a child process. Everything scientific about a conversion lives in
EpiQ-Map. The Combine tab works the same way around `combine_rsm.py`.

Running the work in a child process is what keeps the window responsive, makes
Stop a simple `kill`, and guarantees the output matches what `rsm_monitor`
would produce.

## Files

```
src/epiq_convert/
  __init__.py      version string
  __main__.py      python -m epiq_convert
  cli.py           console entry points: convert() and combine()
  app.py           the whole GUI plus the helper functions
  combine_rsm.py   combine script; runs top to bottom when executed
pyproject.toml     packaging, dependencies, entry points
setup.sh           optional: venv + macOS Desktop .app
```

## app.py

Three layers, top to bottom in the file.

### 1. Helper functions (no Qt)

| Function | Job |
|---|---|
| `parse_scan_folder(path)` | `<Material>_<NNN>` → `(material, number)`, or `None`. Rejects names that would not round-trip through `f"{material}_{number:03d}"` |
| `count_frames(path)` | Number of `.cbf` files, ignoring `._*` |
| `hidden_cbf_files(path)` | The `._*.cbf` AppleDouble files to delete before a run |
| `detect_sample_and_temperature(scan_dir, material)` | Reads the detector `.log` in the scan folder; see below |
| `scan_motor(spec_path, n)` | Motor scanned in SPEC scan `n`, via `epiq_map.make_log_files.get_scan_motor` |
| `output_path(...)` | Where `auto_rsm` will write the map |
| `build_config(...)` | Text of the `auto_rsm` config |
| `combined_name(paths)` | Output file name for a combine |

### 2. Widgets

- **`PathPicker`**: an editable path box plus a **Choose…** button. `path()`
  returns the last *committed* value; typing commits on Enter or focus-out
  (`_typed`). It emits `changed` only when the committed value changes.
- **`CombinePanel`**: the Combine tab. Self-contained, with its own
  `QProcess`.
- **`MainWindow`**: a `QTabWidget` holding the Convert form and a
  `CombinePanel`.

### 3. `main()`

Creates the `QApplication`, checks that `epiq_map.hkl_convert` imports (and
shows a dialog instead of a traceback if not), and shows the window.

## Convert tab: state and flow

`MainWindow.scan` is either `None` or a dict:

```python
{"path", "material", "number", "frames", "sample", "temperature", "motor"}
```

- `_folder_changed` rebuilds it whenever the scan-folder box changes, and
  sets `self.problem` to a sentence when the folder is unusable.
- `_refresh` re-reads the motor from the SPEC file, updates the grey summary
  line, and sets the one-line message (`Ready to convert`,
  `Already converted ✓`, or what is missing). It is connected to every
  picker's `changed` signal and returns early while a process is running.
- `convert_clicked` is both Convert and Stop. As Convert it validates,
  asks before replacing an existing file (`confirm_replace`), writes the
  config, deletes `._*.cbf` files, and starts the `QProcess`.
- `_read_output` → `_handle_line` consumes the child's output.
- `_process_finished` decides success, swaps or cleans files, and reports.

### The config that is written

`build_config` reproduces the format of EpiQ-Map's
`make_log_files.generate_log_content`:

```
PONI File: ...
Material: BaRuO3
Sample Name: TbScO3
Scan Number: 21
Scan List: [21]            # [] for a theta scan
Theta Scan List: []        # [21] for a theta scan
Temperature: 301           # omitted when not known
Max Intensity: 100000.0
Mask File: ...
Specfile: ...
Temperature Directory: <parent of the scan folder>/
Image Directory: <scan folder>/
Output Directory: ...
```

It is saved as `<output>/logs/<Material>_<NNN>.txt` and overwritten on every
run. No `UB`, lattice or range keys are written, which is what makes
`auto_rsm` produce an unindexed lab-frame map in `transformed_objects/`.

### Things `auto_rsm` does that the app depends on

These are behaviours of EpiQ-Map 0.1.x. If they change, the app breaks.

1. **Image folder is derived, not read.** `auto_rsm` ignores
   `Image Directory` and opens
   `<Temperature Directory>/<Material>_<scan:03d>/`. This is why the scan
   folder must be named `<Material>_<NNN>` and why `Temperature Directory`
   is set to its parent.
2. **Scan type comes from the lists.** A scan in `Scan List` is processed as a
   phi scan, one in `Theta Scan List` as a theta scan. The app picks the list
   from the SPEC motor: `th` → theta, anything else → phi. This is the same
   rule as `make_log_files`.
3. **It never overwrites.** If the output exists it appends `_more` before
   `.nxs`. The app does not predict the name; it reads the line
   `Output file: <path>` that `auto_rsm` prints, and `Saved <path>` at the
   end.
4. **Progress is a tqdm bar** on stderr, refreshed with `\r`. The app merges
   stdout and stderr, splits on `\r` and `\n`, and matches `TQDM_RE`.
5. **Config parsing is strict.** Unknown keys raise, values are split on the
   first `": "`, and `Temperature` must be an integer.

### Redo and cleanup

- If the target exists when Convert is pressed and the user agrees,
  `redo_target` is set to it. `auto_rsm` writes `..._more.nxs`; on success
  the app `os.replace`s it over `redo_target`.
- Success requires exit code 0, a normal exit, a `Saved` line, and that file
  existing.
- On failure or Stop, the file named in `Output file:` is deleted unless it
  is `redo_target`. Since `auto_rsm` only ever picks a name that did not
  exist, that file can only be this run's partial output.

### Sample name detection

The detector writes `<scan>_000.log` into the scan folder, listing each image
with the path it had at acquisition:

```
.../raw6M/<material>/<sample>/<temperature>//<Material>_<NNN>/<image>.cbf
```

`detect_sample_and_temperature` takes the two path components before the
scan folder, provided the one before those equals the material. Anything
else falls back to sample `sample` and no temperature. The sample name is
sanitised (`/ \ :` and whitespace → `_`) because it goes into a file name
and into a `Key: value` config line.

### Output folder default

The output box follows the scan folder (`<parent>/output`) until the user
picks or types an output folder (`output_touched`), after which it is left
alone.

## Combine tab

`CombinePanel` holds three `PathPicker`s. `paths()` returns two paths when
the third box is empty, three otherwise.

- `_problem()` validates: first two present, all exist, no duplicates.
- `combined_name()` builds the output name. When every input matches
  `<prefix>_scans_<numbers><rest>.nxs` with the same prefix it produces
  `<prefix>_scans_<a>_<b>[_<c>]<rest>_combined.nxs`; otherwise
  `<first stem>_combined.nxs`. The file goes next to the first input.
- `combine_clicked` runs
  `python -u combine_rsm.py <files> -o <target>.partial`. On success the
  `.partial` file is renamed over the target; on failure or Stop it is
  deleted. An existing combined file is therefore never damaged by a failed
  run.
- Progress comes from lines of the form `<done>/<total>` (`SLAB_RE`).
- The file dialog starts in the folder of an already chosen file, else in
  `<output>/transformed_objects` from the Convert tab (passed in as the
  `start_folder` callable), else in the last folder used.

## combine_rsm.py

A script, not a module of functions: it parses arguments and does the work at
import time. That is deliberate. It is the user's original analysis script
with two changes only: inputs and output come from the command line, and the
third file is optional. `cli.combine()` runs it with `runpy`, and the app runs
it by file path (`COMBINE_SCRIPT`). Do not import it.

Algorithm, for inputs A, B and optional C on the same grid:

1. Open each file's `entry/data`, find the signal and axes from the NXdata
   attributes.
2. Build the max-projection of A and of B along axis 1, reading `chunk`
   slices at a time. Zeros are treated as missing.
3. Cross-correlate the two mean-subtracted projections with
   `scipy.signal.fftconvolve`; the peak position gives an integer shift
   `(d0, d2)` along axes 0 and 2.
4. For each slab along axis 0: `nanmean` of A and shifted B, with NaN → 0.
   If C is given, voxels still equal to 0 are filled from C's raw values. C is
   not shifted.
5. Write `entry/data/counts` (float32, gzip level 1, one chunk per axis-0
   slice) and copy the axes from A.

It reads `2 × chunk` slices at most, so memory use is small.

## Settings

`QSettings("EpiQ", "Convert")`, stored by the OS (on macOS, a plist in
`~/Library/Preferences`).

| Key | Meaning |
|---|---|
| `spec`, `poni`, `mask` | Last files used; restored only if they still exist |
| `max_intensity` | Last "skip frames brighter than" value |
| `last_dir` | Parent of the last scan folder, for the folder dialog |
| `last_nxs_dir` | Folder of the last combine, for the file dialog |

The scan folder and output folder are deliberately not restored, so a new
session cannot silently write into a previous beamtime's folder.

## Packaging

- `pyproject.toml` uses setuptools with a `src/` layout.
- `epiq-convert` is a GUI script, `epiq-combine` a console script.
- Child processes are started with `sys.executable`, so they use the same
  environment the app was installed in.
- `setup.sh` is only a macOS convenience. The `.app` it writes is a folder
  containing an `Info.plist` and a three-line shell launcher that runs
  `python -m epiq_convert` from `~/.venvs/epiq-map` and logs to
  `~/Library/Logs/EpiQ-Convert.log`.
