from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from email_assistant.dotenv_load import load_project_dotenv
from email_assistant.models import SingleEmailInput, ThreadInput, UnifiedInput
from email_assistant.preprocessor import build_thread_text
from email_assistant.summary_pipeline import analyze_reply_decision_thread_text


def _strip_json_comments(text: str) -> str:
    lines = []
    for line in text.splitlines():
        s = line.lstrip()
        if s.startswith("//"):
            continue
        lines.append(line)
    return "\n".join(lines)


def _load_case(case_path: Path) -> dict[str, Any]:
    raw = case_path.read_text(encoding="utf-8")
    data = json.loads(_strip_json_comments(raw))
    if not isinstance(data, dict):
        raise ValueError(f"Case file must be a JSON object: {case_path}")
    return data


def _to_unified_input(payload: dict[str, Any]) -> UnifiedInput:
    if "messages" in payload:
        thread = ThreadInput.model_validate(payload)
        return UnifiedInput(
            input_type="thread",
            thread_id=thread.thread_id,
            subject=thread.subject,
            messages=thread.messages,
        )
    single = SingleEmailInput.model_validate(payload)
    return UnifiedInput(
        input_type="single",
        thread_id="single-email",
        subject=single.subject,
        messages=[
            {
                "sender": single.sender,
                "recipients": single.recipients,
                "timestamp": single.timestamp,
                "body": single.body,
            }
        ],
    )


def _evaluate(actual: dict[str, Any], expected: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    expected_need_reply = expected.get("是否需要回复")
    if isinstance(expected_need_reply, bool):
        if bool(actual.get("是否需要回复")) != expected_need_reply:
            errors.append(
                f"是否需要回复 mismatch: expected={expected_need_reply}, actual={actual.get('是否需要回复')}"
            )

    draft_rule = str(expected.get("回复草稿规则") or "").strip().lower()
    draft_text = str(actual.get("回复草稿") or "").strip()
    if draft_rule == "non_empty" and not draft_text:
        errors.append("回复草稿 should be non-empty, but got empty")
    if draft_rule == "empty" and draft_text:
        errors.append("回复草稿 should be empty, but got non-empty")

    reason = str(actual.get("判断原因") or "")
    contains = expected.get("判断原因应包含") or []
    if isinstance(contains, list):
        for token in contains:
            token_text = str(token).strip()
            if token_text and token_text not in reason:
                errors.append(f"判断原因 missing token: {token_text}")

    return (len(errors) == 0), errors


def main() -> int:
    load_project_dotenv(override=True)

    parser = argparse.ArgumentParser(description="Run dry-run style eval cases for reply-priority agent.")
    parser.add_argument("--cases-dir", default="test/cases", help="Directory for test case JSON files")
    parser.add_argument("--all", action="store_true", help="Run all test cases in cases-dir")
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help="Run specific case id(s), e.g. --case tc01 --case tc02",
    )
    parser.add_argument("--model", default=None, help="Override model name; default uses OPENAI_MODEL")
    parser.add_argument(
        "--save-dir",
        default="test/results",
        help="Where to save actual outputs and summary report",
    )
    parser.add_argument(
        "--current-user-identity",
        default="display_name=test-user; emails_or_aliases=test@company.com",
        help="Injected identity for reply priority analysis",
    )
    args = parser.parse_args()

    cases_dir = Path(args.cases_dir)
    if not cases_dir.exists():
        raise FileNotFoundError(f"Cases directory not found: {cases_dir}")

    if args.all:
        case_files = sorted(cases_dir.glob("*.json"))
    else:
        if not args.case:
            raise ValueError("Use --all or provide at least one --case.")
        case_files = [cases_dir / f"{case_id}.json" for case_id in args.case]

    save_dir = Path(args.save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    summary: list[dict[str, Any]] = []
    passed = 0

    for case_file in case_files:
        if not case_file.exists():
            summary.append(
                {
                    "case": case_file.stem,
                    "status": "FAIL",
                    "errors": [f"Case file not found: {case_file}"],
                }
            )
            continue

        try:
            case_obj = _load_case(case_file)
            case_id = str(case_obj.get("id") or case_file.stem)
            input_obj = case_obj.get("input") or {}
            expected_obj = case_obj.get("expected_output") or {}

            unified = _to_unified_input(input_obj)
            thread_text = build_thread_text(unified)
            actual_obj = analyze_reply_decision_thread_text(
                thread_text,
                model=args.model,
                current_user_identity=args.current_user_identity,
            ).model_dump()

            ok, errors = _evaluate(actual_obj, expected_obj)
            if ok:
                passed += 1

            (save_dir / f"{case_id}.actual.json").write_text(
                json.dumps(actual_obj, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            summary.append(
                {
                    "case": case_id,
                    "status": "PASS" if ok else "FAIL",
                    "errors": errors,
                    "expected": expected_obj,
                    "actual_file": f"{case_id}.actual.json",
                }
            )
        except Exception as err:
            summary.append(
                {
                    "case": case_file.stem,
                    "status": "FAIL",
                    "errors": [str(err)],
                }
            )

    report = {
        "total": len(case_files),
        "passed": passed,
        "failed": len(case_files) - passed,
        "results": summary,
    }
    (save_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed == len(case_files) else 1


if __name__ == "__main__":
    raise SystemExit(main())

