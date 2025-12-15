import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st
from dateutil import parser as date_parser

try:  # Optional OpenAI SDK
    from openai import OpenAI
except Exception:  # pragma: no cover - optional dependency
    OpenAI = None  # type: ignore

try:  # Optional clustering deps
    from sklearn.cluster import KMeans
    from sklearn.feature_extraction.text import TfidfVectorizer
except Exception:  # pragma: no cover - optional dependency
    KMeans = None  # type: ignore
    TfidfVectorizer = None  # type: ignore


# --------------------------- Конфігурація ---------------------------
APP_TITLE = "Ютуба Дивисі🔥"
PAGE_TITLE = "© Сашко Гарматний Ютуба Дивисі🔥"

DEFAULT_DB_PATH = os.environ.get("YT_VIRAL_DB_PATH", "yt_viral.db")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

BASE_URLS = {
    "search": "https://www.googleapis.com/youtube/v3/search",
    "videos": "https://www.googleapis.com/youtube/v3/videos",
    "channels": "https://www.googleapis.com/youtube/v3/channels",
}

SEO_STRONG = ["how to", "tutorial", "guide", "review", "vs", "best", "top"]
SEO_MEDIUM = ["ideas", "tips", "update", "2024", "2025", "beginner"]
SEO_WEAK = ["vlog", "reaction", "prank", "shorts"]

INTENT_VARIATIONS = [
    "explained",
    "documentary",
    "timeline",
    "how to",
    "vs",
    "best",
    "mistakes",
    "myths",
    "facts",
    "update",
    "latest",
    "2025",
]


@dataclass
class VideoRecord:
    video_id: str
    keyword: str
    title: str
    channel_id: str
    channel_title: str
    published_at: datetime
    duration_sec: int
    views: int
    likes: int
    comments: int
    thumb_url: str
    url: str
    subs: int
    channel_published_at: datetime


# --------------------------- Допоміжні функції ---------------------------

def request_with_retry(url: str, params: Dict[str, str], attempts: int = 6, base_delay: float = 1.0) -> Optional[dict]:
    """HTTP GET з експоненційним backoff."""
    for idx in range(attempts):
        try:
            resp = requests.get(url, params=params, timeout=20)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in {429, 500, 502, 503}:
                delay = base_delay * (1.5 ** idx)
                time.sleep(delay)
                continue
            st.error(f"Помилка API: {resp.status_code} — {resp.text}")
            return None
        except requests.RequestException as exc:  # pragma: no cover - network
            delay = base_delay * (1.5 ** idx)
            if idx == attempts - 1:
                st.error(f"Запит не вдався: {exc}")
                return None
            time.sleep(delay)
    return None


def parse_duration_iso8601(duration: str) -> int:
    """Перетворення ISO8601 у секунди."""
    try:
        # Проста ручна обробка, щоб уникнути зовнішніх залежностей
        total = 0
        time_part = duration.replace("P", "").split("T")
        date_block = time_part[0]
        time_block = time_part[1] if len(time_part) > 1 else ""
        number = ""
        for ch in date_block:
            if ch.isdigit():
                number += ch
            elif ch == "D" and number:
                total += int(number) * 24 * 3600
                number = ""
        number = ""
        for ch in time_block:
            if ch.isdigit():
                number += ch
            elif ch == "H" and number:
                total += int(number) * 3600
                number = ""
            elif ch == "M" and number:
                total += int(number) * 60
                number = ""
            elif ch == "S" and number:
                total += int(number)
                number = ""
        return total
    except Exception:
        return 0


def clamp(value: float, min_v: float, max_v: float) -> float:
    return max(min_v, min(max_v, value))


def youtube_search(api_key: str, keywords: List[str], *, per_query: int, order: str, region: str, language: str, published_after: Optional[str]) -> Tuple[List[str], Dict[str, str]]:
    video_ids: List[str] = []
    keyword_map: Dict[str, str] = {}
    for kw in keywords:
        params = {
            "key": api_key,
            "q": kw,
            "type": "video",
            "part": "snippet",
            "maxResults": str(per_query),
            "order": order,
            "regionCode": region,
            "relevanceLanguage": language,
        }
        if published_after:
            params["publishedAfter"] = published_after
        data = request_with_retry(BASE_URLS["search"], params)
        time.sleep(0.35)
        if not data:
            continue
        for item in data.get("items", []):
            vid = item.get("id", {}).get("videoId")
            if not vid:
                continue
            if vid not in keyword_map:
                keyword_map[vid] = kw
                video_ids.append(vid)
    return video_ids, keyword_map


def fetch_videos(api_key: str, video_ids: List[str]) -> Dict[str, dict]:
    details: Dict[str, dict] = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        params = {
            "key": api_key,
            "id": ",".join(batch),
            "part": "snippet,statistics,contentDetails",
            "maxResults": 50,
        }
        data = request_with_retry(BASE_URLS["videos"], params)
        time.sleep(0.35)
        if not data:
            continue
        for item in data.get("items", []):
            details[item.get("id")] = item
    return details


def fetch_channels(api_key: str, channel_ids: List[str]) -> Dict[str, dict]:
    info: Dict[str, dict] = {}
    for i in range(0, len(channel_ids), 50):
        batch = channel_ids[i : i + 50]
        params = {
            "key": api_key,
            "id": ",".join(batch),
            "part": "statistics,snippet",
            "maxResults": 50,
        }
        data = request_with_retry(BASE_URLS["channels"], params)
        time.sleep(0.35)
        if not data:
            continue
        for item in data.get("items", []):
            info[item.get("id")] = item
    return info


def build_records(video_items: Dict[str, dict], keyword_map: Dict[str, str], channel_items: Dict[str, dict]) -> List[VideoRecord]:
    records: List[VideoRecord] = []
    for vid, raw in video_items.items():
        snippet = raw.get("snippet", {})
        stats = raw.get("statistics", {})
        duration = parse_duration_iso8601(raw.get("contentDetails", {}).get("duration", ""))
        channel_id = snippet.get("channelId", "")
        channel_raw = channel_items.get(channel_id, {})
        channel_stats = channel_raw.get("statistics", {})
        channel_snippet = channel_raw.get("snippet", {})
        thumb = (
            snippet.get("thumbnails", {}).get("maxres")
            or snippet.get("thumbnails", {}).get("high")
            or snippet.get("thumbnails", {}).get("medium")
            or snippet.get("thumbnails", {}).get("default")
            or {}
        ).get("url", "")
        try:
            published_at = date_parser.parse(snippet.get("publishedAt", "")).replace(tzinfo=timezone.utc)
        except Exception:
            published_at = datetime.now(timezone.utc)
        try:
            channel_published_at = date_parser.parse(channel_snippet.get("publishedAt", "")).replace(
                tzinfo=timezone.utc
            )
        except Exception:
            channel_published_at = datetime.now(timezone.utc)
        record = VideoRecord(
            video_id=vid,
            keyword=keyword_map.get(vid, ""),
            title=snippet.get("title", ""),
            channel_id=channel_id,
            channel_title=snippet.get("channelTitle", ""),
            published_at=published_at,
            duration_sec=duration,
            views=int(stats.get("viewCount", 0)),
            likes=int(stats.get("likeCount", 0)),
            comments=int(stats.get("commentCount", 0)),
            thumb_url=thumb,
            url=f"https://www.youtube.com/watch?v={vid}",
            subs=int(channel_stats.get("subscriberCount", 0)),
            channel_published_at=channel_published_at,
        )
        records.append(record)
    return records


def compute_metrics(records: List[VideoRecord]) -> pd.DataFrame:
    now = datetime.now(timezone.utc)
    rows = []
    for r in records:
        age_days = max(1.0, (now - r.published_at).total_seconds() / 86400)
        channel_age_days = max(1.0, (now - r.channel_published_at).total_seconds() / 86400)
        views_per_day = r.views / age_days
        engagement_rate = (r.likes + r.comments) / max(r.views, 1)
        engagement_pct = engagement_rate * 100
        duration_min = r.duration_sec / 60
        views_to_subs = r.views / max(r.subs, 1)
        growth_speed = r.views / max(age_days, 1)
        score = views_per_day * (1 + 5 * engagement_rate) * (1 + clamp(views_to_subs, 0, 5) / 10)
        age_bonus = clamp((10 - age_days) / 10, 0, 1)
        trend_score = score * (1 + 0.75 * age_bonus)
        rows.append(
            {
                "video_id": r.video_id,
                "keyword": r.keyword,
                "title": r.title,
                "channel_id": r.channel_id,
                "channel_title": r.channel_title,
                "published_at": r.published_at,
                "channel_created": r.channel_published_at,
                "duration_sec": r.duration_sec,
                "duration_min": duration_min,
                "views": r.views,
                "likes": r.likes,
                "comments": r.comments,
                "subs": r.subs,
                "age_days": age_days,
                "channel_age_days": channel_age_days,
                "views_per_day": views_per_day,
                "engagement_rate": engagement_rate,
                "engagement_pct": engagement_pct,
                "views_to_subs": views_to_subs,
                "growth_speed": growth_speed,
                "score": score,
                "age_bonus": age_bonus,
                "trend_score": trend_score,
                "thumb_url": r.thumb_url,
                "url": r.url,
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    rating = normalize_rating(df["score"].tolist())
    df["rating"] = rating
    df["tier"] = df["rating"].apply(label_tier)
    df["roi_tag"] = df.apply(roi_tag, axis=1)
    return df


def normalize_rating(scores: List[float]) -> List[float]:
    if not scores:
        return []
    min_s, max_s = min(scores), max(scores)
    if math.isclose(min_s, max_s):
        return [50.0 for _ in scores]
    norm = []
    for s in scores:
        val = (math.log10(s + 1) - math.log10(min_s + 1)) / (math.log10(max_s + 1) - math.log10(min_s + 1))
        norm.append(round(val * 100, 2))
    return norm


def label_tier(rating: float) -> str:
    if rating >= 90:
        return "💣 Дуже вірусне"
    if rating >= 75:
        return "🔥 Вистрілило"
    if rating >= 60:
        return "✅ Сильне"
    if rating >= 45:
        return "⚠️ Середнє"
    return "🧊 Слабке"


def roi_tag(row: pd.Series) -> str:
    vpd = row.get("views_per_day", 0)
    engagement = row.get("engagement_rate", 0)
    age = row.get("age_days", 0)
    if vpd > 150000 and engagement > 0.05:
        return "Cash Cow"
    if vpd > 75000 and age < 14:
        return "Viral Spike"
    if vpd > 15000 and engagement > 0.02:
        return "Growth"
    if vpd < 1000 and age > 90:
        return "Dead"
    return "OK"


# --------------------------- SQLite Snapshots ---------------------------

def init_db(path: str = DEFAULT_DB_PATH) -> None:
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            snapshot_id TEXT PRIMARY KEY,
            created_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshot_videos (
            snapshot_id TEXT,
            video_id TEXT,
            title TEXT,
            channel_id TEXT,
            channel_title TEXT,
            published_at TEXT,
            views INTEGER,
            likes INTEGER,
            comments INTEGER,
            age_days REAL,
            views_per_day REAL,
            engagement_pct REAL,
            subs INTEGER,
            keyword TEXT,
            url TEXT,
            PRIMARY KEY (snapshot_id, video_id)
        )
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_video ON snapshot_videos(video_id)")
    conn.commit()
    conn.close()


def save_snapshot(df: pd.DataFrame, path: str = DEFAULT_DB_PATH) -> str:
    snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("INSERT INTO snapshots(snapshot_id, created_at) VALUES (?, ?)", (snapshot_id, datetime.now(timezone.utc).isoformat()))
    records = df[
        [
            "video_id",
            "title",
            "channel_id",
            "channel_title",
            "published_at",
            "views",
            "likes",
            "comments",
            "age_days",
            "views_per_day",
            "engagement_pct",
            "subs",
            "keyword",
            "url",
        ]
    ]
    records.to_sql("snapshot_videos", conn, if_exists="append", index=False)
    conn.commit()
    conn.close()
    return snapshot_id


def load_latest_snapshots(limit: int = 2, path: str = DEFAULT_DB_PATH) -> List[Tuple[str, pd.DataFrame]]:
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("SELECT snapshot_id, created_at FROM snapshots ORDER BY created_at DESC LIMIT ?", (limit,))
    snaps = cur.fetchall()
    results = []
    for sid, created_at in snaps:
        df = pd.read_sql_query(
            "SELECT * FROM snapshot_videos WHERE snapshot_id = ?",
            conn,
            params=(sid,),
        )
        df["created_at"] = created_at
        results.append((sid, df))
    conn.close()
    return results


def compute_movers(path: str = DEFAULT_DB_PATH, top_n: int = 20) -> Optional[pd.DataFrame]:
    snaps = load_latest_snapshots(2, path)
    if len(snaps) < 2:
        return None
    (_, df_new), (_, df_old) = snaps[0], snaps[1]
    merged = df_new.merge(df_old, on="video_id", suffixes=("_new", "_old"))
    if merged.empty:
        return None
    merged["vpd_delta"] = merged["views_per_day_new"] - merged["views_per_day_old"]
    merged["views_delta"] = merged["views_new"] - merged["views_old"]
    merged["eng_delta"] = merged["engagement_pct_new"] - merged["engagement_pct_old"]
    movers = merged.sort_values("vpd_delta", ascending=False).head(top_n)
    display_cols = [
        "video_id",
        "title_new",
        "channel_title_new",
        "keyword_new",
        "views_per_day_new",
        "views_per_day_old",
        "vpd_delta",
        "views_delta",
        "eng_delta",
    ]
    return movers[display_cols]


# --------------------------- Кластеризація ---------------------------

def cluster_titles(df: pd.DataFrame, k: int) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    titles = df["title"].fillna("").tolist()
    clusters = []
    terms: List[str] = []
    if KMeans and TfidfVectorizer:
        vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=2500)
        X = vectorizer.fit_transform(titles)
        model = KMeans(n_clusters=k, n_init=10, random_state=42)
        labels = model.fit_predict(X)
        order_centroids = model.cluster_centers_.argsort()[:, ::-1]
        feature_names = vectorizer.get_feature_names_out()
        terms = [" ".join(feature_names[ind][:6]) for ind in order_centroids]
        clusters = labels
    else:
        clusters = [hash(t[:15]) % k for t in titles]
        terms = ["fallback" for _ in range(k)]
    df = df.copy()
    df["cluster"] = clusters
    df_terms = pd.DataFrame({"cluster": list(range(k)), "cluster_terms": terms})
    return df.merge(df_terms, on="cluster", how="left")


# --------------------------- Keyword аналітика ---------------------------

def keyword_analytics(df: pd.DataFrame, rpm_low: float, rpm_high: float) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    def _seo_score(word_list: Iterable[str], text: str) -> int:
        score = 0
        lower = text.lower()
        for w in word_list:
            if w in lower:
                score += 1
        return score

    grouped = []
    for key, grp in df.groupby("keyword"):
        median_vpd = grp["views_per_day"].median()
        median_rating = grp["rating"].median()
        median_trend = grp["trend_score"].median()
        median_eng = grp["engagement_pct"].median()
        median_subs = grp["subs"].median()
        median_vts = grp["views_to_subs"].median()
        viral_share = (grp["rating"] >= 75).mean()
        superviral_share = (grp["rating"] >= 90).mean()
        rev_low = (grp["views_per_day"].sum() / 1000) * rpm_low
        rev_high = (grp["views_per_day"].sum() / 1000) * rpm_high
        rev30_low = median_vpd * 30 / 1000 * rpm_low
        rev30_high = median_vpd * 30 / 1000 * rpm_high
        stability = viral_share + median_rating / 100
        seo_score = clamp(
            (_seo_score(SEO_STRONG, key) * 2 + _seo_score(SEO_MEDIUM, key) - _seo_score(SEO_WEAK, key)) * 20,
            0,
            100,
        )
        monthly_views = median_vpd * 30
        seo_difficulty = math.log10((median_subs / max(monthly_views, 1)) * 1000 + 1) * 3.3 if monthly_views > 0 else 0
        seo_class = "SEO-friendly" if seo_score >= 60 else "Hybrid" if seo_score >= 30 else "YouTube-only"
        competitiveness = median_vts
        key_score = clamp(
            0.35 * (median_vpd / (median_vpd + 5000)) * 100
            + 0.2 * (median_trend / (median_trend + 1)) * 100
            + 0.2 * stability * 100
            + 0.15 * seo_score
            - 0.1 * clamp(competitiveness, 0, 10) * 10,
            0,
            100,
        )
        grouped.append(
            {
                "keyword": key,
                "videos": len(grp),
                "median_vpd": median_vpd,
                "median_rating": median_rating,
                "median_trend": median_trend,
                "median_engagement": median_eng,
                "median_subs": median_subs,
                "median_views_to_subs": median_vts,
                "viral_share": viral_share,
                "superviral_share": superviral_share,
                "rev_day_low": rev_low,
                "rev_day_high": rev_high,
                "rev30_low": rev30_low,
                "rev30_high": rev30_high,
                "stability": stability,
                "seo_score": seo_score,
                "seo_class": seo_class,
                "seo_difficulty_lite": seo_difficulty,
                "competitiveness": competitiveness,
                "key_score": key_score,
            }
        )
    return pd.DataFrame(grouped).sort_values("key_score", ascending=False)


# --------------------------- AI блок ---------------------------

def build_openai_client() -> Optional[OpenAI]:
    if not OPENAI_API_KEY or not OpenAI:
        return None
    try:
        return OpenAI(api_key=OPENAI_API_KEY)
    except Exception:
        return None


def _call_openai_json(client: OpenAI, prompt: str, schema: dict) -> dict:
    response = client.responses.create(
        model=OPENAI_MODEL,
        input=[{"role": "user", "content": prompt}],
        response_format={"type": "json_schema", "json_schema": {"name": "schema", "schema": schema, "strict": True}},
    )
    content = response.output_parsed if hasattr(response, "output_parsed") else None
    if content:
        return content
    text_chunks = [m.text for m in response.output_text] if hasattr(response, "output_text") else []
    text = "".join(text_chunks)
    try:
        return json.loads(text)
    except Exception:
        return _repair_json(client, text)


def _repair_json(client: OpenAI, broken: str) -> dict:
    prompt = f"Repair цей JSON і поверни лише валідний JSON об'єкт без коментарів: {broken}"
    response = client.responses.create(model=OPENAI_MODEL, input=prompt)
    raw = response.output_text[0].text if hasattr(response, "output_text") else ""
    try:
        return json.loads(raw)
    except Exception:
        return {}


def ai_generate_keywords_multi(client: OpenAI, seed_topics: List[str], niches: List[str], audience: str, total_n: int, seed: str) -> List[str]:
    schema = {
        "type": "object",
        "properties": {"keywords": {"type": "array", "items": {"type": "string"}, "minItems": 20, "maxItems": 300}},
        "required": ["keywords"],
    }
    prompt = (
        "Згенеруй унікальні ключові слова (2-8 слів) для YouTube."
        f" Теми: {seed_topics}. Ніші: {niches}. Аудиторія: {audience}."
        f" Використай різні інтенти: {', '.join(INTENT_VARIATIONS)}."
        f" Всього приблизно {total_n}. seed={seed}."
    )
    data = _call_openai_json(client, prompt, schema)
    keywords = list(dict.fromkeys(data.get("keywords", [])))
    return keywords[:total_n]


def ai_bulk_generate_topics(client: OpenAI, keyword: str, samples: List[str], patterns: dict, n_bulk: int, seed: str) -> List[dict]:
    schema = {
        "type": "object",
        "properties": {
            "topics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "title_variants": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 5},
                        "hook": {"type": "string"},
                        "cold_open_15s": {"type": "string"},
                        "outline": {"type": "array", "items": {"type": "string"}, "minItems": 6, "maxItems": 6},
                        "thumbnail_prompt": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}, "minItems": 8, "maxItems": 18},
                        "cta": {"type": "string"},
                    },
                    "required": ["title", "title_variants", "hook", "cold_open_15s", "outline", "thumbnail_prompt", "tags", "cta"],
                },
            }
        },
        "required": ["topics"],
    }
    prompt = (
        f"Створи {n_bulk} ідей роликів (5-10 хв) англійською для ключа '{keyword}'."
        " Уникай копіювання прикладів, заборони насильство."
        f" Орієнтуйся на зразки: {samples}. Патерни: {patterns}. seed={seed}."
    )
    data = _call_openai_json(client, prompt, schema)
    return data.get("topics", [])


def ai_refine_and_rank(client: OpenAI, keyword: str, drafts: List[dict], n_final: int) -> List[dict]:
    schema = {
        "type": "object",
        "properties": {
            "topics": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "title_variants": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 5},
                        "hook": {"type": "string"},
                        "cold_open_15s": {"type": "string"},
                        "outline": {"type": "array", "items": {"type": "string"}, "minItems": 6, "maxItems": 6},
                        "thumbnail_prompt": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}, "minItems": 8, "maxItems": 18},
                        "cta": {"type": "string"},
                        "score": {"type": "integer", "minimum": 0, "maximum": 100},
                        "why_it_works": {"type": "string"},
                    },
                    "required": [
                        "title",
                        "title_variants",
                        "hook",
                        "cold_open_15s",
                        "outline",
                        "thumbnail_prompt",
                        "tags",
                        "cta",
                        "score",
                        "why_it_works",
                    ],
                },
            }
        },
        "required": ["topics"],
    }
    prompt = (
        f"Оціни та відсортуй ідеї для ключа '{keyword}'. Вибери {n_final} найкращих,"
        " додай score 0-100 та пояснення. Усуни дублікати."
        f" Чернетки: {drafts}."
    )
    data = _call_openai_json(client, prompt, schema)
    topics = data.get("topics", [])
    topics = sorted(topics, key=lambda x: x.get("score", 0), reverse=True)
    return topics[:n_final]


def extract_title_patterns(df: pd.DataFrame, keyword: str, top_k: int = 20) -> dict:
    sample = df[df["keyword"] == keyword].sort_values("rating", ascending=False).head(top_k)
    words = []
    bigrams: Dict[str, int] = {}
    for title in sample["title"].fillna(""):
        tokens = [t.strip().lower() for t in title.split() if len(t) > 2]
        words.extend(tokens)
        for i in range(len(tokens) - 1):
            bigram = f"{tokens[i]} {tokens[i+1]}"
            bigrams[bigram] = bigrams.get(bigram, 0) + 1
    top_words = pd.Series(words).value_counts().head(10).index.tolist() if words else []
    top_bigrams = sorted(bigrams.items(), key=lambda x: x[1], reverse=True)[:10]
    return {"top_words": top_words, "top_bigrams": [b for b, _ in top_bigrams]}


# --------------------------- UI ---------------------------
def render_video_card(row: pd.Series, shortlist: set, shortlist_callback) -> None:
    col1, col2 = st.columns([1, 3])
    with col1:
        if row["thumb_url"]:
            st.image(row["thumb_url"], use_column_width=True)
    with col2:
        st.markdown(f"**[{row['title']}]({row['url']})**")
        st.write(f"Канал: {row['channel_title']}")
        st.write(f"Перегляди: {int(row['views']):,} · VPD: {row['views_per_day']:.0f} · ⏱️ {row['duration_min']:.1f} хв")
        st.write(f"Рейтинг: {row['rating']:.1f} ({row['tier']}) · ER: {row['engagement_pct']:.2f}%")
        if row["video_id"] in shortlist:
            if st.button("❌ Відкріпити", key=f"unpin_{row['video_id']}"):
                shortlist_callback(row["video_id"], False)
        else:
            if st.button("📌 Закріпити", key=f"pin_{row['video_id']}"):
                shortlist_callback(row["video_id"], True)


# --------------------------- Головний застосунок ---------------------------
def main() -> None:
    st.set_page_config(page_title=PAGE_TITLE, layout="wide")
    st.title(APP_TITLE)

    if "shortlist" not in st.session_state:
        st.session_state.shortlist = set()
    if "ai_keywords" not in st.session_state:
        st.session_state.ai_keywords = ""

    st.sidebar.header("Пошук")
    api_key = st.sidebar.text_input("YouTube API ключ", type="password")
    keywords_manual = st.sidebar.text_area("Ключові слова (csv)", "music, football, ai trends")
    use_ai_keywords = st.sidebar.checkbox("Використати AI ключі", value=False)
    days_back = st.sidebar.number_input("Останні днів", min_value=0, max_value=365, value=30)
    region = st.sidebar.selectbox("Регіон", ["US", "CA", "GB", "AU"], index=0)
    language = st.sidebar.selectbox("Мова", ["en", "uk", "pl", "de"], index=0)
    order = st.sidebar.selectbox("Сортування API", ["viewCount", "date", "relevance", "rating"], index=0)
    per_query = st.sidebar.slider("Результатів на ключ", min_value=5, max_value=50, value=25, step=5)

    st.sidebar.markdown("### Фільтри")
    age_min, age_max = st.sidebar.slider("Вік відео (дні)", 0, 365, (0, 365))
    ch_age_min, ch_age_max = st.sidebar.slider("Вік каналу (дні)", 0, 4000, (0, 4000))
    dur_min, dur_max = st.sidebar.slider("Тривалість (хв)", 0, 240, (0, 240))
    min_vpd = st.sidebar.number_input("Мін. views/day", value=0)
    min_eng = st.sidebar.number_input("Мін. engagement %", value=0.0, step=0.1)
    max_subs = st.sidebar.number_input("Макс. підписників (0 = без)", value=0)

    sort_by = st.sidebar.selectbox(
        "Сортування результатів",
        ["trend_score", "rating", "score", "views_per_day", "growth_speed", "views", "engagement_pct", "views_to_subs"],
    )
    rpm_low = st.sidebar.number_input("RPM low", value=2.0)
    rpm_high = st.sidebar.number_input("RPM high", value=8.0)
    save_snap = st.sidebar.checkbox("Зберігати snapshot", value=True)

    if use_ai_keywords and st.session_state.ai_keywords:
        keywords_csv = st.session_state.ai_keywords
    else:
        keywords_csv = keywords_manual
    st.sidebar.text_area("AI ключі (read-only)", st.session_state.ai_keywords, disabled=True)

    run_search = st.sidebar.button("Запустити пошук")

    init_db(DEFAULT_DB_PATH)
    df_results = pd.DataFrame()

    if run_search:
        if not api_key:
            st.error("Введіть YouTube API ключ")
            return
        keyword_list = [k.strip() for k in keywords_csv.split(",") if k.strip()]
        if not keyword_list:
            st.error("Додайте хоча б одне ключове слово")
            return
        published_after = None
        if days_back > 0:
            cutoff = datetime.now(timezone.utc) - pd.Timedelta(days=days_back)
            published_after = cutoff.isoformat("T") + "Z"
        with st.spinner("Шукаємо відео..."):
            video_ids, kw_map = youtube_search(api_key, keyword_list, per_query=per_query, order=order, region=region, language=language, published_after=published_after)
            video_data = fetch_videos(api_key, video_ids)
            channel_ids = list({v.get("snippet", {}).get("channelId", "") for v in video_data.values()})
            channel_data = fetch_channels(api_key, channel_ids)
            records = build_records(video_data, kw_map, channel_data)
            df_results = compute_metrics(records)
        if df_results.empty:
            st.warning("Нічого не знайдено")
            return
        if save_snap:
            save_snapshot(df_results)

    if df_results.empty:
        st.info("Запустіть пошук, щоб побачити результати")
        return

    # Фільтри
    df_filtered = df_results.copy()
    if age_min or age_max < 365:
        df_filtered = df_filtered[(df_filtered["age_days"] >= age_min) & (df_filtered["age_days"] <= age_max)]
    if ch_age_min or ch_age_max < 4000:
        df_filtered = df_filtered[(df_filtered["channel_age_days"] >= ch_age_min) & (df_filtered["channel_age_days"] <= ch_age_max)]
    df_filtered = df_filtered[(df_filtered["duration_min"] >= dur_min) & (df_filtered["duration_min"] <= dur_max)]
    df_filtered = df_filtered[df_filtered["views_per_day"] >= min_vpd]
    df_filtered = df_filtered[df_filtered["engagement_pct"] >= min_eng]
    if max_subs > 0:
        df_filtered = df_filtered[df_filtered["subs"] <= max_subs]
    df_filtered = df_filtered.sort_values(sort_by, ascending=False)

    tabs = st.tabs(["Огляд", "Movers", "Кластери", "AI Теми", "Shortlist"])

    # Огляд
    with tabs[0]:
        st.subheader("Загальні метрики")
        kpi_cols = st.columns(5)
        kpi_vals = [
            ("Відео", len(df_filtered)),
            ("Median VPD", f"{df_filtered['views_per_day'].median():.0f}"),
            ("Median Trend", f"{df_filtered['trend_score'].median():.0f}"),
            ("Median ER%", f"{df_filtered['engagement_pct'].median():.2f}%"),
            ("Top Rating", f"{df_filtered['rating'].max():.1f}"),
        ]
        for col, (label, val) in zip(kpi_cols, kpi_vals):
            col.markdown(f"<div style='padding:12px;border-radius:8px;background:#111;color:#fff'><b>{label}</b><br><span style='font-size:24px'>{val}</span></div>", unsafe_allow_html=True)

        with st.expander("Аналітика по ключових словах (розширена)"):
            kw_df = keyword_analytics(df_filtered, rpm_low, rpm_high)
            st.dataframe(kw_df, use_container_width=True)
            if not kw_df.empty:
                st.write("ТОП-10")
                st.dataframe(kw_df.head(10))

        # Карточки відео
        page_size = 30
        total_pages = math.ceil(len(df_filtered) / page_size)
        page = st.number_input("Сторінка", min_value=1, max_value=max(total_pages, 1), value=1)
        start, end = (page - 1) * page_size, page * page_size
        subset = df_filtered.iloc[start:end]
        cols = st.columns(2)
        for idx, (_, row) in enumerate(subset.iterrows()):
            with cols[idx % 2]:
                render_video_card(row, st.session_state.shortlist, update_shortlist)

        csv = df_filtered.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Завантажити CSV", csv, "videos.csv", "text/csv")

    # Movers
    with tabs[1]:
        st.subheader("Movers")
        movers = compute_movers()
        if movers is None or movers.empty:
            st.info("Потрібно щонайменше 2 snapshot")
        else:
            st.dataframe(movers, use_container_width=True)

    # Кластери
    with tabs[2]:
        st.subheader("Кластери заголовків")
        k = st.slider("Кількість кластерів", 2, 10, 4)
        clustered = cluster_titles(df_filtered, k)
        if clustered.empty:
            st.info("Немає даних для кластеризації")
        else:
            agg = clustered.groupby(["cluster", "cluster_terms"]).agg({"views_per_day": "median", "rating": "median", "video_id": "count"}).reset_index()
            st.dataframe(agg.rename(columns={"video_id": "videos"}), use_container_width=True)
            selected_cluster = st.selectbox("Кластер", agg["cluster"])
            st.dataframe(clustered[clustered["cluster"] == selected_cluster][[
                "title", "channel_title", "views_per_day", "rating", "tier", "url"
            ]])

    # AI Теми
    with tabs[3]:
        st.subheader("AI генератор тем")
        client = build_openai_client()
        if not client:
            st.warning("OpenAI ключ не вказано або бібліотека недоступна")
        else:
            kw_df = keyword_analytics(df_filtered, rpm_low, rpm_high)
            if not kw_df.empty:
                st.write("ТОП ключі")
                st.dataframe(kw_df[["keyword", "key_score", "median_vpd", "seo_class"]].head(10))
            selected_keyword = st.selectbox("Ключ для тем", kw_df["keyword"] if not kw_df.empty else [""], key="ai_keyword")
            col_a, col_b = st.columns(2)
            total_ai_kw = col_a.number_input("Скільки AI ключів", value=50, min_value=10, max_value=200)
            seed_ai = col_b.text_input("Seed", value="42")
            if st.button("AI: Згенерувати ключі"):
                with st.spinner("Генеруємо ключі..."):
                    ai_keywords = ai_generate_keywords_multi(
                        client,
                        seed_topics=[selected_keyword],
                        niches=["general"],
                        audience="global",
                        total_n=int(total_ai_kw),
                        seed=seed_ai,
                    )
                    st.session_state.ai_keywords = ", ".join(ai_keywords)
                    st.success("Ключі оновлено в сайдбарі")

            st.markdown("---")
            col1, col2, col3 = st.columns(3)
            n_bulk = int(col1.number_input("Чернеток (bulk)", value=10, min_value=5, max_value=40))
            n_final = int(col2.number_input("Фінальний набір", value=5, min_value=1, max_value=20))
            seed_topics_text = col3.text_input("Seed topics", "")
            if st.button("AI: Створити теми"):
                if not selected_keyword:
                    st.error("Оберіть ключ")
                else:
                    patterns = extract_title_patterns(df_filtered, selected_keyword)
                    samples = df_filtered[df_filtered["keyword"] == selected_keyword]["title"].head(5).tolist()
                    with st.spinner("Крок 1/3: Чернетки"):
                        drafts = ai_bulk_generate_topics(client, selected_keyword, samples, patterns, n_bulk, seed_ai)
                    with st.spinner("Крок 2/3: Ранжування"):
                        ranked = ai_refine_and_rank(client, selected_keyword, drafts, n_final)
                    st.success("Готово")
                    if ranked:
                        st.dataframe(pd.DataFrame(ranked))
                        st.download_button("⬇️ CSV", pd.DataFrame(ranked).to_csv(index=False).encode("utf-8"), "ai_topics.csv", "text/csv")

    # Shortlist
    with tabs[4]:
        st.subheader("Закріплені відео")
        if not st.session_state.shortlist:
            st.info("Немає закріплених відео")
        else:
            st.dataframe(df_filtered[df_filtered["video_id"].isin(st.session_state.shortlist)][[
                "title", "channel_title", "rating", "views_per_day", "url"
            ]])


def update_shortlist(video_id: str, add: bool) -> None:
    if add:
        st.session_state.shortlist.add(video_id)
    else:
        st.session_state.shortlist.discard(video_id)


if __name__ == "__main__":
    main()
