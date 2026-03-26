from __future__ import annotations

import argparse
import difflib
import json
import os
import re
from pathlib import Path
from typing import Any

from email_assistant.dotenv_load import load_project_dotenv
from email_assistant.llm_client import call_llm_for_reply_decision
from email_assistant.models import Message, UnifiedInput
from email_assistant.preprocessor import build_thread_text
from email_assistant.summary_pipeline import analysis_to_dict, analyze_thread_text
from openai import OpenAI


def _strip_json_comments(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("//"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _load_case(case_path: Path) -> dict[str, Any]:
    raw = case_path.read_text(encoding="utf-8")
    data = json.loads(_strip_json_comments(raw))
    if not isinstance(data, dict):
        raise ValueError(f"Case file must be a JSON object: {case_path}")
    return data


def _sender_to_text(sender: Any) -> str:
    if isinstance(sender, str):
        return sender
    if isinstance(sender, dict):
        display = str(sender.get("display_name") or "").strip()
        email = str(sender.get("email") or "").strip()
        relationship = str(sender.get("relationship") or "").strip()
        core = f"{display} <{email}>" if display and email else (email or display or "unknown")
        return f"{core} [{relationship}]" if relationship else core
    return str(sender or "unknown")


def _to_unified_input(case_input: dict[str, Any]) -> UnifiedInput:
    thread = case_input.get("thread")
    if not isinstance(thread, dict):
        raise ValueError("input.thread is required.")
    messages_raw = thread.get("messages")
    if not isinstance(messages_raw, list) or not messages_raw:
        raise ValueError("input.thread.messages must be non-empty list.")

    messages: list[Message] = []
    for msg in messages_raw:
        if not isinstance(msg, dict):
            continue
        messages.append(
            Message(
                sender=_sender_to_text(msg.get("sender")),
                recipients=msg.get("recipients") or [],
                timestamp=str(msg.get("timestamp") or ""),
                body=str(msg.get("body") or ""),
            )
        )
    if not messages:
        raise ValueError("No valid messages in input.thread.messages.")
    return UnifiedInput(
        input_type="thread",
        thread_id=str(thread.get("thread_id") or "unknown-thread"),
        subject=str(thread.get("subject") or "(no subject)"),
        messages=messages,
    )


def _to_identity(user_context: dict[str, Any], fallback: str) -> str:
    if not isinstance(user_context, dict):
        return fallback
    role = str(user_context.get("user_role") or "unknown")
    important = user_context.get("important_senders") or []
    if isinstance(important, list):
        important_txt = ", ".join([str(x) for x in important if str(x).strip()])
    else:
        important_txt = ""
    usual = str(user_context.get("usual_external_priority") or "unknown")
    working_hours = str(user_context.get("working_hours") or "unknown")
    return (
        f"display_name=test-user; emails_or_aliases=test@company.com; role={role}; "
        f"working_hours={working_hours}; important_senders={important_txt}; "
        f"usual_external_priority={usual}"
    )


def _normalize_triage(raw: dict[str, Any]) -> dict[str, Any]:
    reason = str(raw.get("判断原因") or "")
    priority = str(raw.get("_raw_priority") or "").strip().upper()
    if not priority:
        for p in ("HIGH", "MEDIUM", "UNCERTAIN", "LOW"):
            if f"priority={p}" in reason:
                priority = p
                break
    score = raw.get("_raw_priority_score")
    if score is None:
        marker = "score="
        pos = reason.find(marker)
        if pos >= 0:
            maybe = reason[pos + len(marker):].split("；", 1)[0].strip()
            try:
                score = float(maybe)
            except ValueError:
                score = None
    signals = raw.get("_raw_signals")
    if not isinstance(signals, dict):
        signals = {}
    return {
        "needs_response": bool(raw.get("是否需要回复")),
        "priority": priority,
        "score": score,
        "signals": signals,
        "reason": reason,
        "reply_draft": str(raw.get("回复草稿") or "").strip(),
    }


def _contains_all(text: str, must_items: list[Any]) -> list[str]:
    errors: list[str] = []
    low = text.lower()
    for item in must_items:
        token = str(item).strip().lower()
        if token and not _semantic_match(low, token, threshold=0.74):
            errors.append(f"missing token (semantic): {item}")
    return errors


def _contains_any(text: str, options: list[Any]) -> bool:
    low = text.lower()
    for option in options:
        token = str(option).strip().lower()
        if token and _semantic_match(low, token, threshold=0.72):
            return True
    return False


def _norm_text(text: str) -> str:
    s = (text or "").lower()
    # Keep CJK, letters, numbers; normalize spaces/punctuation.
    s = re.sub(r"[\t\r\n]+", " ", s)
    s = re.sub(r"[^\w\u4e00-\u9fff ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _chunks_for_similarity(text: str) -> list[str]:
    if not text:
        return []
    raw = re.split(r"[。！？!?\n;；,.]+", text)
    out = []
    for seg in raw:
        n = _norm_text(seg)
        if n:
            out.append(n)
    return out


def _keywords(text: str) -> list[str]:
    s = _norm_text(text)
    # Keep longer tokens as anchors; ignore common stop words.
    stop = {
        "the", "a", "an", "to", "of", "in", "on", "for", "and", "or", "is", "are",
        "be", "with", "that", "this", "when", "will", "can", "could", "should",
        "please", "we", "you", "your", "our"
    }
    out: list[str] = []
    for tok in s.split():
        if len(tok) >= 4 and tok not in stop:
            out.append(tok)
    return out


def _has_anchor_overlap(haystack: str, needle: str) -> bool:
    """Prevent over-loose fuzzy matches by requiring anchor token overlap when possible."""
    n_keys = _keywords(needle)
    if not n_keys:
        return True
    h = _norm_text(haystack)
    return any(k in h for k in n_keys)


def _semantic_match(haystack: str, needle: str, threshold: float = 0.72) -> bool:
    """Soft match: exact substring OR high-similarity against sentence chunks."""
    h = _norm_text(haystack)
    n = _norm_text(needle)
    if not n:
        return True
    if n in h:
        return True
    if not _has_anchor_overlap(h, n):
        # For long phrases we require at least one anchor overlap to avoid random similarity hits.
        if len(n) >= 10:
            return False

    # Quick path for short tokens: allow word-level close match
    if len(n) <= 4:
        words = h.split()
        for w in words:
            if difflib.SequenceMatcher(None, w, n).ratio() >= 0.86:
                return True
        return False

    # Compare with chunks and short windows
    candidates = _chunks_for_similarity(h)
    if not candidates:
        return False

    best = 0.0
    for c in candidates:
        r = difflib.SequenceMatcher(None, c, n).ratio()
        if r > best:
            best = r
        # Window around similar length to reduce dilution by long chunks
        if len(c) > len(n) * 2:
            step = max(8, len(n) // 2)
            win = max(len(n) + 12, int(len(n) * 1.5))
            for i in range(0, max(1, len(c) - win + 1), step):
                sub = c[i:i + win]
                rr = difflib.SequenceMatcher(None, sub, n).ratio()
                if rr > best:
                    best = rr
                if best >= threshold:
                    return True
        if best >= threshold:
            return True
    return best >= threshold


def _eval_triage(actual: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    needs = expected.get("needs_response")
    if isinstance(needs, bool) and bool(actual.get("needs_response")) != needs:
        errors.append(f"needs_response mismatch: expected={needs}, actual={actual.get('needs_response')}")

    pr = str(actual.get("priority") or "")
    allowed = expected.get("allowed_priority") or []
    disallowed = expected.get("disallowed_priority") or []
    if isinstance(allowed, list) and allowed and pr not in [str(x).upper() for x in allowed]:
        errors.append(f"priority not allowed: {pr}, allowed={allowed}")
    if isinstance(disallowed, list) and pr in [str(x).upper() for x in disallowed]:
        errors.append(f"priority disallowed: {pr}, disallowed={disallowed}")

    score_band = expected.get("expected_score_band") or []
    if isinstance(score_band, list) and len(score_band) == 2:
        score = actual.get("score")
        if score is None:
            errors.append("score missing")
        else:
            try:
                s = float(score)
                lo = float(score_band[0])
                hi = float(score_band[1])
                if s < lo or s > hi:
                    errors.append(f"score out of band: score={s}, band={score_band}")
            except (TypeError, ValueError):
                errors.append(f"score invalid: {score}")

    signal_assertions = expected.get("expected_signal_assertions") or {}
    signals = actual.get("signals") or {}
    if isinstance(signal_assertions, dict):
        for key, value in signal_assertions.items():
            actual_value = signals.get(key)
            if isinstance(value, list):
                if str(actual_value) not in [str(v) for v in value]:
                    errors.append(f"signal mismatch: {key} expected one of {value}, actual={actual_value}")
            else:
                if str(actual_value) != str(value):
                    errors.append(f"signal mismatch: {key} expected={value}, actual={actual_value}")

    reason_any = expected.get("reason_must_include_any") or []
    if isinstance(reason_any, list) and reason_any:
        if not _contains_any(str(actual.get("reason") or ""), reason_any):
            errors.append(f"reason does not contain any of {reason_any}")

    return errors


def _eval_summary_block(actual: dict[str, Any], expected: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    summary = str(actual.get("summary") or "")
    key_points = actual.get("key_points") or []
    open_questions = actual.get("open_questions") or []
    joined = "\n".join(
        [summary] + [str(x) for x in key_points] + [str(x) for x in open_questions]
    )

    must_capture = expected.get("must_capture") or []
    if isinstance(must_capture, list):
        for e in _contains_all(joined, must_capture):
            errors.append(f"{label}: {e}")

    must_not = expected.get("must_not_hallucinate") or []
    if isinstance(must_not, list):
        low = joined.lower()
        for token in must_not:
            t = str(token).strip().lower()
            if t and t in low:
                errors.append(f"{label}: hallucinated token appears: {token}")

    kp_range = expected.get("expected_key_points_count_range") or []
    if isinstance(kp_range, list) and len(kp_range) == 2:
        n = len(key_points)
        if n < int(kp_range[0]) or n > int(kp_range[1]):
            errors.append(f"{label}: key_points count out of range: {n} not in {kp_range}")

    oq_expected = expected.get("expected_open_questions") or []
    if isinstance(oq_expected, list) and oq_expected:
        joined_oq = "\n".join([str(x) for x in open_questions])
        for token in oq_expected:
            t = str(token).strip()
            if t and not _semantic_match(joined_oq, t, threshold=0.76):
                errors.append(f"{label}: open_questions missing token (semantic): {token}")

    if label == "short":
        max_chars = expected.get("short_summary_max_chars")
        if isinstance(max_chars, int) and len(summary) > max_chars:
            errors.append(f"short: summary too long ({len(summary)} > {max_chars})")
    if label == "long":
        min_chars = expected.get("long_summary_min_chars")
        if isinstance(min_chars, int) and len(summary) < min_chars:
            errors.append(f"long: summary too short ({len(summary)} < {min_chars})")

    return errors


def _eval_reply(actual: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    should_generate = expected.get("should_generate")
    draft = str(actual.get("reply_draft") or "")
    if isinstance(should_generate, bool):
        if should_generate and not draft.strip():
            errors.append("reply should generate but draft is empty")
        if not should_generate and draft.strip():
            errors.append("reply should be empty but got draft")

    if not draft.strip():
        return errors

    must_include = expected.get("must_include") or []
    if isinstance(must_include, list):
        for e in _contains_all(draft, must_include):
            errors.append(e)

    must_not = expected.get("must_not_include") or []
    if isinstance(must_not, list):
        low = draft.lower()
        for token in must_not:
            t = str(token).strip().lower()
            if t and t in low:
                errors.append(f"must_not_include token appears: {token}")

    tone = str(expected.get("tone") or "").strip().lower()
    if tone == "professional_neutral":
        ban = ["hey dude", "lol", "bro", "yo "]
        low = draft.lower()
        for b in ban:
            if b in low:
                errors.append(f"tone violation with slang token: {b}")

    max_cat = str(expected.get("max_length_category") or "").strip().lower()
    if max_cat == "short" and len(draft) > 450:
        errors.append(f"reply too long for short category: {len(draft)} chars")

    return errors


def _status_rank(status: str) -> int:
    s = str(status or "").upper()
    if s == "PASS":
        return 2
    if s == "PARTIAL":
        return 1
    return 0


def _safe_parse_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        # Try to recover the first JSON object block.
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(raw[start:end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}


def _judge_with_rubric(
    *,
    client: OpenAI,
    model: str,
    capability: str,
    thread_text: str,
    expected: dict[str, Any],
    actual: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """
    对生成任务（summary/reply）做 rubric-based 语义评估。
    说明：
    - triage 是低自由度任务，继续 deterministic 规则评测。
    - summary/reply 是高自由度生成任务，不再做字面锚点主判据，改为 rubric 评估。
    """
    rubric = (
        "你是严格但保守的测试评审员。你的职责是评估“actual output 是否完成任务”，"
        "而不是比较措辞是否逐字一致。"
    )
    user_prompt = f"""
请根据以下信息评估 {capability} 的质量：

[邮件原文/线程]
{thread_text}

[期望要求 expected]
{json.dumps(expected, ensure_ascii=False, indent=2)}

[实际输出 actual]
{json.dumps(actual, ensure_ascii=False, indent=2)}

评分标准（必须遵守）：
- PASS：满足全部关键要求，无严重问题。
- PARTIAL：满足核心要求，但有缺失、表达问题或轻微不完整。
- FAIL：未完成核心任务，或存在严重 hallucination / 明显错误理解 / 明显不当语气。

请仅输出严格 JSON（不要 markdown），格式如下：
{{
  "result": "PASS|PARTIAL|FAIL",
  "reason": "一句话总评",
  "checks": {{
    "coverage": "PASS|PARTIAL|FAIL",
    "faithfulness": "PASS|PARTIAL|FAIL",
    "usefulness": "PASS|PARTIAL|FAIL"
  }},
  "missing_items": ["..."],
  "hallucinations": ["..."],
  "notes": ["..."]
}}
""".strip()

    completion = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": rubric},
            {"role": "user", "content": user_prompt},
        ],
    )
    raw = completion.choices[0].message.content or ""
    parsed = _safe_parse_json(raw)
    if not parsed:
        parsed = {
            "result": "FAIL",
            "reason": "Evaluator JSON parse failed.",
            "checks": {"coverage": "FAIL", "faithfulness": "FAIL", "usefulness": "FAIL"},
            "missing_items": [],
            "hallucinations": ["evaluator_output_not_json"],
            "notes": [raw[:500]],
        }
    return parsed, raw


def _normalize_judgment(j: dict[str, Any]) -> dict[str, Any]:
    result = str(j.get("result") or "FAIL").upper()
    if result not in ("PASS", "PARTIAL", "FAIL"):
        result = "FAIL"
    checks = j.get("checks")
    if not isinstance(checks, dict):
        checks = {}
    missing = j.get("missing_items")
    if not isinstance(missing, list):
        missing = []
    hallucinations = j.get("hallucinations")
    if not isinstance(hallucinations, list):
        hallucinations = []
    notes = j.get("notes")
    if not isinstance(notes, list):
        notes = []
    return {
        "result": result,
        "reason": str(j.get("reason") or "").strip(),
        "checks": checks,
        "missing_items": [str(x) for x in missing],
        "hallucinations": [str(x) for x in hallucinations],
        "notes": [str(x) for x in notes],
    }


def _triage_to_result(errors: list[str], actual: dict[str, Any]) -> dict[str, Any]:
    # triage 保持规则评测：无错 PASS；少量轻错 PARTIAL；多项或关键错 FAIL
    if not errors:
        grade = "PASS"
    else:
        critical = any("priority" in e or "needs_response" in e for e in errors)
        if critical or len(errors) >= 2:
            grade = "FAIL"
        else:
            grade = "PARTIAL"
    return {
        "result": grade,
        "reason": "rule-based triage evaluation",
        "checks": {
            "rule_assertions": "PASS" if not errors else ("PARTIAL" if grade == "PARTIAL" else "FAIL"),
        },
        "errors": errors,
        "actual": actual,
    }


def _overall_case_status(triage_status: str, summary_status: str, reply_status: str) -> str:
    """
    overall 判定策略：
    - triage 是决策主干，FAIL 则整体 FAIL；
    - summary/reply 为生成能力，双 FAIL 则整体 FAIL；
    - 任一 PARTIAL/FAIL 则整体 PARTIAL；
    - 三者全 PASS 才 PASS。
    """
    t = str(triage_status).upper()
    s = str(summary_status).upper()
    r = str(reply_status).upper()
    if t == "FAIL":
        return "FAIL"
    if s == "FAIL" and r == "FAIL":
        return "FAIL"
    if t == "PASS" and s == "PASS" and r == "PASS":
        return "PASS"
    return "PARTIAL"


def main() -> int:
    load_project_dotenv(override=True)
    parser = argparse.ArgumentParser(description="Run calibrated triage+summary+reply test cases.")
    parser.add_argument("--cases-dir", default="test/cases", help="Directory for case JSON files.")
    parser.add_argument("--all", action="store_true", help="Run all cases.")
    parser.add_argument("--case", action="append", default=[], help="Run selected case ids.")
    parser.add_argument("--model", default=None, help="Override model. Defaults to OPENAI_MODEL.")
    parser.add_argument("--save-dir", default="test/results", help="Result directory.")
    parser.add_argument(
        "--current-user-identity",
        default="display_name=test-user; emails_or_aliases=test@company.com",
        help="Fallback identity string when case has no user_context.",
    )
    args = parser.parse_args()

    cases_dir = Path(args.cases_dir)
    if not cases_dir.exists():
        raise FileNotFoundError(f"Cases directory not found: {cases_dir}")
    if args.all:
        case_files = sorted(cases_dir.glob("*.json"))
    else:
        if not args.case:
            raise ValueError("Use --all or provide --case.")
        case_files = [cases_dir / f"{cid}.json" for cid in args.case]

    save_dir = Path(args.save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    model_name = args.model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set.")

    summary_rows: list[dict[str, Any]] = []
    passed_cases = 0
    triage_counts = {"PASS": 0, "PARTIAL": 0, "FAIL": 0}
    summary_counts = {"PASS": 0, "PARTIAL": 0, "FAIL": 0}
    reply_counts = {"PASS": 0, "PARTIAL": 0, "FAIL": 0}
    overall_counts = {"PASS": 0, "PARTIAL": 0, "FAIL": 0}
    eval_client = OpenAI(api_key=api_key)

    for path in case_files:
        case_id = path.stem
        if not path.exists():
            summary_rows.append({"case": case_id, "status": "FAIL", "errors": [f"file not found: {path}"]})
            continue
        try:
            case_obj = _load_case(path)
            case_id = str(case_obj.get("id") or case_id)
            case_input = case_obj.get("input") or {}
            expected = case_obj.get("expected_output") or {}
            user_context = case_input.get("user_context") or {}

            unified = _to_unified_input(case_input)
            thread_text = build_thread_text(unified)
            identity = _to_identity(user_context, args.current_user_identity)

            triage_raw = call_llm_for_reply_decision(
                model=model_name,
                thread_text=thread_text,
                api_key=api_key,
                current_user_identity=identity,
            )
            triage_norm = _normalize_triage(triage_raw)
            short_sum = analysis_to_dict(analyze_thread_text(thread_text, model=model_name, style="short", api_key=api_key))
            long_sum = analysis_to_dict(analyze_thread_text(thread_text, model=model_name, style="long", api_key=api_key))

            triage_expected = expected.get("triage") or {}
            summary_expected = expected.get("summary") or {}
            reply_expected = expected.get("reply") or {}

            triage_errors = _eval_triage(triage_norm, triage_expected) if isinstance(triage_expected, dict) else []
            triage_result = _triage_to_result(triage_errors, triage_norm)

            # summary / reply 用 rubric-based semantic judge（而非字面匹配）
            summary_actual = {
                "short": short_sum,
                "long": long_sum,
            }
            summary_judged, summary_raw = _judge_with_rubric(
                client=eval_client,
                model=model_name,
                capability="summary",
                thread_text=thread_text,
                expected=summary_expected if isinstance(summary_expected, dict) else {},
                actual=summary_actual,
            )
            summary_result = _normalize_judgment(summary_judged)

            reply_actual = {
                "reply_draft": triage_norm.get("reply_draft"),
                "needs_response": triage_norm.get("needs_response"),
                "priority": triage_norm.get("priority"),
                "reason": triage_norm.get("reason"),
            }
            reply_judged, reply_raw = _judge_with_rubric(
                client=eval_client,
                model=model_name,
                capability="reply",
                thread_text=thread_text,
                expected=reply_expected if isinstance(reply_expected, dict) else {},
                actual=reply_actual,
            )
            reply_result = _normalize_judgment(reply_judged)

            overall = _overall_case_status(
                triage_result["result"], summary_result["result"], reply_result["result"]
            )
            if overall == "PASS":
                passed_cases += 1

            triage_counts[triage_result["result"]] += 1
            summary_counts[summary_result["result"]] += 1
            reply_counts[reply_result["result"]] += 1
            overall_counts[overall] += 1

            actual_bundle = {
                "triage_raw": triage_raw,
                "triage_normalized": triage_norm,
                "summary_short": short_sum,
                "summary_long": long_sum,
            }
            (save_dir / f"{case_id}.actual.json").write_text(
                json.dumps(actual_bundle, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            (save_dir / f"{case_id}.summary_judgment.raw.txt").write_text(summary_raw, encoding="utf-8")
            (save_dir / f"{case_id}.reply_judgment.raw.txt").write_text(reply_raw, encoding="utf-8")

            per_case = {
                "case": case_id,
                "scenario_type": case_obj.get("scenario_type"),
                "overall_result": overall,
                "triage_result": triage_result,
                "summary_result": summary_result,
                "reply_result": reply_result,
                "expected": expected,
                "actual_file": f"{case_id}.actual.json",
                "summary_judgment_raw_file": f"{case_id}.summary_judgment.raw.txt",
                "reply_judgment_raw_file": f"{case_id}.reply_judgment.raw.txt",
            }
            (save_dir / f"{case_id}.result.json").write_text(
                json.dumps(per_case, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            summary_rows.append(per_case)
        except Exception as err:
            summary_rows.append(
                {
                    "case": case_id,
                    "overall_result": "FAIL",
                    "triage_result": {"result": "FAIL", "reason": "runner_exception", "errors": [str(err)]},
                    "summary_result": {"result": "FAIL", "reason": "runner_exception"},
                    "reply_result": {"result": "FAIL", "reason": "runner_exception"},
                    "errors": [str(err)],
                }
            )
            triage_counts["FAIL"] += 1
            summary_counts["FAIL"] += 1
            reply_counts["FAIL"] += 1
            overall_counts["FAIL"] += 1

    report = {
        "total": len(case_files),
        "passed": passed_cases,
        "failed": len(case_files) - passed_cases,
        "triage_counts": triage_counts,
        "summary_counts": summary_counts,
        "reply_counts": reply_counts,
        "overall_counts": overall_counts,
        "overall_pass_rate": round((overall_counts["PASS"] / len(case_files)) if case_files else 0.0, 4),
        "results": summary_rows,
    }
    (save_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed == len(case_files) else 1


if __name__ == "__main__":
    raise SystemExit(main())

