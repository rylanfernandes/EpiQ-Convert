#!/usr/bin/env python
"""EpiQ Convert -- choose a SPEC file, see its scans, convert and combine them.

Choosing the SPEC file is enough: the image folders, calibration, mask and
output folder are found from the standard CHESS QM2 layout (see beamtime.py).
The window then lists every scan that has images, with its set temperature,
type and angles, and whether its map already exists.

Processing is not done here. A conversion runs EpiQ-Map's own autoRSM
(`python -m epiq_map.hkl_convert.auto_rsm <config>`) and a combine runs
combine_rsm.py, each as a child process, one at a time.
"""

import importlib.util
import json
import os
import re
import sys

from PyQt6 import QtCore, QtGui, QtWidgets

from . import beamtime as bt

APP_NAME = "EpiQ Convert"
AUTORSM_MODULE = "epiq_map.hkl_convert.auto_rsm"
COMBINE_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "combine_rsm.py")
TQDM_RE = re.compile(r"(\d+)%\|.*?(\d+)/(\d+)(?:\s*\[([^\]]*)\])?")
SLAB_RE = re.compile(r"^(\d+)/(\d+)$")
RED, GREEN, BLUE, GRAY = "#c62828", "#1a7f37", "#0b5fff", "#808080"

COLUMNS = ("Scan", "Sample", "T (K)", "Type", "Range", "th", "chi", "phi",
           "Points", "Status", "Combined", "")
(C_SCAN, C_SAMPLE, C_TEMP, C_TYPE, C_RANGE, C_TH, C_CHI, C_PHI,
 C_POINTS, C_STATUS, C_COMBINED, C_ACTION) = range(len(COLUMNS))


def fmt(value):
    """A motor position without trailing zeros: 1, 0.5, 89.8, -0.05."""
    if value is None:
        return "–"
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def temp_key(row):
    return (row["temperature"] if row["temperature"] is not None else 10 ** 9, row["temp"])


SORT_KEYS = {
    C_SCAN: lambda r: r["number"],
    C_SAMPLE: lambda r: (r["sample"], temp_key(r), r["number"]),
    C_TEMP: lambda r: (temp_key(r), r["sample"], r["number"]),
    C_TYPE: lambda r: (r["kind"], r["number"]),
    C_TH: lambda r: (r["th"] if r["th"] is not None else 0, r["number"]),
    C_CHI: lambda r: (r["chi"] if r["chi"] is not None else 0, r["number"]),
    C_STATUS: lambda r: (not r["converted"], r["number"]),
    C_COMBINED: lambda r: (not r["combined"], r["number"]),
}


class PathPicker(QtWidgets.QWidget):
    """A path box with a drop-down of detected choices and a Choose button.
    `changed` fires whenever the path changes; `edited` only when the user
    changed it (typed, picked from the list, or used Choose)."""
    changed = QtCore.pyqtSignal()
    edited = QtCore.pyqtSignal()

    def __init__(self, caption, name_filter=None, directory=False, parent=None):
        super().__init__(parent)
        self.caption, self.name_filter, self.directory = caption, name_filter, directory
        self._path = ""
        self.box = QtWidgets.QComboBox()
        self.box.setEditable(True)
        self.box.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.box.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.box.setMinimumContentsLength(30)
        self.box.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                               QtWidgets.QSizePolicy.Policy.Fixed)
        self.box.lineEdit().setPlaceholderText("Not found – paste a path or click Choose…")
        self.box.activated.connect(lambda _: self._user(self.box.currentText()))
        self.box.lineEdit().editingFinished.connect(self.commit)
        self.button = QtWidgets.QPushButton("Choose…")
        self.button.clicked.connect(self.browse)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.box, 1)
        layout.addWidget(self.button)

    def path(self):
        return self._path

    def commit(self):
        """Take what is typed in the box as the path."""
        text = self.box.currentText().strip().strip("'\"")
        text = os.path.normpath(os.path.expanduser(text)) if text else ""
        if text != self._path:
            self._user(text)

    def _user(self, path):
        if self._set(path):
            self.edited.emit()

    def _set(self, path):
        path = os.path.normpath(path) if path else ""
        self.box.blockSignals(True)
        self.box.setEditText(path)
        self.box.blockSignals(False)
        self.box.setToolTip(path)
        if path == self._path:
            return False
        self._path = path
        self.changed.emit()
        return True

    def set_options(self, options, current=""):
        """Fill the drop-down with detected choices and select one."""
        self.box.blockSignals(True)
        self.box.clear()
        self.box.addItems(list(options))
        self.box.blockSignals(False)
        self._set(current)

    def set_path(self, path):
        self._set(path)

    def browse(self):
        start = self._path if (self.directory and self._path) else os.path.dirname(self._path)
        if self.directory:
            chosen = QtWidgets.QFileDialog.getExistingDirectory(self, self.caption, start)
        else:
            chosen, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, self.caption, start, self.name_filter or "All files (*)")
        if chosen:
            self._user(chosen)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, spec_path=None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1080, 700)
        self.rows = []           # one dict per scan, see beamtime.load_scans
        self.layout_info = None
        self.jobs = []           # waiting jobs
        self.job = None          # running job
        self.process = None
        self.stopping = False
        self.stop_all = False
        self.sort_column, self.sort_reverse = None, False
        self.settings = QtCore.QSettings("EpiQ", "Convert")
        self._build_ui()
        self.max_intensity.setText(self.settings.value("max_intensity", "1e5", str))
        spec_path = spec_path or self.settings.value("spec", "", str)
        if spec_path and os.path.isfile(spec_path):
            self.spec.set_path(spec_path)
        else:
            self._update_actions()
            self.say("Choose a SPEC file to list its scans.")

    # ---- layout ----------------------------------------------------------
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(10)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(6)
        self.spec = PathPicker("Choose the SPEC file")
        self.scans_root = PathPicker("Choose the folder that holds the scan folders", directory=True)
        self.poni = PathPicker("Choose the calibration (.poni) file", "PONI files (*.poni);;All files (*)")
        self.mask = PathPicker("Choose the mask file", "Mask files (*.edf);;All files (*)")
        self.output = PathPicker("Choose the output folder", directory=True)
        self.max_intensity = QtWidgets.QLineEdit("1e5")
        self.max_intensity.setMaximumWidth(110)
        self.max_intensity.setToolTip(
            "Images whose brightest unmasked pixel exceeds this are left out "
            "(detector overload). 1e5 is the EpiQ-Map default.")
        form.addRow("SPEC file", self.spec)
        form.addRow("Scan images in", self.scans_root)
        form.addRow("Calibration (.poni)", self.poni)
        form.addRow("Mask (.edf)", self.mask)
        form.addRow("Save output in", self.output)
        form.addRow("Skip images brighter than", self.max_intensity)
        outer.addLayout(form)
        self.spec.changed.connect(self._spec_changed)
        self.scans_root.changed.connect(self.reload)
        self.output.changed.connect(self.check_outputs)
        self.pickers = {"poni": self.poni, "mask": self.mask, "output": self.output}
        for key, picker in self.pickers.items():
            picker.edited.connect(lambda k=key: self._remember_manual(k))

        bar = QtWidgets.QHBoxLayout()
        self.info = QtWidgets.QLabel("")
        self.info.setStyleSheet(f"color: {GRAY};")
        self.refresh_button = QtWidgets.QPushButton("Refresh")
        self.refresh_button.setToolTip("Re-read the SPEC file and look again for scans and output files.")
        self.refresh_button.clicked.connect(self.reload)
        bar.addWidget(self.info, 1)
        bar.addWidget(self.refresh_button)
        outer.addLayout(bar)

        self.table = QtWidgets.QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(C_STATUS, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._sort_clicked)
        self.table.itemSelectionChanged.connect(self._update_actions)
        outer.addWidget(self.table, 1)

        actions = QtWidgets.QHBoxLayout()
        self.convert_button = QtWidgets.QPushButton("Convert selected")
        self.combine_button = QtWidgets.QPushButton("Combine selected")
        self.stop_button = QtWidgets.QPushButton("Stop all")
        for button in (self.convert_button, self.combine_button):
            button.setMinimumHeight(34)
            font = button.font()
            font.setBold(True)
            button.setFont(font)
        self.convert_button.clicked.connect(self.convert_selected)
        self.combine_button.clicked.connect(self.combine_selected)
        self.stop_button.clicked.connect(self.stop_everything)
        self.stop_button.setVisible(False)
        self.selection_note = QtWidgets.QLabel("")
        self.selection_note.setStyleSheet(f"color: {GRAY};")
        actions.addWidget(self.convert_button)
        actions.addWidget(self.combine_button)
        actions.addWidget(self.selection_note, 1)
        actions.addWidget(self.stop_button)
        outer.addLayout(actions)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(10)
        outer.addWidget(self.progress)

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
        self.details.setMaximumBlockCount(3000)
        self.details.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont))
        self.details.setVisible(False)
        self.details.setFixedHeight(150)
        outer.addWidget(self.details)

    def _toggle_details(self, shown):
        self.details.setVisible(shown)
        self.details_button.setText("Hide details" if shown else "Show details")

    def say(self, text, error=False, color=None):
        color = RED if error else color
        self.message.setStyleSheet(f"color: {color};" if color else "")
        self.message.setText(text)

    def log(self, text):
        self.details.appendPlainText(text)

    def ask(self, text):
        """Yes/No question. (A method so tests can replace it.)"""
        answer = QtWidgets.QMessageBox.question(self, APP_NAME, text)
        return answer == QtWidgets.QMessageBox.StandardButton.Yes

    # ---- finding everything from the SPEC file ---------------------------
    def _manual(self):
        try:
            return json.loads(self.settings.value("manual", "{}", str))
        except ValueError:
            return {}

    def _remember_manual(self, key):
        """Remember a file or folder the user chose by hand, per beamtime, so
        it is used again instead of the detected one."""
        if not self.layout_info:
            return
        manual = self._manual()
        manual.setdefault(self.layout_info["beam"], {})[key] = self.pickers[key].path()
        self.settings.setValue("manual", json.dumps(manual))

    def _spec_changed(self):
        spec = self.spec.path()
        if not os.path.isfile(spec):
            self.layout_info = None
            self.rows = []
            self._rebuild_table()
            self.info.setText("")
            self.say("That SPEC file was not found." if spec else "Choose a SPEC file to list its scans.",
                     error=bool(spec))
            return
        lay = self.layout_info = bt.detect_layout(spec)
        self.settings.setValue("spec", spec)
        manual = self._manual().get(lay["beam"], {})

        def pick(key, detected, exists):
            chosen = manual.get(key, "")
            return chosen if chosen and exists(chosen) else detected

        for picker in (self.spec, self.scans_root, self.output):
            picker.blockSignals(True)      # one reload below, not several
        self.spec.set_options(lay["siblings"] or [spec], spec)
        # The image folder belongs to this SPEC file, so it is always
        # detected afresh; the others can be remembered per beamtime.
        self.scans_root.set_options([lay["scans_root"]], lay["scans_root"])
        self.poni.set_options(lay["ponis"], pick("poni", (lay["ponis"] or [""])[0], os.path.isfile))
        self.mask.set_options(lay["masks"], pick("mask", (lay["masks"] or [""])[0], os.path.isfile))
        self.output.set_options([lay["output"]], pick("output", lay["output"], bool))
        for picker in (self.spec, self.scans_root, self.output):
            picker.blockSignals(False)
        self.reload()

    def reload(self):
        """Re-read the SPEC file, find the scan folders, check the outputs."""
        if self.busy() or not self.layout_info:
            return
        selected = {row["path"] for row in self.selected_rows()}
        try:
            self.rows = bt.load_scans(self.spec.path(), self.scans_root.path())
        except OSError as exc:
            self.rows = []
            self.say(f"Could not read the SPEC file: {exc}", error=True)
        for row in self.rows:
            row.update(state="", note="", percent=0, converted=False, combined=[])
        self._sort_rows()
        self._rebuild_table(selected)
        self.check_outputs()
        if not self.rows:
            self.say("No scan folders were found for this SPEC file. "
                     "Check “Scan images in”.", error=True)
        elif not self.poni.path() or not self.mask.path():
            self.say("The calibration or mask file was not found automatically – choose it above.",
                     error=True)
        else:
            self.say("Select scans and press Convert, or use the button in a row.")

    def check_outputs(self):
        """Look in the output folder for maps that already exist."""
        state = bt.output_state(self.output.path(), self.rows)
        for row in self.rows:
            row["converted"], row["combined"] = state[id(row)]
        for index in range(len(self.rows)):
            self._paint_row(index)
        self._update_info()
        self._update_actions()
        self.open_button.setEnabled(os.path.isdir(self.output.path()))

    def _update_info(self):
        lay = self.layout_info
        if not lay:
            return
        bits = []
        if lay["run"]:
            bits.append(f"Run {lay['run']} · {lay['beamtime']}")
        phi = sum(r["kind"] == "phi" for r in self.rows)
        bits.append(f"{lay['material']}: {len(self.rows)} scans with images "
                    f"({phi} phi, {len(self.rows) - phi} theta)")
        bits.append(f"{sum(r['converted'] for r in self.rows)} converted")
        self.info.setText("   ·   ".join(bits))

    # ---- table ------------------------------------------------------------
    def _sort_clicked(self, column):
        if column not in SORT_KEYS:
            return
        self.sort_reverse = (not self.sort_reverse) if column == self.sort_column else False
        self.sort_column = column
        selected = {row["path"] for row in self.selected_rows()}
        self._sort_rows()
        self._rebuild_table(selected)

    def _sort_rows(self):
        key = SORT_KEYS.get(self.sort_column, SORT_KEYS[C_SAMPLE])   # default: sample, T, scan
        self.rows.sort(key=key, reverse=self.sort_reverse)

    def _rebuild_table(self, selected=()):
        table = self.table
        table.blockSignals(True)
        table.clearSelection()
        table.setRowCount(len(self.rows))
        right = QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        mode = QtCore.QItemSelectionModel.SelectionFlag.Select | QtCore.QItemSelectionModel.SelectionFlag.Rows
        for index, row in enumerate(self.rows):
            span = "–" if row["start"] is None else f"{fmt(row['start'])} → {fmt(row['end'])}"
            scanned = row["motor"]
            cells = {
                C_SCAN: str(row["number"]), C_SAMPLE: row["sample"], C_TEMP: row["temp"],
                C_TYPE: {"phi": "phi scan", "th": "theta scan"}.get(scanned, f"{scanned} scan"),
                C_RANGE: span,
                C_TH: "scanned" if scanned == "th" else fmt(row["th"]),
                C_CHI: fmt(row["chi"]),
                C_PHI: "scanned" if scanned == "phi" else fmt(row["phi"]),
                C_POINTS: str(row["points"]), C_STATUS: "", C_COMBINED: "",
            }
            for column, text in cells.items():
                item = QtWidgets.QTableWidgetItem(text)
                if column in (C_SCAN, C_TEMP, C_TH, C_CHI, C_PHI, C_POINTS):
                    item.setTextAlignment(right)
                if column == C_SCAN:
                    item.setToolTip(f"{row['path']}\n{row['command']}\n{row['date']}")
                table.setItem(index, column, item)
            button = QtWidgets.QPushButton()
            button.clicked.connect(lambda _, r=row: self.row_button(r))
            table.setCellWidget(index, C_ACTION, button)
            self._paint_row(index)
            if row["path"] in selected:
                table.selectionModel().select(table.model().index(index, 0), mode)
        table.blockSignals(False)
        self._update_actions()

    def _paint_row(self, index):
        """Status text, colour and button label for one row."""
        row = self.rows[index]
        if row["state"] == "running":
            text, color, label = f"Converting…  {row['percent']}%", BLUE, "Stop"
        elif row["state"] == "queued":
            text, color, label = "Waiting", BLUE, "Cancel"
        elif row["points"] == 0:
            text, color, label = "No data in the SPEC file (scan aborted)", GRAY, "Convert"
        elif row["state"] == "failed":
            text, color, label = row["note"], RED, "Convert"
        elif row["converted"]:
            text, color, label = "Converted ✓", GREEN, "Redo"
        else:
            text, color, label = "Not converted", None, "Convert"
        item = self.table.item(index, C_STATUS)
        item.setText(text)
        item.setToolTip(text)
        item.setForeground(QtGui.QBrush(QtGui.QColor(color)) if color
                           else self.table.item(index, C_SAMPLE).foreground())
        self.table.item(index, C_COMBINED).setText(
            ",  ".join("+".join(map(str, numbers)) + " ✓" for numbers in row["combined"]))
        button = self.table.cellWidget(index, C_ACTION)
        button.setText(label)
        button.setEnabled(row["points"] > 0)

    def _repaint(self, row):
        if row in self.rows:
            self._paint_row(self.rows.index(row))

    def selected_rows(self):
        indexes = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.rows[i] for i in indexes if i < len(self.rows)]

    def _convertible(self, rows):
        return [r for r in rows if not r["converted"] and r["points"]
                and r["state"] not in ("queued", "running")]

    def _update_actions(self):
        chosen = self.selected_rows()
        todo = self._convertible(chosen)
        self.convert_button.setEnabled(bool(todo))
        self.convert_button.setText(f"Convert selected ({len(todo)})" if todo else "Convert selected")
        problem = self._combine_problem(chosen)
        self.combine_button.setEnabled(problem is None)
        if not chosen:
            note = "Click a row to select it; Shift- or Ctrl-click to select several."
        elif problem is None:
            ordered = sorted(chosen, key=lambda r: r["number"])
            roles = [f"{ordered[0]['number']} reference", f"{ordered[1]['number']} lined up and averaged"]
            if len(ordered) == 3:
                roles.append(f"{ordered[2]['number']} fills gaps")
            note = "Combine: " + ",  ".join(roles)
        elif len(chosen) in (2, 3):
            note = f"Cannot combine: {problem}"
        else:
            note = f"{len(chosen)} selected. Select two or three converted scans to combine."
        self.selection_note.setText(note)
        busy = self.busy()
        self.stop_button.setVisible(busy)
        for widget in (self.spec, self.scans_root, self.poni, self.mask, self.output,
                       self.max_intensity, self.refresh_button):
            widget.setEnabled(not busy)

    # ---- queue ------------------------------------------------------------
    def busy(self):
        return self.process is not None or bool(self.jobs)

    def _files_problem(self):
        for picker in (self.poni, self.mask, self.output):
            picker.commit()
        for label, picker in (("calibration (.poni) file", self.poni), ("mask file", self.mask)):
            if not picker.path():
                return f"Choose the {label}."
            if not os.path.isfile(picker.path()):
                return f"The {label} was not found."
        if not self.output.path():
            return "Choose where to save the output."
        try:
            if float(self.max_intensity.text()) <= 0:
                raise ValueError
        except ValueError:
            return "“Skip images brighter than” must be a positive number such as 1e5."
        return None

    def target(self, row):
        return os.path.join(self.output.path(), "transformed_objects", bt.converted_name(row))

    def row_button(self, row):
        if row["state"] == "running":
            self.stopping = True
            self.process.kill()
        elif row["state"] == "queued":
            self.jobs = [j for j in self.jobs if j.get("row") is not row]
            row["state"] = ""
            self._repaint(row)
            self._update_actions()
        else:
            self.request_convert([row], single=True)

    def convert_selected(self):
        self.request_convert(self._convertible(self.selected_rows()), single=False)

    def request_convert(self, rows, single):
        """Queue conversions. A single request may ask questions; a batch
        skips anything that would need one and says so."""
        problem = self._files_problem()
        if problem:
            self.say(problem, error=True)
            return
        skipped = []
        for row in rows:
            images, _ = bt.inspect_frames(row["path"])
            if images == 0:
                row["state"], row["note"] = "failed", "No .cbf images in the scan folder"
                self._repaint(row)
                continue
            if images < row["points"]:
                question = (f"Scan {row['number']} has {images} images but the SPEC file lists "
                            f"{row['points']} points, so it may still be running or copying.\n\n"
                            f"Convert the {images} images that are there?")
                if not single or not self.ask(question):
                    skipped.append(row["number"])
                    continue
            redo = os.path.isfile(self.target(row))
            if redo and not (single and self.ask(
                    f"Scan {row['number']} is already converted:\n\n{bt.converted_name(row)}\n\n"
                    "Convert it again and replace that file?")):
                continue
            row["state"], row["percent"] = "queued", 0
            self._repaint(row)
            self.jobs.append({"kind": "convert", "row": row, "redo": redo})
        if skipped:
            self.say("Skipped (fewer images than SPEC points, maybe still running): scan "
                     + ", ".join(map(str, skipped)) + ". Use the row's Convert button to force it.")
        self._save()
        self._pump()

    def _combine_problem(self, rows):
        if len(rows) not in (2, 3):
            return "select two or three scans"
        if not all(r["converted"] for r in rows):
            return "convert every selected scan first"
        if any(r["state"] in ("queued", "running") for r in rows):
            return "a selected scan is being converted"
        if len({r["sample"] for r in rows}) > 1:
            return "the scans are from different samples"
        return None

    def combine_selected(self):
        rows = sorted(self.selected_rows(), key=lambda r: r["number"])
        if self._combine_problem(rows):
            return
        paths = [self.target(r) for r in rows]
        target = bt.combined_name(paths)
        numbers = [r["number"] for r in rows]
        warnings = []
        if any(r["kind"] != "phi" for r in rows):
            warnings.append("not all of them are phi scans")
        if any(b - a != 1 for a, b in zip(numbers, numbers[1:])):
            warnings.append("the scan numbers are not consecutive")
        if len({r["temp"] for r in rows}) > 1:
            warnings.append("they are in different temperature folders ("
                            + ", ".join(sorted({r["temp"] for r in rows})) + ")")
        label = " + ".join(map(str, numbers))
        if warnings and not self.ask(f"Combine scans {label}?\n\nNote: " + "; ".join(warnings) + "."):
            return
        if os.path.isfile(target) and not self.ask(
                f"Scans {label} are already combined:\n\n{os.path.basename(target)}\n\n"
                "Combine again and replace that file?"):
            return
        if any(j.get("target") == target for j in self.jobs + [self.job or {}]):
            return
        self.jobs.append({"kind": "combine", "paths": paths, "target": target, "label": label})
        if self.process:
            self.say(f"Combine of scans {label} is waiting.")
        self._pump()

    def _pump(self):
        """Start the next waiting job if nothing is running."""
        self._update_actions()
        if self.process is not None:
            return
        if not self.jobs:
            self.progress.setValue(0)
            return
        job = self.job = self.jobs.pop(0)
        job.update(tail=[], written=None, saved=None)
        self._buffer = ""
        self.stopping = False
        self.progress.setValue(0)
        try:
            args = self._prepare_convert(job) if job["kind"] == "convert" else self._prepare_combine(job)
        except OSError as exc:
            self.log(f"Could not start: {exc}")
            self._finish_job(False, f"could not start: {exc}")
            return
        self.process = QtCore.QProcess(self)
        self.process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.process.start(sys.executable, ["-u", *args])
        self._update_actions()

    def _prepare_convert(self, job):
        row = job["row"]
        job["redo_target"] = self.target(row) if job["redo"] else None
        row["state"], row["percent"] = "running", 0
        self._repaint(row)
        _, hidden = bt.inspect_frames(row["path"])
        for junk in hidden:                      # macOS '._' sidecars would be read as images
            self._remove(junk)
        folder = os.path.join(self.output.path(), "logs", "epiq-convert")
        os.makedirs(folder, exist_ok=True)
        config = os.path.join(folder, f"{row['material']}_{row['sample']}_{row['number']:03d}.txt")
        with open(config, "w") as fh:
            fh.write(bt.build_config(row, self.spec.path(), self.poni.path(), self.mask.path(),
                                     self.output.path(), float(self.max_intensity.text())))
        self.log(f"\n=== Convert scan {row['number']}  ({row['path']}) ===")
        self.say(f"Converting scan {row['number']} – getting ready…")
        return ["-m", AUTORSM_MODULE, config]

    def _prepare_combine(self, job):
        job["partial"] = job["target"] + ".partial"   # only renamed on success
        self.log(f"\n=== Combine scans {job['label']} ===")
        self.say(f"Combining scans {job['label']} – lining up…")
        return [COMBINE_SCRIPT, *job["paths"], "-o", job["partial"]]

    def _read_output(self):
        self._buffer += bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        segments = re.split(r"[\r\n]", self._buffer)
        self._buffer = segments.pop()
        for segment in segments:
            self._handle_line(segment.strip())

    def _handle_line(self, line):
        if not line:
            return
        job = self.job
        waiting = f"   ({len(self.jobs)} more waiting)" if self.jobs else ""
        if job["kind"] == "convert":
            match = TQDM_RE.search(line)
            if match:
                percent, n, total, timing = match.groups()
                row = job["row"]
                if int(percent) != row["percent"]:
                    row["percent"] = int(percent)
                    self._repaint(row)
                    self.progress.setValue(min(100, int(percent)))
                left = ""
                if timing and "<" in timing:
                    left = timing.split("<")[1].split(",")[0].strip()
                    left = f", about {left} left" if left and "?" not in left else ""
                if int(n) == int(total):
                    self.say(f"Scan {row['number']}: all images done, writing the file…{waiting}")
                else:
                    self.say(f"Converting scan {row['number']} – image {n} of {total}{left}{waiting}")
                return
            if line.startswith("Output file: "):
                job["written"] = line[len("Output file: "):].strip()
            elif line.startswith("Saved "):
                job["saved"] = line[len("Saved "):].strip()
        else:
            match = SLAB_RE.match(line)
            if match:
                done, total = int(match.group(1)), int(match.group(2))
                self.progress.setValue(int(100 * done / max(total, 1)))
                self.say(f"Combining scans {job['label']} – slice {done} of {total}{waiting}")
                return
        self.log(line)
        job["tail"] = (job["tail"] + [line])[-6:]

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
        job = self.job
        clean = code == 0 and status == QtCore.QProcess.ExitStatus.NormalExit
        reason = ""
        if job["kind"] == "convert":
            ok = bool(clean and job["saved"] and os.path.isfile(job["saved"]))
            if ok and job["redo_target"] and job["saved"] != job["redo_target"]:
                os.replace(job["saved"], job["redo_target"])     # swap in the new file
            elif not ok:
                # Remove this run's half-written file (never the earlier one).
                written = job["written"]
                if written and written != job["redo_target"] and os.path.isfile(written):
                    self._remove(written)
        else:
            ok = bool(clean and os.path.isfile(job["partial"]))
            if ok:
                os.replace(job["partial"], job["target"])
            elif os.path.isfile(job["partial"]):
                self._remove(job["partial"])
        if not ok and not self.stopping:
            reason = next((l for l in reversed(job["tail"]) if not l.startswith("Saved")),
                          f"exit code {code}")
            if status == QtCore.QProcess.ExitStatus.CrashExit:
                reason = "stopped unexpectedly (possibly out of memory)"
        self._finish_job(ok, reason)

    @staticmethod
    def _remove(path):
        try:
            os.remove(path)
        except OSError:
            pass

    def _clear_waiting(self):
        for waiting in self.jobs:
            if waiting["kind"] == "convert":
                waiting["row"]["state"] = ""
        self.jobs = []

    def _finish_job(self, ok, reason):
        job, self.job = self.job, None
        stopped = self.stopping
        self.stopping = False
        name = (f"scan {job['row']['number']}" if job["kind"] == "convert"
                else f"combine of scans {job['label']}")
        if job["kind"] == "convert":
            row = job["row"]
            row["state"], row["note"] = ("", "") if ok or stopped else ("failed", f"Failed: {reason}")
        if self.stop_all:
            self._clear_waiting()
        self.check_outputs()
        if ok:
            self.say(f"Finished {name} ✓" + ("  Starting the next…" if self.jobs else ""), color=GREEN)
        elif stopped:
            self.say(f"Stopped {name}. Nothing half-finished was kept.")
        else:
            self.say(f"Failed {name}: {reason}", error=True)
            self.details_button.setChecked(True)
        if not self.jobs:
            self.stop_all = False
            QtWidgets.QApplication.alert(self)
        self._pump()

    def stop_everything(self):
        if self.process:
            self.stop_all = True
            self.stopping = True
            self.process.kill()
        else:
            self._clear_waiting()
            self.check_outputs()

    # ---- misc -------------------------------------------------------------
    def _save(self):
        self.settings.setValue("max_intensity", self.max_intensity.text().strip())

    def open_output(self):
        folder = os.path.join(self.output.path(), "transformed_objects")
        if not os.path.isdir(folder):
            folder = self.output.path()
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(folder))

    def closeEvent(self, event):
        if self.busy():
            if not self.ask("Work is still running or waiting. Stop it and quit?"):
                event.ignore()
                return
            self.jobs = []
            if self.process:
                self.stopping = True
                self.process.kill()
                self.process.waitForFinished(5000)
        self._save()
        event.accept()


def main(argv=None):
    argv = sys.argv if argv is None else argv
    app = QtWidgets.QApplication(argv)
    app.setApplicationName(APP_NAME)
    # Check the processing code is installed without importing it (importing
    # pulls in the whole detector stack and would slow the window down).
    try:
        found = importlib.util.find_spec(AUTORSM_MODULE) is not None
    except Exception:
        found = False
    if not found:
        QtWidgets.QMessageBox.critical(
            None, APP_NAME,
            "The EpiQ-Map processing code could not be found.\n\n"
            "Install it with:  pip install EpiQ-Map\n\n"
            "If EpiQ-Map is installed from a folder on an external drive, "
            "connect that drive and open the app again.")
        return 1
    spec = next((a for a in argv[1:] if not a.startswith("-")), None)
    window = MainWindow(os.path.abspath(spec) if spec else None)
    window.show()
    window.raise_()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
