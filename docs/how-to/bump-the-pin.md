# Move a consumer to a new contract version

calibrate, zarr-fits and YODA pin this repo by commit SHA. A change to
`spec/schema.yaml` reaches a consumer only when the consumer moves its pin.

## Before you start

- Merge the spec change to `develop` here, with green CI.
- Note the full 40-character SHA of the commit to pin, or of a release tag such as `v0.1.0`.
- A new major version makes older stores fail. Plan the producer change and the reader shims first (see [versioning](../explanation/versioning.md)).

## kalibrate workspace (calibrate and zarr-fits)

1. In the root `Cargo.toml` of kalibrate, set the new SHA in `rev` of the `zarr-spectral-schema` workspace dependency.
2. Run `cargo update -p zarr-spectral-schema`.
3. Run `cargo test --workspace`. The writer-side tests in `cal-io` validate the stores that calibrate writes.
4. Where the contract version changed, update the writer to write the new fields and the new version.

`cal-schema` re-exports the crate with `pub use zarr_spectral_schema::*`, so code that uses `cal_schema::*` needs no change for a new constant.

To test against a local working copy before you push, swap the dependency for a path:

```toml
zarr-spectral-schema = { path = "../zarr-spectral-schema/bindings/rust" }
```

Do not commit the path form.

## YODA

1. In `pyproject.toml` of YODA, set the new SHA in the `zarr-spectral-schema @ git+https://...@<sha>#subdirectory=bindings/python` dependency.
2. Reinstall YODA into its environment.
3. Run the YODA tests.

To test against a local working copy, install it over the pinned version: `pip install -e ../zarr-spectral-schema/bindings/python`.

## Refresh the real stores

When a producer writes a new version, refresh `conformance/real_stores/` here.
`conformance/real_stores/README.md` holds the commands. Copy the producer output
whole, except `.calibrate.lock`.
