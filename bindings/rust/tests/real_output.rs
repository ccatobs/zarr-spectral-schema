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

#[test]
fn skydiff_family_member_dtype_and_ndim_are_checked() {
    let e = errors("l1_bad_skydiff.zarr");
    let rel = "scan_000001/skydiff/spectrum_0";
    assert!(e.iter().any(|m| m.contains(rel) && m.contains("dtype")), "expected a dtype error, got: {e:?}");
    assert!(e.iter().any(|m| m.contains(rel) && m.contains("ndim")), "expected an ndim error, got: {e:?}");
}

// ── 3. one strict parse path in both bindings ──────────────────────────────
// Each fixture is a former divergence between the Rust and the Python verdict.
// The messages asserted here are identical in both bindings.

#[test]
fn non_finite_attr_is_its_own_error() {
    let e = errors("l1_nan_attr.zarr");
    let want = "[L1] 'scan_000001/zarr.json': non-finite number in metadata";
    assert!(e.iter().any(|m| m == want), "expected {want:?}, got: {e:?}");
}

#[test]
fn malformed_zarr_json_is_an_error() {
    let e = errors("l1_malformed_meta.zarr");
    let want = "[L1] 'scan_000001/t_int/zarr.json': malformed metadata (not valid JSON)";
    assert!(e.iter().any(|m| m == want), "expected {want:?}, got: {e:?}");
}

#[test]
fn object_form_of_data_type_is_accepted() {
    let e = errors("l1_object_dtype.zarr");
    assert!(e.is_empty(), "expected green, got: {e:?}");
}

#[test]
fn group_at_array_path_is_rejected() {
    let e = errors("l1_group_at_array.zarr");
    let want = "[L1] 'scan_000001/lloadsn' is a group, expected an array";
    assert!(e.iter().any(|m| m == want), "expected {want:?}, got: {e:?}");
}

#[test]
fn unicode_digits_in_version_are_rejected() {
    let e = errors("ver_unicode_digits.zarr");
    assert!(
        e.iter().any(|m| m.contains("cal_schema_version") && m.contains("cannot parse version")),
        "expected a version parse error, got: {e:?}"
    );
}

#[test]
fn unicode_digits_in_scan_name_are_rejected() {
    let e = errors("l0_unicode_scan_name.zarr");
    assert!(
        e.iter().any(|m| m.contains("'scan_٠٠٠٠٠٢'") && m.contains("scan pattern")),
        "expected a scan-pattern error, got: {e:?}"
    );
}

// ── structure findings carry the [<level>] prefix ──────────────────────────
// calibrate (kalibrate#82) splits check() errors by this prefix: a structure
// finding only warns there; any other error fails the run. Every structure
// finding must therefore start with "[L0] " or "[L1] ".

#[test]
fn structure_findings_carry_the_level_prefix() {
    for name in [
        "l0_both_groups.zarr",
        "l0_no_group.zarr",
        "l0_wide_scan.zarr",
        "l0_bad_scan_name.zarr",
        "l0_unicode_scan_name.zarr",
        "l0_bad_l0_complete.zarr",
        "l0_incomplete.zarr",
        "l1_incomplete.zarr",
        "l1_bad_raw_attrs.zarr",
        "l1_bad_skydiff.zarr",
        "l1_nan_attr.zarr",
        "l1_malformed_meta.zarr",
        "l1_group_at_array.zarr",
    ] {
        let e = errors(name);
        assert!(!e.is_empty(), "{name}: expected structure findings");
        assert!(
            e.iter().all(|m| m.starts_with("[L0] ") || m.starts_with("[L1] ")),
            "{name}: every structure finding must carry the [<level>] prefix, got: {e:?}"
        );
    }
}
