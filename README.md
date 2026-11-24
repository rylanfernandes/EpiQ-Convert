# EpiQ Convert

A small desktop app that turns raw CHESS QM2 area-detector scans into NeXus
reciprocal-space maps, and merges those maps, without writing a config file.

It is a front end for [EpiQ-Map](https://github.com/as3689-a11y/EpiQ-Map).
The conversion is done by EpiQ-Map's own `auto_rsm` module, so the result is
the same lab-frame Q map that EpiQ-Map's `rsm_monitor` produces. EpiQ Convert
leaves out everything else the monitor does: there is no folder watching, no
orientation (U matrix) fitting and no HKL indexing.

| Document | For |
|---|---|
| This file | Installing and using the app |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the code works |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | Changing, testing and releasing it |
| [CLAUDE.md](CLAUDE.md) | Short brief for AI coding agents |
| [CHANGELOG.md](CHANGELOG.md) | What changed and when |

## Contents

- [Install](#install)
- [What you need for a conversion](#what-you-need-for-a-conversion)
- [Convert a scan](#convert-a-scan)
- [Combine scans](#combine-scans)
- [Command line](#command-line)
- [Limits](#limits)
- [Troubleshooting](#troubleshooting)
- [Status](#status)

## Install

Requires Python 3.10 or newer.

```bash
pip install git+https://github.com/rylanfernandes/EpiQ-Convert.git
```

This also installs EpiQ-Map (from PyPI) and PyQt6. The full install is about
1 GB, mostly EpiQ-Map's own dependencies. Then open the app from any folder:

```bash
epiq-convert
```

To update, add `--upgrade` to the `pip install` line.

While the repository is private, `pip` needs GitHub credentials for an account
that can read it.

### Desktop icon on a Mac (optional)

```bash
git clone https://github.com/rylanfernandes/EpiQ-Convert.git ~/EpiQ-Convert
cd ~/EpiQ-Convert
./setup.sh                      # or: ./setup.sh /path/to/EpiQ-Map
```

`setup.sh` creates a Python environment in `~/.venvs/epiq-map`, installs this
folder into it in editable mode, and puts **EpiQ Convert** on the Desktop.
Passing a path installs EpiQ-Map from that local checkout instead of PyPI.

## What you need for a conversion

| Input | What it is | Notes |
|---|---|---|
| Scan folder | The folder of `.cbf` detector images for one scan | Must be named `<Material>_<NNN>`, e.g. `BaRuO3_021`, with the scan number zero-padded to three digits |
| SPEC file | The beamline's SPEC log for that material | Holds the motor angles and ion-chamber counts for each image. Usually has no file extension |
| Calibration | A pyFAI `.poni` file | Detector distance, centre, tilt and wavelength |
| Mask | An `.edf` image | Pixels above 0.5 are ignored (gaps, hot pixels) |

The scan number in the folder name must exist in the SPEC file as a
`#S <number>` line.

## Convert a scan

1. **Scan folder**: paste the path, click **Choose…**, or drag the folder onto
   the window. A line underneath shows what was found, for example
   `BaRuO3 · scan 21 · phi scan · 3651 images`.
2. **Sample name**: filled in from the detector's `.log` file in the scan
   folder when there is one; otherwise it is `sample`. It becomes part of the
   output file name, so edit it if it is wrong.
3. Choose the **SPEC file**, **Calibration (.poni)** and **Mask (.edf)**. These
   three are remembered the next time the app opens.
4. **Save output in** defaults to a folder named `output` next to the scan
   folder.
5. Press **Convert**. The button becomes **Stop** while it runs.

The map is written to

```
<output folder>/transformed_objects/<Material>_<Sample>_scans_<N>_full.nxs
```

and the settings used for the run are saved as a small text file in
`<output folder>/logs/<Material>_<NNN>.txt`.

**If the file already exists** the message line shows `Already converted ✓`.
Pressing Convert then asks whether to convert again and replace it. The old
file is only replaced after the new one has been written completely.

**Stopping or a failure** leaves nothing half-written behind.

**Skip frames brighter than** is EpiQ-Map's `Max Intensity` setting, default
`1e5`. An image whose brightest unmasked pixel is above this is treated as a
detector overload and left out of the map entirely. The number of skipped
images is printed under **Show details**. If many images are being skipped
and they are real signal, raise this number.

### What is in the output file

A NeXus/HDF5 file with one `NXdata` group, `entry/data`:

| Dataset | Meaning |
|---|---|
| `counts` | Normalised intensity on the grid (accumulated signal divided by coverage) |
| `norm` | How many detector pixels landed in each voxel. `0` means the voxel was never measured |
| `H`, `K`, `L` | The three axes. With no indexing these are lab-frame Q components in Å⁻¹, not Miller indices |

The grid is 1000 × 1000 × 1000 and its range is set from the detector
geometry in the `.poni` file.

## Combine scans

The **Combine scans** tab merges two or three converted maps.

1. Click **Choose files…** and select two or three `.nxs` files. The dialog
   opens in the output folder from the Convert tab. They are filled in below
   in name order; change any of them with its own **Choose…** button.
2. Check the order, because the files are not treated the same:

   | Slot | What happens to it |
   |---|---|
   | File 1 | The reference |
   | File 2 | Shifted to line up with file 1, then averaged with it |
   | File 3 (optional) | Not shifted or averaged. Only fills voxels that are still empty |

3. Press **Combine**.

The result is written next to file 1. Scans 10, 11 and 12 give
`..._scans_10_11_12_full_combined.nxs`; two files give `..._scans_10_11_...`.
If that file exists, the app asks before replacing it.

Details of the method:

- A voxel equal to `0` counts as empty.
- The shift of file 2 is a whole number of pixels along the first and third
  axes, found from the cross-correlation of the two maps' maximum projections
  along the second axis.
- Where file 1 and the shifted file 2 both have data the result is their
  mean; where only one has data, that value is used.
- All files must have the same grid shape.
- The combined file contains `counts` and the axes. It has no `norm` dataset.

The work is streamed a few slices at a time, so combining needs far less
memory than converting.

## Command line

```bash
epiq-convert                                        # open the app
epiq-combine A.nxs B.nxs [C.nxs] -o combined.nxs    # combine without the app
epiq-combine A.nxs B.nxs -o out.nxs --chunk 5       # fewer slices per read
python -m epiq_convert                              # same as epiq-convert
```

A conversion can also be rerun without the app from the saved settings file:

```bash
python -m epiq_map.hkl_convert.auto_rsm <output folder>/logs/BaRuO3_021.txt
```

## Limits

- **Memory**: converting uses a 1000³ grid held in memory several times over.
  Expect to need well over 10 GB of RAM.
- **Speed on macOS**: EpiQ-Map's macOS build runs the histogramming on one
  core. Linux builds use OpenMP.
- **One scan per conversion**: the app does not merge several scans into one
  map during conversion. Convert each scan, then use the Combine tab.
- **Folder naming**: EpiQ-Map finds the images from the folder name, so a
  scan folder that is not named `<Material>_<NNN>` cannot be converted
  without renaming it.
- **Combine takes two or three files**, with the fixed roles above.

## Troubleshooting

| What you see | Likely cause and fix |
|---|---|
| "The scan folder's name must end in the scan number" | Rename the folder to `<Material>_<NNN>`, e.g. `BaRuO3_021` |
| "Scan N is not in this SPEC file" | The wrong SPEC file is chosen, or the folder's number does not match the SPEC scan number |
| "the converter stopped unexpectedly (possibly out of memory)" | Close other programs, or run on a machine with more RAM |
| A conversion finishes but the map looks empty | Check **Show details** for "overloaded frames skipped". Raise **Skip frames brighter than** if most images were skipped |
| "Shape mismatch" when combining | The files were converted with different calibrations or grids |
| The Mac Desktop icon does nothing | Read `~/Library/Logs/EpiQ-Convert.log`. If EpiQ-Map was installed from a folder on an external drive, connect the drive |
| `pip install` asks for a username | The repository is private; sign in to GitHub first |

On macOS, drives formatted as exFAT collect hidden `._name.cbf` files beside
the real images. EpiQ-Map would read them as images and fail, so the app
deletes `._*.cbf` files from the scan folder before each conversion. Nothing
else in the scan folder is modified.

## Status

Version 0.1.0. Written and tested on macOS (Apple silicon) with Python 3.12
and 3.13. It should run on Linux and Windows, where PyQt6 and EpiQ-Map both
provide wheels, but that has not been tried.

What has been checked, and how, is listed in
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md#what-has-been-tested). In short:
conversions were tested end to end on a 40-image subset of a real scan, and
the combine on small synthetic maps. A full-size scan has not been run
through this app.

There is no license file yet, so the usual default applies: all rights
reserved.

## Credits

EpiQ-Map, which does the reconstruction, is by Ben Gregory, Timo Fuchs and
Andrej Singer.
