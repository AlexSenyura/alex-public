from pydantic import BaseModel


class KeywordScore(BaseModel):
    keyword: str
    key_score: float
    seo_intent_score: float
    seo_difficulty_lite: float
    total_score: float
