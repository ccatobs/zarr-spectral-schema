# Versioning and version negotiation

The contract has two version axes. They answer different questions, so they move
independently (ADR 0007, one spec as the source of truth).

## Axis 1: the package version

`spec_version` in `spec/schema.yaml`, and the version of the crate and the Python
package, name a release of this repo. It is `0.1.0` today. This version never
appears in a store. A consumer pins a commit of this repo, not a package version.

## Axis 2: the on-disk schema versions

The producer of a store writes its level version as a root attr:

| Level | Root attr | On-disk type | Current | `min_supported` |
|---|---|---|---|---|
| L0 | `l0_schema_version` | JSON integer | 3 | 3 |
| L1 | `cal_schema_version` | JSON string `major.minor` | 2.7 | 2.0 |

The producer owns its level version. calibrate bumps the L1 version at its own
pace. The L0 version has no minor axis, because it is an integer.

## Negotiation

Both validators compare the store version with the spec:

| Store version | Result |
|---|---|
| Below `min_supported` | One version error. Structure findings follow for the fields the store lacks. |
| From `min_supported` up to the current version | No version finding. |
| Newer minor, same major | One warning. Fields the reader does not know are ignored. The store passes. |
| Newer major | Version error. |

A version error has no prefix and means the consumer cannot read the store. A
consumer stops on it (ADR 0009, validator error classes).

## Why the floor sits below the current version

A minor step is additive: it adds optional fields only. A store written before
the step lacks those fields and is still correct, so the floor stays at the
first minor of the major. L1 2.1 to 2.7 added optional arrays and attrs, so L1
2.0 stores pass. The real stores under `conformance/real_stores/` are L1 2.0.

A major step changes or removes a field. Old stores then break the contract. The
validators accept the current major only. A reader opens an older store through
a version-gated shim (ADR 0007, amendment of 2026-10-14).

## Changes inside an unreleased major

A change inside a major version that no producer has released needs no further
bump (ADR 0007, amendment of 2026-10-15). L0 3 took the `uint32` counts of ADR
0013 (L0 counts are uint32) this way.
