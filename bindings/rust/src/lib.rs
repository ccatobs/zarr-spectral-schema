//! zarr-spectral-schema — Rust binding.
//!
//! The on-disk L0–L1 Zarr contract, derived from the spec (`spec/schema.yaml`).
//! The flat `pub const`s are *generated* at build time (see build.rs); the
//! [`validate`] entry point runs the generic interpreter — the same
//! interpretation as the Python binding — against a store.

// Spec-derived constants (DATA_5D, L0_SCHEMA_VERSION, ...).
include!(concat!(env!("OUT_DIR"), "/generated.rs"));

mod validator;
pub use validator::{check, check_url, validate, ValidationResult};
