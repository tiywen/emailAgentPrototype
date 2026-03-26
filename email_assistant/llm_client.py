from __future__ import annotations

import json
from typing import Any, Dict, Literal

from openai import OpenAI


SummaryStyle = Literal["short", "long"]
ReplyPriority = Literal[1, 2, 3, 4]


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


def build_reply_decision_prompt(thread_text: str, *, current_user_identity: str) -> str:
    return f"""
你是一个帮助用户处理工作邮件的智能助手。
你的任务是分析给定邮件以及可能提供的邮件线程，判断该邮件的处理优先级。
请严格按照以下步骤执行，不要跳步，也不要做超出信息范围的推断。

---步骤1：提取关键信号---
请从邮件中识别以下信息：
1. 发件人重要性（sender_importance）：
- 上级（manager）
- 同级同事（peer）
- 下属（report）
- 外部联系人（external）
- 不明确（unknown）
2. 是否包含明确请求（has_request）：
- 是否要求用户执行动作（如回复、审批、提交）
3. 是否包含时间信息（has_deadline）：
- 是否存在 deadline / 紧急时间要求
4. 邮件语气（tone）：
- 信息通知（FYI）/ 请求 / 紧急 / 不明确
5. 是否需要用户回复（requires_response）：
- 明确需要 / 可能需要 / 不需要
---步骤2：根据规则计算 Priority Score---
请基于以下规则为每个信号赋予分值，并计算总分：
1. 发件人重要性：
- manager = +3
- external = +2
- peer / report = +1
- unknown = 0
2. 请求信号：
- 有明确请求 = +3
- 无请求 = 0
3. 时间紧迫性：
- 有 deadline / 明确时间要求 = +3
- 无 = 0
4. 语气：
- 紧急 = +2
- 请求 = +1
- FYI / 信息类 = -2
5. 是否需要回复：
- 明确需要回复 = +2
- 可能需要 = +1
- 不需要 = 0
计算：
Priority Score = 上述所有分值之和
---步骤3：根据 Score 判断优先级---
请根据以下区间分类：
- 高优先级（HIGH）：Score ≥ 6
- 中优先级（MEDIUM）：Score 在 3–5
- 不确定（UNCERTAIN）：Score 在 1–2
- 低优先级（LOW）：Score ≤ 0
注意：
- 只有在非常确定不需要任何行动时，才允许输出“低优先级”
- 如果存在不确定性或信息不足以做出判断，请优先选择“UNCERTAIN”，而不是低优先级
---步骤4：输出判断理由---
用一句话说明该邮件为什么被归类为该优先级（重点说明关键影响因素）。
---步骤5：输出置信度---
输出0到1的小数，表示你对该判断的信心。
---步骤6：生成回复草稿---
- 当优先级为 HIGH / MEDIUM / UNCERTAIN 时，默认生成一份可直接发送前编辑的回复草稿。
- 当优先级为 LOW 时，回复草稿留空字符串。
- 草稿要求：专业、礼貌、简洁，并尽量贴合原邮件语气；信息不足时使用安全措辞并提出待确认点。
---输出格式（必须严格遵守）---
{{
"priority": "HIGH / MEDIUM / UNCERTAIN / LOW",
"priority_score": 数值,
"confidence": 0.0,
"signals": {{
"sender_importance": "",
"has_request": true,
"has_deadline": false,
"tone": "",
"requires_response": ""
}},
"reasoning": "",
"reply_draft": ""
}}

当前用户身份（用于识别“是否需要当前用户动作”）：
{current_user_identity}

输入邮件：
{thread_text}
""".strip()


def call_llm_for_reply_decision(
    model: str,
    thread_text: str,
    api_key: str | None = None,
    current_user_identity: str = "unknown",
) -> Dict[str, Any]:
    client = OpenAI(api_key=api_key) if api_key else OpenAI()
    prompt = build_reply_decision_prompt(thread_text, current_user_identity=current_user_identity)

    completion = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": "只输出严格 JSON。不要输出 markdown。"},
            {"role": "user", "content": prompt},
        ],
    )

    content = completion.choices[0].message.content or "{}"
    try:
        data = json.loads(content)
        if isinstance(data, dict) and "priority" in data:
            priority = str(data.get("priority") or "").strip().upper()
            score = data.get("priority_score")
            confidence = data.get("confidence")
            reasoning = str(data.get("reasoning") or "").strip()
            signals = data.get("signals") if isinstance(data.get("signals"), dict) else {}
            requires_response = str(signals.get("requires_response") or "").strip()

            need_reply = priority in ("HIGH", "MEDIUM", "UNCERTAIN")
            if not need_reply and requires_response in ("明确需要", "可能需要"):
                need_reply = True
            if priority == "LOW":
                need_reply = False

            draft = str(
                data.get("reply_draft")
                or data.get("回复草稿")
                or ""
            ).strip()
            if priority == "LOW":
                draft = ""

            reason_parts = []
            if reasoning:
                reason_parts.append(reasoning)
            reason_parts.append(f"priority={priority or 'UNCERTAIN'}")
            if score is not None:
                reason_parts.append(f"score={score}")
            if confidence is not None:
                reason_parts.append(f"confidence={confidence}")

            return {
                "是否需要回复": need_reply,
                "判断原因": "；".join(reason_parts),
                "回复草稿": draft,
                "_raw_priority": priority or "UNCERTAIN",
                "_raw_priority_score": score,
                "_raw_confidence": confidence,
                "_raw_signals": signals,
                "_raw_reasoning": reasoning,
                "_raw_reply_draft": str(data.get("reply_draft") or "").strip(),
            }
        return data
    except json.JSONDecodeError as err:
        raise ValueError(
            "LLM output is not valid JSON. Raw output:\n"
            f"{content}"
        ) from err
