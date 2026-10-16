//! Shared helpers of the Rust integration tests.
//!
//! Each test binary runs the spec-driven generator (`conformance/generate.py`)
//! once and writes every file under cargo's scratch dir for integration tests
//! (`target/tmp`), never in the system temp dir.
#![allow(dead_code)] // each test binary uses a subset

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::OnceLock;

/// The repository root.
pub fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

/// An empty dir `<test binary>_<name>` under `target/tmp`.
pub fn scratch(name: &str) -> PathBuf {
    let exe = std::env::current_exe().expect("test binary path");
    let stem = exe.file_stem().expect("test binary name").to_string_lossy();
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!("{stem}_{name}"));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("create scratch dir");
    dir
}

/// The stores of `conformance/generate.py main()`, emitted once per test binary.
pub fn fixtures() -> &'static Path {
    static DIR: OnceLock<PathBuf> = OnceLock::new();
    DIR.get_or_init(|| {
        let out = scratch("fixtures");
        let status = Command::new("python3")
            .arg(repo_root().join("conformance/generate.py"))
            .arg(&out)
            .stdout(std::process::Stdio::null())
            .status()
            .expect("run conformance/generate.py (needs python3 + zarr/numpy)");
        assert!(status.success(), "generator failed");
        out
    })
}

/// The errors `zss::validate` gives for the fixture store `name`.
pub fn errors(name: &str) -> Vec<String> {
    zarr_spectral_schema::validate(&fixtures().join(name))
}
