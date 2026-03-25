from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


class SingleEmailInput(BaseModel):
    subject: str
    sender: str
    recipients: List[str] = Field(default_factory=list)
    timestamp: str
    body: str

    @field_validator("recipients", mode="before")
    @classmethod
    def normalize_recipients(cls, value: object) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return []


class Message(BaseModel):
    sender: str
    recipients: List[str] = Field(default_factory=list)
    timestamp: str
    body: str

    @field_validator("recipients", mode="before")
    @classmethod
    def normalize_recipients(cls, value: object) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return []

    @property
    def parsed_timestamp(self) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))
        except ValueError:
            return None


class ThreadInput(BaseModel):
    thread_id: str
    subject: str
    messages: List[Message] = Field(default_factory=list)


class UnifiedInput(BaseModel):
    input_type: Literal["single", "thread"]
    thread_id: str
    subject: str
    messages: List[Message] = Field(default_factory=list)


class AnalysisOutput(BaseModel):
    summary: str = ""
    key_points: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)

    @field_validator("summary", mode="before")
    @classmethod
    def normalize_summary(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("key_points", "open_questions", mode="before")
    @classmethod
    def normalize_str_list(cls, value: object) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        return [text] if text else []

    @model_validator(mode="after")
    def normalize(self) -> "AnalysisOutput":
        # Keep stable defaults ("" / []), and ignore any extra fields (e.g., legacy action_items)
        return self


def safe_parse_output(raw_data: object) -> AnalysisOutput:
    try:
        return AnalysisOutput.model_validate(raw_data)
    except ValidationError:
        return AnalysisOutput()


class ReplyDecisionOutput(BaseModel):
    是否需要回复: bool = False
    判断原因: str = ""
    回复草稿: str = ""

    @field_validator("判断原因", "回复草稿", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("是否需要回复", mode="before")
    @classmethod
    def normalize_bool(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        text = str(value).strip().lower()
        return text in ("true", "1", "yes", "y", "是", "需要", "要")


def safe_parse_reply_decision(raw_data: object) -> ReplyDecisionOutput:
    try:
        return ReplyDecisionOutput.model_validate(raw_data)
    except ValidationError:
        return ReplyDecisionOutput()
