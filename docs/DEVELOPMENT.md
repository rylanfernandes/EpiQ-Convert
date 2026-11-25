# Development

## Set up

```bash
git clone https://github.com/rylanfernandes/EpiQ-Convert.git
cd EpiQ-Convert
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
epiq-convert
```

## Testing

There is no automated suite. `beamtime.py` has no Qt and can be called
directly. The window is tested by driving it with Qt's offscreen platform:

```bash
QT_QPA_PLATFORM=offscreen python my_check.py
```

```python
import time
from PyQt6 import QtCore, QtWidgets

# Keep test settings out of the real ones.
QtCore.QSettings.setDefaultFormat(QtCore.QSettings.Format.IniFormat)
QtCore.QSettings.setPath(QtCore.QSettings.Format.IniFormat,
                         QtCore.QSettings.Scope.UserScope, "/tmp/epiq-test-settings")
import epiq_convert.app as ec

app = QtWidgets.QApplication([])
w = ec.MainWindow("/path/to/replica/nfs/chess/id4b/2026-3/x-1-a/NAME")
w.show()
w.ask = lambda text: True                 # dialogs cannot be shown offscreen

row = {r["number"]: r for r in w.rows}
w.row_button(row[21])                     # same as clicking the row's button
while w.busy():                           # pump the event loop until done
    app.processEvents(); time.sleep(0.2)
print(w.message.text()); print(w.details.toPlainText())
```

Select rows with
`w.table.selectionModel().select(w.table.model().index(i, 0), Select | Rows)`
and then call `convert_selected()` or `combine_selected()`.

### A replica of the server layout

The app only needs the directory shape, so a replica is cheap:

```
<tmp>/nfs/chess/id4b/2026-3/<beamtime>/<NAME>                     real SPEC file (or a symlink)
<tmp>/nfs/chess/id4b/2026-3/<beamtime>/raw6M/<NAME>/<sample>/<T>/<NAME>_<NNN>/
<tmp>/nfs/chess/id4baux/2026-3/<beamtime>/calibrations/x.poni, mask.edf
```

- Empty scan folders are enough to test detection and the table.
- For a real conversion, fill a scan folder with symlinks to the first few
  dozen `.cbf` images of a real scan. `auto_rsm` uses the images that are
  there. Even 40 images allocate the full 1000³ grid and take a minute or
  more.
- Touch empty files in `processed/output-epiq/transformed_objects/` to test
  the Converted and Combined columns.

## What has been tested

On macOS (Apple silicon), version 0.2.0:

| Checked | How | Result |
|---|---|---|
| SPEC parser | Compared with `spec2nexus` and `make_log_files` on two real SPEC files (80 scans) | No mismatches in th, chi, phi, tth, point counts or scanned motor |
| Layout detection | Replica of the `singer-4778-b` tree built from a real directory listing | Scan root, calibration, mask, output and sibling SPEC files all correct |
| Mask choice | Folder with `mask.edf` and another `.edf` | `mask.edf` chosen, other offered |
| Table | Real `La3Ni2O7-NA-3` SPEC file, replica folders | 9 rows, temperatures, types and angles correct; aborted scan flagged |
| Existing outputs | Touched files in `transformed_objects` | Converted and `7+8+9 ✓` shown on the right rows |
| Selection rules | Several selections | Convert count and combine eligibility correct |
| Convert, queued | Two scans of 40 real images each in a replica of `schlom-4986-a` | Ran one after the other; files named as expected |
| Config written | Read back | Paths, sample and temperature as EpiQ-Map expects |
| Incomplete scan | 40 images vs 3651 SPEC points | Batch skips, row button asks |
| Combine | The two 1000³ maps above | Combined file written with correct shape and axes; Combined column updated |
| Redo then Stop | Stopped during a redo | Old file untouched, no partial file |
| Flat local layout | SPEC file, scan folder and calibrations side by side | Found, sample read from the detector log |
| Load time | Local disk | Under half a second |

Not tested:

- **The real server.** The layout logic comes from directory listings of two
  beamtimes, not from running there.
- A complete full-size conversion through the app.
- Theta-scan conversion (the config follows EpiQ-Map's rule; not run).
- Three-map combine at full size (run on small synthetic maps in 0.1.0).
- Linux and Windows.
- A fresh `pip install` of 0.2.0 (0.1.0 was installed from GitHub into a clean
  environment).

## Making a change

1. Edit under `src/epiq_convert/`.
2. Run the relevant check above.
3. Add a line to `CHANGELOG.md`; update the README and docs if behaviour
   changed.
4. For a release, bump the version in `pyproject.toml` and
   `src/epiq_convert/__init__.py` together.

## Ideas not done

- A smaller-grid option, to fit conversions in less memory.
- Automatic refresh while a beamtime is running.
- An automated test suite built from the patterns above.
