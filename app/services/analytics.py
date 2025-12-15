from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.snapshot import SnapshotVideo
from app.schemas.analytics import KeywordScore


async def top_keywords(db: AsyncSession, limit: int = 10) -> list[KeywordScore]:
    stmt = (
        select(
            SnapshotVideo.keyword,
            func.avg(SnapshotVideo.trend_score).label("score"),
            func.avg(SnapshotVideo.engagement_pct).label("eng"),
            func.avg(SnapshotVideo.views_per_day).label("vpd"),
        )
        .group_by(SnapshotVideo.keyword)
        .order_by(func.avg(SnapshotVideo.trend_score).desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.all()
    scores: list[KeywordScore] = []
    for keyword, score, eng, vpd in rows:
        key_score = float(score or 0)
        seo_intent_score = float((eng or 0) * 1.2 + (vpd or 0) * 0.05)
        seo_difficulty_lite = max(1.0, (vpd or 1) ** 0.25)
        total = key_score + seo_intent_score - seo_difficulty_lite
        scores.append(
            KeywordScore(
                keyword=keyword,
                key_score=key_score,
                seo_intent_score=seo_intent_score,
                seo_difficulty_lite=seo_difficulty_lite,
                total_score=total,
            )
        )
    scores.sort(key=lambda x: x.total_score, reverse=True)
    return scores
