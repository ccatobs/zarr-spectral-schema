"""Meta-schema tests: the spec is well-formed, and the meta-schema has teeth.

Proves both halves of "a meta-schema validates schema.yaml itself": the real
spec passes, and a deliberately-malformed spec is rejected (so the meta-schema
is not vacuous).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "bindings" / "python" / "src"))

from zarr_spectral_schema._spec import (
    _validate_against_meta_schema,
    load_meta_schema,
    load_spec,
)


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
            lambda s: s["levels"]["L1"]["scan_or_band_attrs"][
                "source_l0_store"
            ].__setitem__("format", "uri"),
            id="unknown-attr-format",
        ),
        # A format constrains a string: on any other type it is a spec error.
        pytest.param(
            lambda s: s["levels"]["L1"]["scan_or_band_attrs"][
                "source_l0_store"
            ].__setitem__("type", "integer"),
            id="format-on-non-string-attr",
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
        # #19: every array declares its dims; flag bits fit a uint16; the attr
        # keys sentinel and enum are well-formed.
        pytest.param(
            lambda s: s["levels"]["L0"]["groups"]["source"]["arrays"]["data_5d"].pop(
                "dims"
            ),
            id="array-missing-dims",
        ),
        pytest.param(
            lambda s: s["levels"]["L1"]["groups"]["scan"]["arrays"][
                "spectra"
            ].__setitem__("units", 1),
            id="array-units-not-string",
        ),
        pytest.param(
            lambda s: s["flag_bits"].__setitem__("BAD_CHANNEL", 16),
            id="flag-bit-out-of-range",
        ),
        pytest.param(
            lambda s: s["levels"]["L1"]["scan_or_band_attrs"]["specsys"].__setitem__(
                "sentinel", "NONE"
            ),
            id="sentinel-not-boolean",
        ),
        pytest.param(
            lambda s: s["levels"]["L1"]["scan_or_band_attrs"]["specsys"].__setitem__(
                "enum", []
            ),
            id="enum-empty",
        ),
        # #33: `scan_or_band` is the only placement; dims come from the vocabulary.
        pytest.param(
            lambda s: s["levels"]["L0"]["groups"]["source"].__setitem__(
                "placement", "scan"
            ),
            id="placement-scan",
        ),
        pytest.param(
            lambda s: s["levels"]["L1"]["groups"]["scan"]["arrays"][
                "t_int"
            ].__setitem__("dims", ["time"]),
            id="dims-outside-vocabulary",
        ),
        # A bound constrains a number or an integer, nothing else.
        pytest.param(
            lambda s: s["levels"]["L1"]["scan_or_band_attrs"]["line"].__setitem__(
                "minimum", 0
            ),
            id="bound-on-string-attr",
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


@pytest.mark.parametrize(
    "mutate, finding",
    [
        # Two names for one bit: JSON Schema cannot state it, the spec check does.
        pytest.param(
            lambda s: s["flag_bits"].__setitem__("MANUAL", 0),
            "flag_bits/MANUAL: bit 0 is also BAD_CHANNEL",
            id="flag-bit-twice",
        ),
        # JSON Schema's `integer` admits 4294967295.0; the spec check does not.
        pytest.param(
            lambda s: s["levels"]["L0"]["groups"]["source"]["arrays"][
                "data_5d"
            ].__setitem__("fill_value", 4294967295.0),
            "levels/L0/groups/source/arrays/data_5d/fill_value: 4294967295.0 is not an "
            "integer",
            id="float-fill-value",
        ),
    ],
)
def test_spec_check_rejects_what_json_schema_cannot_state(mutate, finding):
    import copy

    spec = copy.deepcopy(load_spec())
    mutate(spec)
    with pytest.raises(ValueError, match=re.escape(finding)):
        _validate_against_meta_schema(spec)


def test_every_fill_value_is_a_yaml_integer():
    spec = load_spec()
    fills = [
        a["fill_value"]
        for level in spec["levels"].values()
        for group in level["groups"].values()
        for a in group["arrays"].values()
        if "fill_value" in a
    ]
    assert fills and all(type(f) is int for f in fills), fills


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


def test_each_array_declares_one_dim_per_axis():
    spec = load_spec()
    for level_name, level in spec["levels"].items():
        for gname, group in level["groups"].items():
            for aname, aspec in group["arrays"].items():
                assert len(aspec["dims"]) == aspec["ndim"], (
                    f"{level_name}/{gname}/{aname}"
                )


def test_no_attr_is_declared_at_two_placements():
    # A scan-or-band attr resolves to the scan group or to each band, so it may
    # not also be a scan attr or a band attr.
    spec = load_spec()
    for level_name, level in spec["levels"].items():
        sob = set(level.get("scan_or_band_attrs", {}))
        for section in ("scan_attrs", "band_attrs"):
            assert not sob & set(level.get(section, {})), (level_name, section)
