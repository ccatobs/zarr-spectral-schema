//! The generic-interpreter validator (Rust binding).
//!
//! Mirrors `bindings/python/.../_validator.py`: it walks the declarative spec
//! and checks a Zarr v3 store against it, special-casing no field. The spec is
//! parsed at runtime from the same synced spec the consts are generated from,
//! so consts and validator cannot diverge.
//!
//! Two checks: structure (presence + dtype/ndim of declared required arrays) and
//! version negotiation (Slice 3, #4) — each producer-owned on-disk version is
//! range-checked against the spec's supported window (`min_supported` .. `value`):
//! below the floor or a newer *major* is an error; a newer *minor* is a single
//! warning (additive, validation still passes); older-or-equal within the window
//! passes silently. `validate` returns errors only (back-compat); `check` returns
//! the full [`ValidationResult`] with warnings.

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
    value: String,
    min_supported: String,
    // `level` is carried by the contract but unused in the checks; kept so the
    // struct round-trips the full DSL entry.
    #[allow(dead_code)]
    level: String,
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

/// Outcome of validating a store: hard `errors` and advisory `warnings`.
#[derive(Debug, Default)]
pub struct ValidationResult {
    pub errors: Vec<String>,
    pub warnings: Vec<String>,
}

impl ValidationResult {
    /// True when the store is conformant — warnings do not make it fail.
    pub fn ok(&self) -> bool {
        self.errors.is_empty()
    }
}

fn load_spec() -> Spec {
    // The synced copy that build.rs also reads; include_str! is relative to this file.
    serde_yaml::from_str(include_str!("../spec/schema.yaml")).expect("parse synced spec/schema.yaml")
}

/// Validate a Zarr v3 store against the spec. Returns errors; empty = conformant.
///
/// Back-compat surface. Use [`check`] to also surface newer-minor warnings.
pub fn validate(store: &Path) -> Vec<String> {
    check(store).errors
}

/// Validate a Zarr v3 store against the spec, returning errors and warnings.
pub fn check(store: &Path) -> ValidationResult {
    let spec = load_spec();
    let mut result = ValidationResult::default();

    let root = match read_json(&store.join("zarr.json")) {
        Some(v) if v.get("node_type").and_then(|n| n.as_str()) == Some("group") => v,
        _ => {
            result.errors.push(format!(
                "{}: not a Zarr v3 group (missing/!group root zarr.json)",
                store.display()
            ));
            return result;
        }
    };
    let empty = serde_json::Map::new();
    let root_attrs = root
        .get("attributes")
        .and_then(|a| a.as_object())
        .unwrap_or(&empty);

    // Version attributes (store-and-forward identity lives at root in v1):
    // presence, on-disk encoding, then range negotiation.
    for (attr_name, vspec) in &spec.versions {
        let Some(value) = root_attrs.get(attr_name) else {
            result
                .errors
                .push(format!("root attribute '{attr_name}' is required but missing"));
            continue;
        };
        let disk = match vspec.on_disk.as_str() {
            "integer" => match value.as_i64() {
                Some(i) => i.to_string(),
                None => {
                    result.errors.push(format!(
                        "root attribute '{attr_name}' must be an integer on disk, got {value}"
                    ));
                    continue;
                }
            },
            "string" => match value.as_str() {
                Some(s) => s.to_string(),
                None => {
                    result.errors.push(format!(
                        "root attribute '{attr_name}' must be a string on disk, got {value}"
                    ));
                    continue;
                }
            },
            _ => value.to_string(),
        };
        negotiate(attr_name, vspec, &disk, &mut result);
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
                    result
                        .errors
                        .push(format!("[{level_name}] required array '{rel}' is missing"));
                    continue;
                };
                let dtype = meta.get("data_type").and_then(|d| d.as_str()).unwrap_or("");
                let ndim = meta
                    .get("shape")
                    .and_then(|s| s.as_array())
                    .map_or(0, |a| a.len());
                if dtype != aspec.dtype {
                    result.errors.push(format!(
                        "[{level_name}] array '{rel}' dtype {dtype:?} != expected {:?}",
                        aspec.dtype
                    ));
                }
                if ndim != aspec.ndim {
                    result.errors.push(format!(
                        "[{level_name}] array '{rel}' ndim {ndim} != expected {}",
                        aspec.ndim
                    ));
                }
            }
        }
    }
    result
}

/// Range-negotiate one on-disk version against the spec's supported window. The
/// two axes are major (breaking → error) and minor (additive → warn); an integer
/// version (no minor) only exercises major.
fn negotiate(attr_name: &str, vspec: &VersionSpec, disk: &str, result: &mut ValidationResult) {
    let Some(store) = parse_version(disk) else {
        result
            .errors
            .push(format!("root attribute '{attr_name}': cannot parse version {disk:?}"));
        return;
    };
    let (Some(current), Some(floor)) =
        (parse_version(&vspec.value), parse_version(&vspec.min_supported))
    else {
        // The meta-schema guarantees these parse; defensive.
        result
            .errors
            .push(format!("root attribute '{attr_name}': spec version range is malformed"));
        return;
    };

    if store < floor {
        result.errors.push(format!(
            "root attribute '{attr_name}': store version {disk} is older than the minimum supported ({})",
            vspec.min_supported
        ));
    } else if store.0 > current.0 {
        result.errors.push(format!(
            "root attribute '{attr_name}': store version {disk} is a newer major than this build supports ({}); refusing to validate",
            vspec.value
        ));
    } else if store.0 == current.0 && store.1 > current.1 {
        result.warnings.push(format!(
            "root attribute '{attr_name}': store version {disk} is a newer minor than this build knows ({}); additive new fields are ignored",
            vspec.value
        ));
    }
}

/// Parse `major[.minor]` (or a bare integer) into a `(major, minor)` tuple.
fn parse_version(s: &str) -> Option<(u64, u64)> {
    let mut parts = s.trim().split('.');
    let major = parts.next()?.parse::<u64>().ok()?;
    let minor = match parts.next() {
        Some(m) => m.parse::<u64>().ok()?,
        None => 0,
    };
    Some((major, minor))
}

fn read_json(path: &Path) -> Option<serde_json::Value> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}
