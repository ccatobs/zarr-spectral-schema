//! Full L0+L1 structural-contract conformance test (Rust binding) — Slice 4 (#5).
//!
//! Emits the synthetic stores from the spec via the shared Python generator (the
//! single emitter; the "binding parity" leg), then validates them with the Rust
//! interpreter. The Python binding validates the equivalent stores
//! (conformance/test_full_contract.py, test_version_negotiation.py) — together
//! they are the cross-binding proof that one spec drives one interpretation in
//! both languages.
//!
//! The generator's `main()` emits, into the target dir: the three structural
//! shapes (`l0.zarr`, `l1_single.zarr`, `l1_multi.zarr`), the structural
//! negatives (`miss_array.zarr`, `miss_band_index.zarr`), and the
//! version-negotiation fixtures (`ver_*.zarr`).

use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicUsize, Ordering};

use zarr_spectral_schema as zss;

static COUNTER: AtomicUsize = AtomicUsize::new(0);

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .expect("canonicalize repo root")
}

/// Emit fixtures into a unique temp dir using the shared spec-driven generator.
fn emit_fixtures() -> PathBuf {
    let root = repo_root();
    let n = COUNTER.fetch_add(1, Ordering::SeqCst);
    let out = std::env::temp_dir().join(format!("zss_rust_fix_{}_{n}", std::process::id()));
    let _ = std::fs::remove_dir_all(&out);
    let status = Command::new("python3")
        .arg(root.join("conformance/generate.py"))
        .arg(&out)
        .status()
        .expect("run conformance/generate.py (needs python3 + zarr/numpy)");
    assert!(status.success(), "generator failed");
    out
}

#[test]
fn constants_generated_from_spec() {
    assert_eq!(zss::DATA_5D, "data_5d");
    assert_eq!(zss::L0_SCHEMA_VERSION, "2");
}

// ── positive: each shape validates green ───────────────────────────────────

#[test]
fn l0_store_is_green() {
    let f = emit_fixtures();
    let problems = zss::validate(&f.join("l0.zarr"));
    assert!(problems.is_empty(), "expected green, got: {problems:?}");
}

#[test]
fn l1_single_frontend_is_green() {
    let f = emit_fixtures();
    let problems = zss::validate(&f.join("l1_single.zarr"));
    assert!(problems.is_empty(), "expected green, got: {problems:?}");
}

#[test]
fn l1_multi_frontend_is_green() {
    let f = emit_fixtures();
    let problems = zss::validate(&f.join("l1_multi.zarr"));
    assert!(problems.is_empty(), "expected green, got: {problems:?}");
}

// ── negatives: structural failures name the offending path/attr ────────────

#[test]
fn missing_required_array_is_rejected() {
    let f = emit_fixtures();
    let problems = zss::validate(&f.join("miss_array.zarr"));
    assert!(
        problems.iter().any(|p| p.contains("scan_000001/spectra")),
        "expected rejection naming scan_000001/spectra, got: {problems:?}"
    );
}

#[test]
fn multi_missing_band_index_is_rejected() {
    let f = emit_fixtures();
    let problems = zss::validate(&f.join("miss_band_index.zarr"));
    assert!(
        problems
            .iter()
            .any(|p| p.contains("band_index") && p.contains("band_1")),
        "expected rejection naming band_index on band_1, got: {problems:?}"
    );
}

#[test]
fn store_with_no_level_is_rejected() {
    // A bare group carrying neither detect attr declares no known level. Build it
    // by hand (no version attrs) so the generator stays the single emitter.
    let n = COUNTER.fetch_add(1, Ordering::SeqCst);
    let dir = std::env::temp_dir().join(format!("zss_rust_bare_{}_{n}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(
        dir.join("zarr.json"),
        r#"{"zarr_format":3,"node_type":"group","attributes":{}}"#,
    )
    .unwrap();
    let problems = zss::validate(&dir);
    assert!(
        problems.iter().any(|p| p.contains("no known level")),
        "expected no-known-level rejection, got: {problems:?}"
    );
}

// ── the scan-or-band rule is DATA-driven, not hand-branched ────────────────

#[test]
fn scan_or_band_rule_is_data_driven() {
    // Both stores carry the same scan_or_band_attrs declaration; the validator
    // resolves the target from band_<M> presence, never a special case — so a
    // single-frontend store (line/rest_freq_hz at the scan group) and a
    // multi-frontend store (those attrs on each band) both validate green.
    let f = emit_fixtures();
    assert!(zss::validate(&f.join("l1_single.zarr")).is_empty());
    assert!(zss::validate(&f.join("l1_multi.zarr")).is_empty());
}

// ── version negotiation (Slice 3, #4) — validates the SAME spec-emitted
// version fixtures the Python binding does, proving one interpretation. ──

#[test]
fn version_equal_is_green() {
    let f = emit_fixtures();
    let r = zss::check(&f.join("l1_single.zarr"));
    assert!(r.ok(), "expected green, got: {:?}", r.errors);
    assert!(r.warnings.is_empty(), "unexpected warnings: {:?}", r.warnings);
}

#[test]
fn version_newer_minor_warns_but_passes() {
    let f = emit_fixtures();
    let r = zss::check(&f.join("ver_newer_minor.zarr"));
    assert!(r.ok(), "newer-minor must still pass, got errors: {:?}", r.errors);
    assert_eq!(r.warnings.len(), 1, "expected one warning, got: {:?}", r.warnings);
    let w = &r.warnings[0];
    assert!(
        w.contains("cal_schema_version") && w.contains("1.9") && w.contains("1.3"),
        "warning should name the axis and both versions, got: {w}"
    );
    // Back-compat: validate() returns errors only → empty for a warning-only store.
    assert!(zss::validate(&f.join("ver_newer_minor.zarr")).is_empty());
}

#[test]
fn version_newer_major_errors() {
    let f = emit_fixtures();
    let r = zss::check(&f.join("ver_newer_major.zarr"));
    assert!(!r.ok(), "newer-major must error");
    assert!(
        r.errors.iter().any(|e| e.contains("cal_schema_version") && e.contains("2.0")),
        "expected newer-major error, got: {:?}",
        r.errors
    );
}

#[test]
fn version_l0_integer_newer_major_errors() {
    let f = emit_fixtures();
    let r = zss::check(&f.join("ver_l0_newer_major.zarr"));
    assert!(!r.ok(), "integer newer-major must error");
    assert!(
        r.errors.iter().any(|e| e.contains("l0_schema_version")),
        "expected l0 error, got: {:?}",
        r.errors
    );
}

#[test]
fn version_below_floor_is_one_version_error() {
    // min_supported names the oldest version whose stores pass the structural
    // check; the fixtures are structurally current, so the version is the only error.
    let f = emit_fixtures();
    for (name, attr) in [
        ("ver_below_floor.zarr", "cal_schema_version"),
        ("ver_l0_below_floor.zarr", "l0_schema_version"),
    ] {
        let r = zss::check(&f.join(name));
        assert_eq!(r.errors.len(), 1, "{name}: expected one error, got: {:?}", r.errors);
        assert!(
            r.errors[0].contains(attr) && r.errors[0].contains("minimum supported"),
            "{name}: expected a below-floor error, got: {:?}",
            r.errors
        );
    }
}
