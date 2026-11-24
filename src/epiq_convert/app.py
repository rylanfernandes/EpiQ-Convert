#!/usr/bin/env python
"""EpiQ Convert -- a small window that turns one raw scan folder into a NeXus map
(first tab), and merges two or three converted maps into one (second tab, which runs
combine_rsm.py from this folder).

Give it the scan folder (e.g. .../BaRuO3_021), the SPEC file, the .poni file
and the mask file, then press Convert. The scan is reconstructed by EpiQ-Map's
own autoRSM (`python -m epiq_map.hkl_convert.auto_rsm <config>`), so the
processing is identical to what rsm_monitor does for a lab-frame Q map. No
orientation / U-matrix / indexing steps are involved.

Output:  <output folder>/transformed_objects/<Material>_<Sample>_scans_<N>_full.nxs
"""

import os
import re
import sys

from PyQt6 import QtCore, QtGui, QtWidgets

APP_NAME = "EpiQ Convert"
AUTORSM_MODULE = "epiq_map.hkl_convert.auto_rsm"
SCAN_DIR_RE = re.compile(r"^(?P<material>.+)_(?P<num>\d{3,})$")
TQDM_RE = re.compile(r"(\d+)%\|.*?(\d+)/(\d+)(?:\s*\[([^\]]*)\])?")
RED, GREEN = "#c62828", "#1a7f37"
COMBINE_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "combine_rsm.py")
SCANS_RE = re.compile(r"^(?P<prefix>.*_scans_)(?P<scans>\d+(?:_\d+)*)(?P<rest>.*)\.nxs$")
SLAB_RE = re.compile(r"^(\d+)/(\d+)$")


# ---------------------------------------------------------------------------
# Helpers (no GUI)
# ---------------------------------------------------------------------------

def parse_scan_folder(path):
    """Return (material, scan_number) for a folder named <Material>_<NNN>,
    or None. autoRSM locates images as <parent>/<Material>_<NNN>/, so the
    folder name has to follow that pattern exactly."""
    name = os.path.basename(os.path.normpath(path))
    match = SCAN_DIR_RE.match(name)
    if not match:
        return None
    material, number = match.group("material"), int(match.group("num"))
    if f"{material}_{number:03d}" != name:
        return None
    return material, number


def count_frames(path):
    try:
        return sum(1 for e in os.scandir(path)
                   if e.name.endswith("cbf") and not e.name.startswith("._"))
    except OSError:
        return 0


def hidden_cbf_files(path):
    """macOS '._*.cbf' sidecar files (appear on exFAT/network drives). autoRSM
    would try to read them as frames, so they are removed before a run."""
    try:
        return [e.path for e in os.scandir(path)
                if e.name.startswith("._") and e.name.endswith("cbf")]
    except OSError:
        return []


def detect_sample_and_temperature(scan_dir, material):
    """Read sample name / temperature from the detector's frame log, whose
    paths look like .../raw6M/<material>/<sample>/<temperature>//<scan>/x.cbf.
    Returns (sample or None, temperature or None)."""
    scan_name = os.path.basename(os.path.normpath(scan_dir))
    try:
        logs = sorted(e.path for e in os.scandir(scan_dir)
                      if e.name.endswith(".log") and not e.name.startswith("._"))
    except OSError:
        return None, None
    for log in logs:
        try:
            with open(log, "r", errors="replace") as fh:
                head = fh.read(4000)
        except OSError:
            continue
        for line in head.splitlines():
            if ".cbf" not in line or "/" not in line:
                continue
            parts = [p for p in line.split()[-1].split("/") if p]
            if scan_name in parts:
                i = parts.index(scan_name)
                if i >= 3 and parts[i - 3] == material:
                    temp = parts[i - 1]
                    return parts[i - 2], (int(temp) if temp.isdigit() else None)
    return None, None


def scan_motor(spec_path, scan_number):
    """Motor scanned in SPEC scan #N (same lookup EpiQ-Map's make_log_files
    uses), or None if the scan is not in the file."""
    from epiq_map import make_log_files as mlf
    mlf._spec_cache.pop(spec_path, None)
    return mlf.get_scan_motor(spec_path, scan_number)


def output_path(output_dir, material, sample, scan_number):
    """Where autoRSM writes the lab-frame map for a single scan."""
    return os.path.join(output_dir, "transformed_objects",
                        f"{material}_{sample}_scans_{scan_number}_full.nxs")


def build_config(scan, spec, poni, mask, output_dir, max_intensity):
    """The autoRSM config, in the same format make_log_files writes."""
    is_theta = scan["motor"] == "th"
    scan_list = [] if is_theta else [scan["number"]]
    theta_list = [scan["number"]] if is_theta else []
    parent = os.path.dirname(os.path.normpath(scan["path"]))
    lines = [
        f"PONI File: {poni}",
        f"Material: {scan['material']}",
        f"Sample Name: {scan['sample']}",
        f"Scan Number: {scan['number']}",
        f"Scan List: {scan_list}",
        f"Theta Scan List: {theta_list}",
    ]
    if scan.get("temperature") is not None:
        lines.append(f"Temperature: {scan['temperature']}")
    lines += [
        f"Max Intensity: {max_intensity!r}",
        f"Mask File: {mask}",
        f"Specfile: {spec}",
        f"Temperature Directory: {parent}/",
        f"Image Directory: {os.path.normpath(scan['path'])}/",
        f"Output Directory: {output_dir}",
    ]
    return "\n".join(lines) + "\n"


def combined_name(paths):
    """Output file for a combine, next to the first input. Files named
    <prefix>_scans_10_full.nxs, ..._11_..., ..._12_... give
    <prefix>_scans_10_11_12_full_combined.nxs."""
    folder = os.path.dirname(paths[0])
    matches = [SCANS_RE.match(os.path.basename(p)) for p in paths]
    if all(matches) and len({m.group("prefix") for m in matches}) == 1:
        scans = "_".join(m.group("scans") for m in matches)
        first = matches[0]
        return os.path.join(folder, f"{first.group('prefix')}{scans}{first.group('rest')}_combined.nxs")
    stem = os.path.splitext(os.path.basename(paths[0]))[0]
    return os.path.join(folder, f"{stem}_combined.nxs")


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class PathPicker(QtWidgets.QWidget):
    """A path box you can type/paste into, with a Choose button."""
    changed = QtCore.pyqtSignal()

    def __init__(self, caption, name_filter=None, directory=False, parent=None):
        super().__init__(parent)
        self.caption, self.name_filter, self.directory = caption, name_filter, directory
        self.start_dir = ""
        self._last = ""
        self.edit = QtWidgets.QLineEdit()
        self.edit.setPlaceholderText("Paste a path here, or click Choose…")
        self.edit.editingFinished.connect(self._typed)
        self.button = QtWidgets.QPushButton("Choose…")
        self.button.clicked.connect(self.browse)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button)

    def path(self):
        return self._last

    def _typed(self):
        text = self.edit.text().strip().strip("'\"")
        self.set_path(os.path.expanduser(text) if text else "")

    def set_path(self, path):
        path = os.path.normpath(path) if path else ""
        self.edit.setText(path)
        self.edit.setToolTip(path)
        if path != self._last:
            self._last = path
            self.changed.emit()

    def browse(self):
        start = self.path() if (self.directory and self.path()) else \
            os.path.dirname(self.path()) if self.path() else self.start_dir
        if self.directory:
            chosen = QtWidgets.QFileDialog.getExistingDirectory(self, self.caption, start)
        else:
            chosen, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, self.caption, start, self.name_filter or "All files (*)")
        if chosen:
            self.set_path(chosen)
            self.picked()

    def picked(self):
        """Hook: called when the user picked with the dialog."""


class CombinePanel(QtWidgets.QWidget):
    """Pick two or three converted .nxs files and merge them with combine_rsm.py."""
    ROLES = (("File 1", "The reference scan."),
             ("File 2", "Lined up with file 1, then averaged with it."),
             ("File 3 (optional)", "Fills in the points that are still empty. Leave blank to combine only two."))

    def __init__(self, start_folder, parent=None):
        super().__init__(parent)
        self.start_folder = start_folder      # callable -> folder to browse first
        self.process = None
        self.stopping = False
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 14)
        outer.setSpacing(12)

        top = QtWidgets.QHBoxLayout()
        self.pick_all = QtWidgets.QPushButton("Choose files…")
        self.pick_all.setToolTip("Select two or three .nxs files at once; they are filled in below in name order.")
        self.pick_all.clicked.connect(self.choose_three)
        top.addWidget(QtWidgets.QLabel("Pick two or three converted files to combine:"))
        top.addStretch(1)
        top.addWidget(self.pick_all)
        outer.addLayout(top)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.pickers = []
        for label, role in self.ROLES:
            picker = PathPicker(f"Choose {label.split(' (')[0].lower()}", "NeXus files (*.nxs);;All files (*)")
            picker.changed.connect(self._refresh)
            picker.button.pressed.connect(lambda p=picker: self._prime(p))
            self.pickers.append(picker)
            form.addRow(label, picker)
            hint = QtWidgets.QLabel(role)
            hint.setStyleSheet("color: gray;")
            form.addRow("", hint)
        self.result = QtWidgets.QLabel("–")
        self.result.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.result.setWordWrap(True)
        form.addRow("Combined file", self.result)
        outer.addLayout(form)

        run = QtWidgets.QHBoxLayout()
        self.button = QtWidgets.QPushButton("Combine")
        self.button.setMinimumSize(150, 40)
        font = self.button.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        self.button.setFont(font)
        self.button.clicked.connect(self.combine_clicked)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        run.addWidget(self.button)
        run.addWidget(self.progress, 1)
        outer.addLayout(run)

        bottom = QtWidgets.QHBoxLayout()
        self.message = QtWidgets.QLabel("")
        self.message.setWordWrap(True)
        self.details_button = QtWidgets.QPushButton("Show details")
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self._toggle_details)
        bottom.addWidget(self.message, 1)
        bottom.addWidget(self.details_button)
        outer.addLayout(bottom)
        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont))
        self.details.setVisible(False)
        self.details.setMinimumHeight(120)
        outer.addWidget(self.details, 1)
        outer.addStretch(0)
        self._refresh()

    def _toggle_details(self, shown):
        self.details.setVisible(shown)
        self.details_button.setText("Hide details" if shown else "Show details")

    def _browse_dir(self):
        for picker in self.pickers:
            if picker.path():
                return os.path.dirname(picker.path())
        return self.start_folder() or ""

    def _prime(self, picker):
        picker.start_dir = self._browse_dir()

    def choose_three(self):
        chosen, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Select two or three .nxs files to combine", self._browse_dir(),
            "NeXus files (*.nxs);;All files (*)")
        if not chosen:
            return
        if len(chosen) not in (2, 3):
            self.say(f"Select two or three files (you selected {len(chosen)}).", error=True)
            return
        self.set_files(sorted(chosen))

    def set_files(self, paths):
        for picker, path in zip(self.pickers, list(paths) + ["", ""]):
            picker.set_path(path)
        self._refresh()

    def paths(self):
        """The chosen files; the third is left out when its box is blank."""
        paths = [p.path() for p in self.pickers]
        return paths if paths[2] else paths[:2]

    def say(self, text, error=False, color=None):
        color = RED if error else color
        self.message.setStyleSheet(f"color: {color};" if color else "")
        self.message.setText(text)

    def _problem(self):
        paths = self.paths()
        if not all(paths):
            return "Choose two or three files.", False
        for i, path in enumerate(paths, 1):
            if not os.path.isfile(path):
                return f"File {i} was not found.", True
        if len(set(paths)) < len(paths):
            return "The same file is chosen more than once.", True
        return None, False

    def _refresh(self):
        if self.process:
            return
        problem, is_error = self._problem()
        if problem:
            self.result.setText("–")
            self.say(problem, error=is_error)
            return
        target = combined_name(self.paths())
        self.result.setText(os.path.basename(target))
        self.result.setToolTip(target)
        if os.path.isfile(target):
            self.say("Already combined ✓  (this file exists)", color=GREEN)
        else:
            self.say("Ready to combine.")

    def confirm_replace(self, path):
        answer = QtWidgets.QMessageBox.question(
            self, APP_NAME,
            f"These scans are already combined:\n\n{os.path.basename(path)}\n\n"
            "Combine again and replace that file?")
        return answer == QtWidgets.QMessageBox.StandardButton.Yes

    def combine_clicked(self):
        if self.process:
            self.stopping = True
            self.say("Stopping…")
            self.process.kill()
            return
        for picker in self.pickers:
            picker._typed()
        problem, _ = self._problem()
        if problem:
            self.say(problem, error=True)
            return
        self.target = combined_name(self.paths())
        if os.path.isfile(self.target) and not self.confirm_replace(self.target):
            return
        QtCore.QSettings("EpiQ", "Convert").setValue("last_nxs_dir", os.path.dirname(self.target))
        # Write to a temporary name; it only replaces the real file on success.
        self.partial = self.target + ".partial"
        self.details.clear()
        self.tail = []
        self.stopping = False
        self._buffer = ""
        self._set_running(True)
        self.progress.setValue(0)
        self.say("Lining up file 2 with file 1…")
        self.process = QtCore.QProcess(self)
        self.process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self.process.start(sys.executable,
                           ["-u", COMBINE_SCRIPT, *self.paths(), "-o", self.partial])

    def _set_running(self, running):
        self.button.setText("Stop" if running else "Combine")
        self.pick_all.setEnabled(not running)
        for picker in self.pickers:
            picker.setEnabled(not running)

    def _read_output(self):
        self._buffer += bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        segments = re.split(r"[\r\n]", self._buffer)
        self._buffer = segments.pop()
        for segment in segments:
            self._handle_line(segment.strip())

    def _handle_line(self, line):
        if not line:
            return
        match = SLAB_RE.match(line)
        if match:
            done, total = int(match.group(1)), int(match.group(2))
            self.progress.setValue(int(100 * done / max(total, 1)))
            self.say(f"Combining — slice {done} of {total}")
            return
        self.details.appendPlainText(line)
        self.tail = (self.tail + [line])[-6:]

    def _error(self, error):
        if error == QtCore.QProcess.ProcessError.FailedToStart:
            self._finished(1, QtCore.QProcess.ExitStatus.CrashExit)

    def _finished(self, code, status):
        if self.process is None:
            return
        if self._buffer.strip():
            self._handle_line(self._buffer.strip())
        self.process.deleteLater()
        self.process = None
        self._set_running(False)
        self.progress.setValue(0)
        ok = (code == 0 and status == QtCore.QProcess.ExitStatus.NormalExit
              and os.path.isfile(self.partial))
        if ok:
            os.replace(self.partial, self.target)
            self._refresh()
            self.say(f"Finished ✓  Saved {self.target}", color=GREEN)
        else:
            if os.path.isfile(self.partial):
                try:
                    os.remove(self.partial)
                except OSError:
                    pass
            self._refresh()
            if self.stopping:
                self.say("Stopped. Nothing half-finished was kept.")
            else:
                reason = next((l for l in reversed(self.tail) if not l.startswith("Saved")),
                              f"exit code {code}")
                self.say(f"Failed: {reason}", error=True)
                self.details_button.setChecked(True)
        self.stopping = False
        QtWidgets.QApplication.alert(self)

    def kill(self):
        if self.process:
            self.stopping = True
            self.process.kill()
            self.process.waitForFinished(5000)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(760, 470)
        self.setAcceptDrops(True)
        self.scan = None            # dict describing the chosen scan, or None
        self.problem = "Choose the scan folder."
        self.process = None
        self.stopping = False
        self.output_touched = False
        self.settings = QtCore.QSettings("EpiQ", "Convert")
        self._build_ui()
        self._restore()
        self._refresh()

    # ---- layout ----------------------------------------------------------
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setDocumentMode(True)
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(central, "Convert a scan")
        self.combine = CombinePanel(self._nxs_folder)
        self.tabs.addTab(self.combine, "Combine scans")
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(20, 18, 20, 14)
        outer.setSpacing(12)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(9)

        self.folder = PathPicker("Choose the scan folder (e.g. BaRuO3_021)", directory=True)
        self.folder.changed.connect(self._folder_changed)
        self.info = QtWidgets.QLabel("")
        self.info.setStyleSheet("color: gray;")
        self.sample = QtWidgets.QLineEdit()
        self.sample.setMaximumWidth(200)
        self.sample.setToolTip("Used in the output file name.")
        self.sample.editingFinished.connect(self._sample_edited)
        self.spec = PathPicker("Choose the SPEC file")
        self.poni = PathPicker("Choose the calibration (.poni) file", "PONI files (*.poni);;All files (*)")
        self.mask = PathPicker("Choose the mask file", "Mask files (*.edf);;All files (*)")
        self.output = PathPicker("Choose the output folder", directory=True)
        self.output.picked = lambda: setattr(self, "output_touched", True)
        self.output.edit.textEdited.connect(lambda: setattr(self, "output_touched", True))
        self.max_intensity = QtWidgets.QLineEdit("1e5")
        self.max_intensity.setMaximumWidth(110)
        self.max_intensity.setToolTip(
            "Frames whose brightest unmasked pixel exceeds this are left out "
            "(detector overload). 1e5 is the EpiQ-Map default.")

        form.addRow("Scan folder", self.folder)
        form.addRow("", self.info)
        form.addRow("Sample name", self.sample)
        form.addRow("SPEC file", self.spec)
        form.addRow("Calibration (.poni)", self.poni)
        form.addRow("Mask (.edf)", self.mask)
        form.addRow("Save output in", self.output)
        form.addRow("Skip frames brighter than", self.max_intensity)
        outer.addLayout(form)
        for picker in (self.spec, self.poni, self.mask, self.output):
            picker.changed.connect(self._refresh)

        run = QtWidgets.QHBoxLayout()
        self.convert_button = QtWidgets.QPushButton("Convert")
        self.convert_button.setMinimumSize(150, 40)
        self.convert_button.setDefault(True)
        font = self.convert_button.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        self.convert_button.setFont(font)
        self.convert_button.clicked.connect(self.convert_clicked)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        run.addWidget(self.convert_button)
        run.addWidget(self.progress, 1)
        outer.addLayout(run)

        bottom = QtWidgets.QHBoxLayout()
        self.message = QtWidgets.QLabel("")
        self.message.setWordWrap(True)
        self.open_button = QtWidgets.QPushButton("Show output folder")
        self.open_button.clicked.connect(self.open_output)
        self.details_button = QtWidgets.QPushButton("Show details")
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self._toggle_details)
        bottom.addWidget(self.message, 1)
        bottom.addWidget(self.open_button)
        bottom.addWidget(self.details_button)
        outer.addLayout(bottom)

        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumBlockCount(2000)
        self.details.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont))
        self.details.setVisible(False)
        self.details.setMinimumHeight(140)
        outer.addWidget(self.details, 1)
        outer.addStretch(0)

    def _toggle_details(self, shown):
        self.details.setVisible(shown)
        self.details_button.setText("Hide details" if shown else "Show details")

    # ---- settings ---------------------------------------------------------
    def _restore(self):
        for key, picker in (("spec", self.spec), ("poni", self.poni), ("mask", self.mask)):
            path = self.settings.value(key, "", str)
            if path and os.path.isfile(path):
                picker.set_path(path)
        self.max_intensity.setText(self.settings.value("max_intensity", "1e5", str))
        self.folder.start_dir = self.settings.value("last_dir", "", str)

    def _save(self):
        for key, picker in (("spec", self.spec), ("poni", self.poni), ("mask", self.mask)):
            self.settings.setValue(key, picker.path())
        self.settings.setValue("max_intensity", self.max_intensity.text().strip())
        if self.folder.path():
            self.settings.setValue("last_dir", os.path.dirname(self.folder.path()))

    # ---- the scan ---------------------------------------------------------
    def _folder_changed(self):
        """Read everything we can from the chosen scan folder."""
        path = self.folder.path()
        self.scan = None
        if not path:
            self.problem = "Choose the scan folder."
        elif not os.path.isdir(path):
            self.problem = "That scan folder does not exist."
        elif not parse_scan_folder(path):
            self.problem = ("The scan folder's name must end in the scan number, "
                            "like BaRuO3_021.")
        elif not count_frames(path):
            self.problem = "There are no .cbf images in that folder."
        else:
            material, number = parse_scan_folder(path)
            sample, temperature = detect_sample_and_temperature(path, material)
            self.scan = {"path": path, "material": material, "number": number,
                         "frames": count_frames(path), "sample": sample or "sample",
                         "temperature": temperature, "motor": None}
            self.sample.setText(self.scan["sample"])
            parent = os.path.dirname(path)
            for picker in (self.spec, self.poni, self.mask, self.output):
                picker.start_dir = parent
            if not self.output_touched or not self.output.path():
                self.output.set_path(os.path.join(parent, "output"))
        self._refresh()

    def _sample_edited(self):
        text = re.sub(r"[/\\:\s]+", "_", self.sample.text().strip()) or "sample"
        self.sample.setText(text)
        if self.scan:
            self.scan["sample"] = text
        self._refresh()

    def target(self):
        s = self.scan
        return output_path(self.output.path(), s["material"], s["sample"], s["number"])

    def _refresh(self):
        """Re-check the scan against the SPEC file and the output folder, and
        say in one line what state things are in."""
        if self.process:
            return
        self.open_button.setEnabled(os.path.isdir(self.output.path()))
        self.sample.setEnabled(self.scan is not None)
        s = self.scan
        if s is None:
            self.info.setText("")
            self.say(self.problem, error=bool(self.folder.path()))
            return
        s["motor"] = None
        spec = self.spec.path()
        if spec and os.path.isfile(spec):
            s["motor"] = scan_motor(spec, s["number"])
        kind = {None: "", "th": " · theta scan"}.get(s["motor"], f" · {s['motor']} scan")
        self.info.setText(f"{s['material']} · scan {s['number']}{kind} · {s['frames']} images")

        if not spec:
            self.say("Choose the SPEC file.")
        elif not os.path.isfile(spec):
            self.say("The SPEC file was not found.", error=True)
        elif s["motor"] is None:
            self.say(f"Scan {s['number']} is not in this SPEC file.", error=True)
        elif not self.poni.path() or not self.mask.path():
            self.say("Choose the calibration and mask files.")
        elif self.output.path() and os.path.isfile(self.target()):
            self.say(f"Already converted ✓  {os.path.basename(self.target())}", color=GREEN)
        else:
            self.say("Ready to convert.")

    # ---- drag and drop ----------------------------------------------------
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and not self.process:
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isdir(path):
                self.folder.set_path(path)
            elif path.endswith(".poni"):
                self.poni.set_path(path)
            elif path.endswith(".edf"):
                self.mask.set_path(path)

    # ---- converting -------------------------------------------------------
    def say(self, text, error=False, color=None):
        color = RED if error else color
        self.message.setStyleSheet(f"color: {color};" if color else "")
        self.message.setText(text)

    def log(self, text):
        self.details.appendPlainText(text)

    def confirm_replace(self, path):
        answer = QtWidgets.QMessageBox.question(
            self, APP_NAME,
            f"This scan is already converted:\n\n{os.path.basename(path)}\n\n"
            "Convert it again and replace that file?")
        return answer == QtWidgets.QMessageBox.StandardButton.Yes

    def convert_clicked(self):
        if self.process:
            self.stopping = True
            self.say("Stopping…")
            self.process.kill()
            return
        # Pick up anything typed but not yet confirmed with Enter/Tab.
        for picker in (self.folder, self.spec, self.poni, self.mask, self.output):
            picker._typed()
        self._sample_edited()

        s = self.scan
        problems = []
        if s is None:
            problems.append(self.problem)
        for label, picker in (("the SPEC file", self.spec),
                              ("the calibration (.poni) file", self.poni),
                              ("the mask file", self.mask)):
            if not picker.path():
                problems.append(f"Choose {label}.")
            elif not os.path.isfile(picker.path()):
                problems.append(f"{label[0].upper() + label[1:]} was not found.")
        if s and s["motor"] is None and self.spec.path() and os.path.isfile(self.spec.path()):
            problems.append(f"Scan {s['number']} is not in this SPEC file.")
        if not self.output.path():
            problems.append("Choose where to save the output.")
        try:
            limit = float(self.max_intensity.text())
            if limit <= 0:
                raise ValueError
        except ValueError:
            problems.append("“Skip frames brighter than” must be a positive number such as 1e5.")
        if problems:
            self.say(" ".join(problems), error=True)
            return

        self.redo_target = None
        if os.path.isfile(self.target()):
            if not self.confirm_replace(self.target()):
                return
            self.redo_target = self.target()
        try:
            os.makedirs(os.path.join(self.output.path(), "logs"), exist_ok=True)
        except OSError as exc:
            self.say(f"Cannot create the output folder: {exc}", error=True)
            return
        self._save()

        removed = 0
        for junk in hidden_cbf_files(s["path"]):
            try:
                os.remove(junk)
                removed += 1
            except OSError:
                pass
        self.details.clear()
        if removed:
            self.log(f"Removed {removed} hidden macOS '._' files from {s['path']}")

        config_path = os.path.join(self.output.path(), "logs",
                                   f"{s['material']}_{s['number']:03d}.txt")
        with open(config_path, "w") as fh:
            fh.write(build_config(s, self.spec.path(), self.poni.path(),
                                  self.mask.path(), self.output.path(), limit))

        self.written = self.saved = None
        self.tail = []
        self.stopping = False
        self._buffer = ""
        self._set_running(True)
        self.progress.setValue(0)
        self.say(f"Converting scan {s['number']} — getting ready…")

        self.process = QtCore.QProcess(self)
        self.process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
        env = QtCore.QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        self.process.setProcessEnvironment(env)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.process.start(sys.executable, ["-u", "-m", AUTORSM_MODULE, config_path])

    def _set_running(self, running):
        self.convert_button.setText("Stop" if running else "Convert")
        for widget in (self.folder, self.sample, self.spec, self.poni, self.mask,
                       self.output, self.max_intensity):
            widget.setEnabled(not running)

    def _read_output(self):
        self._buffer += bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        segments = re.split(r"[\r\n]", self._buffer)
        self._buffer = segments.pop()
        for segment in segments:
            self._handle_line(segment.strip())

    def _handle_line(self, line):
        if not line:
            return
        number = self.scan["number"]
        match = TQDM_RE.search(line)
        if match:
            percent, n, total, timing = match.groups()
            self.progress.setValue(min(100, int(percent)))
            left = ""
            if timing and "<" in timing:
                left = timing.split("<")[1].split(",")[0].strip()
                left = f", about {left} left" if left and "?" not in left else ""
            if int(n) == int(total):
                self.say(f"Scan {number}: all images done, writing the NeXus file "
                         "(this can take a few minutes)…")
            else:
                self.say(f"Converting scan {number} — image {n} of {total}{left}")
            return
        self.log(line)
        self.tail = (self.tail + [line])[-6:]
        if line.startswith("Output file: "):
            self.written = line[len("Output file: "):].strip()
        elif line.startswith("Saved "):
            self.saved = line[len("Saved "):].strip()

    def _process_error(self, error):
        if error == QtCore.QProcess.ProcessError.FailedToStart:
            self.log(f"Could not start Python: {sys.executable}")
            self._process_finished(1, QtCore.QProcess.ExitStatus.CrashExit)

    def _process_finished(self, code, status):
        if self.process is None:
            return
        if self._buffer.strip():
            self._handle_line(self._buffer.strip())
        self.process.deleteLater()
        self.process = None
        self._set_running(False)
        self.progress.setValue(0)
        ok = (code == 0 and status == QtCore.QProcess.ExitStatus.NormalExit
              and self.saved and os.path.isfile(self.saved))
        if ok:
            if self.redo_target and self.saved != self.redo_target:
                os.replace(self.saved, self.redo_target)   # swap in the new file
            self._refresh()
            self.say(f"Finished ✓  Saved {self.target()}", color=GREEN)
        else:
            # Remove a half-written file from this run (never the earlier one).
            if self.written and self.written != self.redo_target \
                    and os.path.isfile(self.written):
                try:
                    os.remove(self.written)
                except OSError:
                    pass
            self._refresh()
            if self.stopping:
                self.say("Stopped. Nothing half-finished was kept.")
            else:
                reason = self.tail[-1] if self.tail else f"exit code {code}"
                if status == QtCore.QProcess.ExitStatus.CrashExit:
                    reason = "the converter stopped unexpectedly (possibly out of memory)"
                self.say(f"Failed: {reason}", error=True)
                self.details_button.setChecked(True)
        self.stopping = False
        QtWidgets.QApplication.alert(self)

    def _nxs_folder(self):
        """Where converted files are -- the Combine tab starts browsing here."""
        folder = os.path.join(self.output.path(), "transformed_objects")
        if os.path.isdir(folder):
            return folder
        return self.settings.value("last_nxs_dir", "", str)

    def open_output(self):
        folder = os.path.join(self.output.path(), "transformed_objects")
        if not os.path.isdir(folder):
            folder = self.output.path()
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder))

    def closeEvent(self, event):
        if self.combine.process and not self.process:
            answer = QtWidgets.QMessageBox.question(
                self, APP_NAME, "A combine is running. Stop it and quit?")
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        if self.process:
            answer = QtWidgets.QMessageBox.question(
                self, APP_NAME, "A conversion is running. Stop it and quit?")
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.stopping = True
            self.process.kill()
            self.process.waitForFinished(5000)
        self.combine.kill()
        self._save()
        event.accept()


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    try:
        import epiq_map.hkl_convert  # noqa: F401  (the processing code)
    except Exception as exc:
        QtWidgets.QMessageBox.critical(
            None, APP_NAME,
            "The EpiQ-Map processing code could not be loaded.\n\n"
            "If EpiQ-Map is installed from a folder on an external drive, "
            "connect that drive and open the app again. Otherwise run setup.sh "
            "again.\n\n"
            f"Details: {exc}")
        return 1
    window = MainWindow()
    window.show()
    window.raise_()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
