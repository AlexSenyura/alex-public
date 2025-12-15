from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.db.session import get_db
from app.core.templates import templates
from app.models.snapshot import Snapshot, SnapshotVideo

router = APIRouter()


def _mover_payload(prev: SnapshotVideo, latest: SnapshotVideo):
    return {
        "title": latest.title,
        "keyword": latest.keyword,
        "views": latest.views,
        "views_per_day": latest.views_per_day,
        "engagement_pct": latest.engagement_pct,
        "vpd_delta": latest.views_per_day - (prev.views_per_day if prev else 0),
        "views_delta": latest.views - (prev.views if prev else 0),
        "eng_delta": latest.engagement_pct - (prev.engagement_pct if prev else 0),
        "url": latest.url,
    }


@router.get("/api/movers/latest")
async def movers_latest(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    snaps = (
        await db.execute(
            select(Snapshot)
            .options(selectinload(Snapshot.videos))
            .order_by(Snapshot.id.desc())
            .limit(2)
        )
    ).scalars().all()
    if len(snaps) < 2:
        return []
    latest, prev = snaps[0], snaps[1]
    result = []
    prev_map = {v.video_id: v for v in prev.videos}
    for v in latest.videos:
        result.append(_mover_payload(prev_map.get(v.video_id), v))
    result.sort(key=lambda x: x["vpd_delta"], reverse=True)
    return result[:10]


@router.get("/movers", response_class=HTMLResponse)
async def movers_page(request: Request, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    data = await movers_latest(db, user)
    return templates.TemplateResponse("movers.html", {"request": request, "items": data})
