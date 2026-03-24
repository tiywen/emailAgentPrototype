from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from pydantic import ValidationError

from email_assistant.models import Message, SingleEmailInput, ThreadInput, UnifiedInput


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError("Input JSON root must be an object.")
    return payload


def _convert_single_email(single: SingleEmailInput) -> UnifiedInput:
    msg = Message(
        sender=single.sender,
        recipients=single.recipients,
        timestamp=single.timestamp,
        body=single.body,
    )
    return UnifiedInput(
        input_type="single",
        thread_id="single-email",
        subject=single.subject,
        messages=[msg],
    )


def parse_input_file(file_path: str) -> UnifiedInput:
    path = Path(file_path)
    payload = _load_json(path)

    if "messages" in payload:
        try:
            thread = ThreadInput.model_validate(payload)
            return UnifiedInput(
                input_type="thread",
                thread_id=thread.thread_id,
                subject=thread.subject,
                messages=thread.messages,
            )
        except ValidationError as err:
            raise ValueError(f"Invalid thread input schema: {err}") from err

    required_single_fields = {"subject", "sender", "recipients", "timestamp", "body"}
    if required_single_fields.issubset(payload.keys()):
        try:
            single = SingleEmailInput.model_validate(payload)
            return _convert_single_email(single)
        except ValidationError as err:
            raise ValueError(f"Invalid single email input schema: {err}") from err

    raise ValueError(
        "Unsupported input schema. Provide either thread format (thread_id, subject, messages[]) "
        "or single email format (subject, sender, recipients, timestamp, body)."
    )
