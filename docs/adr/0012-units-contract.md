# 0012 — Units are FITS unit strings, compared exactly

- **Status:** Accepted
- **Date:** 2026-10-15
- **Deciders:** the maintainer (2026-10-14)
- **Relates to:**
  - ccatobs/zarr-spectral-schema#19 (dimensions, units, flag bits and the scientific frame)
  - PR #25 (dimensions, units and the scientific frame)
  - PR #30 (one FITS header per pixel and the gamma unit)
  - epic ccatobs/zarr-spectral-schema#21 (production-grade L0–L1 contract)

## Holds

- **Invariant** (test: `conformance/test_self_description.py::test_array_with_other_units_is_rejected`): an array whose `units` attr differs from the declared unit string is a structure finding.
- **Invariant** (test: `conformance/test_self_description.py::test_array_without_its_declared_units_is_rejected`): an array that declares a unit holds a `units` attr.
- **Default:** a unit is a FITS unit string that astropy parses, for example `K`, `Hz`, `km/s`, `deg`, `ct/(K s)`. A dimensionless quantity declares `""`.
- **Default:** a scalar attr carries its unit as a name suffix, for example `pwv_mm` or `site_latitude_deg`.
- **Default:** labels, indices, scan numbers, counts of items, strings and the `flags` bitmask declare no unit.
- **Incidental:** the validators compare the unit strings byte for byte.

## Revisit if

- A producer writes an equal unit in another spelling, for example `ct / (K s)` for `ct/(K s)`. ccatobs/zarr-spectral-schema#33 (validator and spec findings) holds the question whether the validators compare parsed units.
- A consumer needs units that astropy cannot parse.

## Context

- Before L1 2.0, units lived in names, comments and the heads of the authors. `gamma` held counts per second per kelvin, and nothing on disk said so.
- FITS and astropy are the unit vocabulary of the people who read the data.
- Rust has no astropy. A parsed comparison in both bindings needs a unit parser per language.

## Decision

- An array that holds a physical quantity declares `units` in the spec. The producer writes the string as the array's `units` attr.
- A scalar attr carries its unit in its name.
- The validators compare the unit string exactly.

## Consequences / Trade-offs

- A reader gets the unit of each array from the store, not from a document.
- Both bindings stay free of a unit parser.
- Two spellings of one unit fail the check. The spec text is the one spelling.
- A scalar attr cannot change its unit without a rename.

## Alternatives considered

- **Parsed unit comparison.** Rejected for now: a parser per language, and two parsers can disagree.
- **A unit attr for scalar attrs too.** Rejected: an attr cannot carry attrs in Zarr v3, so the unit would need a second attr per value.
- **UCUM or SI-only strings.** Rejected: FITS is the format the team exports to.

## Status

Accepted.

## Amendments

None.
