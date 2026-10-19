# cli/ — inspector (placeholder)

The full inspector CLI (`validate`, `describe`, `diff`, `emit-reference`) is
planned in issue 8 (inspector CLI complete). This directory stays a placeholder
for its entry point.

Today a minimal `validate` command ships inside the Python binding:
`python -m zarr_spectral_schema validate <store>`. See
[docs/how-to/validate-a-store.md](../docs/how-to/validate-a-store.md).
The seed of `emit-reference` is `conformance/generate.py`.
