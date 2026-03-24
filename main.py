from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from email_assistant.input_loader import parse_input_file
from email_assistant.llm_client import call_llm_for_analysis
from email_assistant.models import safe_parse_output
from email_assistant.preprocessor import build_thread_text


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
    return parser


def run() -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args()

    try:
        email_input = parse_input_file(args.input)
        thread_text = build_thread_text(email_input)
    except Exception as err:
        print(f"[ERROR] Failed to load/prepare input: {err}", file=sys.stderr)
        return 1

    if args.dry_run:
        print("=== DRY RUN: Preprocessed Thread Text ===")
        print(thread_text)
        return 0

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[ERROR] OPENAI_API_KEY is not set.", file=sys.stderr)
        return 1

    try:
        raw_output = call_llm_for_analysis(model=args.model, thread_text=thread_text, api_key=api_key)
        parsed_output = safe_parse_output(raw_output)
    except Exception as err:
        print(f"[ERROR] LLM call or output parsing failed: {err}", file=sys.stderr)
        return 1

    output_dict = parsed_output.model_dump()
    print(json.dumps(output_dict, ensure_ascii=False, indent=2))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output_dict, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved result to: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
