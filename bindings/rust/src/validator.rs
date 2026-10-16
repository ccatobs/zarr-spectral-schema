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
//!   (attr value constraint), `format` (`uri-reference`), `enum` (value set),
//!   `sentinel` (not-applicable value), `prefix` (array family), dtype sets,
//!   `fill_value` and array `attrs` are interpreted the same way. Each array's `dimension_names`
//!   and `units` attr are checked against its `dims`/`units`; arrays at one
//!   target agree on the length of each shared dimension; a multi-frontend scan
//!   group holds no scan-or-band attr. Every structure finding starts with
//!   `[<level>] `; version and level-detection errors do not (calibrate splits
//!   on this prefix, kalibrate#82).
//! * **Version negotiation** (Slice 3, #4) — each producer-owned on-disk version
//!   is range-checked against the spec's supported window (`min_supported` ..
//!   `value`): below the floor or a newer *major* is an error; a newer *minor* is
//!   a single warning (additive, validation still passes); older-or-equal within
//!   the window passes silently. Only versions whose `level` is detected are
//!   negotiated.
//!
//! `validate` returns errors only (back-compat); `check` returns the full
//! [`ValidationResult`] with warnings.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

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
    /// The attr may hold the not-applicable sentinel instead of a value.
    #[serde(default)]
    sentinel: bool,
    /// Value set: the attr holds one of these values.
    #[serde(default)]
    r#enum: Option<Vec<Value>>,
    /// Inclusive bounds of a number or integer attr.
    #[serde(default)]
    minimum: Option<serde_json::Number>,
    #[serde(default)]
    maximum: Option<serde_json::Number>,
}

#[derive(Deserialize)]
struct Group {
    /// true ⇒ arrays sit directly in the target dir; false ⇒ under `<target>/<name>/`.
    inline: bool,
    arrays: BTreeMap<String, ArraySpec>,
}

#[derive(Deserialize)]
struct ArraySpec {
    dtype: Dtype,
    ndim: usize,
    /// Ordered axis names, checked against the zarr v3 `dimension_names`.
    dims: Vec<String>,
    /// FITS unit string, checked against the array's `units` attr.
    #[serde(default)]
    units: Option<String>,
    required: bool,
    /// Fill value the array must declare in its Zarr metadata: an integer, so a
    /// spec that writes it as a float fails to load.
    #[serde(default)]
    fill_value: Option<i64>,
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
            Dtype::One(d) => write!(f, "{}", json(&Value::from(d.as_str()))),
            Dtype::Set(set) => write!(f, "{}", json(&Value::from(set.clone()))),
        }
    }
}

/// Outcome of validating a store: hard `errors` and advisory `warnings`.
#[derive(Debug, Default)]
pub struct ValidationResult {
    /// Structure findings, version errors and level-detection errors.
    pub errors: Vec<String>,
    /// Advisory findings, for example a newer minor version.
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
    arrays: BTreeMap<String, ArrayMeta>,
    /// metadata parse errors of non-root nodes, e.g. `'scan_000001/zarr.json': ...`
    errors: Vec<String>,
    /// Nodes left out because their metadata did not parse or could not be
    /// read, and groups that cannot be listed: no node at or below them is
    /// reported missing.
    unknown: BTreeSet<String>,
}

/// One array node of the view (parity with the Python `ArrayView`).
struct ArrayMeta {
    dtype: String,
    /// List of non-negative integers, or `None` when the metadata holds anything else.
    shape: Option<Vec<u64>>,
    /// zarr v3 `dimension_names` as parsed (`None` when absent or null).
    dimension_names: Option<Value>,
    attrs: Map<String, Value>,
    /// zarr v3 `fill_value` as parsed (`None` when absent).
    fill_value: Option<Value>,
}

fn load_spec() -> Spec {
    // The synced copy that build.rs also reads; include_str! is relative to this file.
    serde_yaml::from_str(include_str!("../spec/schema.yaml"))
        .expect("parse synced spec/schema.yaml")
}

/// Validate a Zarr v3 store against the spec. Returns errors; empty = conformant.
///
/// Back-compat surface. Use [`check`] to also surface newer-minor warnings.
/// `store` is a local path, or an `s3://` URL with the `s3` feature, as for
/// [`check`]. The errors are those of [`check`]: a structure finding starts with
/// `[L0] ` or `[L1] `; a version error and a level-detection error do not.
pub fn validate(store: &Path) -> Vec<String> {
    check(store).errors
}

/// Validate a Zarr v3 store against the spec, returning errors and warnings.
///
/// `store` is a local path or, with the `s3` feature, a URL
/// (`Path::new("s3://bucket/store.zarr")`). An S3 store takes its endpoint,
/// region and credentials from the standard `AWS_*` environment variables; its
/// check blocks on a runtime of its own, on a thread of its own, so a caller
/// inside a tokio runtime may call it too. A local check reads with `std::fs`
/// and needs no runtime.
///
/// The check reads `zarr.json` objects only, never a chunk. It accepts the
/// current major version of each level (L0 3, L1 2.0); a store below
/// `min_supported` gets one version error, without a prefix. Structure
/// findings follow when the store lacks fields of the current version; a
/// consumer stops on the version error.
///
/// Returns a [`ValidationResult`]. `errors` holds structure findings (prefix
/// `[L0] ` or `[L1] `), version errors and level-detection errors (no prefix).
/// `warnings` holds a newer minor version. A metadata file that does not parse is
/// an error. An unreadable root group gives one error and nothing else.
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
            "store declares no known level: none of the detect attributes {} \
             are present in root attributes",
            json(&Value::from(detects))
        ));
        return result;
    }

    // Negotiate only the versions belonging to a detected level: an L0 store
    // carries l0_schema_version, an L1 store cal_schema_version — checking the
    // absent one would spuriously fail every single-level store.
    let applicable_levels: Vec<&str> = applicable.iter().map(|(name, _)| name.as_str()).collect();
    negotiate_versions(&spec, &view.root_attrs, &applicable_levels, &mut result);

    // Metadata that did not parse is a structure finding, reported once (under
    // the first detected level).
    let first = applicable[0].0;
    result
        .errors
        .extend(view.errors.iter().map(|e| format!("[{first}] {e}")));
    let kids = children(&view);
    for (level_name, level) in &applicable {
        check_level(&view, &kids, level_name, level, &mut result);
    }

    result
}

/// [`check`] for a store named by a string: a URL (`s3://bucket/store.zarr`,
/// with the `s3` feature) or a local path, the same grammar. A caller that
/// holds a URL need not wrap it in a `Path`, which joins and canonicalizes as a
/// file path.
pub fn check_url(store: &str) -> ValidationResult {
    check(Path::new(store))
}

/// The child node paths of each node, sorted (`""` is the root): one index for
/// the whole check, so each lookup costs the children of one node, not the
/// nodes of the store.
type Children<'a> = BTreeMap<&'a str, Vec<&'a str>>;

fn children(view: &StoreView) -> Children<'_> {
    let mut kids: Children = BTreeMap::new();
    for path in view.groups.keys().chain(view.arrays.keys()) {
        let parent = path.rsplit_once('/').map_or("", |(p, _)| p);
        kids.entry(parent).or_default().push(path);
    }
    for paths in kids.values_mut() {
        paths.sort_unstable();
    }
    kids
}

/// Up to L0 2, frontend b of scan N was a scan of its own, numbered
/// N + 1000000 x b (ADR 0011, the band layout); a leftover is a finding.
const VIRTUAL_OFFSET: u64 = 1_000_000;

/// Walk one applicable level's structural contract against the store view.
fn check_level(
    view: &StoreView,
    kids: &Children,
    level_name: &str,
    level: &Level,
    result: &mut ValidationResult,
) {
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

    let no_kids = Vec::new();
    let kids_of = |p: &str| kids.get(p).unwrap_or(&no_kids);
    for &name in kids_of("") {
        if name.starts_with("scan_") && !is_scan(name) {
            result.errors.push(format!(
                "[{level_name}] root child '{name}' does not match the scan pattern {SCAN_PATTERN}"
            ));
        } else if is_scan(name) && view.arrays.contains_key(name) {
            result.errors.push(format!(
                "[{level_name}] '{name}' is an array, expected a scan group"
            ));
        } else if let Some(n) = is_scan(name)
            .then(|| name[5..].parse::<u64>().ok())
            .flatten()
            .filter(|n| *n >= VIRTUAL_OFFSET)
        {
            let base = format!("scan_{:06}", n % VIRTUAL_OFFSET);
            if view.groups.contains_key(&base) {
                result.errors.push(format!(
                    "[{level_name}] '{name}' is a virtual scan number ({base} + {VIRTUAL_OFFSET} x {}): \
                     a multi-frontend scan holds each band under scan_N/band_B/",
                    n / VIRTUAL_OFFSET
                ));
            }
        }
    }
    let scan_groups: Vec<&str> = kids_of("")
        .iter()
        .copied()
        .filter(|p| is_scan(p) && view.groups.contains_key(*p))
        .collect();
    if needs_scans && scan_groups.is_empty() {
        result.errors.push(format!(
            "[{level_name}] no scan_<NNNNNN> groups found at the store root"
        ));
        return;
    }

    let one_of: Vec<&String> = level
        .one_of
        .iter()
        .filter(|n| level.groups.contains_key(*n))
        .collect();
    for sg in scan_groups {
        let bands: Vec<&str> = kids_of(sg)
            .iter()
            .copied()
            .filter(|p| view.groups.contains_key(*p) && is_band(&p[sg.len() + 1..]))
            .collect();

        // Per-scan-group attrs always checked at the scan group itself.
        check_attrs(&view.groups[sg], &level.scan_attrs, sg, level_name, result);

        // Multi-frontend: per-band attrs on each band group; the scan group
        // holds band groups only, and no scan-or-band attr (it belongs on each
        // band).
        for b in &bands {
            check_attrs(&view.groups[*b], &level.band_attrs, b, level_name, result);
        }
        if !bands.is_empty() {
            for node in kids_of(sg).iter().filter(|n| !bands.contains(n)) {
                result.errors.push(format!(
                    "[{level_name}] '{node}' is not a band group: the scan group of a \
                     multi-frontend scan holds band groups only"
                ));
            }
            for name in level.scan_or_band_attrs.keys() {
                if view.groups[sg].contains_key(name) {
                    result.errors.push(format!(
                        "[{level_name}] attribute '{name}' at '{sg}' belongs on each band \
                         of a multi-frontend scan, not on the scan group"
                    ));
                }
            }
        }

        // `scan_or_band` placement resolves to the scan group (single-frontend)
        // or each band (multi-frontend).
        let targets = if bands.is_empty() { vec![sg] } else { bands };
        for t in targets {
            check_attrs(
                &view.groups[t],
                &level.scan_or_band_attrs,
                t,
                level_name,
                result,
            );
            // (path, dims, shape) of each array checked at this target.
            let mut seen: Vec<Seen> = Vec::new();
            let present: Vec<&String> = one_of
                .iter()
                .copied()
                .filter(|n| {
                    let path = format!("{t}/{n}");
                    view.groups.contains_key(&path) || view.unknown.contains(&path)
                })
                .collect();
            if !one_of.is_empty() && present.len() != 1 {
                result.errors.push(format!(
                    "[{level_name}] '{t}' must hold exactly one of {}, found {}",
                    json(&names(&one_of)),
                    json(&names(&present))
                ));
            }
            for (gname, group) in &level.groups {
                if one_of.contains(&gname) && !present.contains(&gname) {
                    continue;
                }
                check_group_arrays(view, kids, t, gname, group, level_name, result, &mut seen);
            }
            check_shared_dims(t, &seen, level_name, result);
        }
    }
}

/// True when the node at `rel`, or a group above it, was left out of the
/// view: its metadata did not parse or could not be read or listed.
fn unknown(view: &StoreView, rel: &str) -> bool {
    rel.match_indices('/')
        .map(|(i, _)| &rel[..i])
        .chain([rel])
        .any(|p| view.unknown.contains(p))
}

/// One checked array: (store path, declared dims, on-disk shape).
type Seen = (String, Vec<String>, Vec<u64>);

/// Arrays at one target that share an axis name agree on its length.
fn check_shared_dims(
    target: &str,
    arrays: &[Seen],
    level_name: &str,
    result: &mut ValidationResult,
) {
    let mut lengths: BTreeMap<&str, BTreeMap<u64, Vec<&str>>> = BTreeMap::new();
    for (rel, dims, shape) in arrays {
        for (dim, n) in dims.iter().zip(shape) {
            lengths
                .entry(dim)
                .or_default()
                .entry(*n)
                .or_default()
                .push(&rel[target.len() + 1..]);
        }
    }
    for (dim, by_len) in &lengths {
        if by_len.len() > 1 {
            let parts: Vec<String> = by_len
                .iter()
                .map(|(n, names)| {
                    let mut names = names.clone();
                    names.sort_unstable();
                    format!("{n} in [{}]", names.join(", "))
                })
                .collect();
            result.errors.push(format!(
                "[{level_name}] arrays at '{target}' disagree on the length of dimension '{dim}': {}",
                parts.join("; ")
            ));
        }
    }
}

/// Check one group's arrays under `base`.
///
/// `inline` groups place arrays directly in `base`; otherwise they sit under
/// `base/<group_name>/`. Required arrays must exist (dtype + ndim checked);
/// optional arrays are checked only when present. An array family (`prefix`)
/// checks every array whose name starts with the prefix.
#[allow(clippy::too_many_arguments)]
fn check_group_arrays(
    view: &StoreView,
    kids: &Children,
    base: &str,
    group_name: &str,
    group: &Group,
    level_name: &str,
    result: &mut ValidationResult,
    seen: &mut Vec<Seen>,
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
                seen,
            );
            continue;
        };
        let start = format!("{prefix}/{family}");
        let members: Vec<&str> = kids
            .get(prefix.as_str())
            .into_iter()
            .flatten()
            .copied()
            .filter(|r| r.starts_with(&start))
            .collect();
        if members.is_empty() && aspec.required {
            result.errors.push(format!(
                "[{level_name}] required array family '{start}*' is missing"
            ));
        }
        for rel in members {
            check_array(view, rel, aspec, level_name, result, seen);
        }
    }
}

/// Check one array path: node type, presence (if required), dtype, ndim,
/// `fill_value` (when the spec declares one),
/// `dimension_names` against the spec's `dims`, the `units` attr and the
/// array's own `attrs`.
fn check_array(
    view: &StoreView,
    rel: &str,
    aspec: &ArraySpec,
    level_name: &str,
    result: &mut ValidationResult,
    seen: &mut Vec<Seen>,
) {
    if view.groups.contains_key(rel) {
        result.errors.push(format!(
            "[{level_name}] '{rel}' is a group, expected an array"
        ));
        return;
    }
    let Some(arr) = view.arrays.get(rel) else {
        if aspec.required && !unknown(view, rel) {
            result
                .errors
                .push(format!("[{level_name}] required array '{rel}' is missing"));
        }
        return;
    };
    if !aspec.dtype.allows(&arr.dtype) {
        result.errors.push(format!(
            "[{level_name}] array '{rel}' dtype {} != expected {}",
            json(&Value::from(arr.dtype.as_str())),
            aspec.dtype
        ));
    }
    if let Some(want) = &aspec.fill_value {
        let want = Value::from(*want);
        // Exact: serde_json numbers differ by kind, so 4294967295.0 is not 4294967295.
        if arr.fill_value.as_ref() != Some(&want) {
            result.errors.push(format!(
                "[{level_name}] array '{rel}' fill_value {} != expected {}",
                json(arr.fill_value.as_ref().unwrap_or(&Value::Null)),
                json(&want)
            ));
        }
    }
    match &arr.shape {
        None => result.errors.push(format!(
            "[{level_name}] array '{rel}' shape is not a list of non-negative integers"
        )),
        Some(shape) if shape.len() != aspec.ndim => result.errors.push(format!(
            "[{level_name}] array '{rel}' ndim {} != expected {}",
            shape.len(),
            aspec.ndim
        )),
        Some(shape) if shape.len() == aspec.dims.len() => {
            seen.push((rel.to_string(), aspec.dims.clone(), shape.clone()));
        }
        Some(_) => {}
    }
    let dims: Vec<Value> = aspec.dims.iter().cloned().map(Value::String).collect();
    match &arr.dimension_names {
        None => result.errors.push(format!(
            "[{level_name}] array '{rel}' has no dimension_names (expected {})",
            json(&Value::Array(dims))
        )),
        Some(Value::Array(names)) if *names == dims => {}
        Some(other) => result.errors.push(format!(
            "[{level_name}] array '{rel}' dimension_names {} != expected {}",
            json(other),
            json(&Value::Array(dims))
        )),
    }
    if let Some(units) = &aspec.units {
        let expected = Value::String(units.clone());
        match arr.attrs.get("units") {
            None => result.errors.push(format!(
                "[{level_name}] array '{rel}' has no units attr (expected {})",
                json(&expected)
            )),
            Some(got) if *got != expected => result.errors.push(format!(
                "[{level_name}] array '{rel}' units {} != expected {}",
                json(got),
                json(&expected)
            )),
            Some(_) => {}
        }
    } else if let Some(got) = arr.attrs.get("units") {
        result.errors.push(format!(
            "[{level_name}] array '{rel}' has a units attr {} but declares no unit",
            json(got)
        ));
    }
    check_attrs(&arr.attrs, &aspec.attrs, rel, level_name, result);
}

/// Render a JSON value as the Python binding does (`json.dumps` with
/// `ensure_ascii=False, sort_keys=True`: `["a", "b"]`, `{"k": 1}`), so both
/// bindings word a finding the same way.
fn json(value: &Value) -> String {
    let join = |parts: Vec<String>| parts.join(", ");
    match value {
        Value::Array(items) => format!("[{}]", join(items.iter().map(json).collect())),
        // serde_json's Map is sorted by key, as sort_keys sorts.
        Value::Object(map) => format!(
            "{{{}}}",
            join(
                map.iter()
                    .map(|(k, v)| format!("{}: {}", json(&Value::from(k.as_str())), json(v)))
                    .collect()
            )
        ),
        other => other.to_string(),
    }
}

/// A list of names as a JSON array.
fn names(items: &[&String]) -> Value {
    Value::from(items.iter().map(|s| s.as_str()).collect::<Vec<_>>())
}

/// Check declared attrs at one placement location (`where_` is the store path
/// used in messages). Required attrs must be present and (if present)
/// type-checked; optional attrs are type-checked only when present. An attr with
/// `sentinel: true` may hold the not-applicable sentinel instead. An `enum`
/// value set, an `equals` value constraint and a `format` are checked after the
/// type.
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
        if aspec.sentinel && *value == sentinel(&aspec.r#type) {
            continue;
        }
        if !type_matches(&aspec.r#type, value) {
            result.errors.push(format!(
                "[{level_name}] attribute '{name}' at '{where_}' must be of type \
                 {}, got {}",
                json(&Value::from(aspec.r#type.as_str())),
                json(value)
            ));
        } else if let Some(set) = aspec
            .r#enum
            .as_ref()
            .filter(|s| !s.iter().any(|v| same(v, value)))
        {
            result.errors.push(format!(
                "[{level_name}] attribute '{name}' at '{where_}' must be one of {}, got {}",
                json(&Value::Array(set.clone())),
                json(value)
            ));
        } else if let Some(min) = aspec.minimum.as_ref().filter(|m| compare(value, m).is_lt()) {
            result.errors.push(format!(
                "[{level_name}] attribute '{name}' at '{where_}' must be >= {min}, got {}",
                json(value)
            ));
        } else if let Some(max) = aspec.maximum.as_ref().filter(|m| compare(value, m).is_gt()) {
            result.errors.push(format!(
                "[{level_name}] attribute '{name}' at '{where_}' must be <= {max}, got {}",
                json(value)
            ));
        } else if let Some(equals) = aspec.equals.as_ref().filter(|e| !same(e, value)) {
            result.errors.push(format!(
                "[{level_name}] attribute '{name}' at '{where_}' must equal {}, got {}",
                json(equals),
                json(value)
            ));
        } else if aspec.format.as_deref() == Some("uri-reference")
            && !value.as_str().is_some_and(is_uri_reference)
        {
            result.errors.push(format!(
                "[{level_name}] attribute '{name}' at '{where_}' must be a URI reference \
                 (RFC 3986), not an absolute path and not a file: URI, got {}",
                json(value)
            ));
        }
    }
}

/// JSON equality by type and value, as in the Python binding: numbers compare
/// by value (`1` equals `1.0`), integers exactly.
fn same(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(x), Value::Number(y)) if x.is_f64() || y.is_f64() => {
            x.as_f64() == y.as_f64()
        }
        _ => a == b,
    }
}

/// A JSON number against a bound (`Equal` when it is not a number: the type
/// check reports that).
fn compare(value: &Value, bound: &serde_json::Number) -> std::cmp::Ordering {
    value
        .as_f64()
        .zip(bound.as_f64())
        .and_then(|(v, b)| v.partial_cmp(&b))
        .unwrap_or(std::cmp::Ordering::Equal)
}

/// The not-applicable sentinel of an attr type: "NONE" or JSON null.
fn sentinel(attr_type: &str) -> Value {
    match attr_type {
        "string" => Value::String("NONE".into()),
        _ => Value::Null,
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
                        "root attribute '{attr_name}' must be an integer on disk, got {}",
                        json(value)
                    ));
                    continue;
                }
            },
            "string" => match value.as_str() {
                Some(s) => s.to_string(),
                None => {
                    result.errors.push(format!(
                        "root attribute '{attr_name}' must be a string on disk, got {}",
                        json(value)
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
            "root attribute '{attr_name}': cannot parse version {}",
            json(&Value::from(disk))
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
/// empty, not an absolute path and not a `file:` URI: those name a file on one
/// host only.
/// Parity with the Python `_URI_REFERENCE_RE`.
fn is_uri_reference(value: &str) -> bool {
    let allowed = |c: u8| c.is_ascii_alphanumeric() || b"-._~:/?#[]@!$&'()*+,;=".contains(&c);
    let mut rest = value.as_bytes();
    let file_uri = rest
        .get(..5)
        .is_some_and(|p| p.eq_ignore_ascii_case(b"file:"));
    if rest.is_empty() || rest[0] == b'/' || file_uri {
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

/// `scan_<6+ ASCII digits>` (writer pads to 6).
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
//
// Object storage (#20). One input grammar in both bindings: an argument that
// starts with `scheme://` is a URL, read through `object_store` (feature `s3`),
// and its path is percent-decoded. Anything else is a local path, read with
// `std::fs`. Both backends serve one walk: it reads only `zarr.json` objects,
// lists the children of each group node and stops at an array node, so it
// never lists or opens a chunk key. A directory without a `zarr.json` is not a
// node, and the local walk does not follow a symlinked directory. An I/O
// failure is an error of its own, never a gap: a failed listing is
// `'<rel>/': cannot list`, a failed read of a `zarr.json` is
// `'<rel>': cannot read metadata`.

/// Where the walk reads a store: list the child directories of a node, read
/// the `zarr.json` of a node.
trait Backend {
    /// A node: a directory path, or an object-store key prefix.
    type Loc;
    /// The child directories of `dir` with their names, sorted by name.
    fn children(&self, dir: &Self::Loc) -> Result<Vec<(String, Self::Loc)>, ()>;
    /// The `zarr.json` of `dir`: `Ok(None)` when absent, `Err` when unreadable.
    fn zarr_json(&self, dir: &Self::Loc) -> Result<Option<Vec<u8>>, ()>;
}

/// A local directory tree, read with `std::fs`.
struct Fs;

impl Backend for Fs {
    type Loc = PathBuf;

    fn children(&self, dir: &PathBuf) -> Result<Vec<(String, PathBuf)>, ()> {
        let mut children = Vec::new();
        for entry in std::fs::read_dir(dir).map_err(drop)? {
            let entry = entry.map_err(drop)?;
            // file_type() does not follow a symlink: a symlinked directory is
            // skipped. A failed file_type() fails the listing, never a gap.
            if entry.file_type().map_err(drop)?.is_dir() {
                let name = entry.file_name().to_string_lossy().into_owned();
                children.push((name, entry.path()));
            }
        }
        children.sort();
        Ok(children)
    }

    fn zarr_json(&self, dir: &PathBuf) -> Result<Option<Vec<u8>>, ()> {
        match std::fs::read(dir.join("zarr.json")) {
            Ok(bytes) => Ok(Some(bytes)),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(_) => Err(()),
        }
    }
}

/// An object store behind a URL, with a runtime to block on its futures.
#[cfg(feature = "s3")]
struct Obj {
    rt: tokio::runtime::Runtime,
    store: Box<dyn object_store::ObjectStore>,
}

#[cfg(feature = "s3")]
impl Obj {
    /// Open a URL; return the backend and the key of the store root.
    fn open(url: &str) -> Result<(Self, object_store::path::Path), String> {
        let parsed = url::Url::parse(url).map_err(|e| format!("{url}: {e}"))?;
        // Endpoint, region and credentials come from AWS_*; a variable that is
        // not UTF-8 is skipped.
        let aws_env = std::env::vars_os()
            .filter_map(|(k, v)| Some((k.into_string().ok()?, v.into_string().ok()?)))
            .filter(|(k, _)| k.starts_with("AWS_"));
        let (store, root) =
            object_store::parse_url_opts(&parsed, aws_env).map_err(|e| format!("{url}: {e}"))?;
        let rt = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|e| format!("{url}: {e}"))?;
        Ok((Self { rt, store }, root))
    }
}

#[cfg(feature = "s3")]
impl Backend for Obj {
    type Loc = object_store::path::Path;

    fn children(&self, dir: &Self::Loc) -> Result<Vec<(String, Self::Loc)>, ()> {
        let list = self
            .rt
            .block_on(self.store.list_with_delimiter(Some(dir)))
            .map_err(drop)?;
        let mut children: Vec<(String, Self::Loc)> = list
            .common_prefixes
            .into_iter()
            .filter_map(|p| Some((p.filename()?.to_string(), p.clone())))
            .collect();
        children.sort_by(|a, b| a.0.cmp(&b.0));
        Ok(children)
    }

    fn zarr_json(&self, dir: &Self::Loc) -> Result<Option<Vec<u8>>, ()> {
        use object_store::ObjectStoreExt;
        let key = dir.clone().join("zarr.json");
        let get = async { self.store.get(&key).await?.bytes().await };
        match self.rt.block_on(get) {
            Ok(bytes) => Ok(Some(bytes.to_vec())),
            Err(object_store::Error::NotFound { .. }) => Ok(None),
            Err(_) => Err(()),
        }
    }
}

fn not_group(store: &Path) -> String {
    format!(
        "{}: not a Zarr v3 group (missing/!group root zarr.json)",
        store.display()
    )
}

/// `scheme://…` (scheme: an ASCII letter, then letters, digits, `+`, `.`, `-`).
/// Parity with the Python `_URL_RE`.
fn is_url(s: &str) -> bool {
    s.split_once("://").is_some_and(|(scheme, _)| {
        scheme
            .bytes()
            .next()
            .is_some_and(|b| b.is_ascii_alphabetic())
            && scheme
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"+.-".contains(&b))
    })
}

/// Walk a Zarr v3 store and normalize every node keyed by relative path.
/// Fails only when the root is not a readable Zarr v3 group.
fn read_store(store: &Path) -> Result<StoreView, String> {
    match store.to_str().filter(|s| is_url(s)) {
        None => read_with(&Fs, store.to_path_buf(), store),
        #[cfg(feature = "s3")]
        Some(url) => {
            // A thread of its own: a runtime cannot block inside the runtime of
            // a caller.
            std::thread::scope(|s| {
                s.spawn(|| {
                    let (backend, root) = Obj::open(url)?;
                    read_with(&backend, root, store)
                })
                .join()
                .unwrap_or_else(|p| std::panic::resume_unwind(p))
            })
        }
        #[cfg(not(feature = "s3"))]
        Some(_) => Err(format!("{}: s3:// needs the `s3` feature", store.display())),
    }
}

fn read_with<B: Backend>(backend: &B, root: B::Loc, store: &Path) -> Result<StoreView, String> {
    let meta = read_meta(backend, &root, "zarr.json")?.ok_or_else(|| not_group(store))?;
    if meta.get("node_type").and_then(Value::as_str) != Some("group") {
        return Err(not_group(store));
    }
    let mut view = StoreView {
        root_attrs: attributes(&meta),
        ..StoreView::default()
    };
    walk(backend, &root, "", &mut view)?; // a root that cannot be listed fails the read
    Ok(view)
}

/// Record each child node of the group at `dir` (`rel` in the store); recurse
/// into child groups. `Err` when `dir` cannot be listed; a child group that
/// cannot be listed is an error in `view.errors`.
///
/// ponytail: one request at a time; fetch siblings concurrently if a large
/// store on object storage makes the walk slow.
fn walk<B: Backend>(
    backend: &B,
    dir: &B::Loc,
    rel: &str,
    view: &mut StoreView,
) -> Result<(), String> {
    let children = backend
        .children(dir)
        .map_err(|()| format!("'{rel}/': cannot list"))?;
    for (name, child) in children {
        let rel = if rel.is_empty() {
            name
        } else {
            format!("{rel}/{name}")
        };
        match read_meta(backend, &child, &format!("{rel}/zarr.json")) {
            Err(e) => {
                view.errors.push(e);
                view.unknown.insert(rel);
            }
            Ok(None) => {}
            Ok(Some(meta)) => match meta.get("node_type").and_then(Value::as_str) {
                Some("group") => {
                    view.groups.insert(rel.clone(), attributes(&meta));
                    if let Err(e) = walk(backend, &child, &rel, view) {
                        view.errors.push(e);
                        view.unknown.insert(rel);
                    }
                }
                Some("array") => {
                    let shape = meta
                        .get("shape")
                        .and_then(Value::as_array)
                        .and_then(|s| s.iter().map(Value::as_u64).collect());
                    let dimension_names = meta
                        .get("dimension_names")
                        .filter(|v| !v.is_null())
                        .cloned();
                    view.arrays.insert(
                        rel,
                        ArrayMeta {
                            dtype: dtype_name(meta.get("data_type")),
                            shape,
                            dimension_names,
                            attrs: attributes(&meta),
                            fill_value: meta.get("fill_value").cloned(),
                        },
                    );
                }
                _ => {}
            },
        }
    }
    Ok(())
}

/// Parse the `zarr.json` of `dir`: `Ok(None)` when absent, `Err` naming `rel`
/// when it cannot be read or does not parse to a JSON object.
fn read_meta<B: Backend>(
    backend: &B,
    dir: &B::Loc,
    rel: &str,
) -> Result<Option<Map<String, Value>>, String> {
    let text = match backend.zarr_json(dir) {
        Ok(None) => return Ok(None),
        Ok(Some(bytes)) => String::from_utf8(bytes).ok(),
        Err(()) => return Err(format!("'{rel}': cannot read metadata")),
    };
    match text
        .as_deref()
        .map(|t| serde_json::from_str::<Value>(&integer_zero(t)))
    {
        Some(Ok(Value::Object(meta))) => Ok(Some(meta)),
        Some(Err(_)) if text.as_deref().is_some_and(has_non_finite) => {
            Err(format!("'{rel}': non-finite number in metadata"))
        }
        _ => Err(format!("'{rel}': malformed metadata (not valid JSON)")),
    }
}

/// The byte offsets of `text` outside JSON strings.
fn outside_strings(text: &str) -> impl Iterator<Item = usize> + '_ {
    let (mut in_string, mut escaped) = (false, false);
    text.bytes().enumerate().filter_map(move |(i, b)| {
        if in_string {
            match (escaped, b) {
                (true, _) => escaped = false,
                (false, b'\\') => escaped = true,
                (false, b'"') => in_string = false,
                _ => {}
            }
            None
        } else if b == b'"' {
            in_string = true;
            None
        } else {
            Some(i)
        }
    })
}

/// True when `text` holds a bare `NaN` or `Infinity` token outside a string —
/// the non-JSON number literals that serde_json rejects and Python's json accepts.
fn has_non_finite(text: &str) -> bool {
    let bytes = text.as_bytes();
    outside_strings(text)
        .any(|i| bytes[i..].starts_with(b"NaN") || bytes[i..].starts_with(b"Infinity"))
}

/// `text` with each JSON integer `-0` written `0`: serde_json reads `-0` as the
/// float -0.0, Python's json as the integer 0.
fn integer_zero(text: &str) -> std::borrow::Cow<'_, str> {
    let bytes = text.as_bytes();
    let minus: Vec<usize> = outside_strings(text)
        .filter(|&i| {
            bytes[i..].starts_with(b"-0")
                && !bytes
                    .get(i + 2)
                    .is_some_and(|c| c.is_ascii_digit() || b".eE".contains(c))
        })
        .collect();
    if minus.is_empty() {
        return text.into();
    }
    let mut out = bytes.to_vec();
    for i in minus {
        out[i] = b' ';
    }
    String::from_utf8(out)
        .expect("an ASCII byte replaced")
        .into()
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

#[cfg(test)]
mod tests {
    use super::*;

    fn attrs(declared: &str, value: Value) -> Vec<String> {
        let declared: BTreeMap<String, AttrSpec> =
            serde_yaml::from_str(&format!("x: {declared}")).unwrap();
        let mut result = ValidationResult::default();
        let attrs = Map::from_iter([("x".to_string(), value)]);
        check_attrs(&attrs, &declared, "root", "L1", &mut result);
        result.errors
    }

    #[test]
    fn enum_and_equals_compare_numbers_by_value() {
        let one = serde_json::json!(1.0);
        let set = "{ type: number, required: true, enum: [1, 2] }";
        assert_eq!(attrs(set, one.clone()), Vec::<String>::new());
        let equals = "{ type: number, required: true, equals: 1 }";
        assert_eq!(attrs(equals, one), Vec::<String>::new());
    }
}
