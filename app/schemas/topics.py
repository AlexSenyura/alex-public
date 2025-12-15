from pydantic import BaseModel, Field
from typing import List


class TopicRequest(BaseModel):
    keyword: str
    bulk_n: int = Field(20, ge=5, le=100)
    final_n: int = Field(10, ge=5, le=50)
    seed: str | None = None


class TopicIdea(BaseModel):
    title: str
    title_variants: List[str]
    hook: str
    cold_open_15s: str
    outline: List[str]
    thumbnail_prompt: str
    tags: List[str]
    cta: str
    score: int | None = None
    why_it_works: str | None = None
