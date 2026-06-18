//! The generic-interpreter validator (Rust binding).
//!
//! Mirrors `bindings/python/.../_validator.py` exactly: it walks the declarative
//! spec and checks a Zarr v3 store against it, special-casing no field. The spec
//! is parsed at runtime from the same synced spec the consts are generated from,
//! so consts and validator cannot diverge, and the two bindings implement one
//! interpretation that cannot drift.
//!
//! Three checks:
//!
//! * **Level detection** (Slice 4, #5) — a store is L0 *or* L1, detected by which
//!   per-level `detect` root attr it carries (`l0_schema_version` ⇒ L0,
//!   `cal_schema_version` ⇒ L1). A store declaring no known level is an error.
//! * **Structure** — the full L0+L1 contract, interpreted from the spec's
//!   `placement`/`inline` group fields and the per-level `*_attrs` sections: root
//!   attrs, per-scan attrs, per-band attrs (multi-frontend), the scan-or-band
//!   arrays/attrs, the L0 `source`/`calibration` sub-groups, and the diagnostic
//!   sub-groups — all resolved by the same generic walk, never hand-branched.
//! * **Version negotiation** (Slice 3, #4) — each producer-owned on-disk version
//!   is range-checked against the spec's supported window (`min_supported` ..
//!   `value`): below the floor or a newer *major* is an error; a newer *minor* is
//!   a single warning (additive, validation still passes); older-or-equal within
//!   the window passes silently. Only versions whose `level` is detected are
//!   negotiated.
//!
//! `validate` returns errors only (back-compat); `check` returns the full
//! [`ValidationResult`] with warnings.

use std::collections::BTreeMap;
use std::path::Path;

use serde::Deserialize;
use serde_json::{Map, Value};

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
    level: String,
}

#[derive(Deserialize)]
struct Level {
    /// Root attribute whose presence marks a store as this level.
    #[serde(default)]
    detect: Option<String>,
    #[serde(default)]
    root_attrs: BTreeMap<String, AttrSpec>,
    #[serde(default)]
    scan_attrs: BTreeMap<String, AttrSpec>,
    #[serde(default)]
    band_attrs: BTreeMap<String, AttrSpec>,
    #[serde(default)]
    scan_or_band_attrs: BTreeMap<String, AttrSpec>,
    #[serde(default)]
    groups: BTreeMap<String, Group>,
}

#[derive(Deserialize)]
struct AttrSpec {
    r#type: String,
    required: bool,
}

#[derive(Deserialize)]
struct Group {
    /// `scan` | `scan_or_band` — where this group's arrays live relative to a scan.
    #[serde(default)]
    placement: Option<String>,
    /// true ⇒ arrays sit directly in the target dir; false ⇒ under `<target>/<name>/`.
    #[serde(default)]
    inline: bool,
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
    let empty = Map::new();
    let root_attrs = root
        .get("attributes")
        .and_then(|a| a.as_object())
        .unwrap_or(&empty);

    // ── level detection ────────────────────────────────────────────────────
    // A store is exactly one level, marked by which `detect` root attr it carries.
    let applicable: Vec<(&String, &Level)> = spec
        .levels
        .iter()
        .filter(|(_, lvl)| {
            lvl.detect
                .as_deref()
                .is_some_and(|d| root_attrs.contains_key(d))
        })
        .collect();

    if applicable.is_empty() {
        let mut detects: Vec<&str> = spec
            .levels
            .values()
            .filter_map(|l| l.detect.as_deref())
            .collect();
        detects.sort_unstable();
        result.errors.push(format!(
            "store declares no known level: none of the detect attributes {detects:?} \
             are present in root attributes"
        ));
        return result;
    }

    // Negotiate only the versions belonging to a detected level: an L0 store
    // carries l0_schema_version, an L1 store cal_schema_version — checking the
    // absent one would spuriously fail every single-level store.
    let applicable_levels: Vec<&str> = applicable.iter().map(|(name, _)| name.as_str()).collect();
    negotiate_versions(&spec, root_attrs, &applicable_levels, &mut result);

    for (level_name, level) in &applicable {
        check_level(store, level_name, level, root_attrs, &mut result);
    }

    result
}

/// Walk one applicable level's structural contract against the store.
fn check_level(
    store: &Path,
    level_name: &str,
    level: &Level,
    root_attrs: &Map<String, Value>,
    result: &mut ValidationResult,
) {
    // Root attrs declared for this level.
    check_attrs(root_attrs, &level.root_attrs, "root", level_name, result);

    let needs_scans = !level.scan_attrs.is_empty()
        || !level.band_attrs.is_empty()
        || !level.scan_or_band_attrs.is_empty()
        || !level.groups.is_empty();

    let scan_groups = child_groups(store, is_scan);
    if needs_scans && scan_groups.is_empty() {
        result.errors.push(format!(
            "[{level_name}] no scan_<NNNNNN> groups found at the store root"
        ));
        return;
    }

    for sg in &scan_groups {
        let scan_dir = store.join(sg);
        let bands = child_groups(&scan_dir, is_band);
        let multi = !bands.is_empty();

        // Per-scan-group attrs always checked at the scan group itself.
        let scan_attrs = group_attrs(&scan_dir);
        check_attrs(&scan_attrs, &level.scan_attrs, sg, level_name, result);

        // Multi-frontend: per-band attrs on each band group.
        if multi {
            for b in &bands {
                let band_attrs = group_attrs(&scan_dir.join(b));
                let where_ = format!("{sg}/{b}");
                check_attrs(&band_attrs, &level.band_attrs, &where_, level_name, result);
            }
        }

        // scan_or_band resolves to the scan group (single) or each band (multi).
        // Targets carry the relative path (for messages) and the on-disk dir.
        let targets: Vec<(String, std::path::PathBuf)> = if multi {
            bands
                .iter()
                .map(|b| (format!("{sg}/{b}"), scan_dir.join(b)))
                .collect()
        } else {
            vec![(sg.clone(), scan_dir.clone())]
        };

        for (trel, tdir) in &targets {
            let target_attrs = group_attrs(tdir);
            check_attrs(
                &target_attrs,
                &level.scan_or_band_attrs,
                trel,
                level_name,
                result,
            );
            for (gname, group) in &level.groups {
                if group.placement.as_deref() == Some("scan_or_band") {
                    check_group_arrays(tdir, trel, gname, group, level_name, result);
                }
            }
        }

        // `placement: scan` groups (L0 source/calibration) live under the scan
        // group regardless of multi-frontend.
        for (gname, group) in &level.groups {
            if group.placement.as_deref() == Some("scan") {
                check_group_arrays(&scan_dir, sg, gname, group, level_name, result);
            }
        }
    }
}

/// Check one group's arrays under `base_dir` (store-relative path `base_rel`).
///
/// `inline` groups place arrays directly in `base`; otherwise they sit under
/// `base/<group_name>/`. Required arrays must exist (dtype + ndim checked);
/// optional arrays are checked only when present.
fn check_group_arrays(
    base_dir: &Path,
    base_rel: &str,
    group_name: &str,
    group: &Group,
    level_name: &str,
    result: &mut ValidationResult,
) {
    let (prefix_dir, prefix_rel) = if group.inline {
        (base_dir.to_path_buf(), base_rel.to_string())
    } else {
        (base_dir.join(group_name), format!("{base_rel}/{group_name}"))
    };
    for (array_name, aspec) in &group.arrays {
        let rel = format!("{prefix_rel}/{array_name}");
        let meta = read_json(&prefix_dir.join(array_name).join("zarr.json"));
        let Some(meta) = meta else {
            if aspec.required {
                result
                    .errors
                    .push(format!("[{level_name}] required array '{rel}' is missing"));
            }
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

/// Check declared attrs at one placement location (`where_` is the store path
/// used in messages). Required attrs must be present and (if present)
/// type-checked; optional attrs are type-checked only when present.
fn check_attrs(
    attrs: &Map<String, Value>,
    declared: &BTreeMap<String, AttrSpec>,
    where_: &str,
    level_name: &str,
    result: &mut ValidationResult,
) {
    for (name, aspec) in declared {
        let Some(value) = attrs.get(name) else {
            if aspec.required {
                result.errors.push(format!(
                    "[{level_name}] required attribute '{name}' is missing at '{where_}'"
                ));
            }
            continue;
        };
        if !type_matches(&aspec.r#type, value) {
            result.errors.push(format!(
                "[{level_name}] attribute '{name}' at '{where_}' must be of type \
                 {:?}, got {value}",
                aspec.r#type
            ));
        }
    }
}

/// True when a JSON value matches a spec attr `type`.
///
/// `number` admits integers; `integer` excludes booleans (JSON `true`/`false`
/// are not integers); `number` likewise excludes booleans. Mirrors the Python
/// `_TYPE_CHECKS`.
fn type_matches(expected: &str, value: &Value) -> bool {
    match expected {
        "string" => value.is_string(),
        "integer" => value.is_i64() || value.is_u64(),
        "number" => value.is_number(),
        "boolean" => value.is_boolean(),
        "array" => value.is_array(),
        "object" => value.is_object(),
        _ => true,
    }
}

/// Presence/encoding check + two-axis range negotiation for on-disk versions.
///
/// Only versions whose `level` is in `levels` (the detected level(s)) are
/// negotiated, so a single-level store is not penalised for lacking the other
/// level's version attr.
fn negotiate_versions(
    spec: &Spec,
    root_attrs: &Map<String, Value>,
    levels: &[&str],
    result: &mut ValidationResult,
) {
    for (attr_name, vspec) in &spec.versions {
        if !levels.contains(&vspec.level.as_str()) {
            continue;
        }
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
        negotiate(attr_name, vspec, &disk, result);
    }
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

/// Names of immediate child groups of `dir` whose name passes `pred`, sorted.
///
/// A child is a "group" iff it holds a `zarr.json` with `node_type: group`; we
/// only enumerate by directory name and let the attr read confirm the node type
/// where it matters. Sorted to match the Python reader's `sorted(...)` order.
fn child_groups(dir: &Path, pred: impl Fn(&str) -> bool) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let Ok(entries) = std::fs::read_dir(dir) else {
        return out;
    };
    for entry in entries.flatten() {
        if !entry.file_type().map(|t| t.is_dir()).unwrap_or(false) {
            continue;
        }
        let name = entry.file_name().to_string_lossy().into_owned();
        if pred(&name) && entry.path().join("zarr.json").is_file() {
            out.push(name);
        }
    }
    out.sort();
    out
}

/// Read a group node's `attributes` map (empty if absent / not a group).
fn group_attrs(dir: &Path) -> Map<String, Value> {
    read_json(&dir.join("zarr.json"))
        .and_then(|v| v.get("attributes").and_then(|a| a.as_object()).cloned())
        .unwrap_or_default()
}

/// `scan_<6 digits>` (writer pads to 6).
fn is_scan(name: &str) -> bool {
    name.len() == 11
        && name.starts_with("scan_")
        && name[5..].bytes().all(|b| b.is_ascii_digit())
}

/// `band_<digits>` (writer: `band_{index}`, base-0, no zero-pad).
fn is_band(name: &str) -> bool {
    name.len() > 5
        && name.starts_with("band_")
        && name[5..].bytes().all(|b| b.is_ascii_digit())
}

fn read_json(path: &Path) -> Option<Value> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}
