"""Minimal CLI surface for the validator — ``python -m zarr_spectral_schema``.

Slice 3 (#4) needs the version negotiation reachable from a command line. This is
deliberately small (stdlib argparse, no new deps); the full inspector
(``validate`` / ``describe`` / ``diff`` / ``emit-reference``, typer + rich) lands
in Slice 7 (#8).

    python -m zarr_spectral_schema validate <store.zarr>

Exit code: 0 when conformant (warnings allowed), 1 when there are errors.
"""

from __future__ import annotations

import argparse
import sys

from . import check


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zarr_spectral_schema")
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="validate a Zarr store against the spec")
    v.add_argument("store", help="path to a Zarr v3 store")
    args = parser.parse_args(argv)

    if args.command == "validate":
        result = check(args.store)
        for w in result.warnings:
            print(f"warning: {w}", file=sys.stderr)
        for e in result.errors:
            print(f"error: {e}", file=sys.stderr)
        if result.ok:
            print(
                f"OK: {args.store} is conformant"
                + (f" ({len(result.warnings)} warning(s))" if result.warnings else "")
            )
            return 0
        print(f"FAIL: {args.store} has {len(result.errors)} error(s)", file=sys.stderr)
        return 1
    return 2  # unreachable: argparse enforces a known subcommand


if __name__ == "__main__":
    raise SystemExit(main())
