# Validate a store

Check an L0 or L1 Zarr v3 store against the contract. The validator detects the
level from the root attr `l0_schema_version` or `cal_schema_version`.

## From the command line

1. Install the Python binding from a checkout: `pip install ./bindings/python`.
2. Run the validator:

   ```sh
   python -m zarr_spectral_schema validate path/to/store.zarr
   ```

- Exit 0: the store conforms. Warnings, for example a newer minor version, go to stderr.
- Exit 1: the store has errors. Each error goes to stderr with the prefix `error: `.

The package is not on PyPI. To install from git, pin a SHA:

```sh
pip install "zarr-spectral-schema @ git+https://github.com/ccatobs/zarr-spectral-schema@<sha>#subdirectory=bindings/python"
```

## From Python

```python
import zarr_spectral_schema as zss

errors = zss.validate("path/to/store.zarr")   # [] when the store conforms
result = zss.check("path/to/store.zarr")
result.ok        # True when result.errors is empty
result.warnings  # warnings do not fail a store
```

## From Rust

```rust
use std::path::Path;

let result = zarr_spectral_schema::check(Path::new("path/to/store.zarr"));
if !result.ok() {
    for e in &result.errors { eprintln!("{e}"); }
}
```

`validate(&Path)` returns the errors only. In the kalibrate workspace, call it as
`cal_schema::validate`.

## Validate a store on S3

The validators read `zarr.json` objects only and never a chunk.

1. Set the standard AWS variables: `AWS_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `AWS_DEFAULT_REGION`.
2. Python: install the `s3` extra, `pip install "./bindings/python[s3]"`. Pass the `s3://bucket/store.zarr` URL to the CLI, `validate` or `check`.
3. Rust: enable the cargo feature `s3` (off by default). Call `check_url("s3://bucket/store.zarr")`.
4. For an `http://` endpoint, such as a local test server, the Rust binding also needs `AWS_ALLOW_HTTP=true`.

The Rust S3 check blocks on its own tokio runtime, on a thread of its own. A caller inside a tokio runtime may call it.

## Read the findings

- `[L0] ` or `[L1] ` prefix: a structure finding. It names one node that breaks the contract.
- No prefix: a version error or a level-detection error. Stop reading the store. See [versioning](../explanation/versioning.md).
