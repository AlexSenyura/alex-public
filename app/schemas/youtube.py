from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class YouTubeQuery(BaseModel):
    api_key: str = Field(..., min_length=10)
    keywords: List[str]
    days_back: int = 7
    region: str = "US"
    language: str = "en"
    order: str = "viewCount"
    per_query: int = 5
    age_min: Optional[int] = None
    age_max: Optional[int] = None
    channel_age_min: Optional[int] = None
    channel_age_max: Optional[int] = None
    min_minutes: Optional[int] = None
    max_minutes: Optional[int] = None
    min_views_per_day: Optional[float] = None
    min_eng_pct: Optional[float] = None
    max_subs: Optional[int] = None
    rpm_low: Optional[float] = None
    rpm_high: Optional[float] = None


class VideoResult(BaseModel):
    video_id: str
    title: str
    channel_title: str
    channel_id: str | None = None
    published_at: datetime
    duration_min: float
    views: int
    views_per_day: float
    engagement_pct: float
    subs: Optional[int]
    views_to_subs: Optional[float]
    score: float
    rating: float
    trend_score: float
    keyword: str
    url: str
    thumb_url: str | None = None


class SnapshotCreateRequest(BaseModel):
    query: YouTubeQuery
    results: list[VideoResult]
