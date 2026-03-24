from __future__ import annotations

from typing import List

from email_assistant.models import Message, UnifiedInput


def _sort_messages(messages: List[Message]) -> List[Message]:
    def sort_key(msg: Message) -> str:
        parsed = msg.parsed_timestamp
        return parsed.isoformat() if parsed else msg.timestamp

    return sorted(messages, key=sort_key)


def build_thread_text(email_input: UnifiedInput) -> str:
    sorted_messages = _sort_messages(email_input.messages)
    blocks = []

    for index, msg in enumerate(sorted_messages, start=1):
        recipients = ", ".join(msg.recipients) if msg.recipients else "unknown"
        block = (
            f"Message #{index}\n"
            f"From: {msg.sender}\n"
            f"To: {recipients}\n"
            f"Time: {msg.timestamp}\n"
            f"Body:\n{msg.body.strip()}\n"
        )
        blocks.append(block)

    header = (
        f"Thread ID: {email_input.thread_id}\n"
        f"Subject: {email_input.subject}\n"
        f"Input Type: {email_input.input_type}\n"
    )

    return header + "\n" + "\n---\n".join(blocks)
