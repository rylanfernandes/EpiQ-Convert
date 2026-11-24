# Changelog

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
