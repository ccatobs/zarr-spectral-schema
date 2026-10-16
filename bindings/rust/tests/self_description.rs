//! Dimensions, units, flag bits and the scientific frame (Rust binding) — #19.
//!
//! Validates the stores that `conformance/generate.py main()` emits (the
//! `emit_self_description_fixtures` set) and checks each verdict. The Python
//! binding runs the same checks over the same stores
//! (conformance/test_self_description.py).

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::OnceLock;

use zarr_spectral_schema as zss;

/// Emit the fixtures once per test binary with the shared spec-driven generator.
fn fixtures() -> &'static Path {
    static DIR: OnceLock<PathBuf> = OnceLock::new();
    DIR.get_or_init(|| {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
        let out = std::env::temp_dir().join(format!("zss_rust_self_desc_{}", std::process::id()));
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

fn assert_any(name: &str, parts: &[&str]) {
    let e = errors(name);
    assert!(
        e.iter().any(|m| parts.iter().all(|p| m.contains(p))),
        "{name}: expected an error containing {parts:?}, got: {e:?}"
    );
}

// ── 1. dimensions and units per array ──────────────────────────────────────

#[test]
fn spec_emitted_stores_are_green() {
    for name in ["l0.zarr", "l1_single.zarr", "l1_multi.zarr"] {
        let e = errors(name);
        assert!(e.is_empty(), "{name}: expected green, got: {e:?}");
    }
}

#[test]
fn array_without_dimension_names_is_rejected() {
    assert_any(
        "l1_no_dimension_names.zarr",
        &["'scan_000001/spectra'", "no dimension_names"],
    );
}

#[test]
fn dimension_names_differing_from_dims_is_rejected() {
    assert_any(
        "l1_wrong_dimension_names.zarr",
        &["'scan_000001/spectra'", "dimension_names", "!= expected"],
    );
}

#[test]
fn array_without_its_declared_units_is_rejected() {
    assert_any(
        "l1_no_units.zarr",
        &["'scan_000001/spectra'", "no units attr"],
    );
}

#[test]
fn array_with_other_units_is_rejected() {
    assert_any(
        "l1_wrong_units.zarr",
        &["'scan_000001/spectra'", "units \"mK\" != expected \"K\""],
    );
}

#[test]
fn arrays_disagreeing_on_a_shared_dimension_are_rejected() {
    // tau_signal has 2 channels, every other channel axis at the target has 1.
    assert_any(
        "l1_channel_mismatch.zarr",
        &["'scan_000001'", "dimension 'channel'", "2 in [tau_signal]"],
    );
}

#[test]
fn pixel_offset_and_spectra_disagreeing_on_subscans_are_rejected() {
    // The named case of #19: LFA_OTF/scan_025652 kept the 3 L0 subscans in
    // pixel_offset_lon while spectra holds the ON subscans only.
    let e = errors("l1_pixel_offset_subscans.zarr");
    assert!(
        e.iter().any(|m| m.contains("dimension 'subscan'")
            && m.contains("3 in [pixel_offset_lon]")
            && m.split("3 in").next().unwrap().contains("spectra")),
        "expected a subscan-length error naming pixel_offset_lon and spectra, got: {e:?}"
    );
}

#[test]
fn valid_dump_count_disagreeing_on_subscans_is_rejected() {
    // #20 deferred this length check of n_valid_dumps against S to #19.
    for (name, member) in [
        ("l0_valid_dumps_subscans.zarr", "source/n_valid_dumps"),
        ("l1_valid_dumps_subscans.zarr", "n_valid_dumps"),
    ] {
        let want = format!("3 in [{member}]");
        assert_any(name, &["'scan_000001'", "dimension 'subscan'", &want]);
    }
}

// ── 2. flag bits ───────────────────────────────────────────────────────────

#[test]
fn one_constant_per_flag_bit_with_the_python_values() {
    // The bit table of calibrate cal-core/src/flags.rs and YODA io/flags.py,
    // pinned with the same values in conformance/test_self_description.py.
    let bits: [(u16, u16); 9] = [
        (zss::BAD_CHANNEL, 1 << 0),
        (zss::FILL_VALUE, 1 << 1),
        (zss::SPIKE, 1 << 2),
        (zss::RFI, 1 << 3),
        (zss::TSYS_OUTLIER, 1 << 4),
        (zss::ELEVATION_LOW, 1 << 5),
        (zss::ML_FLAGGED, 1 << 6),
        (zss::MANUAL, 1 << 7),
        (zss::LOW_TRANSMISSION, 1 << 8),
    ];
    for (got, want) in bits {
        assert_eq!(got, want);
    }
}

// ── 3. scientific frame ────────────────────────────────────────────────────

/// The required L1 frame set: scan-or-band and scan attrs, then arrays.
const FRAME_ATTRS: &[&str] = &[
    "specsys",
    "veldef",
    "vlsr_source",
    "vlsr_source_frame",
    "ref_channel_base",
    "coord_frame",
    "equinox",
    "projection",
    "offset_convention",
    "temperature_scale",
    "eta_fss",
    "eta_mb",
    "gain_el_correction",
    "sideband_mode",
    "image_gain",
    "site_longitude_deg",
    "site_latitude_deg",
    "site_altitude_m",
];
const FRAME_ARRAYS: &[&str] = &[
    "v_frame",
    "ref_lon",
    "ref_lat",
    "platform_lon",
    "platform_lat",
    "platform_alt",
];

#[test]
fn scan_without_the_frame_set_names_each_missing_field() {
    let e = errors("l1_no_frame.zarr");
    let missing: Vec<String> = FRAME_ATTRS
        .iter()
        .map(|n| format!("attribute '{n}' is missing"))
        .chain(
            FRAME_ARRAYS
                .iter()
                .map(|n| format!("array 'scan_000001/{n}' is missing")),
        )
        .filter(|m| !e.iter().any(|x| x.contains(m.as_str())))
        .collect();
    assert!(missing.is_empty(), "not reported: {missing:?}; got: {e:?}");
}

#[test]
fn l0_store_without_the_site_location_is_rejected() {
    let e = errors("l0_no_site.zarr");
    for name in ["site_longitude_deg", "site_latitude_deg", "site_altitude_m"] {
        let want = format!("[L0] required attribute '{name}' is missing at 'root'");
        assert!(e.contains(&want), "expected {want:?}, got: {e:?}");
    }
}

/// Fields that admit the not-applicable sentinel, with the sentinel they hold
/// in the l1_not_applicable fixture: "NONE" for a string, JSON null for a number.
const NOT_APPLICABLE: &[(&str, &str)] = &[
    ("specsys", "\"NONE\""),
    ("veldef", "\"NONE\""),
    ("vlsr_source", "null"),
    ("vlsr_source_frame", "\"NONE\""),
    ("coord_frame", "\"NONE\""),
    ("equinox", "null"),
    ("projection", "\"NONE\""),
    ("offset_convention", "\"NONE\""),
    ("temperature_scale", "\"NONE\""),
    ("eta_mb", "null"),
    ("sideband_mode", "\"NONE\""),
    ("image_gain", "null"),
    ("site_longitude_deg", "null"),
    ("site_latitude_deg", "null"),
    ("site_altitude_m", "null"),
];

/// Read one node's zarr.json.
fn meta(store: &str, rel: &str) -> serde_json::Value {
    let path = fixtures().join(store).join(rel).join("zarr.json");
    serde_json::from_str(&std::fs::read_to_string(path).expect("read zarr.json"))
        .expect("parse zarr.json")
}

#[test]
fn fixture_holds_the_not_applicable_sentinel_and_is_green() {
    let scan = meta("l1_not_applicable.zarr", "scan_000001");
    for (name, sentinel) in NOT_APPLICABLE {
        // `.get`: indexing a missing key yields null, which would pass vacuously.
        let held = scan["attributes"].get(name).map(|v| v.to_string());
        assert_eq!(held.as_deref(), Some(*sentinel), "{name}");
    }
    // A frame array that does not apply holds its NaN fill value.
    let platform = meta("l1_not_applicable.zarr", "scan_000001/platform_lon");
    assert_eq!(platform["fill_value"], "NaN");
    let e = errors("l1_not_applicable.zarr");
    assert!(e.is_empty(), "expected green, got: {e:?}");
}

#[test]
fn l0_fixture_holds_the_site_sentinel_and_is_green() {
    let root = meta("l0_not_applicable.zarr", "");
    for name in ["site_longitude_deg", "site_latitude_deg", "site_altitude_m"] {
        assert!(root["attributes"][name].is_null(), "{name}");
    }
    let e = errors("l0_not_applicable.zarr");
    assert!(e.is_empty(), "expected green, got: {e:?}");
}

#[test]
fn sentinel_on_a_field_that_admits_none_is_rejected() {
    // eta_fss is the forward efficiency calibrate applied: it always applies.
    assert_any(
        "l1_eta_fss_null.zarr",
        &["'eta_fss'", "must be of type \"number\""],
    );
}

#[test]
fn value_outside_the_declared_set_is_rejected() {
    assert_any(
        "l1_bad_specsys.zarr",
        &["'specsys'", "must be one of", "\"LSRK\"", "got \"LSR\""],
    );
}

// ── 4. multi-frontend layout ───────────────────────────────────────────────

/// Identity, quality record and provenance of one band: on each band group of
/// a multi-frontend L1 scan, never on the scan group.
const BAND_PLACED: &[&str] = &[
    "arrays",
    "pixels_per_array",
    "frontend",
    "raw_attrs",
    "qa",
    "pwv_mm",
    "cal_hash",
    "source_l0_store",
    "source_l0_scans",
    "cal_config",
    "processing_timestamp",
    "atm_table_info",
    "processing_steps",
    "linked_pwv_source",
];

#[test]
fn identity_quality_record_and_provenance_sit_on_each_band() {
    for band in ["scan_000001/band_0", "scan_000001/band_1"] {
        let attrs = meta("l1_multi.zarr", band);
        let missing: Vec<&&str> = BAND_PLACED
            .iter()
            .filter(|n| attrs["attributes"].get(**n).is_none())
            .collect();
        assert!(missing.is_empty(), "{band} lacks {missing:?}");
    }
    let scan = meta("l1_multi.zarr", "scan_000001");
    let on_scan: Vec<&&str> = BAND_PLACED
        .iter()
        .filter(|n| scan["attributes"].get(**n).is_some())
        .collect();
    assert!(on_scan.is_empty(), "scan group holds {on_scan:?}");
    let e = errors("l1_multi.zarr");
    assert!(e.is_empty(), "expected green, got: {e:?}");
}

#[test]
fn band_without_its_quality_record_is_rejected() {
    let e = errors("l1_multi_band_without_qa.zarr");
    let want = "[L1] required attribute 'qa' is missing at 'scan_000001/band_1'";
    assert!(e.iter().any(|m| m == want), "expected {want:?}, got: {e:?}");
}

#[test]
fn multi_frontend_scan_group_holding_arrays_is_rejected() {
    assert_any(
        "l1_multi_scan_arrays.zarr",
        &["attribute 'arrays' at 'scan_000001'", "each band"],
    );
}

#[test]
fn multi_frontend_scan_group_holding_band_identity_is_rejected() {
    // #34: backend, obs_id and spectel1 belong to one frontend.
    for store in ["l0_multi_scan_identity.zarr", "l1_multi_scan_identity.zarr"] {
        for name in ["backend", "obs_id", "spectel1"] {
            assert_any(
                store,
                &[&format!("attribute '{name}' at 'scan_000001'"), "each band"],
            );
        }
    }
}

#[test]
fn l0_multi_frontend_store_holds_each_band_under_its_scan() {
    // scan_N/band_B/ at L0, as at L1: no virtual scan number N + 1000000·b.
    let store = fixtures().join("l0_multi.zarr");
    for (scan, group) in [("scan_000001", "source"), ("scan_000002", "calibration")] {
        for band in ["band_0", "band_1"] {
            assert!(store
                .join(format!("{scan}/{band}/{group}/data_5d/zarr.json"))
                .is_file());
            assert!(
                meta("l0_multi.zarr", &format!("{scan}/{band}"))["attributes"]["frontend"]
                    .is_string()
            );
        }
        assert!(meta("l0_multi.zarr", scan)["attributes"]
            .get("frontend")
            .is_none());
    }
    let e = errors("l0_multi.zarr");
    assert!(e.is_empty(), "expected green, got: {e:?}");
}

#[test]
fn validator_checks_every_band_of_a_multi_frontend_l0_store() {
    let e = errors("l0_multi_band_without_data.zarr");
    let want = "[L0] required array 'scan_000001/band_1/source/data_5d' is missing";
    assert!(e.iter().any(|m| m == want), "expected {want:?}, got: {e:?}");
}

// ── every new structure finding carries the [<level>] prefix ───────────────
// calibrate (kalibrate#82) splits check() errors by this prefix.

#[test]
fn new_structure_findings_carry_the_level_prefix() {
    for name in [
        "l1_no_dimension_names.zarr",
        "l1_wrong_dimension_names.zarr",
        "l1_no_units.zarr",
        "l1_wrong_units.zarr",
        "l1_channel_mismatch.zarr",
        "l1_pixel_offset_subscans.zarr",
        "l1_no_frame.zarr",
        "l0_no_site.zarr",
        "l1_eta_fss_null.zarr",
        "l1_bad_specsys.zarr",
        "l1_multi_band_without_qa.zarr",
        "l1_multi_scan_arrays.zarr",
        "l0_multi_band_without_data.zarr",
        "l0_no_l0_complete.zarr",
        "l1_no_identity.zarr",
        "l0_no_arrays.zarr",
        "l0_multi_scan_identity.zarr",
        "l1_multi_scan_identity.zarr",
        "l0_valid_dumps_subscans.zarr",
        "l1_valid_dumps_subscans.zarr",
    ] {
        let e = errors(name);
        assert!(!e.is_empty(), "{name}: expected structure findings");
        assert!(
            e.iter()
                .all(|m| m.starts_with("[L0] ") || m.starts_with("[L1] ")),
            "{name}: every structure finding must carry the [<level>] prefix, got: {e:?}"
        );
    }
}

// ── a malformed shape is a finding, never an exception (PR 25 review) ──────
// Both bindings pin the same full error list, so their verdicts agree.

#[test]
fn malformed_shape_is_one_finding_in_both_bindings() {
    for name in ["float", "negative", "nested", "null", "string"] {
        let e = errors(&format!("l1_shape_{name}.zarr"));
        assert_eq!(
            e,
            ["[L1] array 'scan_000001/t_int' shape is not a list of non-negative integers"],
            "{name}"
        );
    }
}

// ── required since L0 3 / L1 2.0 (maintainer decisions of 2026-10-14) ──────

#[test]
fn l0_scan_without_its_completeness_marker_is_rejected() {
    let e = errors("l0_no_l0_complete.zarr");
    let want = "[L0] required attribute 'l0_complete' is missing at 'scan_000001'";
    assert!(e.iter().any(|m| m == want), "expected {want:?}, got: {e:?}");
}

#[test]
fn l1_scan_without_its_identity_is_rejected() {
    let e = errors("l1_no_identity.zarr");
    for name in ["frontend", "raw_attrs"] {
        let want = format!("[L1] required attribute '{name}' is missing at 'scan_000001'");
        assert!(e.contains(&want), "expected {want:?}, got: {e:?}");
    }
}

#[test]
fn l0_pixels_per_array_without_arrays_is_rejected() {
    // #34: pixels_per_array is positional over `arrays`; without it a reader
    // cannot pair the pixel lists with their arrays.
    let e = errors("l0_no_arrays.zarr");
    let want = "[L0] required attribute 'arrays' is missing at 'scan_000001'";
    assert!(e.iter().any(|m| m == want), "expected {want:?}, got: {e:?}");
}
