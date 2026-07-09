from __future__ import annotations

import argparse
import os
from pathlib import Path

from .evaluator import ScientificExtractionEvaluator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate a graph extraction JSON file at node and edge granularity."
    )
    parser.add_argument(
        "--input",
        default="result1.json",
        help="Path to the graph extraction result JSON file.",
    )
    parser.add_argument(
        "--output",
        default="evaluation_outputs/result1_report.json",
        help="Path to save the aggregated evaluation report.",
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
        default=int(os.getenv("SCIENTIFIC_EVAL_MAX_CONCURRENCY", "8")),
        help="Maximum number of concurrent node/edge judge requests.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build requests and report shape without calling the model.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    input_path = Path(args.input).resolve()
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path

    evaluator = ScientificExtractionEvaluator(
        input_path=input_path,
        model=args.model,
        openai_base_url=args.base_url,
        openai_api_key=args.api_key,
        max_concurrency=args.max_concurrency,
        dry_run=args.dry_run,
    )
    report = evaluator.evaluate()
    evaluator.save(report, output_path)
    print(f"Saved evaluation report to {output_path}")


if __name__ == "__main__":
    main()
