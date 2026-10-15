# 0009 — Validator errors come in two classes

- **Status:** Accepted
- **Date:** 2026-10-15
- **Deciders:** the maintainer
- **Relates to:**
  - ccatobs/zarr-spectral-schema#18 (accept real producer output, reject broken output)
  - PR #23 (accept real producer output)
  - epic ccatobs/zarr-spectral-schema#21 (production-grade L0–L1 contract)
  - ccatobs/kalibrate#82 (make the calibrated output self-describing and safe to rerun)

## Holds

- **Invariant** (test: `conformance/test_real_output.py::test_structure_findings_carry_the_level_prefix`): every structure finding starts with `[<level>] `, for example `[L0] ` or `[L1] `.
- **Invariant** (test: `conformance/test_version_negotiation.py::test_below_floor_is_one_version_error`): a version error does not start with `[`.
- **Default:** a level-detection error carries no prefix, like a version error.
- **Default:** a consumer tells the two classes apart by the prefix and decides per class whether to stop.
- **Incidental:** calibrate treats only unprefixed errors as fatal and logs structure findings as a warning (`crates/cal-io/src/schema.rs`, kalibrate `epic/21`).

## Revisit if

- ccatobs/kalibrate#131 (decide whether a schema structure finding fails the calibrate run) makes calibrate fail on a structure finding. The prefix then stops being load-bearing for calibrate.
- The validators return structured errors (a class field) instead of strings.

## Context

- Both bindings return a list of strings. A consumer cannot read a class from a string except by its text.
- A version error means the consumer cannot read the store at all: the store is below the floor or a major version ahead.
- A structure finding means one node of a readable store breaks the contract. A consumer may still read the other units.
- calibrate (ccatobs/kalibrate#82) and yoda read the class from the prefix. A change of the prefix breaks both without an error, because a string still parses.

## Decision

The validators report two error classes. A structure finding starts with `[<level>] `. A version error and a level-detection error carry no prefix. The prefix is part of the interface between this repo and its consumers.

## Consequences / Trade-offs

- A consumer can stop on a version error and go on past a structure finding, with one `startswith` check.
- A change of the prefix text is a breaking change of the bindings. The bindings carry no version of their own, so the pin SHA in each consumer is the only guard.
- Structured errors would be cleaner. They would change the return type of both bindings and every consumer.

## Alternatives considered

- **One class, all errors fatal.** Rejected: one bad unit in a streaming store would block every complete unit of the store.
- **Structured error objects.** Rejected for now: a new return type in two languages, and calibrate and yoda already parse strings.
- **Separate lists for the two classes.** Rejected for now: same cost as structured errors, and `check()` already separates warnings from errors.

## Status

Accepted.

## Amendments

None.
