"""Everything the app learns from the file system, with no GUI code.

The CHESS QM2 (ID4B) layout this understands:

    /nfs/chess/id4b/<run>/<beamtime>/<name>                    SPEC file
    /nfs/chess/id4b/<run>/<beamtime>/raw6M/<name>/<sample>/<T>/<name>_<NNN>/   images
    /nfs/chess/id4baux/<run>/<beamtime>/calibrations/*.poni, *.edf
    /nfs/chess/id4baux/<run>/<beamtime>/processed/output-epiq/transformed_objects/

so choosing the SPEC file is enough to find the rest. A flat local copy (SPEC
file, scan folders and a calibrations folder side by side) also works.

Nothing here opens a scan folder at load time: with thousands of images per
scan on a network disk, that would be slow.
"""

import os
import re

SCAN_DIR_RE = re.compile(r"^(?P<material>.+)_(?P<num>\d{3,})$")
COMBINED_RE = re.compile(r"_scans_(\d+(?:_\d+)+)(?:_.*)?combined\.nxs$")


# ---------------------------------------------------------------------------
# SPEC file
# ---------------------------------------------------------------------------

def read_spec(path):
    """Parse a SPEC file in one pass. Returns {scan_number: info} where info
    has: number, command, macro, motor, start, end, date, points (data lines
    written) and positions ({motor name: value} from the #P lines).

    If a scan number appears more than once, the first is kept -- the same
    choice EpiQ-Map's make_log_files makes.
    """
    scans = {}
    names, pending_names = [], []
    current = None
    values = []

    def close():
        if current is not None:
            current["positions"] = dict(zip(current.pop("_names"), values))

    with open(path, "r", errors="replace") as fh:
        for line in fh:
            if not line.startswith("#"):
                if current is not None and line.strip():
                    current["points"] += 1
                continue
            tag = line[:2]
            if tag == "#S":
                close()
                parts = line.split()
                current, values = None, []
                if len(parts) < 4 or not parts[1].isdigit():
                    continue
                number = int(parts[1])
                info = {
                    "number": number, "command": " ".join(parts[2:]),
                    "macro": parts[2], "motor": parts[3],
                    "start": _number(parts[4]) if len(parts) > 4 else None,
                    "end": _number(parts[5]) if len(parts) > 5 else None,
                    "date": "", "points": 0, "_names": names,
                }
                current = info
                if number not in scans:
                    scans[number] = info
            elif tag == "#O":
                # Motor names, two-space separated (names may contain a space).
                index, _, rest = line[2:].partition(" ")
                if index == "0":
                    pending_names = []
                pending_names.extend(n for n in re.split(r"\s{2,}", rest.strip()) if n)
                names = list(pending_names)
            elif current is not None:
                if tag == "#P":
                    values.extend(_number(v) for v in line.split()[1:])
                elif tag == "#D" and not current["date"]:
                    current["date"] = line[3:].strip()
    close()
    return scans


def _number(text):
    try:
        return float(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Folder layout
# ---------------------------------------------------------------------------

def aux_folder(folder):
    """The matching '<station>aux' tree: .../id4b/<run>/<beamtime> ->
    .../id4baux/<run>/<beamtime>. None if there is no such folder."""
    parts = os.path.normpath(folder).split(os.sep)
    for i in range(len(parts) - 1, 0, -1):
        if not parts[i]:
            continue
        candidate = os.sep.join(parts[:i] + [parts[i] + "aux"] + parts[i + 1:])
        if os.path.isdir(candidate):
            return candidate
    return None


def _files(folder, extension):
    """Files with this extension, newest first."""
    try:
        found = [e for e in os.scandir(folder)
                 if e.is_file() and e.name.endswith(extension)
                 and not e.name.startswith(".")]
    except OSError:
        return []
    found.sort(key=lambda e: e.stat().st_mtime, reverse=True)
    return [e.path for e in found]


def detect_layout(spec_path):
    """Work out where everything is from the SPEC file's location."""
    spec_path = os.path.normpath(spec_path)
    beam = os.path.dirname(spec_path)
    name = os.path.basename(spec_path)
    aux = aux_folder(beam)

    raw = os.path.join(beam, "raw6M")
    scans_root = os.path.join(raw, name)
    if not os.path.isdir(scans_root):
        scans_root = beam

    calibrations = next(
        (d for d in ([os.path.join(aux, "calibrations")] if aux else []) +
         [os.path.join(beam, "calibrations")] if os.path.isdir(d)), "")
    ponis = _files(calibrations, ".poni") if calibrations else []
    masks = _files(calibrations, ".edf") if calibrations else []
    # Prefer files that are called a mask over any other .edf.
    masks.sort(key=lambda p: "mask" not in os.path.basename(p).lower())

    if aux:
        output = os.path.join(aux, "processed", "output-epiq")
    else:
        output = os.path.join(beam, "output")

    # Other SPEC files of this beamtime: files that have an image folder.
    siblings = []
    if os.path.isdir(raw):
        try:
            siblings = sorted(
                e.path for e in os.scandir(beam)
                if e.is_file() and os.path.isdir(os.path.join(raw, e.name)))
        except OSError:
            pass

    return {
        "material": name, "beam": beam, "aux": aux,
        "beamtime": os.path.basename(beam) if aux else "",
        "run": os.path.basename(os.path.dirname(beam)) if aux else "",
        "scans_root": scans_root, "ponis": ponis, "masks": masks,
        "output": output, "siblings": siblings,
    }


def parse_scan_folder(name):
    """'<Material>_<NNN>' -> (material, number), else None. The name must
    survive a round trip through f'{material}_{number:03d}', because that is
    how auto_rsm rebuilds the image folder."""
    match = SCAN_DIR_RE.match(name)
    if not match:
        return None
    material, number = match.group("material"), int(match.group("num"))
    if f"{material}_{number:03d}" != name:
        return None
    return material, number


def find_scan_folders(root, material, max_depth=3):
    """Scan folders named <material>_<NNN> at most max_depth levels below
    root. Returns [(path, number, sample, temperature)], where sample and
    temperature come from .../<sample>/<temperature>/<scan> when the folder
    sits that deep, and are None otherwise. Scan folders are never opened."""
    found = []

    def walk(folder, parents):
        try:
            entries = [e for e in os.scandir(folder)
                       if not e.name.startswith(".") and e.is_dir()]
        except OSError:
            return
        for entry in entries:
            parsed = parse_scan_folder(entry.name)
            if parsed and parsed[0] == material:
                sample = parents[-2] if len(parents) >= 2 else None
                temperature = parents[-1] if len(parents) >= 2 else None
                found.append((entry.path, parsed[1], sample, temperature))
            elif len(parents) + 1 < max_depth:
                walk(entry.path, parents + [entry.name])

    walk(root, [])
    return found


def sample_from_log(scan_dir, material):
    """Fallback for a flat copy of the data: read sample and temperature from
    the detector's frame log, whose paths look like
    .../raw6M/<material>/<sample>/<temperature>//<scan>/x.cbf."""
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
                    return parts[i - 2], parts[i - 1]
    return None, None


def load_scans(spec_path, scans_root):
    """The rows of the scan table: every scan that is both in the SPEC file
    and has an image folder. That is EpiQ-Map's rule for what a scan is."""
    material = os.path.basename(os.path.normpath(spec_path))
    spec = read_spec(spec_path)
    rows = []
    for path, number, sample, temperature in find_scan_folders(scans_root, material):
        info = spec.get(number)
        if info is None:
            continue
        if sample is None:
            sample, temperature = sample_from_log(path, material)
        pos = info["positions"]
        motor = info["motor"]
        rows.append({
            "path": path, "material": material, "number": number,
            "sample": sample or "sample",
            "temp": temperature or "",
            "temperature": int(temperature) if temperature and temperature.isdigit() else None,
            "motor": motor,
            # EpiQ-Map's rule: a 'th' scan is a theta scan, anything else is
            # processed as a phi scan.
            "kind": "theta" if motor == "th" else "phi",
            "start": info["start"], "end": info["end"],
            "th": pos.get("th"), "chi": pos.get("chi"), "phi": pos.get("phi"),
            "points": info["points"], "date": info["date"],
            "command": info["command"],
        })
    return rows


# ---------------------------------------------------------------------------
# Scan folders and output files
# ---------------------------------------------------------------------------

def inspect_frames(scan_dir):
    """(number of .cbf images, list of macOS '._*.cbf' sidecar files)."""
    count, hidden = 0, []
    try:
        for entry in os.scandir(scan_dir):
            if entry.name.endswith("cbf"):
                if entry.name.startswith("._"):
                    hidden.append(entry.path)
                else:
                    count += 1
    except OSError:
        pass
    return count, hidden


def converted_name(row):
    """File name auto_rsm gives the lab-frame map of one scan."""
    return f"{row['material']}_{row['sample']}_scans_{row['number']}_full.nxs"


def output_state(output_dir, rows):
    """Look once at <output>/transformed_objects and say, for each row,
    whether its map exists and which combined files include it.
    Returns {id(row): (converted, [combined scan-number tuples])}."""
    folder = os.path.join(output_dir, "transformed_objects")
    try:
        names = set(os.listdir(folder))
    except OSError:
        names = set()
    combined = {}
    for name in names:
        match = COMBINED_RE.search(name)
        if match:
            prefix = name[:match.start()]
            numbers = tuple(int(n) for n in match.group(1).split("_"))
            combined.setdefault(prefix, []).append(numbers)
    state = {}
    for row in rows:
        prefix = f"{row['material']}_{row['sample']}"
        sets = sorted(s for s in combined.get(prefix, []) if row["number"] in s)
        state[id(row)] = (converted_name(row) in names, sets)
    return state


def combined_name(paths):
    """Output file for a combine, next to the first input:
    X_scans_7_full.nxs + ..._8_... + ..._9_... -> X_scans_7_8_9_full_combined.nxs"""
    pattern = re.compile(r"^(?P<prefix>.*_scans_)(?P<scans>\d+(?:_\d+)*)(?P<rest>.*)\.nxs$")
    folder = os.path.dirname(paths[0])
    matches = [pattern.match(os.path.basename(p)) for p in paths]
    if all(matches) and len({m.group("prefix") for m in matches}) == 1:
        scans = "_".join(m.group("scans") for m in matches)
        first = matches[0]
        return os.path.join(folder, f"{first.group('prefix')}{scans}{first.group('rest')}_combined.nxs")
    stem = os.path.splitext(os.path.basename(paths[0]))[0]
    return os.path.join(folder, f"{stem}_combined.nxs")


def build_config(row, spec, poni, mask, output_dir, max_intensity):
    """The auto_rsm config for one scan, in the format EpiQ-Map's
    make_log_files writes. auto_rsm opens the images at
    <Temperature Directory>/<Material>_<NNN>/."""
    is_theta = row["kind"] == "theta"
    scan_list = [] if is_theta else [row["number"]]
    theta_list = [row["number"]] if is_theta else []
    scan_dir = os.path.normpath(row["path"])
    lines = [
        f"PONI File: {poni}",
        f"Material: {row['material']}",
        f"Sample Name: {row['sample']}",
        f"Scan Number: {row['number']}",
        f"Scan List: {scan_list}",
        f"Theta Scan List: {theta_list}",
    ]
    if row.get("temperature") is not None:
        lines.append(f"Temperature: {row['temperature']}")
    lines += [
        f"Max Intensity: {max_intensity!r}",
        f"Mask File: {mask}",
        f"Specfile: {spec}",
        f"Temperature Directory: {os.path.dirname(scan_dir)}/",
        f"Image Directory: {scan_dir}/",
        f"Output Directory: {output_dir}",
    ]
    return "\n".join(lines) + "\n"
