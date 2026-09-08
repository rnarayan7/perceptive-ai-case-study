"""Command-line entry point for figure-corpus ingestion.

Examples::

    # List available sources
    python -m corpus.cli list

    # Pull open-access forest plots from PubMed Central
    python -m corpus.cli pmc "forest plot hazard ratio oncology" --limit 20 \
        --option figure_types=forest

    # Recover verified ground truth for specific trials
    python -m corpus.cli ctgov NCT02773849,NCT04165317

    # Extract figures from supplied FDA PDF URLs
    python -m corpus.cli fda "https://www.fda.gov/media/177652/download" \
        --option doc_label=odac_2024

    # Summarize what has been collected
    python -m corpus.cli stats

``--option key=value`` passes a source-specific option; repeat it as needed.
Values that look like ints/bools/comma-lists are coerced.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any, Dict, List

from corpus.base import FigureStorage
from corpus.sources import REGISTRY


def _coerce(value: str) -> Any:
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    if value.isdigit():
        return int(value)
    if "," in value:
        return [v.strip() for v in value.split(",") if v.strip()]
    return value


def _parse_options(pairs: List[str]) -> Dict[str, Any]:
    options: Dict[str, Any] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SystemExit(f"bad --option {pair!r}; expected key=value")
        key, value = pair.split("=", 1)
        options[key.strip()] = _coerce(value.strip())
    return options


def cmd_list(_: argparse.Namespace) -> int:
    print("Available sources:")
    for name, cls in REGISTRY.items():
        summary = (cls.__doc__ or "").strip().splitlines()[0] if cls.__doc__ else ""
        print(f"  {name:16s} {summary}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    storage = FigureStorage()
    figures = storage.load_figures()
    if not figures:
        print("No figures collected yet.")
        return 0
    by_source: Dict[str, int] = {}
    by_type: Dict[str, int] = {}
    verified = 0
    for record in figures:
        by_source[record.source] = by_source.get(record.source, 0) + 1
        by_type[record.figure_type] = by_type.get(record.figure_type, 0) + 1
        verified += sum(1 for g in record.ground_truth if g.verified)
    print(f"Figures: {len(figures)}  |  verified ground-truth values: {verified}")
    print("By source:", ", ".join(f"{k}={v}" for k, v in sorted(by_source.items())))
    print("By type:  ", ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    cls = REGISTRY.get(args.source)
    if cls is None:
        print(f"Unknown source {args.source!r}. Known: {', '.join(REGISTRY)}", file=sys.stderr)
        return 2
    options = _parse_options(args.option)
    if args.limit is not None:
        options.setdefault("limit", args.limit)
    ingester = cls()
    manifest = ingester.run(args.query, force=args.force, **options)
    print(f"[{manifest.source}] {manifest.figure_count} figures, {len(manifest.errors)} errors")
    for error in manifest.errors[:10]:
        print(f"  ! {error}")
    return 0 if not manifest.errors else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="corpus.cli", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list available sources").set_defaults(func=cmd_list)
    sub.add_parser("stats", help="summarize collected figures").set_defaults(func=cmd_stats)

    ingest = sub.add_parser("ingest", help="run a source ingester")
    ingest.add_argument("source", choices=list(REGISTRY))
    ingest.add_argument("query", help="query/seed for the source (see module docs)")
    ingest.add_argument("--limit", type=int, default=None)
    ingest.add_argument("--force", action="store_true", help="re-write unchanged figures")
    ingest.add_argument("--option", action="append", default=[], help="key=value, repeatable")
    ingest.set_defaults(func=cmd_ingest)

    # Convenience: allow `corpus.cli <source> <query>` without the `ingest` word.
    for name in REGISTRY:
        shortcut = sub.add_parser(name, help=f"ingest from {name}")
        shortcut.add_argument("query")
        shortcut.add_argument("--limit", type=int, default=None)
        shortcut.add_argument("--force", action="store_true")
        shortcut.add_argument("--option", action="append", default=[])
        shortcut.set_defaults(func=cmd_ingest, source=name)

    return parser


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
