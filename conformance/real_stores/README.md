# Real stores

Producer output that both validators must read cleanly. The real-store tests
(`conformance/test_real_stores.py`, `bindings/rust/tests/real_stores.rs`)
validate every store listed here and fail when one is missing. Since #19
(L0 3, L1 2.0) these stores sit below `min_supported`: each gets exactly one
version error, the below-floor error, and no metadata error. Fresh stores
replace them once the producers write the #19 contract (ccatobs/kalibrate#101).

| Store | Level | Shape |
|---|---|---|
| `zarr/SOFIA_UPGREAT_SINGLE_POINT_TP.zarr` | L0 | single frontend, one calibration scan, one source scan |
| `calibrate/SINGLE_POINT_TP` | L1 | single frontend, one scan |
| `zarr/SOFIA_UPGREAT_ORION_KL.zarr` | L0 | multi frontend, scans `scan_0299NN` and `scan_10299NN` |
| `calibrate/ORION_KL` | L1 | multi frontend, one scan with `band_0` and `band_1` |

- Source: `chai-software/calibrate_parity_tests/output/zarr/` (L0, zarr-fits 0.2.0)
  and `chai-software/calibrate_parity_tests/output/calibrate/` (L1, cal engine 0.2.0).
- Copied on 2026-10-14, the day the parity harness wrote them.
- Crop: none. Every file is byte-identical to the producer output.
- The `zarr/` and `calibrate/` layout matches the source, so the L1
  `source_l0_store` attr (`../../zarr/<name>.zarr`) resolves inside this directory.
- Never edit these files. To refresh them, copy the new producer output whole.
