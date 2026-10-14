"""Validators on object storage (Python binding) — #20.

The stores sit on an in-process S3 endpoint (moto, ``s3_server.py``). The
validator takes an ``s3://`` URL, reads only ``zarr.json`` objects and gives the
verdict it gives for the same store on a local disk. The Rust binding runs the
same checks against the same endpoint and compares its verdict with this one
(bindings/rust/tests/object_storage.rs).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "bindings" / "python" / "src"))
sys.path.insert(0, str(_ROOT / "conformance"))

import generate
import s3_server
import zarr_spectral_schema as zss

REAL = _ROOT / "conformance" / "real_stores"


@pytest.fixture(scope="module")
def local(tmp_path_factory) -> dict[str, Path]:
    out = tmp_path_factory.mktemp("object_storage")
    generate.emit_l1_single(out / "l1_single.zarr")
    generate.emit_l1_single(out / "miss_array.zarr", omit={"scan_000001/spectra"})
    generate.emit_l0(out / "l0_sharded.zarr", sharded=True)
    stores = {p.name: p for p in out.iterdir()}
    stores["ORION_KL"] = REAL / "calibrate" / "ORION_KL"
    stores["SOFIA_UPGREAT_ORION_KL.zarr"] = (
        REAL / "zarr" / "SOFIA_UPGREAT_ORION_KL.zarr"
    )
    return stores


@pytest.fixture(scope="module")
def s3(local):
    server = s3_server.S3Server()
    for name, store in local.items():
        server.upload(store, name)
    saved = dict(os.environ)
    os.environ.update(s3_server.env(server.endpoint))
    yield server
    os.environ.clear()
    os.environ.update(saved)
    server.stop()


@pytest.mark.parametrize(
    "name",
    [
        "l1_single.zarr",
        "miss_array.zarr",
        "l0_sharded.zarr",
        "ORION_KL",
        "SOFIA_UPGREAT_ORION_KL.zarr",
    ],
)
def test_s3_store_gets_the_verdict_of_the_local_store(s3, local, name):
    remote = zss.check(f"s3://{s3_server.BUCKET}/{name}")
    assert remote.errors == zss.check(local[name]).errors
    assert remote.ok == (name != "miss_array.zarr"), remote.errors


def test_validator_reads_only_zarr_json_objects(s3):
    s3.take_requests()
    for name in ["ORION_KL", "SOFIA_UPGREAT_ORION_KL.zarr", "l0_sharded.zarr"]:
        assert zss.check(f"s3://{s3_server.BUCKET}/{name}").ok
    served = s3.take_requests()
    assert any(path.endswith("/zarr.json") for _, path, _ in served), served
    assert s3.offending(served) == []
