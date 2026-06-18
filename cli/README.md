# cli/ — inspector (minimal)

The full inspector CLI (`validate` / `describe` / `diff` / `emit-reference`,
typer + rich) lands in Slice 7 (#8). A minimal `validate` surface exists now,
shipped inside the Python binding (stdlib argparse, no extra deps), so Slice 3's
version negotiation is reachable from a command line:

```
python -m zarr_spectral_schema validate <store.zarr>
```

Exit 0 when conformant (newer-minor warnings allowed, printed to stderr), 1 on
errors (missing/mismatched arrays, below-min or newer-major versions).

Primitives also exist as library functions:

- emit-reference → `conformance/generate.py`
- validate / check → `zarr_spectral_schema.validate` / `.check` (Python),
  `zarr_spectral_schema::validate` / `::check` (Rust). `check` returns a
  `ValidationResult { errors, warnings }`; `validate` returns errors only.

This directory stays a placeholder for the full inspector's own entry point (#8).
