# zarr-spectral-schema

Single, versioned **source of truth** for the on-disk Zarr "storage language"
shared by the CCAT/CHAI single-dish heterodyne pipeline:

> **zarr-fits** writes L0, **calibrate** writes L1, **YODA** reads. Today each
> re-declares the contract and they drift. This repo makes the contract one
> hand-authored, language-neutral spec that all three lock to.

Root of the zarr-store dependency chain — a sibling to `calibrate`, `zarr_fits`,
and `yoda` under `chai-software/`, the same *shape* as `data-center` at the root
of the ops-db chain (different domain, not co-located).

## Status

The spec and both bindings are live and tested. The current contract is L0
schema version 3 and L1 schema version 2.2 (epic 21, production-grade L0-L1
contract; 2.1 adds the time stamp per dump of #50, 2.2 the frame velocity (#58, maintainer answers on the frame set)). Start here:

1. Read `docs/adr/0007-zarr-spectral-schema-shared-source-of-truth.md` (shared source of truth). It holds the binding decisions.
2. Skim `spec/schema.yaml` and the conformance tests beside it.
3. Read `CONTEXT.md` for the terms.

## The contract in short

- **Versions.** L0 is version 3 and L1 is version 2.2. Each version has a `min_supported` floor in the spec: L0 3 and L1 2.0. L1 2.1 adds the optional `dump_mjd` array, L1 2.2 fills `v_frame` with the frame correction to LSRK (L0 `velocity_source_kms` minus `v_frame_kms`) and adds three optional scan attrs, so an L1 2.0 store still passes.
- **Old stores.** The validators accept the current major version only. A store below `min_supported` gets one version error, without a prefix. Structure findings follow when the store lacks fields of the current version. A consumer stops on the version error. A newer major version is an error. A newer minor version gives one warning.
- **Layout.** A scan sits at `scan_N/`. A multi-frontend scan holds one `band_B/` group per frontend, at L0 and L1. Scan names match `scan_` plus six or more digits.
- **Arrays.** Every array declares `dims`. Producers write them as the Zarr v3 `dimension_names`. Every array with a physical unit declares `units`. The L1 `flags` array uses the `flag_bits` names. Units compare as exact strings: `ct / (K s)` differs from `ct/(K s)` (ADR 0012, units contract).
- **Identity.** At L0, the scan group of a single-frontend scan, or each band group of a multi-frontend scan, holds an `arrays` attr that lists the array names in the order of the array axis. `pixels_per_array` follows that order. The attrs `backend`, `obs_id` and `spectel1` sit at the same group, and are optional.
- **Offsets.** The per-pixel offsets `pixel_offset_lon` and `pixel_offset_lat` are true-angle offsets (ADR 0014, per-pixel offsets are true-angle offsets).
- **L1 gain.** The L1 attr `image_gain` holds the image share of the total gain. The signal share is `1 - image_gain`.
- **Rounding.** The `keepbits` attr of `spectra` and `t_sys` is an integer from 0 to 52.
- **L0 counts.** `data_5d` is `uint32` with fill value 4294967295.
- **Headers.** `raw_fits_headers` and `frontend_backends` hold one cell per pixel, with dims `[receiver, array, subscan]`.
- **Provenance.** The L1 attr `source_l0_store` is a URI reference to the L0 store. An absolute local path and a `file:` URI are not valid.
- **Object storage.** The validators read a local path or an `s3://` URL. They read `zarr.json` objects only and never a chunk. See the binding READMEs for the install steps.
- **Findings.** A structure finding starts with `[L0] ` or `[L1] ` and names one node. A version error and a level-detection error have no prefix.

## Design in one breath

- **Spec file is the single source of truth** — one hand-authored, language-neutral spec (`spec/schema.yaml`); the Rust crate (`build.rs` codegen) and the Python package (loads the spec as package data) are both *derived* from it. No second hand-maintained copy.
- **Fully-declarative contract** → a generic-interpreter validator, identical in both languages. The lone hand-written per-language seam is string encoding, pinned by conformance tests.
- **v1 scope = L0 + L1 only** — the cross-tool handoffs. L2/L3/provenance stay YODA-owned until the DAG engine writes them. SDFITS (L1/L2) and FITS-map (L3) export kept non-foreclosed.
- **Two-axis versioning** — producer-owned on-disk versions; this repo's package version stays off-disk; consumer checks `>= min` → warn-once on newer minor, error on newer major.
- **Store-and-forward self-description** — identity + science metadata are required, written by the producer, forwarded at every level. Governing law: *a read-time fallback is a schema smell — extract once, forward always, re-derive never.*
- **Three-legged conformance** — spec-generated synthetic fixtures + real producer stores (current versions, zero findings) + writer-side CI in calibrate & zarr-fits; vendored + version-pinned; hard-require.

Full rationale: ADR-0007 and the epic.

## Layout

```
spec/             # source of truth — schema.yaml + meta-schema (self-validating DSL)
bindings/rust/    # build.rs codegen → consts (cal-schema re-exports these)
bindings/python/  # package; ships + loads the spec as package data; `python -m zarr_spectral_schema validate`
conformance/      # generator for synthetic fixtures + real producer stores (real_stores/) + parity tests
cli/              # inspector: minimal validate command; the full inspector is issue 8 (inspector CLI)
docs/             # ADRs + the format spec graduated from chai-software/ARCHITECTURE.md
```
