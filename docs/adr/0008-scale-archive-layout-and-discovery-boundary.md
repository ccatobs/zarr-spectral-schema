# zarr-spectral-schema: scale, archive layout, and the discovery boundary

> Sibling to ADR-0007 (the shared-source-of-truth decision). 0007 settled *what
> a store contains*; this ADR settles *how stores scale into an archive* and
> *where the schema's responsibility ends* — the boundary between the on-disk
> contract and the discovery/transaction layers above it. Authoritative home is
> this repo's `docs/adr/`.

## Context

ADR-0007 froze the L0–L1 contract for a **store**, implicitly sized to the
cross-tool handoff — one calibration run, a few scans. A separate axis was left
unaddressed: the long-term goal is a **flexible L0 Zarr archive on S3** holding
*all* observatory data (including existing per-flight FITS bundles converted
forward), from which a consumer **loads only the groups it needs** to reduce and
calibrate. That raises questions the per-store contract does not answer on its
own:

- Does "a store" mean one observation/flight, or one archive-wide hierarchy?
- How do units that *belong together* (a flight) coexist with units that *arrive
  over time* (streaming during observing)? — the grouping-vs-streaming tension.
- How does a consumer **discover** which groups to load without parsing the whole
  store (thousands of metadata objects on S3)?
- Where does the schema's job stop and an operational layer begin?

Without an explicit boundary, the schema risks either over-reaching (absorbing
catalog/transaction concerns it should not own) or foreclosing the archive (e.g.
baking in POSIX-filesystem assumptions that break on object storage).

## Decision

**Three layers, one boundary.** The schema owns the bottom two and explicitly
does *not* own the third:

| Layer | What it defines | Owner |
|---|---|---|
| **Unit contract** | a scan/subscan subtree: groups, arrays, attrs, required self-description | **this repo** — *scale-free* |
| **Store layout** | how units nest in one Zarr hierarchy (`flight_N/scan_N/band_N/…`) | **this repo**, via `placement` |
| **Discovery / transaction** | cross-store catalog; atomic append/consistency on S3 | **sibling artifacts** — enabled, not owned |

**The contract is scale-free.** A conformant *store* is any Zarr root whose units
conform — 1 scan or 10⁶. The schema sets no size limit; the practical ceiling is
Zarr mechanics (per-store metadata cost), addressed by **consolidated metadata
and/or sharding**, not by the contract. Recommended operational shape:
**per-natural-bundle stores (per flight / per day) + one catalog spanning them**,
not a single mega-store — same contract, but avoids S3 write-contention for no
contract benefit. Converting an existing per-flight FITS bundle is then ordinary
zarr-fits ingestion: the flight becomes a store boundary *or* a top-level
`flight_N/` group via `placement`.

**The schema makes the catalog cheap; the schema is not the catalog.** Because
ADR-0007 mandates store-and-forward self-description, every unit carries its
identity + science metadata as **attributes** — tiny, separate from bulk chunks.
A catalog-builder therefore indexes the whole archive by reading only the
**attribute surface**, never a spectrum chunk. The required-field set frozen in
the identity/science slices *is* this **discovery surface** — it doubles as the
catalog's column set (`arrays`, `pixels_per_array`, `frontend`; `mjd`, `obs_id`,
`aor_id`, `telescope`, `date_obs`; freq coverage, `velocity_frame`,
`temperature_scale`, …). "Required" is thus selected not only for single-reader
self-sufficiency but so the catalog is a **total, well-defined projection** of the
stores. Fields meant to be indexed are flagged in the spec with an **`index`
hint** (a non-binding annotation; it changes nothing on disk, it tells a
catalog-builder which attrs to lift).

**Two-tier discovery.** Discovery has a durable primary and an optional
projection:

1. **Tier 1 — self-contained index object (primary, always).** A manifest
   co-located with the archive on S3, derived purely from the discovery surface,
   **observatory-agnostic**, and sufficient for discovery with **no database
   dependency**. This is the long-term-preservation artifact: the archive plus its
   index is self-describing and survives any DB. Its *schema* is a natural future
   extension of **this repo** (it is a pure projection of the surface this repo
   defines), but is **deferred to v2** — it needs the archive use-case in hand.
2. **Tier 2 — optional database layer (projection).** An ingest of Tier 1 into a
   queryable DB for ops integration. For CCAT/FYST this is **ops-db** (the
   data-center/ops-db tie ADR-0007 named as a v2 reference). For SOFIA — whose
   archive we intend to bring onto the same platform — ops-db is CCAT-tailored and
   would need its own/extended logic; Tier 1 being observatory-agnostic is exactly
   what makes that tractable, because **discovery does not depend on any one
   observatory's DB schema**. Tier 2 is a cache/view that can be rebuilt from
   Tier 1 at any time; it owns no truth.

**Grouping vs. streaming is already resolved by the contract's primitives.** Each
unit is **independently valid** (self-describing) and **atomically markable** (the
completeness marker cal-schema already writes — L0 ingest marker, L1 `l1_complete`).
That is sufficient to serve *both* batch-archival writes (write all units, mark
each) and scan-by-scan streaming appends (write a unit, mark it, update the
index). The completeness marker is therefore promoted to a **first-class contract
element**. What the schema does **not** own is the *consistency mechanism* for
concurrent appends to a shared store — that is Zarr-v3 consolidated-metadata
versioning and/or **Icechunk**, a storage/deployment choice, deliberately
**non-foreclosed**.

**Object-store-agnostic by construction.** The spec assumes **no POSIX
filesystem semantics** — paths are key templates, not directories; no reliance on
listing order, atomic rename, or "directory exists". It stays compatible with
consolidated metadata, sharding, and transactional layers (Icechunk). This is a
hard constraint on how the DSL is written, not a feature to build.

## Constraints this places on the v1 rollout

No change to the Slice-1 tracer. Four constraints carry into later slices:

1. **`placement` (Slice 5, #5) expresses arbitrary hierarchy nesting** —
   `flight → scan → band`, with the multi-frontend scan-or-band rule as one
   property of it — not just the scan-or-band special case. Reserved as a
   commented stub in `spec/schema.yaml` now.
2. **Required attrs (Slices 6–7) are the discovery surface** — a selection
   criterion — and carry an **`index` hint**. Reserved as a commented stub now.
3. **The per-unit completeness marker is a first-class contract element.**
4. **The spec stays object-store-agnostic** — no POSIX assumptions anywhere.

## Consequences

- The schema gains a crisp ceiling on its own responsibility: unit + layout, and
  the *guarantee that discovery is a total projection* — nothing more. Catalog and
  transaction layers are sibling artifacts, enabled-by-design and deferred.
- A future multi-observatory archive (CCAT + SOFIA on one platform) is reachable
  without this repo changing how anyone reads: the observatory-agnostic Tier-1
  index is the common layer; observatory-specific logic lives in the optional
  Tier-2 DB.
- The self-contained-index *schema* is a candidate future addition to this repo
  (v2), keeping the "one source of truth" property extending from store contract
  to archive manifest.
- Choosing Zarr over monolithic FITS pays off precisely here: lazy,
  chunk-addressable, partial reads over object storage are native, so
  "catalog query → group paths → partial S3 read" needs no special machinery in
  the contract.
