# Real stores

Producer output that both validators must read cleanly. The real-store tests
(`conformance/test_real_stores.py`, `bindings/rust/tests/real_stores.rs`)
validate every store listed here and fail when one is missing. Each store is
at the current versions (L0 3, L1 2.0) and gets zero findings, in both
bindings and over object storage (`test_object_storage.py`,
`object_storage.rs`).

| Store | Level | Shape |
|---|---|---|
| `zarr/SOFIA_UPGREAT_SINGLE_POINT_TP.zarr` | L0 | single frontend, one calibration scan, one source scan |
| `calibrate/SINGLE_POINT_TP` | L1 | single frontend, one scan |
| `zarr/SOFIA_UPGREAT_ORION_KL.zarr` | L0 | multi frontend, scans `scan_029951` and `scan_029952`, each with `band_0` and `band_1` |
| `calibrate/ORION_KL` | L1 | multi frontend, one scan with `band_0` and `band_1` |

- Producer: ccatobs/kalibrate `epic/21` at 5bf7c7b (PR #130, the extended
  L0–L1 contract), built with `make release-full`: zarr-fits 0.2.0 and
  calibrate (cal engine 0.2.0).
- Input: the FITS datasets `SOFIA_UPGREAT_SINGLE_POINT_TP` and
  `SOFIA_UPGREAT_ORION_KL` of `chai-software/calibrate_parity_tests/data/`,
  and its ATM table `atm_models/atm-table-test-convert.catm`.
- Written on 2026-10-15 with the commands below, from `<out>`, a scratch
  directory with `zarr/` and `calibrate/` beside each other:

  ```sh
  D=chai-software/calibrate_parity_tests/data
  ATM=chai-software/calibrate_parity_tests/atm_models/atm-table-test-convert.catm
  zarr-fits convert --fits-folder $D/SOFIA_UPGREAT_SINGLE_POINT_TP \
      --zarr-path <out>/zarr/SOFIA_UPGREAT_SINGLE_POINT_TP.zarr
  zarr-fits convert --fits-folder $D/SOFIA_UPGREAT_ORION_KL \
      --zarr-path <out>/zarr/SOFIA_UPGREAT_ORION_KL.zarr
  calibrate -i <out>/zarr/SOFIA_UPGREAT_SINGLE_POINT_TP.zarr \
      --output <out>/calibrate/SINGLE_POINT_TP --atm-table $ATM \
      --physics kalibrate-compat --foeff 0.97 -q
  calibrate -i <out>/zarr/SOFIA_UPGREAT_ORION_KL.zarr \
      --output <out>/calibrate/ORION_KL --atm-table $ATM \
      --physics kalibrate-compat --foeff 0.95 --mode tp -q
  ```

  The calibrate flags are those of the parity harness (`tests.yaml`).
  ORION_KL adds `--mode tp`: its scan is a beam-switch scan, which calibrate
  does not calibrate in the auto-detected mode. Total power calibrates its
  ON against its OFF subscans, as the stores of #18 did.
- Crop: none. Only the session lock `.calibrate.lock` of each L1 output is
  left out; every other file is byte-identical to the producer output.
- The `zarr/` and `calibrate/` layout matches the producer output, so the L1
  `source_l0_store` attr (`../../zarr/<name>.zarr`) resolves inside this directory.
- Never edit these files. To refresh them, write new producer output with the
  commands above and copy it whole.
