from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__
from .auditor import Auditor
from .manifest import load_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dfa",
        description="Audit tabular process datasets for analyst-visible forensic tells.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--manifest", required=True, help="Path to manifest.yaml")
    parser.add_argument(
        "--out", required=True, help="Directory for audit_report.md and audit_report.json"
    )
    parser.add_argument(
        "--fail-on",
        choices=["glance", "standard"],
        default="standard",
        help="Lowest unintended-finding loudness that fails the run. Default: standard.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    manifest = load_manifest(Path(args.manifest))
    auditor = Auditor(manifest)
    auditor.run()
    auditor.write_reports(Path(args.out), fail_on=args.fail_on.upper())
    return auditor.exit_code(fail_on=args.fail_on.upper())
