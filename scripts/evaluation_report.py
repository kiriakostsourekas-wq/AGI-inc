#!/usr/bin/env python3
"""Generate an evaluation report only after strict artifact validation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.reporting import ArtifactValidationError, read_json, write_report_bundle  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate raw evaluation attempts and generate Markdown/JSON/CSV evidence."
    )
    parser.add_argument("--plan", type=Path, required=True, help="Predeclared evaluation plan JSON")
    parser.add_argument("--results", type=Path, required=True, help="Raw execution artifact JSON")
    parser.add_argument("--output", type=Path, required=True, help="Output directory")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        plan = read_json(args.plan)
        results = read_json(args.results)
        summary = write_report_bundle(plan=plan, results=results, output_directory=args.output)
    except (ArtifactValidationError, OSError) as exc:
        print(f"evaluation report refused: {exc}", file=sys.stderr)
        return 2
    print(
        f"generated {summary['evidenceClass']} report from "
        f"{summary['accounting']['originalAttemptRows']} original rows; "
        "no aggregate values were accepted as input"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
