//! Real producer output is read cleanly (Rust binding) — #18, #19.
//!
//! The stores under `conformance/real_stores/` are byte-identical copies of what
//! zarr-fits (L0) and calibrate (L1) wrote. #19 bumped the contract to L0 3 and
//! L1 2.0, so these stores (L0 2, L1 1.3) sit below `min_supported`: each must
//! get exactly one version error, the below-floor error, and no metadata error
//! (the validator still parses every node). A missing store fails the test; it
//! never returns early. The Python binding validates the same stores
//! (conformance/test_real_stores.py).

use std::path::PathBuf;

use zarr_spectral_schema as zss;

/// One single-frontend pair and one multi-frontend pair (see real_stores/README.md),
/// each with the version attr of its level.
const REAL_STORES: &[(&str, &str)] = &[
    (
        "zarr/SOFIA_UPGREAT_SINGLE_POINT_TP.zarr",
        "l0_schema_version",
    ),
    ("calibrate/SINGLE_POINT_TP", "cal_schema_version"),
    ("zarr/SOFIA_UPGREAT_ORION_KL.zarr", "l0_schema_version"),
    ("calibrate/ORION_KL", "cal_schema_version"),
];

#[test]
fn real_stores_get_the_below_floor_error() {
    let real = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../conformance/real_stores");
    for (name, attr) in REAL_STORES {
        let store = real.join(name);
        assert!(
            store.join("zarr.json").is_file(),
            "real store missing: {}",
            store.display()
        );
        let errors = zss::validate(&store);
        // Version errors carry no [<level>] prefix; structure findings do.
        let version: Vec<&String> = errors.iter().filter(|e| !e.starts_with('[')).collect();
        assert!(
            version.len() == 1
                && version[0].contains(attr)
                && version[0].contains("older than the minimum supported"),
            "{name}: expected one below-floor {attr} error, got: {version:?}"
        );
        assert!(
            !errors.iter().any(|e| e.contains("metadata")),
            "{name}: expected no metadata error, got: {errors:?}"
        );
    }
}
