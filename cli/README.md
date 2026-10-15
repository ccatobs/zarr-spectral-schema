# cli/ — inspector (minimal)

The full inspector CLI (`validate` / `describe` / `diff` / `emit-reference`,
typer + rich) is planned in issue 8 (inspector CLI). A minimal `validate`
command ships inside the Python binding (stdlib argparse, no extra
dependencies):

```
python -m zarr_spectral_schema validate <store.zarr | s3://bucket/store.zarr>
```

- Exit 0 when the store is conformant. Newer-minor warnings go to stderr.
- Exit 1 when the store has errors. Errors are structure findings (prefix `[L0] ` or `[L1] `), a version below `min_supported`, a newer major version, or no detectable level.
- An `s3://` URL needs the `s3` extra of the Python binding and the `AWS_*` environment variables. See `bindings/python/README.md`.

Primitives also exist as library functions:

- emit-reference: `conformance/generate.py`
- validate and check: `zarr_spectral_schema.validate` and `.check` (Python), `zarr_spectral_schema::validate` and `::check` (Rust). `check` returns a `ValidationResult { errors, warnings }`. `validate` returns errors only.

This directory stays a placeholder for the full inspector's own entry point.
