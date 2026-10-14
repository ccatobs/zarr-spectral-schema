//! Validators on object storage (Rust binding) — #20.
//!
//! The stores sit on an in-process S3 endpoint (moto, `conformance/s3_server.py`,
//! the same helper the Python tests use). The validator takes an `s3://` URL,
//! reads only `zarr.json` objects, and gives the verdict it gives for the same
//! store on a local disk, and the verdict the Python validator gives for the
//! same `s3://` URL.

use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::{Mutex, OnceLock};

use serde_json::Value;
use zarr_spectral_schema as zss;

/// Generated stores (from `generate.py main()`) and real stores served on S3.
const GENERATED: &[&str] = &[
    "l1_single.zarr",
    "miss_array.zarr",
    "l0_sharded.zarr",
    "l1_l0_store_abs_path.zarr",
];
const REAL: &[&str] = &["calibrate/ORION_KL", "zarr/SOFIA_UPGREAT_ORION_KL.zarr"];

struct S3 {
    _child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    /// store name on S3 → local path
    stores: Vec<(String, PathBuf)>,
}

impl S3 {
    /// The requests served since the previous call, and the offending ones.
    fn requests(&mut self) -> Value {
        writeln!(self.stdin, "requests").unwrap();
        let mut line = String::new();
        self.stdout.read_line(&mut line).unwrap();
        serde_json::from_str(&line).unwrap()
    }
}

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")
}

/// Emit the fixtures, start the S3 helper once, point the AWS environment at it.
/// Tests hold the lock for their whole body, so the request log is theirs alone.
fn s3() -> &'static Mutex<S3> {
    static S3: OnceLock<Mutex<S3>> = OnceLock::new();
    S3.get_or_init(|| {
        let out = std::env::temp_dir().join(format!("zss_rust_s3_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&out);
        let status = Command::new("python3")
            .arg(root().join("conformance/generate.py"))
            .arg(&out)
            .status()
            .expect("run conformance/generate.py (needs python3 + zarr/numpy)");
        assert!(status.success(), "generator failed");
        let real = root().join("conformance/real_stores");
        let stores: Vec<(String, PathBuf)> = GENERATED
            .iter()
            .map(|n| out.join(n))
            .chain(REAL.iter().map(|n| real.join(n)))
            .map(|p| (p.file_name().unwrap().to_string_lossy().into_owned(), p))
            .collect();
        let mut child = Command::new("python3")
            .arg(root().join("conformance/s3_server.py"))
            .args(stores.iter().map(|(_, p)| p))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .expect("run conformance/s3_server.py (needs python3 + moto[server])");
        let stdin = child.stdin.take().unwrap();
        let mut stdout = BufReader::new(child.stdout.take().unwrap());
        let mut line = String::new();
        stdout.read_line(&mut line).unwrap();
        let hello: Value = serde_json::from_str(&line).expect("s3_server.py hello line");
        for (k, v) in hello["env"].as_object().unwrap() {
            std::env::set_var(k, v.as_str().unwrap());
        }
        Mutex::new(S3 {
            _child: child,
            stdin,
            stdout,
            stores,
        })
    })
}

fn url(name: &str) -> String {
    format!("s3://zss/{name}")
}

/// The Python validator's errors for each URL, run in a subprocess.
fn python_errors(urls: &[String]) -> Vec<Vec<String>> {
    let out = Command::new("python3")
        .arg("-c")
        .arg(
            "import json, sys\n\
             import zarr_spectral_schema as zss\n\
             print(json.dumps([zss.check(u).errors for u in sys.argv[1:]]))",
        )
        .args(urls)
        .env("PYTHONPATH", root().join("bindings/python/src"))
        .output()
        .expect("run the Python validator");
    assert!(
        out.status.success(),
        "Python validator failed: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    serde_json::from_slice(&out.stdout).unwrap()
}

#[test]
fn s3_verdict_matches_the_local_and_the_python_verdict() {
    let s3 = s3().lock().unwrap();
    let urls: Vec<String> = s3.stores.iter().map(|(n, _)| url(n)).collect();
    let python = python_errors(&urls);
    for ((name, local), py) in s3.stores.iter().zip(python) {
        let remote = zss::check(Path::new(&url(name)));
        assert_eq!(
            remote.errors,
            zss::check(local).errors,
            "{name}: s3 vs local"
        );
        assert_eq!(remote.errors, py, "{name}: Rust vs Python");
        let green = !matches!(
            name.as_str(),
            "miss_array.zarr" | "l1_l0_store_abs_path.zarr"
        );
        assert_eq!(remote.ok(), green, "{name}: {:?}", remote.errors);
    }
}

#[test]
fn validator_reads_only_zarr_json_objects() {
    let mut s3 = s3().lock().unwrap();
    s3.requests();
    for name in ["ORION_KL", "SOFIA_UPGREAT_ORION_KL.zarr", "l0_sharded.zarr"] {
        let r = zss::check(Path::new(&url(name)));
        assert!(r.ok(), "{name}: {:?}", r.errors);
    }
    let log = s3.requests();
    let served = log["served"].as_array().unwrap();
    assert!(
        served
            .iter()
            .any(|r| r[1].as_str().unwrap().ends_with("/zarr.json")),
        "no zarr.json read: {served:?}"
    );
    assert_eq!(log["offending"], Value::Array(vec![]), "{log}");
}
