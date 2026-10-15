<!-- lore:ccatobs/zarr-spectral-schema:#36 -->
**2026-10-15** · Christof Buchbender · ccatobs/zarr-spectral-schema#36

**Why:** Real producer output failed the spec, and the spec held no dimensions, units, frames or object-storage rules. Epic [#21](https://github.com/ccatobs/zarr-spectral-schema/issues/21) made the L0–L1 contract production grade.

**What we did:** Six features landed through [PR #36](https://github.com/ccatobs/zarr-spectral-schema/pull/36). The spec moved to L0 3 and L1 2.0. A multi-frontend scan holds its bands under scan_N/band_B/ at both levels. Arrays declare dims and units, L0 counts are uint32, and both validators read S3 stores.

**Consequences:** ADRs 0009 to 0013 and four ADR 0007 amendments record the decisions. The validators reject older stores; readers use version-gated shims. kalibrate and yoda pin 151df9f, so this branch merges with a merge commit.

**Not done yet:** Issues #32 to #35 carry the follow-ups. The science review of the L1 frame set (#35) is open.
