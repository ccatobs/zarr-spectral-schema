# 0011 — The layout scan_N/band_B/ replaces the virtual scan number

- **Status:** Accepted
- **Date:** 2026-10-15
- **Deciders:** the maintainer (2026-10-14)
- **Relates to:**
  - ccatobs/zarr-spectral-schema#19 (dimensions, units, flag bits and the scientific frame)
  - PR #25 (dimensions, units and the scientific frame)
  - ccatobs/kalibrate#101 (write the extended L0–L1 contract)
  - epic ccatobs/zarr-spectral-schema#21 (production-grade L0–L1 contract)

## Holds

- **Invariant** (test: `conformance/test_self_description.py::test_l0_multi_frontend_store_holds_each_band_under_its_scan`): an L0 store of a multi-frontend scan holds each band under `scan_N/band_B/`.
- **Invariant** (test: `conformance/test_self_description.py::test_multi_frontend_scan_group_holding_arrays_is_rejected`): a scan-or-band attr on the scan group of a multi-frontend scan is a structure finding.
- **Invariant** (test: `conformance/test_full_contract.py::test_scan_or_band_rule_is_data_driven`): the validators resolve the placement target from the `band_<B>` children, from spec data only.
- **Default:** a scan number on disk is the scan number the observatory assigned.
- **Incidental:** the spec declares the placement with `placement: scan_or_band` and the validators read it generically.

## Revisit if

- ccatobs/zarr-spectral-schema#34 (close two spec gaps) moves `backend`, `obs_id` and `spectel1` to band placement.
- A leftover virtual scan group in an L0 3 store still passes the validators. ccatobs/zarr-spectral-schema#33 (validator and spec findings) tracks the gap.

## Context

- Up to L0 2, zarr-fits wrote each frontend of a multi-frontend scan as a scan of its own, numbered `N + 1000000 × b`.
- The virtual number is a scan number the observatory never assigned. Each tool decoded it with `% 1_000_000`, and the decode leaked into QA reports and provenance.
- L1 already used `scan_N/band_B/`. L0 and L1 therefore differed in shape for the same scan.
- The maintainer rejected the virtual scan number on 2026-10-14.

## Decision

- L0 and L1 hold a multi-frontend scan as `scan_N/band_B/`, with `N` the observatory scan number and `B` the band index.
- An attr declared at scan-or-band placement sits on the scan group of a single-frontend scan, or on each band group of a multi-frontend scan. On the scan group of a multi-frontend scan, such an attr is a structure finding.

## Consequences / Trade-offs

- L0 and L1 have one shape. A reader walks one layout.
- Scan numbers on disk match the observing log.
- The change needed the L0 major bump to L0 3. The validators reject older L0 stores (ADR 0007 amendment of 2026-10-14).
- The discovery surface of ADR 0008 (scale, archive layout and the discovery boundary) gains a band level under the scan.
- zarr-fits refuses a scan that mixes source and calibration records. A split would need an invented scan number again.

## Alternatives considered

- **Keep the virtual scan number.** Rejected by the maintainer: it invents scan numbers and every consumer decodes them.
- **One store per frontend.** Rejected: the frontends of one scan share pointing, time and calibration, and a reader would join stores.
- **Bands at L1 only.** Rejected: L0 and L1 would keep two shapes for one scan.

## Status

Accepted.

## Amendments

None.
