from __future__ import annotations

from pydantic import BaseModel, Field


class MessageCreate(BaseModel):
    from_id: str = Field(min_length=1)
    to_id: str = Field(min_length=1)
    content: str = Field(min_length=1)


class MessageRead(BaseModel):
    from_: str = Field(alias="from")
    to: str
    content: str
    ts: float


class HumanQuestionCreate(BaseModel):
    question: str = Field(min_length=1)
    options: str = ""
    from_agent: str = ""


class HumanQuestionReply(BaseModel):
    answer: str = Field(min_length=1)


class HumanQuestionRead(BaseModel):
    id: str
    from_: str = Field(alias="from")
    question: str
    options: str = ""
    answer: str | None = None
