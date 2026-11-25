# EpiQ Convert

Originally developed by Benjamin Gregory, Timo Fuchs, and Andrej Singer at the MSE Department at Cornell University

A small desktop app for CHESS QM2 (ID4B) beamtimes. Choose a SPEC file and it
lists that sample's scans, shows which are already converted, and converts or
combines the ones you pick. There is no config file to write.

It is a front end for [EpiQ-Map](https://github.com/as3689-a11y/EpiQ-Map).
A conversion is done by EpiQ-Map's own `auto_rsm` module, so the result is the
same lab-frame Q map that EpiQ-Map's `rsm_monitor` produces. There is no
orientation (U matrix) fitting and no HKL indexing.

| Document | For |
|---|---|
| This file | Installing and using the app |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the code works |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Changing, testing and releasing it |
| [CLAUDE.md](CLAUDE.md) | Short brief for AI coding agents |
| [CHANGELOG.md](CHANGELOG.md) | What changed and when |

## Install

Requires Python 3.10 or newer.

```bash
python3 -m venv ~/epiq-convert-env
source ~/epiq-convert-env/bin/activate
pip install git+https://github.com/rylanfernandes/EpiQ-Convert.git
```

This also installs EpiQ-Map (from PyPI) and PyQt6; about 1 GB in all. To
update later, add `--upgrade` to the `pip install` line.

## Run

```bash
source ~/epiq-convert-env/bin/activate
epiq-convert                                    # reopens the last SPEC file
epiq-convert /nfs/chess/id4b/2026-3/singer-4778-b/La3Ni2O7-NA-3
```

The app is a window, so on a server use a remote desktop (NoMachine) or
`ssh -X`.

## The folder layout it expects

Everything is found from where the SPEC file is:

| What | Where |
|---|---|
| SPEC file | `/nfs/chess/id4b/<run>/<beamtime>/<name>` |
| Images | `/nfs/chess/id4b/<run>/<beamtime>/raw6M/<name>/<sample>/<set T>/<name>_<NNN>/` |
| Calibration and mask | `/nfs/chess/id4baux/<run>/<beamtime>/calibrations/*.poni`, `*.edf` |
| Output | `/nfs/chess/id4baux/<run>/<beamtime>/processed/output-epiq/` |

For example, SPEC file `/nfs/chess/id4b/2026-3/singer-4778-b/La3Ni2O7-NA-3`
gives images under `.../raw6M/La3Ni2O7-NA-3/standard/16/La3Ni2O7-NA-3_009/`
and maps in
`/nfs/chess/id4baux/2026-3/singer-4778-b/processed/output-epiq/transformed_objects/`.

- `<sample>` is the folder under the SPEC name, such as `standard` or
  `TbScO3`. It becomes part of the output file name.
- `<set T>` is the temperature folder, such as `16` or `300`.
- If the calibrations folder has several `.poni` or `.edf` files, the newest
  is used (for masks, a file with "mask" in its name is preferred) and the
  others are in the drop-down.
- A flat copy of the data also works: SPEC file, scan folders and a
  `calibrations` folder side by side. Output then goes to `output` next to
  the SPEC file.

Every detected path can be changed in the window. A calibration, mask or
output folder you choose by hand is remembered for that beamtime.

## Using it

### 1. Choose the SPEC file

Paste the path or click **Choose…**. The drop-down lists the other SPEC files
of the same beamtime, so switching sample is one click. The four boxes below
fill in by themselves.

### 2. Read the table

One row per scan that is in the SPEC file and has an image folder.

| Column | Meaning |
|---|---|
| Scan | SPEC scan number. Hover for the folder, command and start time |
| Sample | Folder under the SPEC name |
| T (K) | Set-temperature folder |
| Type | `phi scan` or `theta scan`, from the motor in the SPEC file |
| Range | Start and end of the scanned motor |
| th, chi, phi | Motor positions at the start of the scan. The scanned motor shows `scanned` |
| Points | Data points recorded in the SPEC file |
| Status | `Converted ✓` if the map is already in the output folder |
| Combined | Combined files that include this scan, e.g. `7+8+9 ✓` |

Rows are sorted by sample, then temperature, then scan number. Click a column
heading to sort by it instead. **Refresh** re-reads the SPEC file and looks
again for new scans and output files; the output folder is also checked every
time the app opens and after every job.

### 3. Convert

- **One scan**: press **Convert** in its row.
- **Several**: select rows (Shift- or Ctrl-click, or Ctrl-A for all) and press
  **Convert selected**. Scans that are already converted are left alone.

Conversions run one at a time; the rest show `Waiting`. A row's button turns
into **Stop** (running) or **Cancel** (waiting), and **Stop all** clears
everything.

The map is written to
`<output>/transformed_objects/<name>_<sample>_scans_<N>_full.nxs`.

- **Already converted**: the row button reads **Redo** and asks before
  replacing. The old file is only replaced once the new one is complete.
- **Scan still running**: if a scan has fewer images than SPEC points, the row
  button asks whether to convert what is there, and **Convert selected** skips
  it.
- **Aborted scans** with no data in the SPEC file cannot be converted.
- **Stopping or a failure** leaves no half-written file.

**Skip images brighter than** is EpiQ-Map's `Max Intensity` (default `1e5`):
an image whose brightest unmasked pixel exceeds it is left out as a detector
overload. The count of skipped images is under **Show details**.

### 4. Combine

Select two or three converted scans and press **Combine selected**. The line
beside the button says what will happen before you press it, for example
`Combine: 7 reference, 8 lined up and averaged, 9 fills gaps`.

The scans are always taken in scan-number order:

| Position | What happens to it |
|---|---|
| Lowest number | The reference |
| Second | Shifted to line up with the reference, then averaged with it |
| Third (optional) | Not shifted or averaged. Only fills voxels that are still empty |

Nothing is combined automatically. The app asks first if the scans are not
consecutive, are not all phi scans, or are in different temperature folders,
and before replacing an existing combined file.

The result is written beside the maps as
`<name>_<sample>_scans_7_8_9_full_combined.nxs`.

## What is in the files

A converted map is a NeXus/HDF5 file with one `NXdata` group, `entry/data`:

| Dataset | Meaning |
|---|---|
| `counts` | Normalised intensity on a 1000 × 1000 × 1000 grid |
| `norm` | Coverage of each voxel. `0` means it was never measured |
| `H`, `K`, `L` | The axes. Without indexing these are lab-frame Q components in Å⁻¹, not Miller indices |

A combined file has `counts` and the axes, and no `norm`.

How the combine works:

- A voxel equal to `0` counts as empty.
- The shift of the second map is a whole number of pixels along the first and
  third axes, from the cross-correlation of the two maps' maximum projections
  along the second axis.
- Where both maps have data the result is their mean; where only one has, that
  value is used.

## Command line

```bash
epiq-convert [SPEC file]                            # open the app
epiq-combine A.nxs B.nxs [C.nxs] -o combined.nxs    # combine without the app
```

Each conversion's settings are saved in
`<output>/logs/epiq-convert/<name>_<sample>_<NNN>.txt` and can be rerun with
`python -m epiq_map.hkl_convert.auto_rsm <that file>`.

## Limits

- **Memory**: a conversion holds a 1000³ grid in memory several times over;
  expect well over 10 GB.
- **Speed on macOS**: EpiQ-Map's macOS build uses one core. Linux uses OpenMP.
- **Scan folder names** must be `<name>_<NNN>`, because EpiQ-Map finds the
  images by that name.

## Troubleshooting

| What you see | Likely cause and fix |
|---|---|
| "No scan folders were found for this SPEC file" | Set **Scan images in** to the folder that holds the sample/temperature folders |
| Calibration or mask box is empty | Nothing was found in `calibrations`; choose the file by hand |
| "stopped unexpectedly (possibly out of memory)" | Close other programs or use a machine with more RAM |
| The map looks empty | Check **Show details** for "overloaded frames skipped" and raise **Skip images brighter than** |
| "Shape mismatch" when combining | The maps were made with different calibrations or grids |
| A new scan is missing | Press **Refresh** |

On macOS, exFAT drives collect hidden `._name.cbf` files beside the images.
EpiQ-Map would read them as images, so the app deletes `._*.cbf` files from a
scan folder before converting it. Nothing else in the raw data is changed.

## Status

Version 0.2.0. Developed on macOS; the server layout was built from directory
listings of two real beamtimes and tested against a local replica of it. See
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#what-has-been-tested) for exactly
what has and has not been run. There is no license file yet.

## Credits

EpiQ-Map, which does the reconstruction, is by Ben Gregory, Timo Fuchs and
Andrej Singer.
