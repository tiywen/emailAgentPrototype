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
你是一个邮件助手，需要判断一封邮件的回复优先级，并在需要时生成回复草稿。

当前用户身份（你必须据此判断“是否需要用户本人回复”）：
{current_user_identity}

任务：
1. 阅读邮件内容
2. 判断该邮件的回复优先级（1/2/3/4）
3. 根据优先级判断是否需要回复
4. 如果需要回复，生成一封合适的回复草稿

优先级定义：
- Priority 1：必须尽快回复。邮件包含明确请求、直接提问、紧急事项、临近截止时间、重要决策确认，或如果不回复可能造成明显延误/误解。
- Priority 2：建议回复。邮件包含一般性问题、协作请求、礼貌性确认、需要给出态度或反馈，但紧急性较低。
- Priority 3：通常不需要回复。邮件主要是通知、同步进展、抄送信息，虽然与用户有关，但没有明确要求用户行动。
- Priority 4：明确不需要回复。邮件纯通知、群发公告、系统邮件、营销邮件，或内容已闭环，无回复价值。

回复规则：
- Priority 1 和 Priority 2：需要回复
- Priority 3 和 Priority 4：不需要回复

优先级上调规则：
在初步判断后，如果邮件满足以下任一条件，则上调优先级：
- 出现明确问题、请求、确认需求
- 提到 deadline、as soon as possible、urgent、today、tomorrow、by [date] 等时间要求
- 发件人是重要联系人，或邮件涉及关键合作、面试、录用、客户、老师、上级、重要项目
- 若不回复，可能影响流程推进、关系维护或业务结果
- 对方在跟进此前未获回复的事项
- 邮件明确期待用户给出决定、材料、时间安排或批准

线程责任归属规则（必须优先判断）：
- 不能只看单封邮件字面内容，必须结合整个 thread 的上下文判断“任务是否属于当前用户本人”。
- 仅当线程中存在明确证据表明当前用户是责任方/决策方/被直接点名执行者时，才允许按请求或时效因素上调优先级。
- 如果当前用户只是 FYI 被动卷入（例如仅被抄送、未被直接提问、未被分配任务、历史发言中未承担责任），则不应因为礼貌性措辞或一般性推进语句而上调优先级。
- 对“你/你们”指代不清、责任主体不明确时，默认按较低优先级处理（保守判断），并在判断原因里说明“责任归属不明确”。
- 若邮件中出现的收件人/责任人不是“当前用户身份”中的邮箱或别名，则默认不判定为“需要用户本人回复”，除非线程明确要求当前用户做决策/批准/提供材料。

上调规则说明：
- 轻微触发条件：上调 1 级
- 强触发条件（如紧急 deadline、直接催办、关键决策）：可上调 2 级
- Priority 1 为最高级，不能继续上调

要求：
- 判断要保守、准确，不要因为礼貌措辞就误判为需要回复
- 不编造邮件中没有的信息
- 如果生成回复草稿，语言要专业、礼貌、简洁，并贴合原邮件语气（正式/半正式）
- 如果信息不足但仍可能需要回复，可生成一个安全、通用的草稿（明确指出需要对方确认的信息）
- 在“是否需要回复=true”前，必须先确认“该任务对当前用户存在直接责任或明确期待动作”；若无，则倾向 false（Priority 3/4）。

语言规则（必须遵守）：
- 回复与原因的语言要与邮件语言一致：中文邮件用中文；英文邮件用英文；多语种邮件则分别用对应语言（可在同一字段中分段呈现）。

输出格式（必须严格遵守，仅输出 JSON，不要输出其它任何文本）：
{{
  "是否需要回复": true/false,
  "判断原因": "...（必须包含：优先级=1/2/3/4 + 简短原因）",
  "回复草稿": \"...\"  // 如果不需要回复则为空字符串
}}

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
        return json.loads(content)
    except json.JSONDecodeError as err:
        raise ValueError(
            "LLM output is not valid JSON. Raw output:\n"
            f"{content}"
        ) from err
