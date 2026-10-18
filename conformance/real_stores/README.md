# Real stores

Producer output that both validators must read cleanly. The real-store tests
(`conformance/test_real_stores.py`, `bindings/rust/tests/real_stores.rs`)
validate every store listed here and fail when one is missing. Each store is
at the current versions (L0 3, L1 2.0) and gets zero findings in both
bindings. The ORION_KL pair is also served on S3, and the object-storage
tests (`test_object_storage.py`, `object_storage.rs`) read it there without
an error. `test_real_stores.py` also checks that no attribute and no string
array cell holds a local absolute path.

| Store | Level | Shape |
|---|---|---|
| `zarr/SOFIA_UPGREAT_SINGLE_POINT_TP.zarr` | L0 | single frontend, one calibration scan, one source scan |
| `calibrate/SINGLE_POINT_TP` | L1 | single frontend, one scan |
| `zarr/SOFIA_UPGREAT_ORION_KL.zarr` | L0 | multi frontend, scans `scan_029951` and `scan_029952`, each with `band_0` and `band_1` |
| `calibrate/ORION_KL` | L1 | multi frontend, one scan with `band_0` and `band_1` |

- Producer: ccatobs/kalibrate `epic/zss-38` at 53e8e8b (PR #152, the #34
  attrs: L0 `arrays`, per-band identity, `image_gain`), built with
  `make release-full` in a git checkout, so
  `cal_engine_commit` names the commit: zarr-fits 0.2.0 and calibrate (cal
  engine 0.2.0).
- Input: the FITS datasets `SOFIA_UPGREAT_SINGLE_POINT_TP` and
  `SOFIA_UPGREAT_ORION_KL` of `chai-software/calibrate_parity_tests/data/`,
  and its ATM table `atm_models/atm-table-test-convert.catm`.
- Written on 2026-10-16 with the commands below. The working directory is the
  one that holds `chai-software/`. The producers store the input paths as
  given, so `fits_folder`, `original_fits_file` (in `raw_fits_headers`) and
  `atm_table_info.path` read `chai-software/calibrate_parity_tests/…`.
  `O` is a scratch directory; `zarr/` and `calibrate/` sit beside each other
  in it.

  ```sh
  O=/path/to/scratch
  D=chai-software/calibrate_parity_tests/data
  ATM=chai-software/calibrate_parity_tests/atm_models/atm-table-test-convert.catm
  zarr-fits convert --fits-folder $D/SOFIA_UPGREAT_SINGLE_POINT_TP \
      --zarr-path $O/zarr/SOFIA_UPGREAT_SINGLE_POINT_TP.zarr
  zarr-fits convert --fits-folder $D/SOFIA_UPGREAT_ORION_KL \
      --zarr-path $O/zarr/SOFIA_UPGREAT_ORION_KL.zarr
  calibrate -i $O/zarr/SOFIA_UPGREAT_SINGLE_POINT_TP.zarr \
      --output $O/calibrate/SINGLE_POINT_TP --atm-table $ATM \
      --physics kalibrate-compat --foeff 0.97 -q
  calibrate -i $O/zarr/SOFIA_UPGREAT_ORION_KL.zarr \
      --output $O/calibrate/ORION_KL --atm-table $ATM \
      --physics kalibrate-compat --foeff 0.95 --mode tp -q
  ```

  The calibrate flags are those of the parity harness (`tests.yaml`).
  ORION_KL adds `--mode tp`: its scan is a beam-switch scan, and calibrate at
  53e8e8b does not calibrate a beam-switch scan in the auto-detected mode.
  Total power calibrates its ON against its OFF subscans, as the stores of
  #18 (accept real output) did. kalibrate PR #156 (calibrate beam-switch scans
  outside on-the-fly mode through the chopped path) is not in 53e8e8b. With
  PR #156, calibrate detects the scan as `beam_switch`: a refresh drops
  `--mode tp`.
- Crop: none. Only the session lock `.calibrate.lock` of each L1 output is
  left out; every other file is byte-identical to the producer output.
- The `zarr/` and `calibrate/` layout matches the producer output, so the L1
  `source_l0_store` attr (`../../zarr/<name>.zarr`) resolves inside this directory.
- Never edit these files. To refresh them, write new producer output with the
  commands above and copy it whole except `.calibrate.lock`.
