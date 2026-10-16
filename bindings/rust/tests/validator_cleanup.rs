//! Validator and spec findings of the contract epic (Rust binding) — #33.
//!
//! Validates the stores that `conformance/generate.py main()` emits (the
//! `emit_cleanup_fixtures` set). The Python binding runs the same checks over
//! the same stores (conformance/test_validator_cleanup.py).

mod common;

use common::errors;

// ── 1. validator walk and error output ─────────────────────────────────────

#[test]
fn parse_error_is_reported_once() {
    let e = errors("two_levels.zarr");
    let parse: Vec<&String> = e
        .iter()
        .filter(|m| m.contains("malformed metadata"))
        .collect();
    assert_eq!(
        parse,
        ["[L0] 'scan_000001/t_int/zarr.json': malformed metadata (not valid JSON)"]
    );
}

#[test]
fn version_errors_come_in_sorted_order() {
    let e = errors("two_levels.zarr");
    let versions: Vec<&str> = e
        .iter()
        .filter(|m| m.starts_with("root attribute"))
        .map(|m| m.split('\'').nth(1).unwrap())
        .collect();
    assert_eq!(versions, ["cal_schema_version", "l0_schema_version"]);
}

#[test]
fn array_at_a_scan_path_is_a_finding() {
    assert_eq!(
        errors("l0_array_at_scan_path.zarr"),
        ["[L0] 'scan_000003' is an array, expected a scan group"]
    );
}

// ── 2. spec checks ─────────────────────────────────────────────────────────

#[test]
fn node_at_a_multi_frontend_scan_group_is_a_finding() {
    for (name, level, node) in [
        ("l1_multi_scan_array.zarr", "L1", "scan_000001/spectra"),
        ("l0_multi_scan_group.zarr", "L0", "scan_000001/source"),
    ] {
        let want = format!(
            "[{level}] '{node}' is not a band group: the scan group of a multi-frontend \
             scan holds band groups only"
        );
        assert_eq!(errors(name), [want], "{name}");
    }
}

#[test]
fn virtual_scan_group_is_a_finding() {
    assert_eq!(
        errors("l0_virtual_scan.zarr"),
        [
            "[L0] 'scan_1029951' is a virtual scan number (scan_029951 + 1000000 x 1): \
          a multi-frontend scan holds each band under scan_N/band_B/"
        ]
    );
}

#[test]
fn axis_outside_the_dimension_vocabulary_is_a_finding() {
    assert_eq!(
        errors("l1_dims_outside_vocabulary.zarr"),
        [
            r#"[L1] array 'scan_000001/spectra' dimension_names ["channel", "time", "receiver", "array", "subscan"] != expected ["channel", "dump", "receiver", "array", "subscan"]"#
        ]
    );
}

#[test]
fn units_attr_on_an_array_without_a_unit_is_a_finding() {
    assert_eq!(
        errors("l1_units_on_flags.zarr"),
        [r#"[L1] array 'scan_000001/flags' has a units attr "" but declares no unit"#]
    );
}

#[test]
fn non_ascii_text_is_not_escaped() {
    let e = errors("l1_non_ascii_specsys.zarr");
    assert!(e.len() == 1 && e[0].ends_with("got \"LSR\u{fc}\""), "{e:?}");
}

#[test]
fn negative_zero_is_an_integer() {
    let store = common::fixtures().join("l1_negative_zero.zarr");
    let meta = std::fs::read_to_string(store.join("scan_000001/zarr.json")).unwrap();
    assert!(meta.contains("\"ref_channel_base\": -0"));
    assert_eq!(errors("l1_negative_zero.zarr"), Vec::<String>::new());
}

#[test]
fn frame_attrs_come_from_the_spec() {
    assert_eq!(zarr_spectral_schema::FRAME_ATTRS.len(), 18);
    assert!(zarr_spectral_schema::FRAME_ATTRS.contains(&"image_gain"));
}

// ── 5. unit comparison ─────────────────────────────────────────────────────

#[test]
fn units_compare_as_exact_strings() {
    // ADR 0012 (units contract): no unit parsing, `ct / (K s)` is not `ct/(K s)`.
    assert_eq!(
        errors("l1_gamma_spaced_unit.zarr"),
        [r#"[L1] array 'scan_000001/gamma' units "ct / (K s)" != expected "ct/(K s)""#]
    );
}

// ── 3. object storage input and output ─────────────────────────────────────

#[test]
fn malformed_metadata_is_a_parse_error_not_a_missing_node() {
    assert_eq!(
        errors("l1_malformed_meta.zarr"),
        ["[L1] 'scan_000001/t_int/zarr.json': malformed metadata (not valid JSON)"]
    );
}

#[test]
fn keepbits_outside_its_range_is_a_finding() {
    for (name, finding) in [
        ("l1_keepbits_above.zarr", "<= 52, got 53"),
        ("l1_keepbits_below.zarr", ">= 0, got -1"),
    ] {
        let want = format!("[L1] attribute 'keepbits' at 'scan_000001/spectra' must be {finding}");
        assert_eq!(errors(name), [want], "{name}");
    }
}

#[cfg(not(feature = "s3"))]
#[test]
fn check_url_takes_a_string() {
    let r = zarr_spectral_schema::check_url("s3://zss/l1_single.zarr");
    assert_eq!(
        r.errors,
        ["s3://zss/l1_single.zarr: s3:// needs the `s3` feature"]
    );
    let local = common::fixtures().join("l1_single.zarr");
    assert!(zarr_spectral_schema::check_url(local.to_str().unwrap()).ok());
}

// ── findings of the whole-epic review of PR #36 ────────────────────────────

#[test]
fn metadata_past_127_levels_is_malformed_in_both_bindings() {
    assert_eq!(errors("l1_depth_127.zarr"), Vec::<String>::new());
    assert_eq!(
        errors("l1_depth_128.zarr"),
        ["[L1] 'scan_000001/t_int/zarr.json': malformed metadata (not valid JSON)"]
    );
}

#[cfg(feature = "s3")]
#[test]
fn url_without_its_object_store_backend_is_an_error() {
    // The `s3` feature builds object_store with AWS only.
    let r = zarr_spectral_schema::check_url("gs://bucket/store.zarr");
    assert_eq!(r.errors.len(), 1, "{:?}", r.errors);
    assert!(
        r.errors[0].starts_with("gs://bucket/store.zarr: "),
        "{:?}",
        r.errors
    );
}

// ── review of PR #40 ───────────────────────────────────────────────────────

#[test]
fn truncated_one_of_group_gives_the_parse_error_only() {
    assert_eq!(
        errors("l0_truncated_one_of.zarr"),
        ["[L0] 'scan_000001/source/zarr.json': malformed metadata (not valid JSON)"]
    );
}

#[test]
fn exponent_minus_zero_is_a_number() {
    let store = common::fixtures().join("l1_exponent_zero.zarr");
    let meta = std::fs::read_to_string(store.join("scan_000001/zarr.json")).unwrap();
    assert!(meta.contains("\"rest_freq_hz\": 9.7e-0"));
    assert_eq!(errors("l1_exponent_zero.zarr"), Vec::<String>::new());
}
