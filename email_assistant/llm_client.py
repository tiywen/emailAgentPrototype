from __future__ import annotations

import json
from typing import Any, Dict, Literal

from openai import OpenAI


SummaryStyle = Literal["short", "long"]


def build_prompt(thread_text: str, *, style: SummaryStyle) -> str:
    style_instructions = (
        "Produce a compact, executive-style summary (2-4 sentences) and 3-6 bullet points."
        if style == "short"
        else "Produce a detailed summary (1-3 short paragraphs) and 6-12 bullet points with more context."
    )
    return f"""
You are an email analysis assistant for workplace communication.

Task:
Analyze the input email thread and extract:
1) summary
2) key_points
3) open_questions

Rules:
- Only use facts present in the input.
- Do not hallucinate or infer unsupported details.
- Return strict JSON only. No markdown, no extra text.
- Language (must follow the email thread):
  - If the substantive content is mainly one language (e.g. Chinese or English), write **summary**, **key_points**, **action_items** (task/owner/deadline), and **open_questions** entirely in that language. Do not translate to another language unless the thread itself mixes languages for the same point.
  - If the thread clearly mixes **multiple languages** in meaningful amounts, produce a **multilingual** result: keep **summary** as one coherent text that covers the same facts in each language that appears substantially in the input (e.g. a short Chinese paragraph plus a short English paragraph when both are present), and let **key_points** / **open_questions** items use the language of each cited part (or duplicate key facts per language when needed for clarity). **action_items** should use the language of the task as stated in the thread, or bilingual task text if the thread mixes languages for that item.
- Keep wording concise, professional, and useful for work.

Output detail level:
- {style_instructions}
- Do NOT include action items in the output for this version.

Return schema:
{{
  "summary": "string",
  "key_points": ["string"],
  "open_questions": ["string"]
}}

Input:
{thread_text}
""".strip()


def call_llm_for_analysis(
    model: str,
    thread_text: str,
    api_key: str | None = None,
    style: SummaryStyle = "short",
) -> Dict[str, Any]:
    client = OpenAI(api_key=api_key) if api_key else OpenAI()
    prompt = build_prompt(thread_text, style=style)

    completion = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": (
                    "You output strict JSON only. Match output language(s) to the email thread "
                    "as specified in the user prompt (single language vs multilingual)."
                ),
            },
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
