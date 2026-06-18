"""Two-axis version-negotiation conformance test (Python binding) — Slice 3 (#4).

A store carries producer-owned on-disk versions (``l0_schema_version`` integer,
``cal_schema_version`` ``major.minor``). The validator range-negotiates each
against the spec's supported window (``min_supported`` .. ``value``):

  below min_supported            → error
  older / equal within the range → pass, no warning
  newer minor than `value`       → warn once, validation still passes
  newer major than `value`       → error

The Rust binding runs the equivalent checks over the SAME emitted stores
(bindings/rust/tests/conformance.rs) — together they prove one spec drives one
negotiation in both languages. The repo's own ``spec_version`` is off-disk and
independent of these on-disk values.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "bindings" / "python" / "src"))
sys.path.insert(0, str(_ROOT / "conformance"))

import generate  # noqa: E402
import zarr_spectral_schema as zss  # noqa: E402


@pytest.fixture(scope="module")
def stores(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("ver_stores")
    generate.emit(out / "equal.zarr")  # spec-current: l0=2, cal=1.3
    for name, versions in generate.VERSION_FIXTURES.items():
        generate.emit(out / name, versions=versions)
    return out


def test_equal_passes_no_warnings(stores):
    result = zss.check(stores / "equal.zarr")
    assert result.ok, result.errors
    assert result.errors == []
    assert result.warnings == []


def test_older_minor_passes_no_warnings(stores):
    result = zss.check(stores / "ver_older.zarr")
    assert result.ok, result.errors
    assert result.warnings == []


def test_newer_minor_warns_but_passes(stores):
    result = zss.check(stores / "ver_newer_minor.zarr")
    # Additive: validation still succeeds (no errors)...
    assert result.ok, result.errors
    assert result.errors == []
    # ...but exactly one warning is surfaced, naming the axis and both versions.
    assert len(result.warnings) == 1, result.warnings
    w = result.warnings[0]
    assert "cal_schema_version" in w
    assert "1.9" in w and "1.3" in w
    # Back-compat surface: validate() returns errors only → empty here.
    assert zss.validate(stores / "ver_newer_minor.zarr") == []


def test_newer_major_errors(stores):
    result = zss.check(stores / "ver_newer_major.zarr")
    assert not result.ok
    assert any("cal_schema_version" in e and "2.0" in e for e in result.errors), result.errors
    assert zss.validate(stores / "ver_newer_major.zarr") != []


def test_l0_integer_newer_major_errors(stores):
    result = zss.check(stores / "ver_l0_newer_major.zarr")
    assert not result.ok
    assert any("l0_schema_version" in e for e in result.errors), result.errors


def test_below_floor_errors(stores):
    result = zss.check(stores / "ver_below_floor.zarr")
    assert not result.ok
    assert any("cal_schema_version" in e for e in result.errors), result.errors


def test_package_version_independent_of_on_disk(stores):
    spec = zss.load_spec()
    # spec_version is off-disk provenance: never written into the store.
    view = zss._store.read_store(stores / "equal.zarr")  # type: ignore[attr-defined]
    assert "l0_schema_version" in view.root_attrs
    assert "cal_schema_version" in view.root_attrs
    assert "spec_version" not in view.root_attrs
    # ...and its value is decoupled from the on-disk version values.
    assert spec["spec_version"] not in {"2", "1.3"}
    assert spec["spec_version"] != spec["versions"]["l0_schema_version"]["value"]
    assert spec["spec_version"] != spec["versions"]["cal_schema_version"]["value"]


def test_cli_validate_exit_codes(stores):
    env_root = str(_ROOT / "bindings" / "python" / "src")
    def run(name: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "zarr_spectral_schema", "validate", str(stores / name)],
            capture_output=True, text=True,
            env={**__import__("os").environ, "PYTHONPATH": env_root},
        )
    ok = run("equal.zarr")
    assert ok.returncode == 0, ok.stderr
    bad = run("ver_newer_major.zarr")
    assert bad.returncode != 0
    warn = run("ver_newer_minor.zarr")
    assert warn.returncode == 0  # warning does not fail validation
    assert "cal_schema_version" in (warn.stdout + warn.stderr)
