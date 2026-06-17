# cli/ — inspector (placeholder)

The inspector CLI (`validate` / `describe` / `diff` / `emit-reference`) lands in
Slice 7 (#8). It keeps the spec machine-readable and doubles as the golden
generator. For now the primitives exist as library functions:

- emit-reference → `conformance/generate.py`
- validate → `zarr_spectral_schema.validate` (Python) / `zarr_spectral_schema::validate` (Rust)

This directory is a placeholder so the target layout from Slice 1 is in place.
