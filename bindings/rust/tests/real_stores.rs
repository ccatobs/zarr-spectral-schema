//! Real producer output validates green (Rust binding) — #18.
//!
//! The stores under `conformance/real_stores/` are byte-identical copies of what
//! zarr-fits (L0) and calibrate (L1) wrote. Every one must validate with zero
//! errors. A missing store fails the test; it never returns early. The Python
//! binding validates the same stores (conformance/test_real_stores.py).

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
fn real_stores_are_green() {
    let real = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../conformance/real_stores");
    for name in REAL_STORES {
        let store = real.join(name);
        assert!(
            store.join("zarr.json").is_file(),
            "real store missing: {}",
            store.display()
        );
        let r = zss::check(&store);
        assert!(
            r.errors.is_empty(),
            "{name}: expected zero errors, got: {:?}",
            r.errors
        );
    }
}
