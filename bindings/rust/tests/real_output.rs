//! Accept real producer output, reject broken output (Rust binding) — #18.
//!
//! Validates the stores that `conformance/generate.py main()` emits (the
//! `emit_real_output_fixtures` set) and checks each verdict. The Python binding
//! runs the same checks over the same stores (conformance/test_real_output.py).

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::OnceLock;

use zarr_spectral_schema as zss;

/// Emit the fixtures once per test binary with the shared spec-driven generator.
fn fixtures() -> &'static Path {
    static DIR: OnceLock<PathBuf> = OnceLock::new();
    DIR.get_or_init(|| {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
        let out = std::env::temp_dir().join(format!("zss_rust_real_output_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&out);
        let status = Command::new("python3")
            .arg(root.join("conformance/generate.py"))
            .arg(&out)
            .status()
            .expect("run conformance/generate.py (needs python3 + zarr/numpy)");
        assert!(status.success(), "generator failed");
        out
    })
}

fn errors(name: &str) -> Vec<String> {
    zss::validate(&fixtures().join(name))
}

// ── 1. a scan holds exactly one of the source group and the calibration group ──

#[test]
fn scan_with_only_source_or_only_calibration_is_green() {
    // l0.zarr: scan_000001 holds only source/, scan_000002 only calibration/.
    let l0 = fixtures().join("l0.zarr");
    assert!(l0.join("scan_000001/source").is_dir() && !l0.join("scan_000001/calibration").exists());
    assert!(l0.join("scan_000002/calibration").is_dir() && !l0.join("scan_000002/source").exists());
    let e = errors("l0.zarr");
    assert!(e.is_empty(), "expected green, got: {e:?}");
}

#[test]
fn scan_with_both_or_neither_group_is_rejected() {
    for name in ["l0_both_groups.zarr", "l0_no_group.zarr"] {
        let e = errors(name);
        assert!(
            e.iter().any(|m| m.contains("scan_000001") && m.contains("exactly one of")),
            "{name}: expected a one-of error, got: {e:?}"
        );
    }
}

#[test]
fn provenance_attrs_as_objects_are_green() {
    // calibrate writes source_l0_scans and processing_steps as JSON objects.
    let e = errors("l1_provenance_objects.zarr");
    assert!(e.is_empty(), "expected green, got: {e:?}");
}

#[test]
fn l1_raw_attrs_must_be_object() {
    let e = errors("l1_bad_raw_attrs.zarr");
    assert!(
        e.iter().any(|m| m.contains("raw_attrs") && m.contains("must be of type") && m.contains("object")),
        "expected a raw_attrs type error, got: {e:?}"
    );
}

// ── 2. reject broken output ────────────────────────────────────────────────

#[test]
fn scan_with_seven_digit_name_is_checked() {
    let e = errors("l0_wide_scan.zarr");
    assert!(
        e.iter().any(|m| m.contains("scan_1003537/source/data_5d")),
        "expected scan_1003537 to be checked, got: {e:?}"
    );
}

#[test]
fn root_child_not_matching_scan_pattern_is_rejected() {
    let e = errors("l0_bad_scan_name.zarr");
    assert!(
        e.iter().any(|m| m.contains("'scan_12'") && m.contains("scan pattern")),
        "expected a scan-pattern error naming scan_12, got: {e:?}"
    );
}

#[test]
fn l1_complete_false_is_rejected() {
    let e = errors("l1_incomplete.zarr");
    assert!(
        e.iter().any(|m| m.contains("l1_complete") && m.contains("must equal true")),
        "expected a value-constraint error, got: {e:?}"
    );
}

#[test]
fn l0_complete_marker_is_checked() {
    assert_eq!(zss::L0_COMPLETE_ATTR, "l0_complete");
    for (name, phrase) in [
        ("l0_bad_l0_complete.zarr", "must be of type \"boolean\""),
        ("l0_incomplete.zarr", "must equal true"),
    ] {
        let e = errors(name);
        assert!(
            e.iter().any(|m| m.contains("l0_complete") && m.contains(phrase)),
            "{name}: expected an l0_complete error, got: {e:?}"
        );
    }
}
