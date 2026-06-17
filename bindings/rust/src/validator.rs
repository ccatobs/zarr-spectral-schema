//! The generic-interpreter validator (Rust binding).
//!
//! Mirrors `bindings/python/.../_validator.py`: it walks the declarative spec
//! and checks a Zarr v3 store against it, special-casing no field. The spec is
//! parsed at runtime from the same synced spec the consts are generated from,
//! so consts and validator cannot diverge.
//!
//! Slice 1 (tracer) scope: presence + on-disk encoding of declared versions,
//! and presence + dtype/ndim of declared required arrays. The `>= min_supported`
//! negotiation (warn-minor / error-major) lands in Slice 3 (#4).

use std::collections::BTreeMap;
use std::path::Path;

use serde::Deserialize;

#[derive(Deserialize)]
struct Spec {
    versions: BTreeMap<String, VersionSpec>,
    levels: BTreeMap<String, Level>,
}

#[derive(Deserialize)]
struct VersionSpec {
    on_disk: String,
    // `level`, `value`, `min_supported` are carried by the contract but unused in
    // the tracer's checks; kept so the struct round-trips the full DSL entry.
    #[allow(dead_code)]
    level: String,
    #[allow(dead_code)]
    value: String,
    #[allow(dead_code)]
    min_supported: String,
}

#[derive(Deserialize)]
struct Level {
    groups: BTreeMap<String, Group>,
}

#[derive(Deserialize)]
struct Group {
    arrays: BTreeMap<String, ArraySpec>,
}

#[derive(Deserialize)]
struct ArraySpec {
    dtype: String,
    ndim: usize,
    required: bool,
}

fn load_spec() -> Spec {
    // The synced copy that build.rs also reads; include_str! is relative to this file.
    serde_yaml::from_str(include_str!("../spec/schema.yaml")).expect("parse synced spec/schema.yaml")
}

/// Validate a Zarr v3 store against the spec. Returns problems; empty = conformant.
pub fn validate(store: &Path) -> Vec<String> {
    let spec = load_spec();
    let mut problems = Vec::new();

    let root = match read_json(&store.join("zarr.json")) {
        Some(v) if v.get("node_type").and_then(|n| n.as_str()) == Some("group") => v,
        _ => {
            problems.push(format!(
                "{}: not a Zarr v3 group (missing/!group root zarr.json)",
                store.display()
            ));
            return problems;
        }
    };
    let empty = serde_json::Map::new();
    let root_attrs = root
        .get("attributes")
        .and_then(|a| a.as_object())
        .unwrap_or(&empty);

    // Version attributes (store-and-forward identity lives at root in v1).
    for (attr_name, vspec) in &spec.versions {
        match root_attrs.get(attr_name) {
            None => problems.push(format!("root attribute '{attr_name}' is required but missing")),
            Some(value) => match vspec.on_disk.as_str() {
                "integer" if !(value.is_i64() || value.is_u64()) => problems.push(format!(
                    "root attribute '{attr_name}' must be an integer on disk, got {value}"
                )),
                "string" if !value.is_string() => problems.push(format!(
                    "root attribute '{attr_name}' must be a string on disk, got {value}"
                )),
                _ => {}
            },
        }
    }

    // Structural arrays: group '<g>' / array '<g>/<a>'.
    for (level_name, level) in &spec.levels {
        for (group_name, group) in &level.groups {
            for (array_name, aspec) in &group.arrays {
                if !aspec.required {
                    continue;
                }
                let rel = format!("{group_name}/{array_name}");
                let meta = read_json(&store.join(group_name).join(array_name).join("zarr.json"));
                let Some(meta) = meta else {
                    problems.push(format!("[{level_name}] required array '{rel}' is missing"));
                    continue;
                };
                let dtype = meta.get("data_type").and_then(|d| d.as_str()).unwrap_or("");
                let ndim = meta
                    .get("shape")
                    .and_then(|s| s.as_array())
                    .map_or(0, |a| a.len());
                if dtype != aspec.dtype {
                    problems.push(format!(
                        "[{level_name}] array '{rel}' dtype {dtype:?} != expected {:?}",
                        aspec.dtype
                    ));
                }
                if ndim != aspec.ndim {
                    problems.push(format!(
                        "[{level_name}] array '{rel}' ndim {ndim} != expected {}",
                        aspec.ndim
                    ));
                }
            }
        }
    }
    problems
}

fn read_json(path: &Path) -> Option<serde_json::Value> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}
