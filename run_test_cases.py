from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from email_assistant.dotenv_load import load_project_dotenv
from email_assistant.models import SingleEmailInput, ThreadInput, UnifiedInput
from email_assistant.preprocessor import build_thread_text
from email_assistant.llm_client import call_llm_for_reply_decision


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


def _normalize_raw(actual: dict[str, Any]) -> dict[str, Any]:
    reason = str(actual.get("判断原因") or "")
    raw_priority = str(actual.get("_raw_priority") or "").strip().upper()
    if not raw_priority:
        if "priority=HIGH" in reason:
            raw_priority = "HIGH"
        elif "priority=MEDIUM" in reason:
            raw_priority = "MEDIUM"
        elif "priority=UNCERTAIN" in reason:
            raw_priority = "UNCERTAIN"
        elif "priority=LOW" in reason:
            raw_priority = "LOW"
    raw_score = actual.get("_raw_priority_score")
    if raw_score is None:
        marker = "score="
        pos = reason.find(marker)
        if pos >= 0:
            raw = reason[pos + len(marker):].split("；", 1)[0].strip()
            try:
                raw_score = float(raw)
            except ValueError:
                raw_score = None
    raw_signals = actual.get("_raw_signals")
    if not isinstance(raw_signals, dict):
        raw_signals = {}
    return {
        "priority": raw_priority,
        "priority_score": raw_score,
        "signals": raw_signals,
        "need_reply": bool(actual.get("是否需要回复")),
        "draft_text": str(actual.get("回复草稿") or "").strip(),
        "reason_text": reason,
    }


def _match_signal(actual_value: Any, expected_value: Any) -> bool:
    if isinstance(expected_value, list):
        return str(actual_value) in [str(v) for v in expected_value]
    return str(actual_value) == str(expected_value)


def _eval_priority_rules(norm: dict[str, Any], rules: dict[str, Any], errors: list[str]) -> None:
    priority = norm.get("priority") or ""
    allowed = rules.get("allowed") or []
    forbidden = rules.get("forbidden") or []
    not_low = bool(rules.get("not_low"))
    if isinstance(allowed, list) and allowed and priority not in [str(v).upper() for v in allowed]:
        errors.append(f"priority not in allowed set: actual={priority}, allowed={allowed}")
    if isinstance(forbidden, list) and priority in [str(v).upper() for v in forbidden]:
        errors.append(f"priority in forbidden set: actual={priority}, forbidden={forbidden}")
    if not_low and priority == "LOW":
        errors.append("priority should not be LOW")

    high_requires_any = rules.get("high_requires_any") or []
    if priority == "HIGH" and isinstance(high_requires_any, list) and high_requires_any:
        signals = norm.get("signals") or {}
        ok = False
        for cond in high_requires_any:
            c = str(cond).strip()
            if c == "has_deadline" and bool(signals.get("has_deadline")):
                ok = True
            if c == "sender_importance=manager" and str(signals.get("sender_importance")) == "manager":
                ok = True
        if not ok:
            errors.append(
                "priority=HIGH violates high_requires_any gating rule "
                f"(need one of {high_requires_any}, signals={signals})"
            )


def _evaluate(actual: dict[str, Any], expected: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    errors: list[str] = []
    norm = _normalize_raw(actual)

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

    # New calibration-oriented assertions
    signals_rule = expected.get("signals") or {}
    if isinstance(signals_rule, dict) and signals_rule:
        signals = norm.get("signals") or {}
        for key, expected_value in signals_rule.items():
            actual_value = signals.get(key)
            if not _match_signal(actual_value, expected_value):
                errors.append(
                    f"signal mismatch: {key} expected={expected_value}, actual={actual_value}"
                )

    score_band = expected.get("score_band") or {}
    if isinstance(score_band, dict) and score_band:
        score = norm.get("priority_score")
        if score is None:
            errors.append("priority_score missing")
        else:
            try:
                score_num = float(score)
            except (TypeError, ValueError):
                errors.append(f"priority_score is not numeric: {score}")
            else:
                if "min" in score_band and score_num < float(score_band["min"]):
                    errors.append(f"priority_score below min: score={score_num}, min={score_band['min']}")
                if "max" in score_band and score_num > float(score_band["max"]):
                    errors.append(f"priority_score above max: score={score_num}, max={score_band['max']}")

    priority_rule = expected.get("priority_rule") or {}
    if isinstance(priority_rule, dict) and priority_rule:
        _eval_priority_rules(norm, priority_rule, errors)

    return (len(errors) == 0), errors, norm


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
            model_name = args.model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY is not set.")
            actual_obj = call_llm_for_reply_decision(
                model=model_name,
                thread_text=thread_text,
                api_key=api_key,
                current_user_identity=args.current_user_identity,
            )

            ok, errors, normalized = _evaluate(actual_obj, expected_obj)
            if ok:
                passed += 1

            (save_dir / f"{case_id}.actual.json").write_text(
                json.dumps(actual_obj, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (save_dir / f"{case_id}.normalized.json").write_text(
                json.dumps(normalized, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            summary.append(
                {
                    "case": case_id,
                    "status": "PASS" if ok else "FAIL",
                    "errors": errors,
                    "expected": expected_obj,
                    "actual_file": f"{case_id}.actual.json",
                    "normalized_file": f"{case_id}.normalized.json",
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

