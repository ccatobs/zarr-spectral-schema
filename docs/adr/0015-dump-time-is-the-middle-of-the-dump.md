# 0015 — The L1 dump time is the middle of the dump

- **Status:** Accepted
- **Date:** 2026-10-18
- **Deciders:** the maintainer (2026-10-17, merge of PR #51)
- **Relates to:**
  - ccatobs/zarr-spectral-schema#50 (add a time stamp per dump to L1)
  - PR #51 (time stamp per dump: L0 dump step inputs, L1 `dump_mjd`, L1 2.1)
  - ccatobs/kalibrate#213 (write a time stamp per dump to L1, the producer side)
  - ccatobs/kalibrate#203 (legacy chopped OTF time stamps)

## Holds

- **Default:** a producer writes L1 `dump_mjd [dump, subscan]` as the MJD (UTC) of the middle of each dump: dump start + dump step / 2.
- **Default:** the dump start of dump j is the subscan start + `otf_clear` · `bck_len` + j · dump step.
- **Default:** the dump step is `otf_srcyc` / `chop_freq` for a chopped OTF subscan (`otf_srcyc` > 0), else `spec_time`.
- **Default:** a consumer that needs the legacy instant, the start of the dump, subtracts half a dump step itself. The spec holds no second time array.
- **Incidental:** L0 carries the dump step inputs as the typed, optional per-subscan arrays `otf_clear`, `bck_len`, `spec_time` and `otf_srcyc` (FITS OTFCLEAR, BCKLEN, SPECTIME, OTFSRCYC). L1 2.1 adds `dump_mjd` as optional; the floor stays 2.0.
- **Incidental:** a dump past `n_valid_dumps` holds NaN. A merged beam pair (calibrate `--merge-beams`) keeps the time of beam A, as it keeps the position of beam A.
- **Incidental:** the producer code is `dump_mid_mjd` in ccatobs/kalibrate `crates/cal-io/src/raw.rs`, tested by `raw::tests::dump_mid_mjd_follows_the_dump_step` there. No test in this repo checks the value of `dump_mjd`: the validator checks its dtype and shape.

## Revisit if

- A consumer needs the dump start and finds the half-step correction error-prone.
- A backend writes dumps of unequal length within one subscan, so one dump step per subscan no longer holds.
- A new observing mode sets the dump step from inputs other than the four L0 arrays.

## Context

- Up to L1 2.0, L1 held no time per dump. Downstream steps need one: CLASS and SDFITS output, local sidereal time (LST), gridding by time and pointing checks (#50).
- Legacy kalibrate stamps the start of a dump, after the clear time OTFCLEAR · BCKLEN.
- Legacy steps a chopped OTF dump by 1 / CHOPFREQ and ignores OTFSRCYC (ccatobs/kalibrate#203). The legacy time then slips by (OTFSRCYC − 1) / CHOPFREQ per dump.
- A dump integrates over its whole step. The OTF position of a dump and the spectrum belong to the middle of that interval, not to its start.
- Before PR #51, a consumer rebuilt the dump time from `raw_fits_headers`. The parity harness did so in `legacy_dump_time` (calibrate_parity_tests commit 3edde38).

## Decision

- L1 `dump_mjd` is the middle of the dump: start + step / 2.
- The step is `otf_srcyc` / `chop_freq` when chopped, else `spec_time`.
- L0 carries the inputs as typed fields: `otf_clear`, `bck_len`, `spec_time`, `otf_srcyc`.
- L1 goes to 2.1.

## Consequences / Trade-offs

- `dump_mjd` differs from the legacy instant by half a dump step (ccatobs/kalibrate#213). The difference is 0.15 s for total-power OTF, 7.5 s for total power and 15-40 s for beam switch.
- For chopped OTF, the difference reaches 22.2 s: half a step plus the legacy step error of ccatobs/kalibrate#203.
- The parity harness compares legacy UT against its own rebuild of the legacy instant from the typed L0 arrays. It reports `dump_mjd` minus the legacy instant as info, not gated.
- No consumer reads `raw_fits_headers` for the dump time.
- An L1 2.0 store and an L1 store from an L0 written before #50 lack `dump_mjd`. A consumer handles its absence.

## Alternatives considered

- **The start of the dump, as legacy writes.** Rejected. The start biases positions and drift fits by half a dump.
- **The legacy chopped step 1 / CHOPFREQ.** Rejected. The step ignores OTFSRCYC and slips the time per dump (ccatobs/kalibrate#203).
- **Rebuild the time in each consumer from `raw_fits_headers`.** Rejected. Each consumer would repeat the cadence rule, and the raw headers are not a typed contract.

## Status

Accepted.

## Amendments

None.
