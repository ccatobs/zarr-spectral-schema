//! Real producer output is read cleanly (Rust binding) — #18, #19.
//!
//! The stores under `conformance/real_stores/` are producer output of
//! ccatobs/kalibrate `epic/21` at 5bf7c7b: zarr-fits wrote L0 3, calibrate wrote
//! L1 2.0 (see real_stores/README.md). Each store validates with zero findings.
//! A missing store fails the test; it never returns early. The Python binding
//! validates the same stores (conformance/test_real_stores.py).

use std::path::PathBuf;

use zarr_spectral_schema as zss;

/// One single-frontend pair and one multi-frontend pair (see real_stores/README.md).
const REAL_STORES: &[&str] = &[
    "zarr/SOFIA_UPGREAT_SINGLE_POINT_TP.zarr",
    "calibrate/SINGLE_POINT_TP",
    "zarr/SOFIA_UPGREAT_ORION_KL.zarr",
    "calibrate/ORION_KL",
];

#[test]
#[ignore = "the real stores predate #34 (L0 arrays, per-band identity, image_gain); ccatobs/zarr-spectral-schema#37 writes them again"]
fn real_stores_have_zero_findings() {
    let real = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../conformance/real_stores");
    for name in REAL_STORES {
        let store = real.join(name);
        assert!(
            store.join("zarr.json").is_file(),
            "real store missing: {}",
            store.display()
        );
        let result = zss::check(&store);
        assert!(result.errors.is_empty(), "{name}: {:?}", result.errors);
        assert!(result.warnings.is_empty(), "{name}: {:?}", result.warnings);
    }
}
