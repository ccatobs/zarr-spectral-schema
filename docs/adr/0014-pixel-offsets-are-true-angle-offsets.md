# 0014 — Per-pixel offsets are true-angle offsets, added without a cos(dec) division

- **Status:** Accepted
- **Date:** 2026-10-16
- **Deciders:** the maintainer (2026-10-16)
- **Relates to:**
  - ccatobs/yoda#59 (gridding header and offsets; decision comment of 2026-10-16)
  - ccatobs/yoda PR #67 (CRPIX/CRVAL for even pixel counts, no cos(dec) division)
  - epic ccatobs/zarr-spectral-schema#38 (contract epic follow-ups)

## Holds

- **Default:** a consumer adds `pixel_offset_lon` and `pixel_offset_lat` to the map offsets as they are. It does not divide the longitude offset by cos(dec).
- **Default:** a writer stores `pixel_offset_lon` and `pixel_offset_lat` as angles measured in the projection plane (true angles), not as coordinate differences.
- **Incidental:** the legacy kalibrate code that sets the convention is `kalibrate/buffers.cpp:2364-2412`.

## Revisit if

- A receiver needs offsets stored as coordinate differences, not as true angles.
- The `offset_convention` attribute gets a value that declares the pixel offsets separately from `otf_*`.

## Context

- The spec declares `offset_convention` (enum `equatorial_cosdec`, `equatorial_coord`, `horizontal`) as "how otf_* and pixel_offset_* offsets are formed". The spec does not say which value means "true angle".
- Legacy kalibrate `buffers.cpp:2364-2412` sets `rdx = fs->pixoffx` and `rdy = fs->pixoffy` (file version above 2.11), adds `otfslam`/`otfsbet`, then does `lambda_map += rdx / (180*60*60) * M_PI`. No cos(dec) division occurs.
- `write_class.cpp:241-243` writes `lambda_map` to CLASS as `lamof` with `PROJ_RADIO`: a true-angle offset in the projection plane.
- yoda divided the pixel offset by cos(dec) while gridding. The sky-coverage endpoint `/scans` did the same. Issue yoda#59 recorded the maintainer decision of 2026-10-16 to follow legacy kalibrate.
- Three tools meet at this convention: kalibrate writes the offsets, yoda grids them, the spec's `offset_convention` attribute describes them.

## Decision

Per-pixel offsets are true-angle offsets in the projection plane. yoda adds them to the map offsets without a cos(dec) division, as legacy kalibrate does. yoda PR #67 removed the division in `gridding.py` and in the `/scans` sky-coverage endpoint.

## Consequences / Trade-offs

- Gridded positions of off-axis pixels match legacy kalibrate maps.
- The convention holds in the projection plane only. A consumer that works in plain coordinate differences must multiply the longitude difference by cos(dec) itself.
- No test in this repo checks the convention: the validator checks shape and subscan length of the offset arrays, not their meaning. The tests that pin it live in yoda (gridding reference, parity and sky-coverage tests, PR #67).
- Mismatch to resolve later: the spec text for `offset_convention` does not say which enum value stands for true-angle offsets, nor whether it covers `pixel_offset_*` as it covers `otf_*`. This ADR changes neither the spec nor its bindings.

## Alternatives considered

- **Divide by cos(dec) (the old yoda behaviour).** Rejected: it differs from legacy kalibrate, which this pipeline must match.
- **Store coordinate differences in the file.** Rejected: legacy kalibrate and its CLASS output use true angles.

## Status

Accepted.

## Amendments

None.
