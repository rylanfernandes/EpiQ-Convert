# Changelog

## 0.2.0 - 2026-11-24

The window is rebuilt around the SPEC file.

- Choosing a SPEC file finds the image folders, calibration, mask and output
  folder from the CHESS QM2 layout (`id4b` / `id4baux`). The other SPEC files
  of the beamtime are offered in a drop-down.
- One table lists every scan with images: sample, set temperature, phi or
  theta, range, th, chi, phi, points, whether it is converted, and which
  combined files include it. Sorted by sample, temperature and scan number.
- A Convert button in every row, plus Convert selected. Conversions queue and
  run one at a time, with Stop, Cancel and Stop all.
- Combine is no longer a separate tab: select two or three converted scans and
  press Combine selected. The roles are shown before anything runs.
- Existing outputs are checked when the app opens, on Refresh and after every
  job.
- Warns about scans with fewer images than SPEC points and flags aborted
  scans.
- Configs are written to `<output>/logs/epiq-convert/` so they do not appear
  in `rsm_monitor`.
- Own SPEC reader, so the window opens without loading the detector stack.
- `epiq-convert` accepts a SPEC file path.
- Removed: choosing a scan folder directly, and the editable sample name
  (both now come from the layout).

## 0.1.0 - 2026-11-23

First version.

- Convert tab: one scan folder, SPEC file, `.poni` and mask in; lab-frame Q
  map out, through EpiQ-Map's `auto_rsm`.
- Detects scan type from the SPEC file and sample name from the detector
  log.
- Detects an existing output and asks before replacing it; Stop and failures
  leave no partial files.
- Combine tab: merges two or three converted maps with `combine_rsm.py`.
- Installable with `pip` from GitHub; `epiq-convert` and `epiq-combine`
  commands.
- Optional macOS Desktop icon via `setup.sh`.
- Documentation: README, architecture and development notes, and an agent
  brief.
