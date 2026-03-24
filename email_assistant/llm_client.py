from __future__ import annotations

import json
from typing import Any, Dict

from openai import OpenAI


def build_prompt(thread_text: str) -> str:
    return f"""
You are an email analysis assistant for workplace communication.

Task:
Analyze the input email thread and extract:
1) summary
2) key_points
3) action_items
4) open_questions

Rules:
- Only use facts present in the input.
- Do not hallucinate or infer unsupported details.
- Action items must include: task, owner, deadline.
- If owner or deadline is unclear, set them to "unknown".
- Return strict JSON only. No markdown, no extra text.
- Keep language concise, professional, and useful for work.

Return schema:
{{
  "summary": "string",
  "key_points": ["string"],
  "action_items": [
    {{
      "task": "string",
      "owner": "string or unknown",
      "deadline": "string or unknown"
    }}
  ],
  "open_questions": ["string"]
}}

Input:
{thread_text}
""".strip()


def call_llm_for_analysis(
    model: str,
    thread_text: str,
    api_key: str | None = None,
) -> Dict[str, Any]:
    client = OpenAI(api_key=api_key) if api_key else OpenAI()
    prompt = build_prompt(thread_text)

    completion = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": "You output strict JSON only."},
            {"role": "user", "content": prompt},
        ],
    )

    content = completion.choices[0].message.content or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError as err:
        raise ValueError(
            "LLM output is not valid JSON. Raw output:\n"
            f"{content}"
        ) from err
