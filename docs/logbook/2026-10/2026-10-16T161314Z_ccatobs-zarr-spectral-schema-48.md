<!-- lore:ccatobs/zarr-spectral-schema:#48 -->
**2026-10-16** · Christof Buchbender · ccatobs/zarr-spectral-schema#48

**Why:** the L0 3 and L1 2.0 contract needed per-band identity, L0 `arrays` and `image_gain`. See [epic 38](https://github.com/ccatobs/zarr-spectral-schema/issues/38).

**What we did:** [PR 48](https://github.com/ccatobs/zarr-spectral-schema/pull/48) carries #33, #34 and #37. It amends the spec without a version bump, fixes the validator findings and refreshes the real stores.

**Consequences:** ADR 0014 records true-angle pixel offsets. Merge with a merge commit so the pinned kalibrate and yoda SHAs stay reachable.

**Not done yet:** Issues #42 to #44 carry the follow-ups.
