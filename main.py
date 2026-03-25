from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from email_assistant.dotenv_load import load_project_dotenv
from email_assistant.input_loader import parse_input_file
from email_assistant.summary_pipeline import analysis_to_dict, analyze_unified_input


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal Email Processing Agent prototype")
    parser.add_argument(
        "--input",
        default="data/input.json",
        help="Path to input JSON file",
    )
    parser.add_argument(
        "--output",
        default="data/output.json",
        help="Path to save output JSON file",
    )
    parser.add_argument(
        "--model",
        default="gpt-4o-mini",
        help="OpenAI model name",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip LLM call and print preprocessed thread text only",
    )
    parser.add_argument(
        "--style",
        choices=["short", "long"],
        default="short",
        help="Summary style: short or long",
    )
    return parser


def run() -> int:
    load_project_dotenv(override=True)
    parser = build_parser()
    args = parser.parse_args()

    try:
        email_input = parse_input_file(args.input)
    except Exception as err:
        print(f"[ERROR] Failed to load/prepare input: {err}", file=sys.stderr)
        return 1

    if args.dry_run:
        from email_assistant.preprocessor import build_thread_text

        print("=== DRY RUN: Preprocessed Thread Text ===")
        print(build_thread_text(email_input))
        return 0

    try:
        parsed_output = analyze_unified_input(email_input, model=args.model, style=args.style)
    except Exception as err:
        print(f"[ERROR] LLM call or output parsing failed: {err}", file=sys.stderr)
        return 1

    output_dict = analysis_to_dict(parsed_output)
    print(json.dumps(output_dict, ensure_ascii=False, indent=2))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output_dict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved result to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
