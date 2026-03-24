from __future__ import annotations

import os
from typing import Any, Dict

from email_assistant.llm_client import call_llm_for_analysis
from email_assistant.models import AnalysisOutput, UnifiedInput, safe_parse_output
from email_assistant.preprocessor import build_thread_text


def analyze_unified_input(
    email_input: UnifiedInput,
    *,
    model: str | None = None,
    api_key: str | None = None,
) -> AnalysisOutput:
    """Build thread text from unified input and run LLM analysis."""
    thread_text = build_thread_text(email_input)
    return analyze_thread_text(thread_text, model=model, api_key=api_key)


def analyze_thread_text(
    thread_text: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
) -> AnalysisOutput:
    """Send preformatted thread text to the LLM and return parsed analysis."""
    key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY is not set.")
    model_name = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    raw = call_llm_for_analysis(model=model_name, thread_text=thread_text, api_key=key)
    return safe_parse_output(raw)


def analysis_to_dict(output: AnalysisOutput) -> Dict[str, Any]:
    return output.model_dump()
