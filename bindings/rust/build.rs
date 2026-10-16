//! Build-time codegen: derive the flat `pub const`s from the spec.
//!
//! Reproduces the cal-schema constant vocabulary so the lift makes calibrate
//! consume this crate via `pub use`. There is no hand-typed duplicate — edit
//! spec/schema.yaml, the consts regenerate.
//!
//! Identifier rules: array → UPPER(on-disk name); group → UPPER(name)_GROUP
//! (or none if `const: false`); version → UPPER(attr key). Any node may override
//! with `const:`. `lists:` → `&[&str]`; `markers:` → `&str`; `flag_bits:` →
//! `u16` masks (`1 << bit`). Consts are
//! deduplicated by name (an array shared across groups yields one const); a name
//! bound to two different values is a hard error.

use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::path::Path;

use serde_yaml::Value;

/// Insert a `pub const NAME: &str = "value";`, asserting no conflicting redefinition.
fn add_scalar(consts: &mut BTreeMap<String, String>, name: String, value: &str) {
    let decl = format!("pub const {name}: &str = {value:?};");
    if let Some(existing) = consts.get(&name) {
        assert_eq!(
            existing, &decl,
            "const {name} declared with conflicting values"
        );
    }
    consts.insert(name, decl);
}

fn main() {
    println!("cargo:rerun-if-changed=spec/schema.yaml");
    let spec_text = fs::read_to_string("spec/schema.yaml").expect("read spec/schema.yaml");
    let spec: Value = serde_yaml::from_str(&spec_text).expect("parse spec/schema.yaml");

    let mut consts: BTreeMap<String, String> = BTreeMap::new();

    // ── versions ──
    if let Some(versions) = spec.get("versions").and_then(Value::as_mapping) {
        for (key, vspec) in versions {
            let attr = key.as_str().expect("version key is a string");
            let name = vspec
                .get("const")
                .and_then(Value::as_str)
                .map(str::to_string)
                .unwrap_or_else(|| attr.to_uppercase());
            let value = vspec
                .get("value")
                .and_then(Value::as_str)
                .expect("version.value is a string");
            add_scalar(&mut consts, name, value);
        }
    }

    // ── levels → groups → arrays ──
    if let Some(levels) = spec.get("levels").and_then(Value::as_mapping) {
        for (_lvl, level) in levels {
            let Some(groups) = level.get("groups").and_then(Value::as_mapping) else {
                continue;
            };
            for (gkey, group) in groups {
                let gname = gkey.as_str().expect("group key is a string");
                match group.get("const") {
                    Some(Value::Bool(false)) => {} // structural group: emit no const
                    Some(Value::String(ident)) => add_scalar(&mut consts, ident.clone(), gname),
                    _ => add_scalar(
                        &mut consts,
                        format!("{}_GROUP", gname.to_uppercase()),
                        gname,
                    ),
                }
                let Some(arrays) = group.get("arrays").and_then(Value::as_mapping) else {
                    continue;
                };
                for (akey, aspec) in arrays {
                    let aname = akey.as_str().expect("array key is a string");
                    let name = aspec
                        .get("const")
                        .and_then(Value::as_str)
                        .map(str::to_string)
                        .unwrap_or_else(|| aname.to_uppercase());
                    add_scalar(&mut consts, name, aname);
                }
            }
        }
    }

    // ── markers (single attribute-name consts) ──
    if let Some(markers) = spec.get("markers").and_then(Value::as_mapping) {
        for (key, val) in markers {
            let name = key.as_str().expect("marker key is a string").to_string();
            let value = val.as_str().expect("marker value is a string");
            add_scalar(&mut consts, name, value);
        }
    }

    // ── flag bits (bit index → u16 mask), one name per bit ──
    if let Some(bits) = spec.get("flag_bits").and_then(Value::as_mapping) {
        let mut names: BTreeMap<u64, String> = BTreeMap::new();
        for (key, val) in bits {
            let name = key.as_str().expect("flag bit key is a string").to_string();
            let bit = val.as_u64().expect("flag bit is an integer");
            if let Some(other) = names.insert(bit, name.clone()) {
                panic!("flag bit {bit} has two names: {other} and {name}");
            }
            let decl = format!("pub const {name}: u16 = 1 << {bit};");
            if let Some(existing) = consts.get(&name) {
                assert_eq!(
                    existing, &decl,
                    "const {name} declared with conflicting values"
                );
            }
            consts.insert(name, decl);
        }
    }

    // ── lists (ordered name lists → &[&str]) ──
    if let Some(lists) = spec.get("lists").and_then(Value::as_mapping) {
        for (key, val) in lists {
            let name = key.as_str().expect("list key is a string").to_string();
            let items: Vec<String> = val
                .as_sequence()
                .expect("list value is a sequence")
                .iter()
                .map(|x| format!("{:?}", x.as_str().expect("list item is a string")))
                .collect();
            let decl = format!("pub const {name}: &[&str] = &[{}];", items.join(", "));
            if let Some(existing) = consts.get(&name) {
                assert_eq!(
                    existing, &decl,
                    "const {name} declared with conflicting values"
                );
            }
            consts.insert(name, decl);
        }
    }

    let mut out = String::from("// @generated from spec/schema.yaml by build.rs — do not edit.\n");
    for decl in consts.values() {
        out.push_str(decl);
        out.push('\n');
    }

    let out_dir = env::var("OUT_DIR").expect("OUT_DIR set by cargo");
    fs::write(Path::new(&out_dir).join("generated.rs"), out).expect("write generated.rs");
}
