"""Meta-schema tests: the spec is well-formed, and the meta-schema has teeth.

Proves both halves of "a meta-schema validates schema.yaml itself": the real
spec passes, and a deliberately-malformed spec is rejected (so the meta-schema
is not vacuous).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "bindings" / "python" / "src"))

from zarr_spectral_schema._spec import load_meta_schema, load_spec  # noqa: E402


def test_meta_schema_is_itself_valid_json_schema():
    # Self-validating DSL: the meta-schema must be a valid Draft 2020-12 schema.
    Draft202012Validator.check_schema(load_meta_schema())


def test_real_spec_passes_meta_schema():
    # load_spec() validates against the meta-schema internally; no raise == pass.
    spec = load_spec()
    assert "data_5d" in spec["levels"]["L0"]["groups"]["source"]["arrays"]


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda s: s.pop("versions"), id="missing-versions"),
        pytest.param(
            lambda s: s["levels"]["L0"]["groups"]["source"]["arrays"]["data_5d"].__setitem__(
                "dtype", "complex128"
            ),
            id="unknown-dtype",
        ),
        pytest.param(
            lambda s: s["versions"]["l0_schema_version"].__setitem__("on_disk", "float"),
            id="bad-on_disk",
        ),
        pytest.param(lambda s: s.__setitem__("spec_version", "1.x"), id="bad-spec_version"),
    ],
)
def test_malformed_spec_is_rejected(mutate):
    import copy

    spec = copy.deepcopy(load_spec())
    mutate(spec)
    validator = Draft202012Validator(load_meta_schema())
    assert list(validator.iter_errors(spec)), "meta-schema should have rejected this mutation"
