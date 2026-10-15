# CONTEXT — zarr-spectral-schema

Ubiquitous language for the L0–L1 Zarr store contract. The spec
(`spec/schema.yaml`) is the single source of truth; the bindings and this
glossary must agree with it. Design rationale lives in `docs/adr/`.

## Glossary

### Scan
A single unit of observation at L0, stored under `scan_<N>/`. A scan is
**single-purpose**: it is *either* a source scan *or* a calibration scan, never
both. Which one is determined by **SOBSMODE** (the *scan* observing mode, the
`S`-prefix meaning "scan"), which routes every dump of the scan to one purpose.

On disk this purpose appears as exactly one child group — `source/` **XOR**
`calibration/` (see below). A scan carrying both groups, or neither, is not a
valid shape: it is a producer error the validator must reject. The per-record
SOBSMODE partition in the producer (`calibrate` `converter.rs`) is sorting the
dumps of one single-purpose scan, not composing a mixed scan.

### source group / calibration group
The two mutually exclusive L0 obs sub-groups under a scan
(`SOURCE_GROUP` = `source/`, `CALIBRATION_GROUP` = `calibration/`). Contract
rule: **exactly one of {source, calibration} is present per scan** (`oneOf`, not
`anyOf`). When present, all of that group's required arrays are fully enforced.

### Identity (store-and-forward)
Producer-written self-description that the pipeline forwards rather than
re-derives. Governing law (ADR-0007): *extract once, forward always, re-derive
never*. Fields: `frontend`, `pixels_per_array` (canonical = list-of-lists
positionally aligned with the scan's `arrays` order), and `raw_attrs` (verbatim
source-keyword provenance). Required since L0 3 and L1 2.0.

### Placement (scan-or-band)
Where an attribute or group sits, at L0 and L1: at the **scan group** of a
single-frontend scan, or at each **band group** (`band_<B>/`) of a
multi-frontend scan. The validator resolves this generically from the
`band_<B>` children. A scan-or-band attr on the scan group of a multi-frontend
scan is a structure finding. Producers write per band to avoid last-band-wins
misattribution of geometry (ADR 0011).

### Band
The per-frontend group `band_<B>` under a scan, at L0 and L1. `B` is the band
index. A scan with at least one band group is a multi-frontend scan. The layout
`scan_N/band_B/` replaces the virtual scan number `N + 1000000 × b` (ADR 0011).

### Placement target
A scan group, or a band group of a multi-frontend scan. The shared-dimension
rule works per placement target: arrays at one target that share an axis name
agree on its length.

### Dimension names
The axis vocabulary of the arrays: `channel`, `dump`, `receiver` (a pixel of an
array), `array` (a frontend-backend pair), `subscan`, and `load_dump` for the L0
load temperature tables. Each array declares `dims`; producers write them as the
Zarr v3 `dimension_names` field.

### Array family
Arrays that a spec group matches by name prefix instead of by full name, for
example `spectrum_` in the skydiff groups.

### Value constraint
The `equals` field of a spec attr: the attr holds exactly that value, for
example `l1_complete` holds `true`.

### Structure finding / version error
The two classes of validator error (ADR 0009). A structure finding starts with
`[<level>] ` and names one node that breaks the contract. A version error (and a
level-detection error) has no prefix and means the store cannot be read at all.

### Below-floor store
An L0 or L1 store older than `min_supported`. The validators report one version
error for it; readers open it through version-gated shims (ADR 0007, amendment
of 2026-10-14).

### Completeness marker
`l0_complete` and `l1_complete`: the per-unit attr a producer writes last, and a
reader checks before it reads the unit. A unit without its marker is incomplete.

### Not-applicable sentinel
The value of a required field that does not apply: `"NONE"` in a string attr,
JSON null in a number attr, NaN fill in an array. The field is present all the
same (ADR 0007, amendment of 2026-10-14).

### Frame set
The required L1 fields of the spectral, position, intensity and site frame of a
scan or band, for example `specsys`, `ref_lon`, `temperature_scale` and
`site_latitude_deg`.

### Saturated counter
The L0 count fill value 4294967295 (u32::MAX). A reading equal to it is not a
valid count (ADR 0013).

### Count rate
Counts per second. The base of the L1 `gamma` unit, `ct/(K s)`.

### Valid dump count (`n_valid_dumps`)
The number of valid dumps per subscan before padding.

### Producer / consumer
A producer is the one tool that writes a store: zarr-fits writes L0, calibrate
writes L1. A consumer only reads a store and writes its output to a store of
its own (ADR 0008, amendment 1).

### URI reference (store link)
A link from one store to another, for example `source_l0_store`: an absolute URI
(`s3://bucket/x.zarr`), or a relative reference resolved against the root of
the linking store, taken as a directory.

### Real store
Committed producer output under `conformance/real_stores`, as opposed to a
fixture that the spec emits.
