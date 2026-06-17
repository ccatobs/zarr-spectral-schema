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
    if errors:
        joined = "\n".join(f"  - {'/'.join(map(str, e.path))}: {e.message}" for e in errors)
        raise ValueError(f"spec/schema.yaml violates the meta-schema:\n{joined}")
