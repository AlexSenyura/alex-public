import asyncio
import math
from datetime import datetime, timedelta, timezone
from typing import List

import httpx
import isodate
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.schemas.youtube import VideoResult, YouTubeQuery


YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"


def _normalize_rating(score: float) -> float:
    return max(0.0, min(100.0, (math.log1p(score) / math.log1p(1_000_000)) * 100))


def _age_bonus(published_at: datetime) -> float:
    days = max((datetime.now(timezone.utc) - published_at).days, 1)
    return max(0.0, min(1.0, (30 - min(days, 30)) / 30))


def compute_metrics(item: dict, keyword: str, channel_info: dict | None = None) -> VideoResult:
    stats = item["statistics"]
    snippet = item["snippet"]
    content = item.get("contentDetails", {})
    published_at = datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00"))
    duration = isodate.parse_duration(content.get("duration", "PT0S"))
    duration_min = duration.total_seconds() / 60 if duration else 0
    views = int(stats.get("viewCount", 0))
    likes = int(stats.get("likeCount", 0))
    comments = int(stats.get("commentCount", 0))
    engagement_pct = ((likes + comments) / views * 100) if views else 0
    age_days = max((datetime.now(timezone.utc) - published_at).days, 1)
    views_per_day = views / age_days
    subs = int(channel_info.get("statistics", {}).get("subscriberCount", 0)) if channel_info else None
    views_to_subs = (views / subs) if subs else None
    base_score = views_per_day * (1 + engagement_pct / 100)
    rating = _normalize_rating(base_score)
    age_boost = _age_bonus(published_at)
    trend_score = base_score * (1 + 0.75 * age_boost)
    return VideoResult(
        video_id=item["id"],
        title=snippet.get("title", ""),
        channel_title=snippet.get("channelTitle", ""),
        channel_id=snippet.get("channelId"),
        published_at=published_at,
        duration_min=duration_min,
        views=views,
        views_per_day=views_per_day,
        engagement_pct=engagement_pct,
        subs=subs,
        views_to_subs=views_to_subs,
        score=base_score,
        rating=rating,
        trend_score=trend_score,
        keyword=keyword,
        url=f"https://www.youtube.com/watch?v={item['id']}",
        thumb_url=snippet.get("thumbnails", {}).get("high", {}).get("url"),
    )


@retry(wait=wait_exponential(min=1, max=10), stop=stop_after_attempt(4))
async def _get_json(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    resp = await client.get(url, params=params, timeout=20)
    if resp.status_code in {429, 500, 502, 503, 504}:
        resp.raise_for_status()
    resp.raise_for_status()
    return resp.json()


async def fetch_videos(query: YouTubeQuery) -> List[VideoResult]:
    settings = get_settings()
    if query.region not in settings.allowed_regions:
        raise ValueError("Непідтримуваний регіон")

    async with httpx.AsyncClient() as client:
        tasks = []
        published_after = (datetime.utcnow() - timedelta(days=query.days_back)).isoformat("T") + "Z"
        for keyword in query.keywords:
            params = {
                "part": "id,snippet",
                "q": keyword,
                "type": "video",
                "order": query.order,
                "regionCode": query.region,
                "relevanceLanguage": query.language,
                "publishedAfter": published_after,
                "maxResults": query.per_query,
                "key": query.api_key,
            }
            tasks.append(_get_json(client, YOUTUBE_SEARCH_URL, params))
        search_results = await asyncio.gather(*tasks)

        video_ids = []
        keyword_map = {}
        for kw, data in zip(query.keywords, search_results):
            for item in data.get("items", []):
                vid = item["id"]["videoId"]
                video_ids.append(vid)
                keyword_map[vid] = kw

        videos = []
        channel_ids = set()
        for chunk_start in range(0, len(video_ids), 50):
            chunk = video_ids[chunk_start : chunk_start + 50]
            params = {
                "part": "snippet,contentDetails,statistics",
                "id": ",".join(chunk),
                "key": query.api_key,
            }
            data = await _get_json(client, YOUTUBE_VIDEOS_URL, params)
            for item in data.get("items", []):
                vid = item["id"]
                channel_ids.add(item["snippet"]["channelId"])
                videos.append((item, keyword_map.get(vid, "")))

        channel_map: dict[str, dict] = {}
        if channel_ids:
            for chunk_start in range(0, len(channel_ids), 50):
                chunk = list(channel_ids)[chunk_start : chunk_start + 50]
                params = {
                    "part": "snippet,statistics",
                    "id": ",".join(chunk),
                    "key": query.api_key,
                }
                data = await _get_json(client, YOUTUBE_CHANNELS_URL, params)
                for ch in data.get("items", []):
                    channel_map[ch["id"]] = ch

        parsed_videos: list[VideoResult] = []
        for item, kw in videos:
            channel_info = channel_map.get(item["snippet"].get("channelId"))
            parsed_videos.append(compute_metrics(item, kw, channel_info))

    parsed_videos = [v for v in parsed_videos if _apply_filters(v, query, channel_map)]
    return parsed_videos


def _apply_filters(video: VideoResult, query: YouTubeQuery, channel_map: dict[str, dict]) -> bool:
    age_days = max((datetime.now(timezone.utc) - video.published_at).days, 1)
    if query.age_min and age_days < query.age_min:
        return False
    if query.age_max and age_days > query.age_max:
        return False
    if query.min_minutes and video.duration_min < query.min_minutes:
        return False
    if query.max_minutes and video.duration_min > query.max_minutes:
        return False
    if query.min_views_per_day and video.views_per_day < query.min_views_per_day:
        return False
    if query.min_eng_pct and video.engagement_pct < query.min_eng_pct:
        return False
    if query.max_subs and video.subs and video.subs > query.max_subs:
        return False
    if (query.channel_age_min or query.channel_age_max) and video.channel_id:
        channel = channel_map.get(video.channel_id)
        if not channel:
            return False
        published = channel.get("snippet", {}).get("publishedAt")
        if published:
            ch_age = max((datetime.now(timezone.utc) - datetime.fromisoformat(published.replace("Z", "+00:00"))).days, 1)
            if query.channel_age_min and ch_age < query.channel_age_min:
                return False
            if query.channel_age_max and ch_age > query.channel_age_max:
                return False
    return True
