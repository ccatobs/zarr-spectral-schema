---
title: Make the L0–L1 contract production grade in schema, calibrate and YODA
status: draft
epic: https://github.com/ccatobs/zarr-spectral-schema/issues/21
repos:
  - ccatobs/zarr-spectral-schema
  - ccatobs/kalibrate
  - ccatobs/yoda
---

# PRD 0001: Make the L0–L1 contract production grade in schema, calibrate and YODA

> Source of truth for this epic. Tracker: [epic issue](https://github.com/ccatobs/zarr-spectral-schema/issues/21).
> The epic links here; this file is not embedded in the issue body.

## Problem

A review on 2026-10-14 checked the spec at `e47928d`, the revision calibrate pins. The review used all real stores of the calibrate parity harness, the writers in calibrate and the readers in calibrate and YODA.

- The spec rejects every real store: 20 of 20 L0 stores and 24 of 24 L1 stores at version 1.3. The tests do not see the failures. The fixtures come from the spec, and the real-store tests return early without data.
- The spec declares the name, the dtype and the ndim of each array. The spec declares no dimension names, no units, no flag bits, no frames and no chunk rules.
- Each missing field already causes a wrong science product:
  - The L1 pixel offsets keep the L0 subscan axis. YODA clamps the index and places part of `LFA_OTF_CHOPPED` 20″ off, more than the beam.
  - `vlsr_source` holds 0.0 for SOFIA data. YODA labels the topocentric axis `LSRK`, and the velocity axis is off by about 23 km/s.
  - The store holds offsets only. YODA writes the offsets as ICRS coordinates, so the cube sits near (0.4°, 0.1°).
  - The store records `eta_fss = 1.0`. calibrate divides by a forward efficiency of 0.97.
  - Two hand copies of the flag bit table disagree with the architecture document on bit 5.
- The completeness markers exist on paper only. YODA ignores `l1_complete`. zarr-fits writes no marker per scan and skips a half-written scan on rerun.
- YODA keeps its own copy of the contract and checks no version. The YODA status check can report an L0 store as safe to delete without a complete L1 copy.
- The implementations assume a local file system. Object size and memory per scan grow without a limit as scans grow.

## Solution

- A person opens a conformant store with a generic Zarr tool and finds the axis names, the units, the frames and the absolute positions.
- A producer change that writes a store the spec rejects fails in continuous integration (CI).
- A reader skips a unit without its completeness marker.
- YODA takes the contract from the spec binding and negotiates the version of each store.
- The spec states the layout rules for object storage and production volume.

## Implementation decisions

### Contract gate — ccatobs/zarr-spectral-schema#18 (accept real output)

- Each scan holds exactly one of the source group and the calibration group.
- `source_l0_scans` and `processing_steps` are objects. L1 declares `raw_attrs` at scan-or-band placement.
- The scan name pattern takes 6 or more digits. A `scan_*` child that does not match is an error.
- The DSL (domain-specific language of the spec) gains value constraints. `l1_complete` holds `true`.
- The spec declares an optional `l0_complete` marker on each L0 scan.
- The DSL matches array families by name prefix, for the skydiff spectra.
- `min_supported` names the oldest version whose stores pass the structural check.
- The meta-schema requires `placement`, `inline` and `detect`, and constrains version values.
- Both bindings share one parse grammar: ASCII only, one rule for non-finite numbers, errors returned and not raised.
- The repo holds one small real single-frontend L0 and L1 pair and one real multi-frontend pair.

### Self-description — ccatobs/zarr-spectral-schema#19 (dimensions, units and frames)

- The DSL gains `dims` and `units` per array. Producers write the zarr v3 `dimension_names` field and a `units` attr.
- The validator checks the length of each shared dimension across the arrays of one placement target.
- The spec holds the flag bit table. Both bindings generate the bit constants.
- The required L1 set extends #7 (required science self-description set):
  - spectral frame, velocity convention, Doppler velocity per subscan, systemic velocity with its frame;
  - base of the reference channel and the channel-to-frequency formula;
  - reference position, coordinate frame, equinox, projection, offset convention;
  - temperature scale, applied forward efficiency, main-beam efficiency, sideband mode, sideband gain;
  - observatory location, per subscan for an airborne observatory.
- A field that does not apply holds a not-applicable sentinel, as ADR-0007 (shared source of truth) decides.
- In a multi-frontend L1 store, identity, quality record and provenance sit at band placement.
- The spec declares the L0 multi-frontend layout.
- A person decides five points in ccatobs/zarr-spectral-schema#19:
  - the unit vocabulary;
  - the field names and the sentinels;
  - the L0 multi-frontend layout;
  - one or two L0 major version bumps;
  - the producer of record.

### Storage — ccatobs/zarr-spectral-schema#20 (object storage and volume)

- The spec states the chunk independence invariant below.
- The spec recommends one shard per pixel and subscan with inner chunks of about 1 MB. The recommendation binds nobody.
- The DSL accepts a set of dtypes per array. L1 `spectra` and `t_sys` accept float32 and float64. An optional `keepbits` attr records bit rounding.
- The spec declares an optional array for the valid dump count per subscan.
- The spec states whether a finished store holds consolidated metadata.
- Both validators read only the `zarr.json` objects that the spec names. Both validators accept a local path and an `s3://` URL.
- One producer writes a store. A consumer writes no group into the store of a producer. An amendment to ADR-0008 (archive layout and discovery boundary) records the rule.

### Producers — ccatobs/kalibrate#101 (producers write the contract)

- The workspace pins the spec revision with the three spec issues above.
- A test converts a small real FITS dataset to L0, calibrates the result to L1 and validates both stores, inside the existing test job.
- zarr-fits writes `l0_complete` as the last write of a scan. A rerun converts a scan without the marker again.
- zarr-fits keeps the header of each record and the per-pixel values that ccatobs/zarr-spectral-schema#19 declares.
- calibrate writes each new field from the value that the calculation applied.
- ccatobs/kalibrate#86 (review findings checked against legacy kalibrate) owns the other writer and reader fixes.

### YODA — ccatobs/yoda#50 (YODA reads complete data) and ccatobs/yoda#51 (YODA uses the binding)

- YODA reads a scan or a band only with `l1_complete` set. The watcher reports a scan on completion and detects zarr v3 levels.
- The status check matches L0 scans to complete L1 scans through the provenance attrs.
- One keep mask covers every array with a subscan axis. For older stores, YODA maps L1 subscans to L0 pixel offsets through the provenance attrs.
- YODA takes identity from the band group.
- YODA writes reduction output to a work store and opens the L1 store read only.
- YODA depends on the spec binding, pinned by commit SHA. A newer minor version warns once. A newer major version is an error.
- YODA writes frames from the store. Without the fields, YODA writes no frame keyword and logs a warning.

### Order

- ccatobs/zarr-spectral-schema#18, ccatobs/zarr-spectral-schema#19, ccatobs/zarr-spectral-schema#20 and ccatobs/yoda#50 start in parallel.
- ccatobs/kalibrate#101 follows the three spec issues.
- ccatobs/yoda#51 follows ccatobs/zarr-spectral-schema#18 and ccatobs/zarr-spectral-schema#19.

### Invariants

- Invariant: a reader gives the same result for any chunk shape and any shard shape. Test: the non-default chunk fixtures of ccatobs/zarr-spectral-schema#20.
- Invariant: every store that a producer writes in CI passes the validator. Test: the real-data test of ccatobs/kalibrate#101.
- Invariant: a reader reads no unit without its completeness marker. Test: the incomplete-scan test of ccatobs/yoda#50.

## Testing decisions

- A good test gives a store to the public entry point and checks the verdict or the output. A test does not inspect validator internals.
- Both bindings run the same fixtures. Each Rust and Python divergence that the review found becomes one fixture.
- Real data enters at three points:
  - the committed real store pairs in the schema repo;
  - the end-to-end conversion of a small real FITS dataset in calibrate;
  - the YODA fixtures, rebuilt from the committed real stores.
- Science checks use named cases:
  - the pixel offset of `LFA_OTF_CHOPPED` subscan 5;
  - the pixel labels of band 0 in `SIM_LFA_HFA`;
  - the frame keyword in the cube header.
- calibrate changes report the system-temperature deviation per parity test against the baseline of ccatobs/kalibrate#86.
- Prior art: the identity conformance tests in both bindings, the L1 identity forwarding tests in calibrate, the executor and export tests in YODA.

## Out of scope

- The calibrate fixes that ccatobs/kalibrate#86 and its follow-up issues own.
- The schema of L2, L3 and the reduction groups. ADR-0007 keeps them YODA-owned until the reduction engine of ccatobs/yoda#35 writes them.
- The discovery index and the catalog. ADR-0008 defers both.
- Support for a second observatory beyond the SOFIA ingest.
- #8 (inspector command line) and #9 (docs graduation) stay under #1 (zarr-spectral-schema epic).
- A transactional storage layer such as Icechunk.
