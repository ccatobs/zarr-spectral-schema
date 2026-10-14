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

from zarr_spectral_schema._spec import load_meta_schema, load_spec


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
            lambda s: s["levels"]["L0"]["groups"]["source"]["arrays"][
                "data_5d"
            ].__setitem__("dtype", "complex128"),
            id="unknown-dtype",
        ),
        # #20: a dtype set holds known names only, and at least one.
        pytest.param(
            lambda s: s["levels"]["L1"]["groups"]["scan"]["arrays"][
                "spectra"
            ].__setitem__("dtype", ["float32", "complex128"]),
            id="unknown-dtype-in-set",
        ),
        pytest.param(
            lambda s: s["levels"]["L1"]["groups"]["scan"]["arrays"][
                "spectra"
            ].__setitem__("dtype", []),
            id="empty-dtype-set",
        ),
        pytest.param(
            lambda s: s["levels"]["L1"]["groups"]["scan"]["arrays"]["spectra"]["attrs"][
                "keepbits"
            ].__setitem__("type", "int"),
            id="array-attr-unknown-type",
        ),
        pytest.param(
            lambda s: s["versions"]["l0_schema_version"].__setitem__(
                "on_disk", "float"
            ),
            id="bad-on_disk",
        ),
        pytest.param(
            lambda s: s.__setitem__("spec_version", "1.x"), id="bad-spec_version"
        ),
        # The top level stays strict (additionalProperties: false): an unknown key
        # is still rejected even after precedence_aliases was admitted.
        pytest.param(
            lambda s: s.__setitem__("bogus_top_level", {}), id="unknown-top-level-key"
        ),
        # precedence_aliases has teeth: an entry must carry a `from` list of strings.
        pytest.param(
            lambda s: s["precedence_aliases"]["pixel_offset"].pop("from"),
            id="precedence-alias-missing-from",
        ),
        pytest.param(
            lambda s: s["precedence_aliases"]["pixel_offset"].__setitem__(
                "from", "PIXOFFX"
            ),
            id="precedence-alias-from-not-array",
        ),
        pytest.param(
            lambda s: s["precedence_aliases"]["pixel_offset"].__setitem__("from", []),
            id="precedence-alias-from-empty",
        ),
        pytest.param(
            lambda s: s["precedence_aliases"]["pixel_offset"].__setitem__("bogus", 1),
            id="precedence-alias-extra-key",
        ),
        # #18: a group without placement/inline, or a level without detect, would
        # make the validators skip its content; the meta-schema rejects it.
        pytest.param(
            lambda s: s["levels"]["L0"]["groups"]["source"].pop("placement"),
            id="group-missing-placement",
        ),
        pytest.param(
            lambda s: s["levels"]["L0"]["groups"]["source"].pop("inline"),
            id="group-missing-inline",
        ),
        pytest.param(
            lambda s: s["levels"]["L1"].pop("detect"), id="level-missing-detect"
        ),
        pytest.param(
            lambda s: s["versions"]["cal_schema_version"].__setitem__("value", "abc"),
            id="version-value-abc",
        ),
        pytest.param(
            lambda s: s["versions"]["cal_schema_version"].__setitem__(
                "min_supported", "1.x"
            ),
            id="version-min_supported-not-numeric",
        ),
    ],
)
def test_malformed_spec_is_rejected(mutate):
    import copy

    spec = copy.deepcopy(load_spec())
    mutate(spec)
    validator = Draft202012Validator(load_meta_schema())
    assert list(validator.iter_errors(spec)), (
        "meta-schema should have rejected this mutation"
    )


def test_each_detect_attr_names_a_version_of_its_level():
    # Level detection and version negotiation share one attr: the detect attr of
    # a level must be a `versions` entry of that level.
    spec = load_spec()
    for level_name, level in spec["levels"].items():
        version = spec["versions"].get(level["detect"])
        assert version is not None, (
            f"{level_name}: detect {level['detect']!r} names no version"
        )
        assert version["level"] == level_name


def test_each_one_of_member_names_a_group_of_its_level():
    spec = load_spec()
    for level_name, level in spec["levels"].items():
        for name in level.get("one_of", []):
            assert name in level["groups"], (
                f"{level_name}: one_of names unknown group {name!r}"
            )
