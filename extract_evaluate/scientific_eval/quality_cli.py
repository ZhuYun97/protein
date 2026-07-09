from __future__ import annotations

import argparse
import os
from pathlib import Path

from .quality_evaluator import ScientificQualityEvaluator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate scientific quality of a graph extraction JSON file using "
            "nodes, edges, evidence, graph structure, and optional factuality scores."
        )
    )
    parser.add_argument(
        "--input",
        default="result1.json",
        help="Path to the graph extraction result JSON file.",
    )
    parser.add_argument(
        "--output",
        default="evaluation_outputs/result1_scientific_quality_report.json",
        help="Path to save the scientific quality report.",
    )
    parser.add_argument(
        "--factuality-report",
        default=None,
        help="Optional factuality report JSON. Existing scientific_eval.cli reports are supported.",
    )
    parser.add_argument(
        "--target-kg-goal",
        default=None,
        help="Optional short description of the target KG goal or domain constraints.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("OPENAI_MODEL", "gpt-4.1"),
        help="OpenAI model used as judge.",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("OPENAI_BASE_URL"),
        help="Optional OpenAI-compatible API base URL.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("OPENAI_API_KEY"),
        help="OpenAI API key. Defaults to OPENAI_API_KEY environment variable.",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=int(os.getenv("SCIENTIFIC_QUALITY_MAX_CONCURRENCY", "4")),
        help="Maximum number of concurrent unit-quality batch judge requests.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.getenv("SCIENTIFIC_QUALITY_BATCH_SIZE", "10")),
        help="Number of graph units evaluated per unit-quality judge request.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build report shape without calling the model.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    input_path = Path(args.input).resolve()
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path

    factuality_report_path = Path(args.factuality_report).resolve() if args.factuality_report else None

    evaluator = ScientificQualityEvaluator(
        input_path=input_path,
        model=args.model,
        factuality_report_path=factuality_report_path,
        target_kg_goal=args.target_kg_goal,
        openai_base_url=args.base_url,
        openai_api_key=args.api_key,
        max_concurrency=args.max_concurrency,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )
    report = evaluator.evaluate()
    evaluator.save(report, output_path)
    print(f"Saved scientific quality report to {output_path}")


if __name__ == "__main__":
    main()
