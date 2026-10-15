# 0010 — Chunk independence binds every reader; the layout and consolidated metadata are recommendations

- **Status:** Accepted
- **Date:** 2026-10-15
- **Deciders:** the maintainer
- **Relates to:**
  - ccatobs/zarr-spectral-schema#20 (object storage and production volume)
  - PR #24 (object storage and production volume)
  - epic ccatobs/zarr-spectral-schema#21 (production-grade L0–L1 contract)
  - ADR 0008 (scale, archive layout and the discovery boundary)

## Holds

- **Invariant** (test: `conformance/test_storage_contract.py::test_store_with_non_default_chunk_layout_is_green`): a store with a chunk or shard layout other than the writer default passes both validators.
- **Invariant** (test: `conformance/test_object_storage.py::test_validator_reads_only_zarr_json_objects`): the validators read only `zarr.json` objects and open no chunk key.
- **Default:** a reader gives the same result for any chunk shape and any shard shape.
- **Default:** a producer writes the recommended layout from `spec/schema.yaml`: one shard per pixel and subscan, inner chunks of about 1 MB.
- **Default:** a finished store may hold zarr-python consolidated metadata, written by the producer as its last write. The validators ignore it.
- **Incidental:** the conformance set holds `l0_sharded.zarr` and `l1_sharded.zarr` as the non-default layout.

## Revisit if

- A reader needs a chunk rule for a measured performance target that no layout recommendation meets.
- The Zarr v3 core spec adopts consolidated metadata. The validators could then check it.
- calibrate keeps its own chunk check on read (ccatobs/kalibrate#102, the chunk layout of L1). The check conflicts with the invariant.

## Context

- A 10-minute on-the-fly scan with 2 × 64 pixels and 64k channels at 10 Hz is large. With one chunk per array, its spectra make one object of 403 GB.
- An object store charges per request and has no partial write. Readers of one pixel or one subscan want one object per pixel and subscan.
- The tools use two Zarr libraries. zarr-fits and calibrate write with zarrs in Rust. yoda reads with zarr-python.
- Consolidated metadata is a zarr-python convention, not part of the Zarr v3 core spec. zarrs writes it but does not read it. A streaming store gains units after each write, so a root cache is stale between appends.

## Decision

- Chunk independence binds every reader. The contract states no chunk rule.
- The layout in the spec header is a recommendation. It binds no producer.
- Consolidated metadata is optional and not authoritative. The validators check the `zarr.json` of each node.

## Consequences / Trade-offs

- A producer can change its chunk layout without a schema version bump.
- A reader that assumes the chunk shape of one writer is a bug, even when its tests pass on that writer's stores.
- A reader on object storage that skips consolidated metadata pays one listing per group and one GET per node.
- A reader that uses consolidated metadata still checks the completeness marker of each unit.

## Alternatives considered

- **A required chunk layout.** Rejected: a rule would bind every producer to one Zarr library's sharding support and would reject every existing store.
- **Required consolidated metadata.** Rejected: zarrs does not read it, a streaming store has no stable root, and no real store holds it.
- **Consolidated metadata as the source of truth for the validators.** Rejected: a stale cache would hide a missing or changed node.

## Status

Accepted.

## Amendments

None.
