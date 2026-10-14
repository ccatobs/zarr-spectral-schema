//! Validators on object storage (Rust binding) — #20.
//!
//! The stores sit on an in-process S3 endpoint (moto, `conformance/s3_server.py`,
//! the same helper the Python tests use). The validator takes an `s3://` URL,
//! reads only `zarr.json` objects, and gives the verdict it gives for the same
//! store on a local disk, and the verdict the Python validator gives for the
//! same `s3://` URL. Needs the `s3` feature: `cargo test --features s3`.
#![cfg(feature = "s3")]

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
/// Two errors when readable; served for the deny cases only (its dtype message
/// quotes the dtype differently in the two bindings, a finding older than #20).
const BAD_SKYDIFF: &str = "l1_bad_skydiff.zarr";
/// A store under a key with a space, read through its `%20` URL.
const SPACED: &str = "my store.zarr";
const REAL: &[&str] = &["calibrate/ORION_KL", "zarr/SOFIA_UPGREAT_ORION_KL.zarr"];

struct S3 {
    _child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    /// store name on S3 → local path
    stores: Vec<(String, PathBuf)>,
}

impl S3 {
    /// Send one command line to the helper; return its one-line JSON answer.
    fn send(&mut self, command: &str) -> Value {
        writeln!(self.stdin, "{command}").unwrap();
        let mut line = String::new();
        self.stdout.read_line(&mut line).unwrap();
        serde_json::from_str(&line).unwrap()
    }

    /// The requests served since the previous call, and the offending ones.
    fn requests(&mut self) -> Value {
        self.send("requests")
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
        let mut stores: Vec<(String, PathBuf)> = GENERATED
            .iter()
            .map(|n| out.join(n))
            .chain(REAL.iter().map(|n| real.join(n)))
            .map(|p| (p.file_name().unwrap().to_string_lossy().into_owned(), p))
            .collect();
        stores.push((SPACED.to_string(), out.join("l1_single.zarr")));
        let mut child = Command::new("python3")
            .arg(root().join("conformance/s3_server.py"))
            .args(stores.iter().map(|(n, p)| format!("{n}={}", p.display())))
            .arg(out.join(BAD_SKYDIFF))
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
        // A variable that is not UTF-8 must not stop the validator.
        std::env::set_var(
            "ZSS_NOT_UTF8",
            <std::ffi::OsStr as std::os::unix::ffi::OsStrExt>::from_bytes(b"\xff"),
        );
        Mutex::new(S3 {
            _child: child,
            stdin,
            stdout,
            stores,
        })
    })
}

fn url(name: &str) -> String {
    format!("s3://zss/{}", name.replace(' ', "%20"))
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
        // The committed real stores (L0 2, L1 1.3) sit below min_supported since #19.
        let green = !matches!(
            name.as_str(),
            "miss_array.zarr"
                | "l1_l0_store_abs_path.zarr"
                | "ORION_KL"
                | "SOFIA_UPGREAT_ORION_KL.zarr"
        );
        assert_eq!(remote.ok(), green, "{name}: {:?}", remote.errors);
    }
}

#[test]
fn validator_reads_only_zarr_json_objects() {
    let mut s3 = s3().lock().unwrap();
    s3.requests();
    for name in ["ORION_KL", "SOFIA_UPGREAT_ORION_KL.zarr", "l0_sharded.zarr"] {
        // Every node read: no listing or metadata error (the real stores carry
        // the below-floor version error since #19).
        let r = zss::check(Path::new(&url(name)));
        assert!(
            !r.errors.iter().any(|e| e.contains("cannot")),
            "{name}: {:?}",
            r.errors
        );
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

/// (store, object key or listing prefix answered with 403, expected errors);
/// the same cases as `DENY_CASES` in conformance/test_object_storage.py.
const DENY_CASES: &[(&str, &str, &[&str])] = &[
    (
        "l1_bad_skydiff.zarr",
        "l1_bad_skydiff.zarr/scan_000001/skydiff/",
        &["[L1] 'scan_000001/skydiff/': cannot list"],
    ),
    (
        "l1_single.zarr",
        "l1_single.zarr/scan_000001/t_int/zarr.json",
        &[
            "[L1] 'scan_000001/t_int/zarr.json': cannot read metadata",
            "[L1] required array 'scan_000001/t_int' is missing",
        ],
    ),
    ("l1_single.zarr", "l1_single.zarr/", &["'/': cannot list"]),
    (
        "l1_single.zarr",
        "l1_single.zarr/zarr.json",
        &["'zarr.json': cannot read metadata"],
    ),
];

#[test]
fn unreadable_node_is_an_error_in_both_bindings() {
    let mut s3 = s3().lock().unwrap();
    for (name, deny, expected) in DENY_CASES {
        s3.send(&format!("deny {deny}"));
        let rust = zss::check(Path::new(&url(name))).errors;
        let python = python_errors(&[url(name)]).remove(0);
        s3.send("allow");
        assert_eq!(rust, *expected, "{deny}: Rust");
        assert_eq!(python, *expected, "{deny}: Python");
    }
}
