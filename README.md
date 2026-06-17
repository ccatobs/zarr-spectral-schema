# zarr-spectral-schema

Single, versioned **source of truth** for the on-disk Zarr "storage language"
shared by the CCAT/CHAI single-dish heterodyne pipeline:

> **zarr-fits** writes L0, **calibrate** writes L1, **YODA** reads. Today each
> re-declares the contract and they drift. This repo makes the contract one
> hand-authored, language-neutral spec that all three lock to.

Root of the zarr-store dependency chain — a sibling to `calibrate`, `zarr_fits`,
and `yoda` under `chai-software/`, the same *shape* as `data-center` at the root
of the ops-db chain (different domain, not co-located).

## Status: scaffolding

The design is settled; the code is not yet built. **Start here:**

1. Read **`docs/adr/0007-zarr-spectral-schema-shared-source-of-truth.md`** — the binding decisions (its authoritative home is this repo).
2. Read the **epic (issue #1)** and its child issues — the tracer-bullet rollout.
3. Begin with **Slice 1** (the tracer bullet: repo skeleton + spec DSL + one field end-to-end). Do **not** bulk-port before the one-field path is green.

## Design in one breath

- **Spec file is the single source of truth** — one hand-authored, language-neutral spec (`spec/schema.yaml`); the Rust crate (`build.rs` codegen) and the Python package (loads the spec as package data) are both *derived* from it. No second hand-maintained copy.
- **Fully-declarative contract** → a generic-interpreter validator, identical in both languages. The lone hand-written per-language seam is string encoding, pinned by conformance tests.
- **v1 scope = L0 + L1 only** — the cross-tool handoffs. L2/L3/provenance stay YODA-owned until the DAG engine writes them. SDFITS (L1/L2) and FITS-map (L3) export kept non-foreclosed.
- **Two-axis versioning** — producer-owned on-disk versions; this repo's package version stays off-disk; consumer checks `>= min` → warn-once on newer minor, error on newer major.
- **Store-and-forward self-description** — identity + science metadata are required, written by the producer, forwarded at every level. Governing law: *a read-time fallback is a schema smell — extract once, forward always, re-derive never.*
- **Three-legged conformance** — spec-generated synthetic + frozen-legacy fixtures + writer-side CI in calibrate & zarr-fits; vendored + version-pinned; hard-require.

Full rationale: ADR-0007 and the epic.

## Layout (target — created by the scaffolding slices)

```
spec/             # source of truth — schema.yaml + meta-schema (self-validating DSL)
bindings/rust/    # build.rs codegen → consts (cal-schema migrates here)
bindings/python/  # package; ships + loads the spec as package data
conformance/      # golden fixtures (synthetic + frozen-legacy) + parity tests
cli/              # inspector: validate / describe / diff / emit-reference
docs/             # ADRs + the format spec graduated from chai-software/ARCHITECTURE.md
```
