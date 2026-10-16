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

mod common;

use common::{fixtures, python_errors, repo_root};
use serde_json::Value;
use zarr_spectral_schema as zss;

/// Generated stores (from `generate.py main()`) and real stores served on S3.
const GENERATED: &[&str] = &[
    "l1_single.zarr",
    "miss_array.zarr",
    "l0_sharded.zarr",
    "l1_l0_store_abs_path.zarr",
];
/// Three errors when readable (skydiff/spectrum_0 dtype, ndim and
/// dimension_names); served for the deny cases only.
const BAD_SKYDIFF: &str = "l1_bad_skydiff.zarr";
/// A store under a key with a space, read through its `%20` URL.
const SPACED: &str = "my store.zarr";
const REAL: &[&str] = &["calibrate/ORION_KL", "zarr/SOFIA_UPGREAT_ORION_KL.zarr"];
/// Real stores that are not green until #37: the real stores predate #34 (L0 arrays, per-band identity, image_gain); ccatobs/zarr-spectral-schema#37 writes them again.
const PENDING_37: &[&str] = &["ORION_KL", "SOFIA_UPGREAT_ORION_KL.zarr"];

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

impl S3 {
    /// Start the S3 helper over the generated and the real stores, and point
    /// the AWS environment at it.
    fn start() -> Self {
        let out = fixtures();
        let real = repo_root().join("conformance/real_stores");
        let mut stores: Vec<(String, PathBuf)> = GENERATED
            .iter()
            .map(|n| out.join(n))
            .chain(REAL.iter().map(|n| real.join(n)))
            .map(|p| (p.file_name().unwrap().to_string_lossy().into_owned(), p))
            .collect();
        stores.push((SPACED.to_string(), out.join("l1_single.zarr")));
        let mut child = Command::new("python3")
            .arg(repo_root().join("conformance/s3_server.py"))
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
        // Safe: this binary holds one test, so no other thread reads the
        // environment while it changes.
        for (k, v) in hello["env"].as_object().unwrap() {
            std::env::set_var(k, v.as_str().unwrap());
        }
        // A variable that is not UTF-8 must not stop the validator.
        std::env::set_var(
            "ZSS_NOT_UTF8",
            <std::ffi::OsStr as std::os::unix::ffi::OsStrExt>::from_bytes(b"\xff"),
        );
        S3 {
            _child: child,
            stdin,
            stdout,
            stores,
        }
    }
}

/// The only test of this binary: it sets the AWS environment, which is safe
/// only while no other test thread runs. Each step is one former test.
#[test]
fn validators_on_object_storage() {
    let mut s3 = S3::start();
    s3_verdict_matches_the_local_and_the_python_verdict(&mut s3);
    validator_reads_only_zarr_json_objects(&mut s3);
    unreadable_node_is_an_error_in_both_bindings(&mut s3);
    check_inside_a_tokio_runtime_returns_a_result();
    check_url_takes_a_url_string();
}

/// #33: a caller inside a tokio runtime gets a result, not a panic.
fn check_inside_a_tokio_runtime_returns_a_result() {
    let rt = tokio::runtime::Builder::new_current_thread()
        .build()
        .unwrap();
    let r = rt.block_on(async { zss::check(Path::new(&url("l1_single.zarr"))) });
    assert_eq!(r.errors, Vec::<String>::new());
}

/// #33: `check_url` takes the URL as a string and gives the verdict of `check`.
fn check_url_takes_a_url_string() {
    let u = url("miss_array.zarr");
    assert_eq!(zss::check_url(&u).errors, zss::check(Path::new(&u)).errors);
    assert_eq!(
        zss::check_url(&u).errors,
        ["[L1] required array 'scan_000001/spectra' is missing"]
    );
}

fn url(name: &str) -> String {
    format!("s3://zss/{}", name.replace(' ', "%20"))
}

fn s3_verdict_matches_the_local_and_the_python_verdict(s3: &mut S3) {
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
        // The committed real stores (L0 3, L1 2.0) are green.
        let green = !matches!(
            name.as_str(),
            "miss_array.zarr" | "l1_l0_store_abs_path.zarr"
        );
        if !PENDING_37.contains(&name.as_str()) {
            assert_eq!(remote.ok(), green, "{name}: {:?}", remote.errors);
        }
    }
}

fn validator_reads_only_zarr_json_objects(s3: &mut S3) {
    s3.requests();
    for name in ["ORION_KL", "SOFIA_UPGREAT_ORION_KL.zarr", "l0_sharded.zarr"] {
        // Every node read, and every store green.
        let r = zss::check(Path::new(&url(name)));
        if !PENDING_37.contains(&name) {
            assert_eq!(r.errors, Vec::<String>::new(), "{name}");
        }
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
        // The node exists: no missing-node error beside the read error (#33).
        &["[L1] 'scan_000001/t_int/zarr.json': cannot read metadata"],
    ),
    ("l1_single.zarr", "l1_single.zarr/", &["'/': cannot list"]),
    (
        "l1_single.zarr",
        "l1_single.zarr/zarr.json",
        &["'zarr.json': cannot read metadata"],
    ),
];

fn unreadable_node_is_an_error_in_both_bindings(s3: &mut S3) {
    for (name, deny, expected) in DENY_CASES {
        s3.send(&format!("deny {deny}"));
        let rust = zss::check(Path::new(&url(name))).errors;
        let python = python_errors(&[url(name)]).remove(0);
        s3.send("allow");
        assert_eq!(rust, *expected, "{deny}: Rust");
        assert_eq!(python, *expected, "{deny}: Python");
    }
}
