from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.db.session import get_db
from app.models.snapshot import Snapshot, SnapshotVideo
from app.schemas.youtube import SnapshotCreateRequest

router = APIRouter()


@router.post("/api/snapshots/create")
async def create_snapshot(payload: SnapshotCreateRequest, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    snapshot = Snapshot(params=payload.query.model_dump(exclude={"api_key"}))
    db.add(snapshot)
    await db.flush()
    for v in payload.results:
        db.add(
            SnapshotVideo(
                snapshot_id=snapshot.id,
                video_id=v.video_id,
                title=v.title,
                channel_title=v.channel_title,
                keyword=v.keyword,
                published_at=v.published_at,
                duration_min=v.duration_min,
                views=v.views,
                views_per_day=v.views_per_day,
                engagement_pct=v.engagement_pct,
                subs=v.subs,
                views_to_subs=v.views_to_subs,
                score=v.score,
                rating=v.rating,
                trend_score=v.trend_score,
                url=v.url,
                thumb_url=v.thumb_url,
            )
        )
    await db.commit()
    return {"snapshot_id": snapshot.id}
