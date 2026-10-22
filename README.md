# zarr-spectral-schema

The on-disk Zarr contract for L0 and L1 spectral data of the CCAT/CHAI
single-dish heterodyne pipeline. zarr-fits writes L0, calibrate writes L1, and
YODA reads both. All three lock to one hand-authored, language-neutral spec:
`spec/schema.yaml`.

## Status

| | |
|---|---|
| Release | `v0.1.0` (tag on `develop`, commit 1041d17) |
| L0 schema version | 3 (`min_supported` 3) |
| L1 schema version | 2.7 (`min_supported` 2.0) |
| Bindings | Rust crate `bindings/rust/`, Python package `bindings/python/`; same contract, same findings |

L1 2.1 adds `dump_mjd`, L1 2.2 the frame velocity in `v_frame`, L1 2.3
`pwv_spectrum` and `tau_signal_spectrum`, L1 2.4 the line range
`line_ranges_kms` (also an optional L0 3 scan attr), L1 2.5 the reference
position in `ref_lon` and `ref_lat` (from the optional L0 3 scan attrs
`ref_lon_deg`, `ref_lat_deg`, `coordsys`), L1 2.6 the transmission
`transmission_signal_spectrum` that calibrate applied to each spectrum, and L1 2.7 the
reference airmass and the two sky-emission templates (`airmass_reference`,
`sky_dt_dairmass_spectrum`, `sky_dt_dpwv_spectrum`). Each step is additive, so an L1 2.0
store still passes. The `versions` section of the spec holds the full history.

## Consumers

| Consumer | How it pins | What it uses |
|---|---|---|
| kalibrate workspace (calibrate, zarr-fits) | `Cargo.toml` workspace dependency, git `rev` 1041d17 | `cal-schema` re-exports the crate (`pub use zarr_spectral_schema::*`) for `zarr-fits-core` and `cal-io`. Writer-side tests in `cal-io` call `cal_schema::validate` on the stores they write. |
| YODA | `pyproject.toml` dependency, git SHA 1041d17, `subdirectory=bindings/python` | `load_spec`, the constants, and `check`/`validate` in `yoda inspect`. L2 and L3 stay YODA-owned. |

To move a consumer to a new contract version, see
[docs/how-to/bump-the-pin.md](docs/how-to/bump-the-pin.md).

## Documentation

- How-to: [validate a store](docs/how-to/validate-a-store.md)
- How-to: [move a consumer to a new contract version](docs/how-to/bump-the-pin.md)
- Explanation: [versioning and version negotiation](docs/explanation/versioning.md)
- Reference: `spec/schema.yaml` is the reference. Each field carries its comment there. No generated reference page exists yet (issue 8, inspector CLI with `emit-reference`).
- Glossary: [CONTEXT.md](CONTEXT.md)
- Decisions: [docs/adr/](docs/adr/). Start with ADR 0007 (one spec as the source of truth).
- Open work: [ROADMAP.md](ROADMAP.md) and epic issue 1 (versioned source of truth for the L0–L1 contract).

## The contract in short

- **Layout.** A scan sits at `scan_N/`. A multi-frontend scan holds one `band_B/` group per frontend, at L0 and L1. Scan names match `scan_` plus six or more digits.
- **Arrays.** Every array declares `dims`. Producers write them as the Zarr v3 `dimension_names`. Every array with a physical unit declares `units`. The L1 `flags` array uses the `flag_bits` names. Units compare as exact strings: `ct / (K s)` differs from `ct/(K s)` (ADR 0012, units contract).
- **Identity.** At L0, the scan group of a single-frontend scan, or each band group of a multi-frontend scan, holds an `arrays` attr that lists the array names in the order of the array axis. `pixels_per_array` follows that order. The attrs `backend`, `obs_id` and `spectel1` sit at the same group, and are optional.
- **Offsets.** The per-pixel offsets `pixel_offset_lon` and `pixel_offset_lat` are true-angle offsets (ADR 0014, per-pixel offsets are true-angle offsets).
- **L1 gain.** The L1 attr `image_gain` holds the image share of the total gain. The signal share is `1 - image_gain`.
- **Rounding.** The `keepbits` attr of `spectra` and `t_sys` is an integer from 0 to 52.
- **L0 counts.** `data_5d` is `uint32` with fill value 4294967295 (ADR 0013, L0 counts are uint32).
- **Headers.** `raw_fits_headers` and `frontend_backends` hold one cell per pixel, with dims `[receiver, array, subscan]`.
- **Provenance.** The L1 attr `source_l0_store` is a URI reference to the L0 store. An absolute local path and a `file:` URI are not valid.
- **Object storage.** The validators read a local path or an `s3://` URL. They read `zarr.json` objects only and never a chunk.
- **Findings.** A structure finding starts with `[L0] ` or `[L1] ` and names one node. A version error and a level-detection error have no prefix (ADR 0009, validator error classes).

## Design in one breath

- **One spec.** `spec/schema.yaml` is the only hand-authored copy. The Rust crate generates its constants from it in `build.rs`. The Python package ships it as package data.
- **Declarative contract.** One generic validator per language interprets the spec. String encoding is the only hand-written per-language part, and conformance tests pin it.
- **Scope is L0 and L1.** L2, L3 and their provenance stay YODA-owned until the DAG engine writes them.
- **Two-axis versioning.** See [docs/explanation/versioning.md](docs/explanation/versioning.md).
- **Store-and-forward self-description.** The producer writes identity and science metadata once. Every later level forwards them. A read-time fallback is a schema smell: extract once, forward always, re-derive never.
- **Three-legged conformance.** Synthetic fixtures that the spec emits, real producer stores under `conformance/real_stores/`, and writer-side tests in the kalibrate workspace.

## Layout

```
spec/             # source of truth: schema.yaml + meta-schema.json (validates the spec itself)
bindings/rust/    # crate; build.rs generates the constants; spec/ is a synced copy
bindings/python/  # package; ships the spec as package data; spec/ is a synced copy
conformance/      # synthetic fixture generator, real producer stores, tests of both bindings
cli/              # placeholder for the full inspector (issue 8)
docs/             # how-to, explanation, ADRs, logbook
```

## Develop

- Edit `spec/schema.yaml` only. Do not edit the synced copies under `bindings/`.
- `make sync` copies the spec into both bindings. CI runs `make check-sync` and fails on a stale copy.
- `make test` runs the conformance tests of both bindings. `make lint` runs ruff on the Python binding.
- The Rust tests call `conformance/generate.py`, so they need the `emit` extra: `pip install "./bindings/python[emit]"`.
