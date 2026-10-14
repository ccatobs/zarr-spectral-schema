//! The generic-interpreter validator (Rust binding).
//!
//! Mirrors `bindings/python/.../_validator.py` exactly: it walks the declarative
//! spec and checks a Zarr v3 store against it, special-casing no field. The spec
//! is parsed at runtime from the same synced spec the consts are generated from,
//! so consts and validator cannot diverge, and the two bindings implement one
//! interpretation that cannot drift.
//!
//! The store is first read into the same normalized view the Python reader
//! builds (root attrs, group attrs and arrays keyed by store-relative path), so
//! both interpreters walk the same shape.
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
//!   The spec's `one_of` (exactly one of these groups per target), `equals`
//!   (attr value constraint) and `prefix` (array family) fields are interpreted
//!   the same way. Every structure finding starts with `[<level>] `; version and
//!   level-detection errors do not (calibrate splits on this prefix, kalibrate#82).
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
    detect: String,
    /// Groups of which each placement target holds exactly one.
    #[serde(default)]
    one_of: Vec<String>,
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
    /// Value constraint: the attr must hold exactly this value.
    #[serde(default)]
    equals: Option<Value>,
    /// String format constraint (`uri-reference`).
    #[serde(default)]
    format: Option<String>,
}

#[derive(Deserialize)]
struct Group {
    /// `scan` | `scan_or_band` — where this group's arrays live relative to a scan.
    placement: String,
    /// true ⇒ arrays sit directly in the target dir; false ⇒ under `<target>/<name>/`.
    inline: bool,
    arrays: BTreeMap<String, ArraySpec>,
}

#[derive(Deserialize)]
struct ArraySpec {
    dtype: Dtype,
    ndim: usize,
    required: bool,
    /// Attributes of the array itself (e.g. `keepbits`).
    #[serde(default)]
    attrs: BTreeMap<String, AttrSpec>,
    /// Array family: matches every array whose name starts with this prefix.
    #[serde(default)]
    prefix: Option<String>,
}

/// One dtype name, or a set of names the array may take (#20).
#[derive(Deserialize)]
#[serde(untagged)]
enum Dtype {
    One(String),
    Set(Vec<String>),
}

impl Dtype {
    fn allows(&self, dtype: &str) -> bool {
        match self {
            Dtype::One(d) => d == dtype,
            Dtype::Set(set) => set.iter().any(|d| d == dtype),
        }
    }
}

impl std::fmt::Display for Dtype {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Dtype::One(d) => write!(f, "{d:?}"),
            Dtype::Set(set) => write!(f, "{set:?}"),
        }
    }
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

/// The normalized store view (parity with the Python `StoreView`): every node
/// keyed by its store-relative path, e.g. `scan_000001/source/data_5d`.
#[derive(Default)]
struct StoreView {
    root_attrs: Map<String, Value>,
    groups: BTreeMap<String, Map<String, Value>>,
    /// path → (dtype name, ndim, attributes)
    arrays: BTreeMap<String, (String, usize, Map<String, Value>)>,
    /// metadata parse errors of non-root nodes, e.g. `'scan_000001/zarr.json': ...`
    errors: Vec<String>,
}

fn load_spec() -> Spec {
    // The synced copy that build.rs also reads; include_str! is relative to this file.
    serde_yaml::from_str(include_str!("../spec/schema.yaml"))
        .expect("parse synced spec/schema.yaml")
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
    let view = match read_store(store) {
        Ok(view) => view,
        Err(e) => {
            result.errors.push(e);
            return result;
        }
    };

    // ── level detection ────────────────────────────────────────────────────
    // A store is exactly one level, marked by which `detect` root attr it carries.
    let applicable: Vec<(&String, &Level)> = spec
        .levels
        .iter()
        .filter(|(_, lvl)| view.root_attrs.contains_key(&lvl.detect))
        .collect();

    if applicable.is_empty() {
        let mut detects: Vec<&str> = spec.levels.values().map(|l| l.detect.as_str()).collect();
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
    negotiate_versions(&spec, &view.root_attrs, &applicable_levels, &mut result);

    for (level_name, level) in &applicable {
        check_level(&view, level_name, level, &mut result);
    }

    result
}

/// Walk one applicable level's structural contract against the store view.
fn check_level(view: &StoreView, level_name: &str, level: &Level, result: &mut ValidationResult) {
    // Metadata that did not parse is a structure finding of this level.
    result
        .errors
        .extend(view.errors.iter().map(|e| format!("[{level_name}] {e}")));

    // Root attrs declared for this level.
    check_attrs(
        &view.root_attrs,
        &level.root_attrs,
        "root",
        level_name,
        result,
    );

    let needs_scans = !level.scan_attrs.is_empty()
        || !level.band_attrs.is_empty()
        || !level.scan_or_band_attrs.is_empty()
        || !level.groups.is_empty();

    let root_children = view
        .groups
        .keys()
        .chain(view.arrays.keys())
        .filter(|p| !p.contains('/'));
    for name in root_children {
        if name.starts_with("scan_") && !is_scan(name) {
            result.errors.push(format!(
                "[{level_name}] root child '{name}' does not match the scan pattern {SCAN_PATTERN}"
            ));
        }
    }
    let scan_groups: Vec<&String> = view.groups.keys().filter(|p| is_scan(p)).collect();
    if needs_scans && scan_groups.is_empty() {
        result.errors.push(format!(
            "[{level_name}] no scan_<NNNNNN> groups found at the store root"
        ));
        return;
    }

    for sg in scan_groups {
        let bands: Vec<String> = view
            .groups
            .keys()
            .filter(|p| p.strip_prefix(&format!("{sg}/")).is_some_and(is_band))
            .cloned()
            .collect();

        // Per-scan-group attrs always checked at the scan group itself.
        check_attrs(&view.groups[sg], &level.scan_attrs, sg, level_name, result);

        // Multi-frontend: per-band attrs on each band group.
        for b in &bands {
            check_attrs(&view.groups[b], &level.band_attrs, b, level_name, result);
        }

        // `scan` placement resolves to the scan group; `scan_or_band` to the
        // scan group (single-frontend) or each band (multi-frontend).
        let sob = if bands.is_empty() {
            vec![sg.clone()]
        } else {
            bands
        };
        for t in &sob {
            check_attrs(
                &view.groups[t],
                &level.scan_or_band_attrs,
                t,
                level_name,
                result,
            );
        }
        for (placement, targets) in [("scan", vec![sg.clone()]), ("scan_or_band", sob)] {
            let placed: Vec<(&String, &Group)> = level
                .groups
                .iter()
                .filter(|(_, g)| g.placement == placement)
                .collect();
            let one_of: Vec<&String> = level
                .one_of
                .iter()
                .filter(|n| placed.iter().any(|(p, _)| p == n))
                .collect();
            for t in &targets {
                let present: Vec<&String> = one_of
                    .iter()
                    .copied()
                    .filter(|n| view.groups.contains_key(&format!("{t}/{n}")))
                    .collect();
                if !one_of.is_empty() && present.len() != 1 {
                    result.errors.push(format!(
                        "[{level_name}] '{t}' must hold exactly one of {one_of:?}, found {present:?}"
                    ));
                }
                for (gname, group) in &placed {
                    if one_of.contains(gname) && !present.contains(gname) {
                        continue;
                    }
                    check_group_arrays(view, t, gname, group, level_name, result);
                }
            }
        }
    }
}

/// Check one group's arrays under `base`.
///
/// `inline` groups place arrays directly in `base`; otherwise they sit under
/// `base/<group_name>/`. Required arrays must exist (dtype + ndim checked);
/// optional arrays are checked only when present. An array family (`prefix`)
/// checks every array whose name starts with the prefix.
fn check_group_arrays(
    view: &StoreView,
    base: &str,
    group_name: &str,
    group: &Group,
    level_name: &str,
    result: &mut ValidationResult,
) {
    let prefix = if group.inline {
        base.to_string()
    } else {
        format!("{base}/{group_name}")
    };
    for (array_name, aspec) in &group.arrays {
        let Some(family) = &aspec.prefix else {
            check_array(
                view,
                &format!("{prefix}/{array_name}"),
                aspec,
                level_name,
                result,
            );
            continue;
        };
        let start = format!("{prefix}/{family}");
        let mut members: Vec<&String> = view
            .arrays
            .keys()
            .chain(view.groups.keys())
            .filter(|r| r.starts_with(&start) && !r[prefix.len() + 1..].contains('/'))
            .collect();
        members.sort();
        if members.is_empty() && aspec.required {
            result.errors.push(format!(
                "[{level_name}] required array family '{start}*' is missing"
            ));
        }
        for rel in members {
            check_array(view, rel, aspec, level_name, result);
        }
    }
}

/// Check one array path: node type, presence (if required), dtype, ndim, attrs.
fn check_array(
    view: &StoreView,
    rel: &str,
    aspec: &ArraySpec,
    level_name: &str,
    result: &mut ValidationResult,
) {
    if view.groups.contains_key(rel) {
        result.errors.push(format!(
            "[{level_name}] '{rel}' is a group, expected an array"
        ));
        return;
    }
    let Some((dtype, ndim, attrs)) = view.arrays.get(rel) else {
        if aspec.required {
            result
                .errors
                .push(format!("[{level_name}] required array '{rel}' is missing"));
        }
        return;
    };
    if !aspec.dtype.allows(dtype) {
        result.errors.push(format!(
            "[{level_name}] array '{rel}' dtype {dtype:?} != expected {}",
            aspec.dtype
        ));
    }
    if *ndim != aspec.ndim {
        result.errors.push(format!(
            "[{level_name}] array '{rel}' ndim {ndim} != expected {}",
            aspec.ndim
        ));
    }
    check_attrs(attrs, &aspec.attrs, rel, level_name, result);
}

/// Check declared attrs at one placement location (`where_` is the store path
/// used in messages). Required attrs must be present and (if present)
/// type-checked; optional attrs are type-checked only when present. An `equals`
/// value constraint and a `format` are checked after the type.
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
        } else if let Some(equals) = aspec.equals.as_ref().filter(|e| *e != value) {
            result.errors.push(format!(
                "[{level_name}] attribute '{name}' at '{where_}' must equal {equals}, got {value}"
            ));
        } else if aspec.format.as_deref() == Some("uri-reference")
            && !value.as_str().is_some_and(is_uri_reference)
        {
            result.errors.push(format!(
                "[{level_name}] attribute '{name}' at '{where_}' must be a URI reference \
                 (RFC 3986) and not an absolute path, got {value}"
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
            result.errors.push(format!(
                "root attribute '{attr_name}' is required but missing"
            ));
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
        result.errors.push(format!(
            "root attribute '{attr_name}': cannot parse version {disk:?}"
        ));
        return;
    };
    let (Some(current), Some(floor)) = (
        parse_version(&vspec.value),
        parse_version(&vspec.min_supported),
    ) else {
        // The meta-schema guarantees these parse; defensive.
        result.errors.push(format!(
            "root attribute '{attr_name}': spec version range is malformed"
        ));
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
///
/// ASCII digits only (`parse::<u64>` alone would take a `+` sign), the same
/// grammar as the meta-schema and the Python binding.
fn parse_version(s: &str) -> Option<(u64, u64)> {
    let digits = |p: &str| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit());
    let (major, minor) = s.split_once('.').unwrap_or((s, "0"));
    if !(digits(major) && digits(minor)) {
        return None;
    }
    Some((major.parse().ok()?, minor.parse().ok()?))
}

/// An RFC 3986 URI reference (ASCII; a `%` starts a two-hex-digit escape), not
/// empty and not an absolute path: a leading `/` names a file on one host only.
/// Parity with the Python `_URI_REFERENCE_RE`.
fn is_uri_reference(value: &str) -> bool {
    let allowed = |c: u8| c.is_ascii_alphanumeric() || b"-._~:/?#[]@!$&'()*+,;=".contains(&c);
    let mut rest = value.as_bytes();
    if rest.is_empty() || rest[0] == b'/' {
        return false;
    }
    while let Some((&c, tail)) = rest.split_first() {
        if c == b'%' {
            if !(tail.len() >= 2 && tail[0].is_ascii_hexdigit() && tail[1].is_ascii_hexdigit()) {
                return false;
            }
            rest = &tail[2..];
        } else if allowed(c) {
            rest = tail;
        } else {
            return false;
        }
    }
    true
}

/// The scan-group name pattern, quoted in errors (parity with the Python `_SCAN_PATTERN`).
const SCAN_PATTERN: &str = "^scan_[0-9]{6,}$";

/// `scan_<6+ ASCII digits>` (writer pads to 6; frontend b of scan N is
/// `scan_<N + 1000000·b>`).
fn is_scan(name: &str) -> bool {
    name.len() >= 11 && name.starts_with("scan_") && name[5..].bytes().all(|b| b.is_ascii_digit())
}

/// `band_<digits>` (writer: `band_{index}`, base-0, no zero-pad).
fn is_band(name: &str) -> bool {
    name.len() > 5 && name.starts_with("band_") && name[5..].bytes().all(|b| b.is_ascii_digit())
}

// ── store reader (the per-language seam; parity with `_store.py`) ──────────
//
// Parse rule (both bindings, stated in the spec): metadata is strict JSON. A
// non-finite number (`NaN`, `Infinity`, `-Infinity`) is its own error; any other
// unparseable `zarr.json` is a malformed-metadata error. The node is then left
// out of the view.

/// Walk a Zarr v3 store and normalize every node keyed by relative path.
/// Fails only when the root is not a readable Zarr v3 group.
fn read_store(store: &Path) -> Result<StoreView, String> {
    let not_group = || {
        format!(
            "{}: not a Zarr v3 group (missing/!group root zarr.json)",
            store.display()
        )
    };
    let root = read_meta(&store.join("zarr.json"), "zarr.json")?.ok_or_else(not_group)?;
    if root.get("node_type").and_then(Value::as_str) != Some("group") {
        return Err(not_group());
    }
    let mut view = StoreView {
        root_attrs: attributes(&root),
        ..StoreView::default()
    };
    walk(store, store, &mut view);
    Ok(view)
}

/// Recurse below `dir`, recording each child node that holds a `zarr.json`.
fn walk(root: &Path, dir: &Path, view: &mut StoreView) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        if !entry.file_type().is_ok_and(|t| t.is_dir()) {
            continue;
        }
        let path = entry.path();
        let rel = path
            .strip_prefix(root)
            .expect("walked path lies under the store root")
            .components()
            .map(|c| c.as_os_str().to_string_lossy())
            .collect::<Vec<_>>()
            .join("/");
        match read_meta(&path.join("zarr.json"), &format!("{rel}/zarr.json")) {
            Err(e) => view.errors.push(e),
            Ok(None) => {}
            Ok(Some(meta)) => match meta.get("node_type").and_then(Value::as_str) {
                Some("group") => {
                    view.groups.insert(rel, attributes(&meta));
                }
                Some("array") => {
                    let ndim = meta
                        .get("shape")
                        .and_then(Value::as_array)
                        .map_or(0, Vec::len);
                    view.arrays.insert(
                        rel,
                        (dtype_name(meta.get("data_type")), ndim, attributes(&meta)),
                    );
                }
                _ => {}
            },
        }
        walk(root, &path, view);
    }
}

/// Parse one `zarr.json`: `Ok(None)` when absent, `Err` naming `rel` when it
/// does not parse to a JSON object.
fn read_meta(path: &Path, rel: &str) -> Result<Option<Map<String, Value>>, String> {
    if !path.is_file() {
        return Ok(None);
    }
    let text = std::fs::read(path)
        .ok()
        .and_then(|b| String::from_utf8(b).ok());
    match text.as_deref().map(serde_json::from_str::<Value>) {
        Some(Ok(Value::Object(meta))) => Ok(Some(meta)),
        Some(Err(_)) if text.as_deref().is_some_and(has_non_finite) => {
            Err(format!("'{rel}': non-finite number in metadata"))
        }
        _ => Err(format!("'{rel}': malformed metadata (not valid JSON)")),
    }
}

/// True when `text` holds a bare `NaN` or `Infinity` token outside a string —
/// the non-JSON number literals that serde_json rejects and Python's json accepts.
fn has_non_finite(text: &str) -> bool {
    let bytes = text.as_bytes();
    let (mut in_string, mut escaped) = (false, false);
    for (i, &b) in bytes.iter().enumerate() {
        if in_string {
            match (escaped, b) {
                (true, _) => escaped = false,
                (false, b'\\') => escaped = true,
                (false, b'"') => in_string = false,
                _ => {}
            }
        } else if b == b'"' {
            in_string = true;
        } else if bytes[i..].starts_with(b"NaN") || bytes[i..].starts_with(b"Infinity") {
            return true;
        }
    }
    false
}

/// A node's `attributes` map (empty if absent or not an object).
fn attributes(meta: &Map<String, Value>) -> Map<String, Value> {
    meta.get("attributes")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default()
}

/// Normalize a Zarr v3 `data_type` to its plain name: the string form, or the
/// `name` of the object (extension) form; anything else has no name ("").
fn dtype_name(data_type: Option<&Value>) -> String {
    match data_type {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Object(o)) => o
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string(),
        _ => String::new(),
    }
}
