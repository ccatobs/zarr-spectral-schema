# zarr-spectral-schema — developer entry points.
#
# `sync` is the load-bearing target: it copies the single hand-authored spec
# (spec/) into each binding's package location. CI runs `make sync` then
# `git diff --exit-code` so a stale copy fails the build — the copies are
# generated artifacts, the root spec/ is the only authored source.

PY_SPEC_DIR := bindings/python/src/zarr_spectral_schema/spec
RUST_SPEC_DIR := bindings/rust/spec

.PHONY: sync check-sync test test-python test-rust conformance lint clean

sync:
	@mkdir -p $(PY_SPEC_DIR) $(RUST_SPEC_DIR)
	cp spec/schema.yaml $(PY_SPEC_DIR)/schema.yaml
	cp spec/meta-schema.json $(PY_SPEC_DIR)/meta-schema.json
	cp spec/schema.yaml $(RUST_SPEC_DIR)/schema.yaml

check-sync: sync
	@git diff --exit-code -- $(PY_SPEC_DIR) $(RUST_SPEC_DIR) \
		|| (echo "ERROR: synced spec copies are stale — run 'make sync' and commit" && exit 1)

test: test-python test-rust

test-python: sync
	cd bindings/python && python -m pytest -q ../../conformance

test-rust: sync
	cd bindings/rust && cargo test --offline

conformance: test-python test-rust

lint:
	cd bindings/python && ruff check .

clean:
	rm -rf bindings/rust/target conformance/.pytest_cache
