"""Load the spec (the synced copy) as package data and validate it.

The spec under ``spec/schema.yaml`` here is a mechanically-synced copy of the
repo-root ``spec/schema.yaml`` (see ``make sync``); it is never hand-edited.
Both the constants module and the validator derive from what this loads, so the
binding has no second hand-maintained copy of the contract.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

import yaml
from jsonschema import Draft202012Validator


@lru_cache(maxsize=1)
def load_spec() -> dict[str, Any]:
    """Parse the synced spec and validate it against the meta-schema.

    Validating on load makes a malformed spec fail fast at import-adjacent
    time rather than producing silently-wrong constants downstream.
    """
    spec_text = (files(__package__) / "spec" / "schema.yaml").read_text(encoding="utf-8")
    spec = yaml.safe_load(spec_text)
    _validate_against_meta_schema(spec)
    return spec


@lru_cache(maxsize=1)
def load_meta_schema() -> dict[str, Any]:
    meta_text = (files(__package__) / "spec" / "meta-schema.json").read_text(encoding="utf-8")
    return json.loads(meta_text)


def _validate_against_meta_schema(spec: dict[str, Any]) -> None:
    validator = Draft202012Validator(load_meta_schema())
    errors = sorted(validator.iter_errors(spec), key=lambda e: e.path)
    found = [f"{'/'.join(map(str, e.path))}: {e.message}" for e in errors]
    if not found:  # the rules below assume a spec of the meta-schema's shape
        found = _rules_beyond_json_schema(spec)
    if found:
        joined = "\n".join(f"  - {f}" for f in found)
        raise ValueError(f"spec/schema.yaml violates the meta-schema:\n{joined}")


def _rules_beyond_json_schema(spec: dict[str, Any]) -> list[str]:
    """The spec rules JSON Schema cannot state: one name per flag bit, and an
    integer ``fill_value`` that is no float (JSON Schema's ``integer`` admits
    ``4294967295.0``)."""
    found = []
    names: dict[int, str] = {}
    for name, bit in spec.get("flag_bits", {}).items():
        if bit in names:
            found.append(f"flag_bits/{name}: bit {bit} is also {names[bit]}")
        names.setdefault(bit, name)
    for level_name, level in spec["levels"].items():
        for group_name, group in level["groups"].items():
            for array_name, aspec in group["arrays"].items():
                fill = aspec.get("fill_value")
                if fill is not None and type(fill) is not int:
                    found.append(
                        f"levels/{level_name}/groups/{group_name}/arrays/{array_name}/"
                        f"fill_value: {fill!r} is not an integer"
                    )
    return found
