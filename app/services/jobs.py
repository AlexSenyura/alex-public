import asyncio
from typing import Any, Callable

from redis import Redis
from rq import Queue, get_current_job

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.snapshot import Snapshot, SnapshotVideo
from app.schemas.topics import TopicRequest
from app.schemas.youtube import VideoResult, YouTubeQuery
from app.services.ai_topics import generate_topics
from app.services.youtube import fetch_videos


_settings = get_settings()
redis_conn = Redis.from_url(_settings.redis_url)
queue = Queue("default", connection=redis_conn)


def enqueue_job(func: Callable, *args, **kwargs):
    return queue.enqueue(func, *args, **kwargs)


def _update_progress(job, value: int):
    job.meta["progress"] = value
    job.save_meta()


def youtube_job(query_data: dict) -> list[dict[str, Any]]:
    job = get_current_job()  # type: ignore[assignment]
    _update_progress(job, 5)

    async def _run():
        query = YouTubeQuery(**query_data)
        videos = await fetch_videos(query)
        _update_progress(job, 70)
        async with SessionLocal() as db:
            snapshot = Snapshot(params=query.model_dump(exclude={"api_key"}))
            db.add(snapshot)
            await db.flush()
            for v in videos:
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
                        extra={"rpm_range": [query.rpm_low, query.rpm_high], "channel_id": v.channel_id},
                    )
                )
            await db.commit()
        _update_progress(job, 100)
        return [v.model_dump(mode="json") for v in videos]

    return asyncio.run(_run())


def topics_job(request_data: dict) -> list[dict[str, Any]]:
    job = get_current_job()  # type: ignore[assignment]
    _update_progress(job, 5)

    async def _run():
        req = TopicRequest(**request_data)
        ideas = await generate_topics(req)
        _update_progress(job, 100)
        return [i.model_dump(mode="json") for i in ideas]

    return asyncio.run(_run())


def job_status(job_id: str) -> dict[str, Any]:
    job = queue.fetch_job(job_id)
    if not job:
        return {"status": "not_found"}
    meta = job.meta or {}
    progress = meta.get("progress", 0)
    return {
        "status": job.get_status(),
        "progress": progress,
        "result": job.result,
        "error": getattr(job, "exc_info", None),
    }
