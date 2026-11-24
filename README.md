# EpiQ Convert

A small Mac app for turning raw CHESS QM2 area-detector scans into NeXus
reciprocal-space maps, without writing a config file.

It is a front end for [EpiQ-Map](https://github.com/as3689-a11y/EpiQ-Map):
the conversion is done by EpiQ-Map's own `auto_rsm` module, so the output is
the same lab-frame Q map that `rsm_monitor` produces. There is no orientation
(U matrix) or HKL indexing step.

## Install

Requires macOS, Python 3, and the Xcode command-line tools.

```bash
git clone <this repo> ~/EpiQ-Convert
cd ~/EpiQ-Convert
./setup.sh                      # installs EpiQ-Map from PyPI
# or, with a local EpiQ-Map checkout:
./setup.sh /path/to/EpiQ-Map
```

`setup.sh` creates a Python environment in `~/.venvs/epiq-map`, installs
EpiQ-Map into it, and puts **EpiQ Convert** on the Desktop. Keep this folder
where it is afterwards; the Desktop icon points at it.

## Convert a scan

1. **Scan folder**: paste, choose or drag in the folder of `.cbf` images. Its
   name must end in the scan number, like `BaRuO3_021`.
2. Choose the **SPEC file**, the **calibration (.poni)** file and the **mask
   (.edf)** file. These are remembered for next time.
3. Press **Convert**.

The app reads the scan type (phi or theta) from the SPEC file and the sample
name from the detector's `.log` file in the scan folder. The map is written to

```
<output folder>/transformed_objects/<Material>_<Sample>_scans_<N>_full.nxs
```

where the output folder defaults to `output` next to the scan folder. If that
file already exists, the app says so and asks before replacing it.

**Skip frames brighter than** is EpiQ-Map's `Max Intensity` setting (default
`1e5`): frames whose brightest unmasked pixel exceeds it are left out as
detector overloads.

The default map is a 1000 x 1000 x 1000 grid, so a conversion needs well over
10 GB of memory, and on macOS it runs single-threaded.

## Combine scans

The second tab merges two or three converted maps with `combine_rsm.py`:

- **File 1** is the reference.
- **File 2** is aligned to file 1 (integer-pixel shift from the
  cross-correlation of max-projections) and averaged with it.
- **File 3** (optional) fills the voxels that are still empty.

All files must be on the same grid. The result is written next to file 1 as
`..._scans_<a>_<b>_<c>_full_combined.nxs`.

The script also runs on its own:

```bash
python combine_rsm.py A.nxs B.nxs [C.nxs] -o combined.nxs
```

## Files

| File | Purpose |
|---|---|
| `epiq_convert.py` | The app (PyQt6) |
| `combine_rsm.py` | Streaming combine of two or three maps |
| `setup.sh` | Environment, EpiQ-Map install, Desktop icon |

If the app does not open, see `~/Library/Logs/EpiQ-Convert.log`.
