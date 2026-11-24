# Development

## Set up

```bash
git clone https://github.com/rylanfernandes/EpiQ-Convert.git
cd EpiQ-Convert
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
epiq-convert
```

`pip install -e .` makes edits to `src/epiq_convert/` take effect the next
time the app is opened. There is no build step.

To develop against a local EpiQ-Map checkout, install it first with
`pip install -e /path/to/EpiQ-Map`.

## Testing

There is no automated test suite. Changes so far were verified by driving the
real window without a display, using Qt's offscreen platform:

```bash
QT_QPA_PLATFORM=offscreen python my_check.py
```

The pattern for such a script:

```python
import time
from PyQt6 import QtWidgets
import epiq_convert.app as ec

app = QtWidgets.QApplication([])
w = ec.MainWindow(); w.show()

w.folder.set_path("/data/BaRuO3_021")
w.spec.set_path("/data/BaRuO3")
w.poni.set_path("/data/calibrations/CeO2_20keV.poni")
w.mask.set_path("/data/calibrations/mask_20keV.edf")
w.output.set_path("/tmp/out"); w.output_touched = True
print(w.info.text(), "|", w.message.text())

w.confirm_replace = lambda path: True      # dialogs cannot be shown offscreen
w.convert_clicked()
while w.process:                           # pump the event loop until done
    app.processEvents(); time.sleep(0.2)
print(w.message.text()); print(w.details.toPlainText())
```

Notes:

- Replace `confirm_replace` (on `MainWindow` and on `CombinePanel`) with a
  lambda; a real `QMessageBox` would block forever.
- The Combine tab is `w.combine`; use `set_files([...])`,
  `combine_clicked()` and wait on `w.combine.process`.
- Settings persist between runs through `QSettings`, including in tests.

### Getting a fast conversion test

A full scan is thousands of images and takes a long time. For a quick end to
end check, make a folder with the same name as the scan folder containing
only the first few dozen images (symlinks are fine, on a filesystem that
supports them) plus the `.log` file. `auto_rsm` warns that the image count
does not match the SPEC file and uses the images that are there.

Even a 40-image run allocates the full 1000³ grid and takes a few minutes,
most of it setup and saving.

### Getting test files for the combine

Small synthetic maps in the same layout `auto_rsm` writes:

```python
import numpy as np
from nexusformat.nexus import NXdata, NXfield
ax = np.linspace(-1, 1, 48).astype("float32")
vol = ...  # (48, 48, 48) float32, zeros where "unmeasured"
NXdata(NXfield(vol, name="counts"),
       (NXfield(ax, name="H"), NXfield(ax, name="K"), NXfield(ax, name="L"))
       ).save("X_scans_10_full.nxs")
```

Use a volume with a clear feature (for example a Gaussian peak) if the
alignment is to be checked; `np.roll` one copy by a known shift and confirm
the reported shift is its negative.

## What has been tested

On macOS, Apple silicon:

| Checked | How | Result |
|---|---|---|
| Convert end to end | First 40 images of a real 3651-image phi scan | Valid `.nxs` written, named as expected |
| Already-converted detection | Second Convert on the same scan | Asks before replacing; declining does nothing |
| Stop | Killed mid-run during a redo | Old file kept, no partial file left |
| Bad scan folders | Missing folder, wrong name, no images | Clear message for each |
| Real full scan folder recognised | Loaded, not converted | Material, number, type, image count, sample all correct |
| Combine, three files | 48³ synthetic maps | Correct axes and shape; third file filled exactly the empty voxels |
| Combine, two files | Same maps | Identical to the three-file result except the unfilled voxels |
| Alignment | Known shift applied to file 2 | Shift recovered |
| `pip install` from GitHub | Fresh virtual environment | Both commands installed; app loaded and read a scan folder |

Not tested:

- A complete full-size conversion through the app.
- A combine of real 1000³ maps.
- Theta scans (the code path exists and mirrors EpiQ-Map's rule).
- Linux and Windows.
- `setup.sh` creating a brand-new environment (only the path where the
  environment already exists was run).

## Making a change

1. Edit under `src/epiq_convert/`.
2. Run the relevant check above.
3. Add a line to `CHANGELOG.md`.
4. For a release, bump the version in both `pyproject.toml` and
   `src/epiq_convert/__init__.py`.

## Ideas not done

- A smaller-grid option, to fit conversions in less memory. `auto_rsm`
  accepts `H Range`, `K Range`, `L Range` and `Grid Shape` together; the
  default ranges would have to be computed from the `.poni` as
  `auto_rsm.build_grids` does.
- More than three files in a combine. This needs a decision about which
  files are averaged and which only fill.
- An automated test suite built from the offscreen pattern above.
