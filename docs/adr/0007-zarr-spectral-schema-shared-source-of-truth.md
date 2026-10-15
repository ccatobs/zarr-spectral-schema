# zarr-spectral-schema: one hand-authored spec as the source of truth for the L0–L1 store contract

> Authoritative home is the `zarr-spectral-schema` repo's own `docs/adr/` once
> that repo exists (it binds calibrate, zarr-fits, and YODA, so it sits above
> any one of them). Staged here alongside ADR-0001…0006 during design.
> Supersedes the original framing of yoda#34; PRD-adjacent to yoda#35.

## Context

The on-disk Zarr "storage language" is written by **zarr-fits** (L0) and
**calibrate** (L1) and read by **YODA**. On the Rust side this contract is
already single-sourced: `calibrate/crates/cal-schema` is the source of truth for
array names, group names, and schema versions, consumed by both writers via
`pub use cal_schema::*`. The drift is one-sided: **YODA re-declares the same
contract by hand** in `io/schema.py` and has fallen behind (it reads L1 `"1.0"`
while calibrate writes `"1.3"`), and its version check is fiction — it requires
`cal_schema_version` to be *present* but never compares the value, while
`pipeline_runner.py` writes a fake `"1.0"` to masquerade as L1. Gaps get patched
reactively on the consumer side (the `pixel_labels.py` fallback chain; the
`pwv`/`pwv_mm` and dict-vs-list normalisations). This is the drift-tax: an
unbounded, recurring cost paid in the reader.

## Decision

Stand up **`zarr-spectral-schema`** as a sibling repo under `chai-software/` —
the root of the zarr-store chain, the same *shape* as `data-center` at the root
of the ops-db chain, but a separate domain (not co-located with it). It owns the
**data-interchange contract** (on-disk store shape), never the recipe/DAG config
(calibrate runs no recipes; see ADR-0001/0006).

**One hand-authored, language-neutral spec file is the single source of truth.** The Rust consts
and the Python tables are both **generated** from it — there is no second
hand-maintained copy to drift. The entire on-disk contract is **declarative**,
including the multi-frontend `band_N` "this attr is allowed at scan *or* band
level" rule, expressed as a `placement` field. Because the contract is data, the
**validator is a generic interpreter** — written once per language, judgment-free,
so the two cannot diverge. The single irreducibly per-language seam is the
*write mechanism* for string arrays (`str_array`'s library-version-conditional
encoding); it is hand-written in each binding and pinned by conformance tests.

**v1 freezes L0 + L1 only** — exactly `cal-schema`'s current footprint, i.e. the
two real cross-tool writer→reader handoffs. Everything YODA writes (`reduce/`,
L2, L3, provenance/quality blocks) stays YODA-owned and **provisional** until the
DAG engine (ADR-0001…0006) has written it once; it graduates into a later schema
version then. v1 is chosen so that future **SDFITS** (L1/L2) and **FITS-map** (L3)
export remain possible — the required metadata below is precisely what those
exporters will need.

**Versioning** adopts the two-axis model (`ARCHITECTURE.md §3.9.5`) verbatim:
producer-owned on-disk versions (`l0_schema_version`, `cal_schema_version`) plus a
per-instrument `profile_version`; the spec **repo's** package version never
appears on disk. A consumer checks `>= min`, **warns once** on a newer *minor*
(additive — unknown fields ignored), **errors** on a newer *major*. YODA's check
becomes real and lands **warn-first**; the faked `"1.0"` is removed. This is
asymmetric, not lockstep — calibrate keeps bumping L1 at its own cadence.

**Identity is store-and-forward, required, and single-representation.** `arrays`,
`pixels_per_array` (canonical = a list-of-lists parallel to the ordered `arrays`,
retiring both the L0 dict's non-deterministic ordering and YODA's `_normalise_ppa`),
and `frontend` (stored and forwarded, its longest-common-prefix extractor run
**once** at L0, retiring calibrate's L1 re-derivation) are written by the
authoritative producer and **carried verbatim L0→L1→L2** so every store reads
alone. The `pixel_labels.py` fallback chain demotes to a version-gated **legacy
shim** (strategies 2–4) plus a UI default (strategy 5); it is dead code for
conformant stores.

**The required contract is full scientific self-description**, promoted from
convention to contract and written **strict-unconditional with explicit
"not-applicable" sentinels** (every required field always present; `sideband_mode`
/`temperature_scale` say *why* a value is unity): `velocity_convention` +
`velocity_frame`, `temperature_scale` (=`TA*`) + `eta_mb`/`eta_fss`/
`gain_el_correction`, `observatory_lon/lat/alt`, `sideband_mode` + `sideband_gain`,
with `tau_*` documented as **zenith** opacity. Deferred (stay optional): per-pixel
`eta_mb`, `noise_expected`, housekeeping. Source-keyword **precedence-aliases**
(`from: [PIXOFFX, RXDX]`) are declared in the spec and resolved **once at
ingestion**, with raw keywords preserved verbatim in a required `raw_attrs` block.
Ingestion does **precedence only, never `else_compute`** — derived/computed values
(e.g. the `mjd_gap` instrument-grouping for PCA epochs) stay **reduction-side
roles**, recorded as computed-vs-keyword and overridable.

**Conformance is a three-legged, vendored, version-pinned stool**, run in every
relevant CI: spec-generated **synthetic** reference stores (binding parity — they
cannot drift from the spec because the spec emits them), a small set of **frozen
real legacy** stores (back-compat — existing stores keep validating), and
**writer-side** jobs in calibrate and zarr-fits that write a store and validate it
against the vendored spec binding (producer parity). New required fields
**hard-require** immediately — the writer's conformance job fails until it
conforms, which is the desired forcing function at this scale. An inspector CLI
(`validate` / `describe` / `diff` / `emit-reference`) keeps the spec
machine-readable and doubles as the golden generator.

## The governing principle

**A read-time fallback is a schema smell.** If the producer knows a fact, the
schema *requires* it, the producer *writes* it, and the reader only *reads*.
Reader-side logic is legitimate only for genuine UI/environmental defaults or as a
back-compat shim for data written before the field was required. Said the other
way: **extract once, forward always, re-derive never** — self-description travels
at every level; bulk intermediate data need not (ADR-0005). This is the
legitimate way to retire the "calibrate metadata gap — workaround in YODA, not
upstream" guidance: not by hacking calibrate, but by making the fact a required
field of a shared contract that calibrate (already, since the native-label work)
satisfies.

## Consequences

- The drift becomes a **bounded, deliberate versioning cost** at release
  boundaries instead of an unbounded reader-side tax.
- A future multi-observatory **core/extension** split can happen *inside* the repo
  without calibrate or YODA changing how they read — they keep reading the names
  they read. Genericity (canonical vocabulary, role-vs-name resolution, IVOA/ObsCore
  projection, focal-plane geometry as namespaced extension) stays **deferred but
  non-foreclosed**, to be settled with a real second observatory in hand.
- A migration window exists where new stores resolve identity/aliases upstream
  while legacy stores still need the read-side shim; the shim therefore stays,
  gated on schema version, rather than being deleted.

## Amendments

- 2026-10-14: The validators accept only the current major version of each level. A store below `min_supported` gets one version error. A reader opens such a store through a version-gated shim. This replaces the conformance leg of frozen real legacy stores that keep validating: the real stores under `conformance/real_stores` are now current producer output (ccatobs/zarr-spectral-schema PR #31, real stores at L0 3 and L1 2.0). Maintainer approval of 2026-10-14 in ccatobs/zarr-spectral-schema#19 (dimensions, units and the scientific frame).
- 2026-10-14: Not-applicable sentinels. A required field is present in every store, also where it does not apply. A string then holds `"NONE"`, a number holds JSON null, and an array holds NaN as its fill value. NaN is not an attr value. Maintainer decision in ccatobs/zarr-spectral-schema#19.
- 2026-10-15: A spec array may declare `fill_value`. Both validators compare it exactly with the Zarr v3 metadata of the array, an integer fill value as a JSON integer. The generic interpreter of each binding reads the field like any other spec field (ccatobs/zarr-spectral-schema PR #29, uint32 L0 counts and flag bit 8).
- 2026-10-15: A change inside a major version that is not yet released needs no further version bump. L0 3 took the `uint32` counts of ADR 0013 (L0 counts are uint32) while L0 3 existed only on `epic/21`. Maintainer decision in ccatobs/zarr-spectral-schema#27 (L0 count dtype for counts of 2^31 and above).
