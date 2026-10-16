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
BAD_SKYDIFF = {
    "scan_000001/skydiff/spectrum_0": {
        "dtype": "float32",
        "ndim": 2,
        "dims": ["channel", "receiver"],
    }
}


@pytest.fixture(scope="module")
def local(tmp_path_factory) -> dict[str, Path]:
    out = tmp_path_factory.mktemp("object_storage")
    generate.emit_l1_single(out / "l1_single.zarr")
    generate.emit_l1_single(out / "miss_array.zarr", omit={"scan_000001/spectra"})
    generate.emit_l0(out / "l0_sharded.zarr", sharded=True)
    # Three errors when every node is readable (skydiff/spectrum_0 dtype, ndim
    # and dimension_names).
    generate.emit_l1_single(out / "l1_bad_skydiff.zarr", array_overrides=BAD_SKYDIFF)
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


# Real stores that are not green until #37: the real stores predate #34 (L0 arrays, per-band identity, image_gain); ccatobs/zarr-spectral-schema#37 writes them again.
PENDING_37 = {"ORION_KL", "SOFIA_UPGREAT_ORION_KL.zarr"}


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
    # The committed real stores (L0 3, L1 2.0) are green.
    if name not in PENDING_37:
        assert remote.ok == (name != "miss_array.zarr"), remote.errors


def test_validator_reads_only_zarr_json_objects(s3):
    s3.take_requests()
    for name in ["ORION_KL", "SOFIA_UPGREAT_ORION_KL.zarr", "l0_sharded.zarr"]:
        # Every node read, and every store green.
        errors = zss.check(f"s3://{s3_server.BUCKET}/{name}").errors
        if name not in PENDING_37:
            assert errors == [], errors
    served = s3.take_requests()
    assert any(path.endswith("/zarr.json") for _, path, _ in served), served
    assert s3.offending(served) == []
    # One GET per zarr.json, with no HEAD before it (#33).
    assert [r for r in served if r[0] == "HEAD"] == []


# ── an unreadable node is an error, never a silent gap or an exception ─────

# (store, object key or listing prefix answered with 403, expected errors);
# the same cases as DENY_CASES in bindings/rust/tests/object_storage.rs.
DENY_CASES = [
    (
        "l1_bad_skydiff.zarr",
        "l1_bad_skydiff.zarr/scan_000001/skydiff/",
        ["[L1] 'scan_000001/skydiff/': cannot list"],
    ),
    (
        "l1_single.zarr",
        "l1_single.zarr/scan_000001/t_int/zarr.json",
        # The node exists: no missing-node error beside the read error (#33).
        ["[L1] 'scan_000001/t_int/zarr.json': cannot read metadata"],
    ),
    ("l1_single.zarr", "l1_single.zarr/", ["'/': cannot list"]),
    (
        "l1_single.zarr",
        "l1_single.zarr/zarr.json",
        ["'zarr.json': cannot read metadata"],
    ),
]


@pytest.mark.parametrize("name, deny, expected", DENY_CASES)
def test_unreadable_node_is_an_error(s3, name, deny, expected):
    s3.deny.add(deny)
    try:
        errors = zss.check(f"s3://{s3_server.BUCKET}/{name}").errors
    finally:
        s3.deny.clear()
    assert errors == expected


def test_unreachable_endpoint_is_an_error_not_an_exception(s3, monkeypatch):
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://127.0.0.1:9")  # nothing listens
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    result = zss.check(f"s3://{s3_server.BUCKET}/l1_single.zarr")
    assert result.errors == ["'zarr.json': cannot read metadata"]


# ── each check reads the store as it is now ────────────────────────────────


def test_second_check_sees_a_scan_added_after_the_first(s3, local):
    url = s3.upload(local["l1_single.zarr"], "l1_growing.zarr")
    assert zss.check(url).ok
    s3.put(
        "l1_growing.zarr/scan_000002/zarr.json",
        b'{"zarr_format": 3, "node_type": "group", "attributes": {}}',
    )
    assert any("scan_000002" in e for e in zss.check(url).errors)


# ── one input grammar: a scheme:// argument is a URL with a %-encoded path ──


def test_percent_encoded_url_names_the_decoded_key(s3, local):
    s3.upload(local["l1_single.zarr"], "my store.zarr")
    assert zss.check(f"s3://{s3_server.BUCKET}/my%20store.zarr").errors == []
