from typing import Literal
from pydantic import BaseModel, Field, field_validator


class Profile(BaseModel):
    interests: list[str] = Field(default_factory=lambda: ["多智能体协作", "Agent 评测", "需求理解", "Agent 强化学习", "大模型后训练"] , max_length=20)
    include_keywords: list[str] = Field(default_factory=list, max_length=30)
    exclude_keywords: list[str] = Field(default_factory=list, max_length=30)
    days: int = Field(default=730, ge=0, le=36500)
    page_size: int = Field(default=10, ge=1, le=50)
    language: Literal["zh-CN", "en"] = "zh-CN"
    topic_weight: float = Field(default=0.75, ge=0, le=1)

    @field_validator("interests", "include_keywords", "exclude_keywords")
    @classmethod
    def clean_keywords(cls, values: list[str]) -> list[str]:
        if any(len(value) > 160 for value in values):
            raise ValueError("每个关键词或兴趣不超过 160 字符")
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class CollectionPatch(BaseModel):
    saved: bool | None = None
    read: bool | None = None
    interested: bool | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=20000)

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(len(tag) > 60 for tag in value):
            raise ValueError("标签不能超过 60 字符")
        return list(dict.fromkeys(t.strip() for t in value if t.strip())) if value is not None else None


class QueryInput(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
