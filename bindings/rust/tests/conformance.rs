//! Tracer-bullet conformance test (Rust binding).
//!
//! Emits the synthetic stores from the spec via the shared Python generator (the
//! single emitter; the "binding parity" leg), then validates them with the Rust
//! interpreter. The Python binding validates the equivalent stores
//! (conformance/test_tracer.py) — together they are the cross-binding proof that
//! one spec drives one interpretation in both languages.

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

#[test]
fn valid_store_is_green() {
    let fixtures = emit_fixtures();
    let problems = zss::validate(&fixtures.join("valid.zarr"));
    assert!(problems.is_empty(), "expected green, got: {problems:?}");
}

#[test]
fn missing_data_5d_is_rejected() {
    let fixtures = emit_fixtures();
    let problems = zss::validate(&fixtures.join("missing_data_5d.zarr"));
    assert!(
        problems.iter().any(|p| p.contains("source/data_5d")),
        "expected rejection mentioning source/data_5d, got: {problems:?}"
    );
}

// ── version negotiation (Slice 3, #4) — validates the SAME spec-emitted
// version fixtures the Python binding does, proving one interpretation. ──

#[test]
fn version_equal_is_green() {
    let f = emit_fixtures();
    let r = zss::check(&f.join("valid.zarr"));
    assert!(r.ok(), "expected green, got: {:?}", r.errors);
    assert!(r.warnings.is_empty(), "unexpected warnings: {:?}", r.warnings);
}

#[test]
fn version_older_minor_is_green() {
    let f = emit_fixtures();
    let r = zss::check(&f.join("ver_older.zarr"));
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
fn version_below_floor_errors() {
    let f = emit_fixtures();
    let r = zss::check(&f.join("ver_below_floor.zarr"));
    assert!(!r.ok(), "below-min_supported must error");
    assert!(
        r.errors.iter().any(|e| e.contains("cal_schema_version")),
        "expected below-floor error, got: {:?}",
        r.errors
    );
}
