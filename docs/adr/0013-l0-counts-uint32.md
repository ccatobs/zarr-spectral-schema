# 0013 — L0 counts are uint32 with a declared fill value

- **Status:** Accepted
- **Date:** 2026-10-15
- **Deciders:** the maintainer (2026-10-15)
- **Relates to:**
  - ccatobs/zarr-spectral-schema#27 (L0 count dtype for counts of 2^31 and above)
  - PR #29 (uint32 L0 counts and flag bit 8)
  - ccatobs/kalibrate#101 (write the extended L0–L1 contract)
  - epic ccatobs/zarr-spectral-schema#21 (production-grade L0–L1 contract)

## Holds

- **Invariant** (test: `conformance/test_storage_contract.py::test_l0_counts_are_uint32_with_the_saturated_fill_value`): L0 `data_5d` is `uint32` with `fill_value` 4294967295.
- **Invariant** (test: `conformance/test_storage_contract.py::test_l0_int32_counts_are_a_dtype_error_naming_data_5d`): an L0 3 store with `int32` counts is a structure finding.
- **Default:** a reader masks the declared fill value of the array before it uses a count. A count equal to 4294967295 is not a valid reading.
- **Incidental:** zarr-fits writes the fill value into the padding dumps of a subscan shorter than the longest one.

## Revisit if

- A backend delivers counts of 2^32 or above.
- A producer needs a count of exactly 4294967295.

## Context

- CHAI FITS files hold counts as uint32: signed 32-bit data with `BZERO = 2^31`.
- Up to PR #29, L0 stored the counts as `int32` with fill value −2^31. A count of 2^31 mapped onto the fill value, and larger counts wrapped. Scan 37404 holds such counts.
- `int64` would double the size of `data_5d`, the largest L0 array.

## Decision

L0 `data_5d` is `uint32`. Its declared fill value is 4294967295 (u32::MAX), the saturated counter value.

## Consequences / Trade-offs

- Every count a CHAI backend writes fits without loss.
- The size of `data_5d` stays the same.
- A reader of an older L0 store masks the fill value the array declares, so one masking rule covers `int32` and `uint32` stores.
- The change went into L0 3 without a further version bump, because L0 3 was unreleased (ADR 0007 amendment of 2026-10-15).
- Calibrated output changed only for scans with counts of 2^31 and above: the median T_sys of scan 37404 moved from 4362.43 K to 4368.00 K.

## Alternatives considered

- **Keep `int32`.** Rejected: counts of 2^31 and above are lost or wrap.
- **`int64`.** Rejected: twice the storage for no value range the backends use.
- **`uint32` with fill value 0.** Rejected: 0 is a valid count.

## Status

Accepted.

## Amendments

None.
