"""Emit synthetic Zarr v3 reference stores directly from the spec.

This is the seed of the inspector CLI's `emit-reference` (Slice 7, #8) and the
"binding parity" leg of the conformance stool: because the store is generated
*from* the spec, it cannot drift from it. Both bindings then validate the same
emitted artifact.

Usage:
    python conformance/generate.py <out_dir>

Writes:
    <out_dir>/valid.zarr     — conformant store (every required field present)
    <out_dir>/missing_data_5d.zarr — negative store (required array omitted)
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
import zarr

SPEC = Path(__file__).resolve().parent.parent / "spec" / "schema.yaml"

# Declarative dtype names map straight onto zarr v3 data types; `string` and the
# integer/float names are all accepted by create_array(dtype=...) and round-trip
# to the same on-disk `data_type` the writers emit (probed against zarr 3.2).


def load_spec() -> dict:
    return yaml.safe_load(SPEC.read_text(encoding="utf-8"))


def emit(out_dir: Path, omit: set[str] | None = None) -> Path:
    """Emit one store from the spec, optionally omitting array paths (for negatives)."""
    omit = omit or set()
    spec = load_spec()
    root = zarr.open_group(store=str(out_dir), mode="w", zarr_format=3)

    # Producer-owned version attributes, encoded per `on_disk`.
    for attr_name, vspec in spec.get("versions", {}).items():
        value = int(vspec["value"]) if vspec["on_disk"] == "integer" else str(vspec["value"])
        root.attrs[attr_name] = value

    # Structural arrays: group '<g>' / array '<g>/<a>', shape = unit-length in
    # every dimension (ndim from the spec). Content is irrelevant to the L0–L1
    # *contract*; only metadata is validated.
    for level in spec.get("levels", {}).values():
        for group_name, group in level.get("groups", {}).items():
            g = root.require_group(group_name)
            for array_name, aspec in group.get("arrays", {}).items():
                rel = f"{group_name}/{array_name}"
                if rel in omit:
                    continue
                shape = tuple([1] * aspec["ndim"])
                g.create_array(name=array_name, shape=shape, dtype=aspec["dtype"])
    return out_dir


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python conformance/generate.py <out_dir>", file=sys.stderr)
        raise SystemExit(2)
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    emit(out / "valid.zarr")
    emit(out / "missing_data_5d.zarr", omit={"source/data_5d"})
    print(f"emitted: {out/'valid.zarr'}")
    print(f"emitted: {out/'missing_data_5d.zarr'}")


if __name__ == "__main__":
    main()
