//! Object storage and production volume (Rust binding) — #20.
//!
//! Validates the stores that `conformance/generate.py main()` emits (the
//! `emit_storage_fixtures` set) and checks each verdict. The Python binding
//! runs the same checks over the same stores (conformance/test_storage_contract.py).

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::OnceLock;

use zarr_spectral_schema as zss;

/// Emit the fixtures once per test binary with the shared spec-driven generator.
fn fixtures() -> &'static Path {
    static DIR: OnceLock<PathBuf> = OnceLock::new();
    DIR.get_or_init(|| {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
        let out = std::env::temp_dir().join(format!("zss_rust_storage_{}", std::process::id()));
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

// ── 3. dtype choice ────────────────────────────────────────────────────────

#[test]
fn l1_float32_and_float64_spectra_are_green() {
    for name in ["l1_float32.zarr", "l1_float64.zarr"] {
        let e = errors(name);
        assert!(e.is_empty(), "{name}: expected green, got: {e:?}");
    }
}

#[test]
fn l1_int16_spectra_is_rejected() {
    let e = errors("l1_int16_spectra.zarr");
    assert!(
        e.iter()
            .any(|m| m.contains("scan_000001/spectra") && m.contains("\"int16\"")),
        "expected a dtype error on spectra, got: {e:?}"
    );
}

#[test]
fn l1_integer_keepbits_is_green() {
    let e = errors("l1_keepbits.zarr");
    assert!(e.is_empty(), "expected green, got: {e:?}");
}

#[test]
fn l1_non_integer_keepbits_is_rejected() {
    let e = errors("l1_bad_keepbits.zarr");
    assert!(
        e.iter().any(|m| m.contains("keepbits")
            && m.contains("scan_000001/spectra")
            && m.contains("\"integer\"")),
        "expected a keepbits type error, got: {e:?}"
    );
}

// ── 4. valid dump count ────────────────────────────────────────────────────

#[test]
fn valid_dump_count_dtype_and_dims_are_checked() {
    for (name, rel) in [
        ("l0_bad_valid_dumps.zarr", "scan_000001/source/n_valid_dumps"),
        ("l1_bad_valid_dumps.zarr", "scan_000001/n_valid_dumps"),
    ] {
        let e = errors(name);
        assert!(
            e.iter().any(|m| m.contains(rel) && m.contains("dtype")),
            "{name}: expected a dtype error, got: {e:?}"
        );
        assert!(
            e.iter().any(|m| m.contains(rel) && m.contains("ndim")),
            "{name}: expected an ndim error, got: {e:?}"
        );
    }
}
