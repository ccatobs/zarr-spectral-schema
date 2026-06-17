//! Build-time codegen: derive the flat `pub const`s from the spec.
//!
//! These reproduce the cal-schema constant vocabulary (array key `data_5d` →
//! `DATA_5D`; version key `l0_schema_version` → `L0_SCHEMA_VERSION`) so the
//! Slice 3 (#3) lift makes calibrate consume this crate via `pub use`. There is
//! no hand-typed duplicate — edit spec/schema.yaml, the consts regenerate.

use std::env;
use std::fs;
use std::path::Path;

fn main() {
    println!("cargo:rerun-if-changed=spec/schema.yaml");
    let spec_text = fs::read_to_string("spec/schema.yaml").expect("read spec/schema.yaml");
    let spec: serde_yaml::Value = serde_yaml::from_str(&spec_text).expect("parse spec/schema.yaml");

    let mut out = String::from("// @generated from spec/schema.yaml by build.rs — do not edit.\n");

    if let Some(versions) = spec.get("versions").and_then(|v| v.as_mapping()) {
        for (key, vspec) in versions {
            let name = key.as_str().expect("version key is a string").to_uppercase();
            let value = vspec
                .get("value")
                .and_then(|x| x.as_str())
                .expect("version.value is a string");
            out.push_str(&format!("pub const {name}: &str = {value:?};\n"));
        }
    }

    if let Some(levels) = spec.get("levels").and_then(|v| v.as_mapping()) {
        for (_lvl, level) in levels {
            let Some(groups) = level.get("groups").and_then(|v| v.as_mapping()) else {
                continue;
            };
            for (_g, group) in groups {
                let Some(arrays) = group.get("arrays").and_then(|v| v.as_mapping()) else {
                    continue;
                };
                for (array_key, _aspec) in arrays {
                    let key = array_key.as_str().expect("array key is a string");
                    let name = key.to_uppercase();
                    out.push_str(&format!("pub const {name}: &str = {key:?};\n"));
                }
            }
        }
    }

    let out_dir = env::var("OUT_DIR").expect("OUT_DIR set by cargo");
    fs::write(Path::new(&out_dir).join("generated.rs"), out).expect("write generated.rs");
}
