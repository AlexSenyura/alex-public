import os
import time
import math
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Tuple

import requests
import pandas as pd
import streamlit as st
from dateutil import parser as dtparser

# === OpenAI (AI-генератор) ===
try:
    from openai import OpenAI
except Exception:
    OpenAI = None


# =========================
# CONSTANTS
# =========================
YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

DB_PATH = os.getenv("YT_VIRAL_DB_PATH", "yt_viral.db")
OPENAI_MODEL = (os.getenv("OPENAI_MODEL", "gpt-5-mini") or "gpt-5-mini").strip()

# Streamlit config must be the first Streamlit command
st.set_page_config(page_title="Ютуба Дивисі", layout="wide")


# =========================
# HELPERS
# =========================
def iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat().replace("+00:00", "Z")


def parse_iso8601_duration_to_seconds(iso: str) -> int:
    if not iso or not iso.startswith("PT"):
        return 0
    iso = iso[2:]
    hours = minutes = seconds = 0
    num = ""
    for ch in iso:
        if ch.isdigit():
            num += ch
        else:
            if ch == "H":
                hours = int(num or "0")
            elif ch == "M":
                minutes = int(num or "0")
            elif ch == "S":
                seconds = int(num or "0")
            num = ""
    return hours * 3600 + minutes * 60 + seconds


def safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def fmt_int(n: int) -> str:
    n = int(n or 0)
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def clamp(x: float, a: float, b: float) -> float:
    return max(a, min(b, x))


def label_viral_tier(rating: float) -> str:
    if rating >= 90:
        return "💣 Дуже вірусне"
    if rating >= 75:
        return "🔥 Вистрілило"
    if rating >= 60:
        return "✅ Сильне"
    if rating >= 45:
        return "⚠️ Середнє"
    return "🧊 Слабке"


def roi_tag(views_per_day: float, engagement_pct: float, age_days: float) -> str:
    if views_per_day >= 9000 and engagement_pct >= 1.0:
        return "💰 Cash Cow"
    if age_days <= 10 and views_per_day >= 3000 and engagement_pct >= 0.7:
        return "🔥 Growth"
    if views_per_day >= 12000 and engagement_pct < 0.6:
        return "💣 Viral Spike"
    if views_per_day < 800 and engagement_pct < 0.3:
        return "🧊 Dead"
    return "✅ OK"


def estimate_revenue_range_from_views(views: float, rpm_low: float, rpm_high: float) -> Tuple[float, float]:
    low = (max(0.0, float(views)) / 1000.0) * float(rpm_low)
    high = (max(0.0, float(views)) / 1000.0) * float(rpm_high)
    return low, high


def estimate_revenue_range(views_per_day: float, rpm_low: float, rpm_high: float, horizon_days: int = 30) -> Tuple[float, float, float]:
    pred = max(0.0, float(views_per_day)) * horizon_days
    low = (pred / 1000.0) * float(rpm_low)
    high = (pred / 1000.0) * float(rpm_high)
    return pred, low, high


def estimate_daily_revenue_range(views_per_day: float, rpm_low: float, rpm_high: float) -> Tuple[float, float]:
    low = (max(0.0, float(views_per_day)) / 1000.0) * float(rpm_low)
    high = (max(0.0, float(views_per_day)) / 1000.0) * float(rpm_high)
    return low, high


def seo_intent_score(keyword: str) -> int:
    if not keyword:
        return 0
    k = keyword.lower()
    strong = ["explained", "documentary", "timeline", "full story", "what happened", "update", "truth"]
    medium = ["best", "top", "how", "why", "meaning", "examples", "guide", "beginner", "tutorial"]
    weak = ["shorts", "clip", "tiktok", "edit"]
    score = 0
    score += 25 * sum(1 for w in strong if w in k)
    score += 10 * sum(1 for w in medium if w in k)
    score -= 20 * sum(1 for w in weak if w in k)
    return int(clamp(score, 0, 100))


def seo_class(score: int) -> str:
    if score >= 70:
        return "SEO-friendly"
    if score >= 40:
        return "Hybrid"
    return "YouTube-only"


def seo_difficulty_lite(median_subs: float, median_monthly_views: float) -> float:
    v = max(float(median_monthly_views), 1.0)
    s = max(float(median_subs), 1.0)
    raw = s / v
    diff = math.log10(raw * 1000 + 1)
    return round(diff * 3.3, 2)


def _request_with_retry(url: str, params: Dict[str, Any], timeout: int = 30, tries: int = 6) -> requests.Response:
    backoff = 0.6
    last_err = None
    for _ in range(tries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(backoff)
                backoff = min(backoff * 1.7, 7.0)
                continue
            return r
        except Exception as e:
            last_err = e
            time.sleep(backoff)
            backoff = min(backoff * 1.7, 7.0)
    raise RuntimeError(f"HTTP failed after retries. Last error: {last_err}")


# =========================
# JSON PARSE + AI REPAIR
# =========================
def _extract_json_object(text: str) -> Dict[str, Any]:
    if not text:
        raise ValueError("Порожній output моделі")
    text = text.strip()

    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("Не знайшов JSON-обʼєкт у відповіді моделі")

    chunk = m.group(0).strip()
    obj = json.loads(chunk)
    if not isinstance(obj, dict):
        raise ValueError("JSON не є object")
    return obj


def _get_openai_client():
    if OpenAI is None:
        return None
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


def _ai_repair_json(client, broken_text: str) -> Dict[str, Any]:
    """
    Важливо: repair не має бути "json_schema strict", бо ми не знаємо структуру.
    Просимо повернути валідний JSON object.
    """
    prompt = f"""
You are a strict JSON repair tool.
Fix the JSON below into VALID JSON that parses with json.loads().
Return ONLY the fixed JSON object. No markdown, no commentary.

BROKEN JSON:
{broken_text}
""".strip()

    resp = client.responses.create(
        model=OPENAI_MODEL,
        input=prompt,
        # дозволяємо моделі просто повернути JSON, далі ми парсимо
        text={"format": {"type": "json"}},
        max_output_tokens=3500,
    )
    fixed = (getattr(resp, "output_text", "") or "").strip()
    return _extract_json_object(fixed)


def _parse_or_repair_json(client, raw_text: str) -> Dict[str, Any]:
    try:
        return _extract_json_object(raw_text)
    except Exception:
        return _ai_repair_json(client, raw_text)


# =========================
# SQLite (Movers / History)
# =========================
def db_init():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS snapshots (
      snapshot_id TEXT PRIMARY KEY,
      created_at TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS snapshot_videos (
      snapshot_id TEXT NOT NULL,
      video_id TEXT NOT NULL,
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
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_snapshot_videos_video_id ON snapshot_videos(video_id)")
    con.commit()
    con.close()


def save_snapshot(df: pd.DataFrame) -> Optional[str]:
    if df.empty:
        return None

    snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    created_at = datetime.now(timezone.utc).isoformat()

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("INSERT OR REPLACE INTO snapshots(snapshot_id, created_at) VALUES(?,?)", (snapshot_id, created_at))

    cols = [
        "video_id", "title", "channel_id", "channel_title", "published_at",
        "views", "likes", "comments", "age_days", "views_per_day", "engagement_pct",
        "subs", "keyword", "url"
    ]
    d = df.copy()
    d["published_at"] = d["published_at"].astype(str)
    rows = d[cols].to_dict(orient="records")

    for r in rows:
        cur.execute("""
        INSERT OR REPLACE INTO snapshot_videos(
          snapshot_id, video_id, title, channel_id, channel_title, published_at,
          views, likes, comments, age_days, views_per_day, engagement_pct, subs, keyword, url
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            snapshot_id,
            r.get("video_id"),
            r.get("title"),
            r.get("channel_id"),
            r.get("channel_title"),
            r.get("published_at"),
            int(r.get("views") or 0),
            int(r.get("likes") or 0),
            int(r.get("comments") or 0),
            float(r.get("age_days") or 0),
            float(r.get("views_per_day") or 0),
            float(r.get("engagement_pct") or 0),
            int(r.get("subs") or 0),
            r.get("keyword"),
            r.get("url"),
        ))
    con.commit()
    con.close()
    return snapshot_id


def load_latest_snapshots(limit: int = 2) -> pd.DataFrame:
    con = sqlite3.connect(DB_PATH)
    q = "SELECT snapshot_id, created_at FROM snapshots ORDER BY created_at DESC LIMIT ?"
    df = pd.read_sql_query(q, con, params=(int(limit),))
    con.close()
    return df


def compute_movers(top_n: int = 60) -> pd.DataFrame:
    snaps = load_latest_snapshots(limit=2)
    if len(snaps) < 2:
        return pd.DataFrame()

    s_new = snaps.iloc[0]["snapshot_id"]
    s_old = snaps.iloc[1]["snapshot_id"]

    con = sqlite3.connect(DB_PATH)
    new_df = pd.read_sql_query("SELECT * FROM snapshot_videos WHERE snapshot_id = ?", con, params=(s_new,))
    old_df = pd.read_sql_query("SELECT * FROM snapshot_videos WHERE snapshot_id = ?", con, params=(s_old,))
    con.close()

    if new_df.empty or old_df.empty:
        return pd.DataFrame()

    merged = new_df.merge(
        old_df[["video_id", "views_per_day", "views", "engagement_pct"]],
        on="video_id",
        how="left",
        suffixes=("", "_prev")
    )

    merged["views_per_day_prev"] = pd.to_numeric(merged["views_per_day_prev"], errors="coerce").fillna(0.0)
    merged["views_prev"] = pd.to_numeric(merged["views_prev"], errors="coerce").fillna(0)
    merged["engagement_pct_prev"] = pd.to_numeric(merged["engagement_pct_prev"], errors="coerce").fillna(0.0)

    merged["vpd_delta"] = merged["views_per_day"] - merged["views_per_day_prev"]
    merged["views_delta"] = merged["views"] - merged["views_prev"]
    merged["eng_delta"] = merged["engagement_pct"] - merged["engagement_pct_prev"]

    return merged.sort_values("vpd_delta", ascending=False).head(int(top_n)).copy()


# =========================
# YOUTUBE FETCH
# =========================
@st.cache_data(show_spinner=False, ttl=600)
def fetch_videos_with_channels(
    api_key: str,
    keywords: List[str],
    region: str,
    lang: str,
    published_after: str,
    per_query: int,
    order: str = "viewCount",
) -> pd.DataFrame:
    all_ids: List[str] = []
    id_to_keyword: Dict[str, str] = {}

    for kw in keywords:
        params = {
            "part": "snippet",
            "type": "video",
            "maxResults": str(int(per_query)),
            "order": order,
            "publishedAfter": published_after,
            "q": kw,
            "regionCode": region,
            "relevanceLanguage": lang,
            "key": api_key,
        }
        r = _request_with_retry(YOUTUBE_SEARCH_URL, params=params, timeout=30, tries=6)
        if r.status_code != 200:
            time.sleep(0.12)
            continue

        items = r.json().get("items", []) or []
        for it in items:
            vid = (it.get("id") or {}).get("videoId")
            if vid:
                all_ids.append(vid)
                id_to_keyword.setdefault(vid, kw)
        time.sleep(0.12)

    unique_ids = list(dict.fromkeys(all_ids))
    rows: List[Dict[str, Any]] = []
    channel_ids: set = set()

    for i in range(0, len(unique_ids), 50):
        batch = unique_ids[i:i + 50]
        params = {"part": "snippet,statistics,contentDetails", "id": ",".join(batch), "key": api_key}
        r = _request_with_retry(YOUTUBE_VIDEOS_URL, params=params, timeout=30, tries=6)
        if r.status_code != 200:
            time.sleep(0.12)
            continue

        items = r.json().get("items", []) or []
        for it in items:
            vid = it.get("id", "") or ""
            sn = it.get("snippet", {}) or {}
            stt = it.get("statistics", {}) or {}
            cd = it.get("contentDetails", {}) or {}

            published_at = sn.get("publishedAt")
            if not published_at:
                continue

            try:
                published_dt = dtparser.isoparse(published_at)
            except Exception:
                continue

            age_days = max(1.0, (datetime.now(timezone.utc) - published_dt).total_seconds() / 86400.0)
            duration_sec = parse_iso8601_duration_to_seconds(cd.get("duration", "PT0S"))

            views = int(stt.get("viewCount") or 0)
            likes = int(stt.get("likeCount") or 0)
            comments = int(stt.get("commentCount") or 0)

            views_per_day = views / age_days
            engagement_rate = (likes + comments) / max(views, 1)

            thumbs = sn.get("thumbnails", {}) or {}
            thumb_url = (
                (thumbs.get("maxres") or {}).get("url")
                or (thumbs.get("high") or {}).get("url")
                or (thumbs.get("medium") or {}).get("url")
                or (thumbs.get("default") or {}).get("url")
                or ""
            )

            channel_id = sn.get("channelId", "") or ""
            if channel_id:
                channel_ids.add(channel_id)

            rows.append({
                "video_id": vid,
                "title": sn.get("title", "") or "",
                "channel_id": channel_id,
                "channel_title": sn.get("channelTitle", "") or "",
                "published_at": published_dt,
                "duration_sec": duration_sec,
                "views": views,
                "likes": likes,
                "comments": comments,
                "age_days": round(age_days, 2),
                "views_per_day": float(views_per_day),
                "engagement_rate": float(engagement_rate),
                "keyword": id_to_keyword.get(vid, "") or "",
                "thumb_url": thumb_url,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "channel_url": f"https://www.youtube.com/channel/{channel_id}" if channel_id else "",
            })

        time.sleep(0.12)

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # channel stats
    ch_subs: Dict[str, int] = {}
    ch_created: Dict[str, datetime] = {}
    channel_ids_list = list(channel_ids)

    for i in range(0, len(channel_ids_list), 50):
        batch = channel_ids_list[i:i + 50]
        params = {"part": "statistics,snippet", "id": ",".join(batch), "key": api_key}
        r = _request_with_retry(YOUTUBE_CHANNELS_URL, params=params, timeout=30, tries=6)
        if r.status_code != 200:
            time.sleep(0.12)
            continue

        items = r.json().get("items", []) or []
        for it in items:
            cid = it.get("id", "") or ""
            stats = it.get("statistics", {}) or {}
            subs = int(stats.get("subscriberCount") or 0)
            ch_subs[cid] = subs

            sn = it.get("snippet", {}) or {}
            created_at = sn.get("publishedAt")
            if created_at:
                try:
                    ch_created[cid] = dtparser.isoparse(created_at)
                except Exception:
                    pass

        time.sleep(0.12)

    now = datetime.now(timezone.utc)
    df["subs"] = df["channel_id"].map(lambda x: ch_subs.get(x, 0))
    df["channel_created_at"] = df["channel_id"].map(lambda x: ch_created.get(x, None))
    df["channel_age_days"] = df["channel_created_at"].apply(
        lambda dt: None if dt is None or pd.isna(dt) else max(0, int((now - dt).total_seconds() / 86400.0))
    )

    df["duration_min"] = df["duration_sec"] / 60.0
    df["engagement_pct"] = df["engagement_rate"] * 100.0
    df["views_to_subs"] = df.apply(lambda r: safe_div(r["views"], max(r["subs"], 1)), axis=1)
    df["growth_speed"] = df.apply(lambda r: safe_div(r["views"], max(r["age_days"], 1.0)), axis=1)

    df["score"] = df.apply(
        lambda r: r["views_per_day"]
                  * (1.0 + 5.0 * r["engagement_rate"])
                  * (1.0 + clamp(r["views_to_subs"], 0.0, 5.0) / 10.0),
        axis=1
    )

    df["age_bonus"] = df["age_days"].apply(lambda d: clamp((10.0 - float(d)) / 10.0, 0.0, 1.0))
    df["trend_score"] = df["score"] * (1.0 + 0.75 * df["age_bonus"])

    s = df["score"].astype(float)
    s_log = s.apply(lambda x: math.log10(float(x) + 1.0))
    lo, hi = float(s_log.min()), float(s_log.max())
    df["rating"] = 50.0 if (hi - lo < 1e-9) else ((s_log - lo) / (hi - lo) * 100.0)
    df["tier"] = df["rating"].apply(label_viral_tier)

    for col in ["views_per_day", "growth_speed", "engagement_pct", "views_to_subs", "score", "trend_score", "duration_min", "age_bonus"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).round(2)
    df["rating"] = pd.to_numeric(df["rating"], errors="coerce").fillna(0).round(1)

    return df


# =========================
# CLUSTERING
# =========================
def cluster_titles(df: pd.DataFrame, k: int = 8) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    titles = df["title"].fillna("").astype(str).tolist()
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.cluster import KMeans

        vec = TfidfVectorizer(stop_words="english", max_features=2500, ngram_range=(1, 2))
        X = vec.fit_transform(titles)

        k = int(clamp(float(k), 2, 20))
        km = KMeans(n_clusters=k, n_init="auto", random_state=42)
        labels = km.fit_predict(X)

        d = df.copy()
        d["cluster"] = labels

        terms = vec.get_feature_names_out()
        centers = km.cluster_centers_
        top_terms = []
        for i in range(k):
            idxs = centers[i].argsort()[::-1][:6]
            top_terms.append(", ".join([terms[j] for j in idxs]))
        term_map = {i: top_terms[i] for i in range(k)}
        d["cluster_terms"] = d["cluster"].map(term_map)
        return d
    except Exception:
        d = df.copy()
        kk = max(2, int(k))
        d["cluster"] = d["title"].fillna("").str.lower().apply(
            lambda t: hash(" ".join(sorted(set(t.split()[:6])))) % kk
        )
        d["cluster_terms"] = "fallback"
        return d


# =========================
# KEYWORD TABLES
# =========================
def compute_keyword_table(df2: pd.DataFrame, rpm_low: float, rpm_high: float) -> pd.DataFrame:
    if df2.empty:
        return pd.DataFrame()

    d = df2.copy()
    d["is_viral"] = d["rating"] >= 75
    d["is_superviral"] = d["rating"] >= 90

    d["pred_30d_views"] = d["views_per_day"].clip(lower=0) * 30.0
    d["rev_30d_low"] = (d["pred_30d_views"] / 1000.0) * rpm_low
    d["rev_30d_high"] = (d["pred_30d_views"] / 1000.0) * rpm_high

    d["rev_day_low"] = (d["views_per_day"] / 1000.0) * rpm_low
    d["rev_day_high"] = (d["views_per_day"] / 1000.0) * rpm_high

    g = d.groupby("keyword", as_index=False).agg(
        videos=("video_id", "count"),
        median_vpd=("views_per_day", "median"),
        median_rating=("rating", "median"),
        median_trend=("trend_score", "median"),
        median_eng=("engagement_pct", "median"),
        viral_share=("is_viral", "mean"),
        superviral_share=("is_superviral", "mean"),
        median_subs=("subs", "median"),
        median_vts=("views_to_subs", "median"),
        key_rev_30d_low=("rev_30d_low", "sum"),
        key_rev_30d_high=("rev_30d_high", "sum"),
        median_video_rev_day_low=("rev_day_low", "median"),
        median_video_rev_day_high=("rev_day_high", "median"),
    )

    g["stability"] = g["viral_share"] + (g["median_rating"] / 100.0)
    g["seo_intent_score"] = g["keyword"].fillna("").apply(seo_intent_score)
    g["seo_class"] = g["seo_intent_score"].apply(seo_class)
    g["seo_difficulty"] = g.apply(lambda r: seo_difficulty_lite(r["median_subs"], r["median_vpd"] * 30.0), axis=1)

    g["competitiveness"] = g["median_vts"]

    max_vpd = max(float(g["median_vpd"].max() or 1.0), 1.0)
    max_rev = max(float(g["key_rev_30d_low"].max() or 1.0), 1.0)
    max_vts = max(float(g["competitiveness"].max() or 1.0), 1.0)
    max_trend = max(float(g["median_trend"].max() or 1.0), 1.0)

    g["key_score"] = (
        g["stability"] * 0.35 +
        (g["median_vpd"] / max_vpd) * 0.20 +
        (g["median_trend"] / max_trend) * 0.15 +
        (g["key_rev_30d_low"] / max_rev) * 0.20 +
        (g["competitiveness"] / max_vts) * 0.10
    )

    return g.sort_values(["key_score", "stability", "median_rating", "median_vpd"], ascending=False)


def compute_top_keywords(df2: pd.DataFrame, rpm_low: float, rpm_high: float, top_n: int = 10) -> pd.DataFrame:
    g = compute_keyword_table(df2, rpm_low, rpm_high)
    if g.empty:
        return pd.DataFrame()

    top = g.head(int(top_n)).copy()
    out = pd.DataFrame({
        "Ключове слово": top["keyword"].fillna("—"),
        "Клас": top["seo_class"],
        "Key Score": top["key_score"].round(3),
        "Стабільність": top["stability"].round(2),
        "Медіана рейтингу": top["median_rating"].round(1),
        "Медіана Trend": top["median_trend"].round(0).astype(int),
        "Медіана переглядів/день": top["median_vpd"].round(0).astype(int),
        "Частка 🔥": (top["viral_share"] * 100).round(0).astype(int).astype(str) + "%",
        "Частка 💣": (top["superviral_share"] * 100).round(0).astype(int).astype(str) + "%",
        "Дохід ключа / 30д (low)": top["key_rev_30d_low"].round(0).astype(int).apply(lambda x: f"${x:,}"),
        "SEO Intent": top["seo_intent_score"].astype(int),
        "SEO Difficulty": top["seo_difficulty"].round(2),
    })
    return out


def render_keyword_analytics(df2: pd.DataFrame, rpm_low: float, rpm_high: float):
    g = compute_keyword_table(df2, rpm_low, rpm_high)
    if g.empty:
        st.info("Аналітика по ключах недоступна: недостатньо даних.")
        return

    out = g.copy()
    out["Ключове слово"] = out["keyword"].fillna("—")
    out["Відео"] = out["videos"]
    out["Key Score"] = out["key_score"].round(3)
    out["Стабільність ключа"] = out["stability"].round(2)
    out["Медіана рейтингу"] = out["median_rating"].round(1)
    out["Медіана Trend"] = out["median_trend"].round(0).astype(int)
    out["Медіана переглядів/день"] = out["median_vpd"].round(0).astype(int)
    out["Медіана залучення (%)"] = out["median_eng"].round(2)
    out["Частка 🔥 (>=75)"] = (out["viral_share"] * 100).round(0).astype(int).astype(str) + "%"
    out["Частка 💣 (>=90)"] = (out["superviral_share"] * 100).round(0).astype(int).astype(str) + "%"

    out["Мед. дохід 1 відео / день (low)"] = out["median_video_rev_day_low"].round(2).apply(lambda x: f"${x:,.2f}")
    out["Мед. дохід 1 відео / день (high)"] = out["median_video_rev_day_high"].round(2).apply(lambda x: f"${x:,.2f}")
    out["Дохід ключа / 30 днів (low)"] = out["key_rev_30d_low"].round(0).astype(int).apply(lambda x: f"${x:,}")
    out["Дохід ключа / 30 днів (high)"] = out["key_rev_30d_high"].round(0).astype(int).apply(lambda x: f"${x:,}")

    out["Конкурентність (мед. views/subs)"] = out["competitiveness"].round(2)
    out["SEO Intent (0–100)"] = out["seo_intent_score"].astype(int)
    out["SEO Difficulty (0–10)"] = out["seo_difficulty"].round(2)
    out["Клас ключа"] = out["seo_class"]

    st.dataframe(out.head(60), use_container_width=True, height=640)


def extract_title_patterns(df2: pd.DataFrame, keyword: str, top_k: int = 25) -> Tuple[List[str], Dict[str, Any]]:
    d = df2[df2["keyword"] == keyword].copy()
    d = d.sort_values(["trend_score", "rating", "views_per_day"], ascending=False).head(int(top_k))
    titles = d["title"].fillna("").tolist()

    from collections import Counter
    words: List[str] = []
    for t in titles:
        w = [x.strip(".,!?\"'():;").lower() for x in t.split()]
        w = [x for x in w if 2 <= len(x) <= 18]
        words.extend(w)

    c = Counter(words)
    top_words = [w for w, _ in c.most_common(30)]

    bigrams: List[str] = []
    for t in titles:
        w = [x.strip(".,!?\"'():;").lower() for x in t.split()]
        w = [x for x in w if 2 <= len(x) <= 18]
        bigrams.extend([" ".join(w[i:i + 2]) for i in range(len(w) - 1)])

    cb = Counter(bigrams)
    top_bigrams = [b for b, _ in cb.most_common(20)]

    patterns = {"top_words": top_words, "top_bigrams": top_bigrams}
    return titles, patterns


# =========================
# AI SCHEMAS
# =========================
TOPIC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "topics": {
            "type": "array",
            "minItems": 10,
            "maxItems": 80,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "title_variants": {"type": "array", "minItems": 3, "maxItems": 5, "items": {"type": "string"}},
                    "hook": {"type": "string"},
                    "cold_open_15s": {"type": "string"},
                    "outline": {"type": "array", "minItems": 6, "maxItems": 6, "items": {"type": "string"}},
                    "thumbnail_prompt": {"type": "string"},
                    "tags": {"type": "array", "minItems": 8, "maxItems": 18, "items": {"type": "string"}},
                    "cta": {"type": "string"},
                },
                "required": ["title", "title_variants", "hook", "cold_open_15s", "outline", "thumbnail_prompt", "tags", "cta"],
            }
        }
    },
    "required": ["topics"],
}

RANK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "topics": {
            "type": "array",
            "minItems": 10,
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string"},
                    "title_variants": {"type": "array", "minItems": 3, "maxItems": 5, "items": {"type": "string"}},
                    "hook": {"type": "string"},
                    "cold_open_15s": {"type": "string"},
                    "outline": {"type": "array", "minItems": 6, "maxItems": 6, "items": {"type": "string"}},
                    "thumbnail_prompt": {"type": "string"},
                    "tags": {"type": "array", "minItems": 8, "maxItems": 18, "items": {"type": "string"}},
                    "cta": {"type": "string"},
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "why_it_works": {"type": "string"},
                },
                "required": ["title", "title_variants", "hook", "cold_open_15s", "outline", "thumbnail_prompt", "tags", "cta", "score", "why_it_works"],
            }
        }
    },
    "required": ["topics"],
}

KEYWORDS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "keywords": {
            "type": "array",
            "minItems": 20,
            "maxItems": 300,
            "items": {"type": "string"},
        }
    },
    "required": ["keywords"],
}


# =========================
# AI: TOPICS
# =========================
def ai_bulk_generate_topics(keyword: str, sample_titles: List[str], patterns: Dict[str, Any], n_bulk: int = 35, seed: int = 123) -> List[Dict[str, Any]]:
    client = _get_openai_client()
    if client is None:
        raise RuntimeError("Нема OpenAI: встанови `pip install openai` і задай OPENAI_API_KEY.")

    prompt = f"""
You are a YouTube producer. Generate {int(n_bulk)} NEW video topic packages for long-form English videos (5–10 minutes).
Niche keyword: "{keyword}"

Rules:
- Return ONLY JSON that matches the schema exactly.
- No markdown, no commentary, no extra text.
- Do NOT copy sample titles.
- Keep titles under 80 characters.
- Avoid graphic violence.
- Seed: {int(seed)}

Sample titles (do not copy):
{json.dumps(sample_titles[:25], ensure_ascii=False)}

Patterns:
{json.dumps(patterns, ensure_ascii=False)}
""".strip()

    resp = client.responses.create(
        model=OPENAI_MODEL,
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "topic_batch",
                "schema": TOPIC_SCHEMA,
                "strict": True,
            }
        },
        max_output_tokens=8000,
    )

    raw = (getattr(resp, "output_text", "") or "")
    data = _parse_or_repair_json(client, raw)

    topics = data.get("topics", [])
    if not isinstance(topics, list) or len(topics) < 10:
        raise ValueError("AI повернув невалідну структуру topics.")
    return topics


def ai_refine_and_rank(keyword: str, drafts: List[Dict[str, Any]], n_final: int = 15) -> List[Dict[str, Any]]:
    client = _get_openai_client()
    if client is None:
        raise RuntimeError("Нема OpenAI: встанови `pip install openai` і задай OPENAI_API_KEY.")

    prompt = f"""
You are a senior YouTube editor. Pick the BEST {int(n_final)} topic packages from drafts.
Niche keyword: "{keyword}"

Rules:
- Return ONLY JSON that matches the schema exactly.
- No markdown, no commentary.
- Remove duplicates and overly similar ideas.
- Each outline MUST have exactly 6 beats.
- Add score 0–100 and a short reason.

DRAFTS:
{json.dumps(drafts, ensure_ascii=False)}
""".strip()

    resp = client.responses.create(
        model=OPENAI_MODEL,
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "topic_ranked",
                "schema": RANK_SCHEMA,
                "strict": True,
            }
        },
        max_output_tokens=8000,
    )

    raw = (getattr(resp, "output_text", "") or "")
    data = _parse_or_repair_json(client, raw)

    topics = data.get("topics", [])
    if not isinstance(topics, list) or len(topics) < 10:
        raise ValueError("AI повернув невалідну структуру topics.")
    return topics


def topics_to_df(topics: List[Dict[str, Any]], keyword: str) -> pd.DataFrame:
    rows = []
    for t in topics:
        outline = (t.get("outline") or [])[:6]
        rows.append({
            "Ключ": keyword,
            "Заголовок": t.get("title", "") or "",
            "Title Variants": " | ".join((t.get("title_variants") or [])[:5]),
            "Cold Open (15s)": t.get("cold_open_15s", "") or "",
            "Хук": t.get("hook", "") or "",
            "План (6)": " • " + "\n • ".join(outline),
            "Thumbnail Prompt": t.get("thumbnail_prompt", "") or "",
            "Tags": ", ".join((t.get("tags") or [])[:18]),
            "CTA": t.get("cta", "") or "",
            "Оцінка": t.get("score", None),
            "Чому зайде": t.get("why_it_works", "") or "",
        })
    return pd.DataFrame(rows)


# =========================
# AI: KEYWORDS (MULTI) — БЕЗ ЛОМАННЯ ПОШУКУ
# =========================
def ai_generate_keywords_multi(
    seed_topics: List[str],
    niche_modes: List[str],
    audience: str = "English (US/Global)",
    total_n: int = 120,
    seed: int = 123
) -> List[str]:
    client = _get_openai_client()
    if client is None:
        raise RuntimeError("Нема OpenAI: встанови `pip install openai` і задай OPENAI_API_KEY.")

    seed_topics = [s.strip() for s in (seed_topics or []) if s and s.strip()]
    niche_modes = [m.strip() for m in (niche_modes or []) if m and m.strip()]

    if not seed_topics:
        raise ValueError("Додай хоча б 1 seed-тему/ключ.")
    if not niche_modes:
        niche_modes = ["Mixed"]

    total_n = int(clamp(float(total_n), 20, 300))
    per_seed = max(10, int(math.ceil(total_n / len(seed_topics))))

    prompt = f"""
You are a YouTube search keyword strategist.

Goal:
Generate English YouTube search keywords.

Inputs:
- Seed topics (multiple): {json.dumps(seed_topics, ensure_ascii=False)}
- Niche modes (multiple): {json.dumps(niche_modes, ensure_ascii=False)}
- Audience: {audience}

Target:
- Produce ~{total_n} UNIQUE keywords total.
- Roughly {per_seed} keywords per seed topic (balanced).

Rules:
- Return ONLY JSON: {{ "keywords": [...] }} (no markdown, no commentary).
- Each keyword must be 2–8 words, realistic YouTube searches.
- Mix intents across seeds: explained / documentary / timeline / how to / beginner / vs / best / mistakes / fix / myths / facts / update / latest / 2025.
- Avoid graphic violence and sexual content.
- Avoid duplicates and near-duplicates across ALL seeds.

Seed: {int(seed)}
""".strip()

    resp = client.responses.create(
        model=OPENAI_MODEL,
        input=prompt,
        text={
            "format": {
                "type": "json_schema",
                "name": "keyword_batch_multi",
                "schema": KEYWORDS_SCHEMA,
                "strict": True,
            }
        },
        max_output_tokens=3200,
    )

    raw = (getattr(resp, "output_text", "") or "")
    data = _parse_or_repair_json(client, raw)
    kws = data.get("keywords", [])

    if not isinstance(kws, list) or len(kws) < 10:
        raise ValueError("AI повернув невалідну структуру keywords.")

    cleaned: List[str] = []
    seen = set()
    for k in kws:
        if not isinstance(k, str):
            continue
        k2 = re.sub(r"\s+", " ", k.strip())
        if not k2:
            continue
        low = k2.lower()
        if low in seen:
            continue
        seen.add(low)
        cleaned.append(k2)

    return cleaned[:total_n]


# =========================
# VIDEO CARD
# =========================
def render_video_card(v: Dict[str, Any], rpm_low: float, rpm_high: float):
    pred_30d, rev_30d_low, rev_30d_high = estimate_revenue_range(v["views_per_day"], rpm_low, rpm_high, horizon_days=30)
    total_low, total_high = estimate_revenue_range_from_views(v["views"], rpm_low, rpm_high)
    day_low, day_high = estimate_daily_revenue_range(v["views_per_day"], rpm_low, rpm_high)
    roi = roi_tag(v["views_per_day"], v["engagement_pct"], v["age_days"])

    ch_age = v.get("channel_age_days", None)
    ch_age_txt = f"{int(ch_age)} днів" if ch_age is not None and not pd.isna(ch_age) else "—"

    st.caption(
        f"**Рейтинг:** {v['rating']} / 100  •  **Тег:** {v['tier']}  •  **ROI:** {roi}  •  "
        f"**Score:** {v['score']}  •  **Trend:** {float(v.get('trend_score', 0)):.0f}"
    )
    st.markdown(f"**[{v['title']}]({v['url']})**")

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        if st.button("📌 Закріпити", key=f"pin_{v['video_id']}"):
            st.session_state.shortlist.add(v["video_id"])
    with c2:
        if st.button("❌ Відкріпити", key=f"unpin_{v['video_id']}"):
            st.session_state.shortlist.discard(v["video_id"])
    with c3:
        st.caption(f"Ключ: **{(v.get('keyword') or '—')}**")

    st.caption(
        f"Канал: [{v['channel_title']}]({v['channel_url']})  •  "
        f"Вік каналу: {ch_age_txt}  •  "
        f"Опубліковано: {v['published_at'].date()}  •  "
        f"Вік відео: {v['age_days']:.0f} днів  •  "
        f"Тривалість: {v['duration_min']:.1f} хв"
    )

    st.markdown(f"""
    <div class="kpi-grid">
      <div class="kpi"><div class="kpi-label">Перегляди</div><div class="kpi-value">{fmt_int(int(v["views"]))}</div></div>
      <div class="kpi"><div class="kpi-label">Переглядів/день</div><div class="kpi-value">{int(round(v["views_per_day"]))}</div></div>
      <div class="kpi"><div class="kpi-label">Темп росту</div><div class="kpi-value small">{int(round(v["growth_speed"]))}</div></div>
      <div class="kpi"><div class="kpi-label">Залучення</div><div class="kpi-value small">{v["engagement_pct"]:.2f}%</div></div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div class="kpi-grid-3">
      <div class="kpi"><div class="kpi-label">Підписники</div><div class="kpi-value">{fmt_int(int(v["subs"]))}</div></div>
      <div class="kpi"><div class="kpi-label">Перегляди / підписники</div><div class="kpi-value tiny">{v["views_to_subs"]:.2f}</div></div>
      <div class="kpi"><div class="kpi-label">Age bonus</div><div class="kpi-value tiny">{float(v.get('age_bonus', 0)):.2f}</div></div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div class="kpi-grid-3">
      <div class="kpi"><div class="kpi-label">Дохід / день (оцінка)</div><div class="kpi-value tiny">${day_low:,.2f} – ${day_high:,.2f}</div></div>
      <div class="kpi"><div class="kpi-label">Дохід відео (загалом)</div><div class="kpi-value tiny">${total_low:,.0f} – ${total_high:,.0f}</div></div>
      <div class="kpi"><div class="kpi-label">Дохід за 30 днів</div><div class="kpi-value tiny">${rev_30d_low:,.0f} – ${rev_30d_high:,.0f}</div></div>
    </div>
    """, unsafe_allow_html=True)

    st.caption(f"Прогноз переглядів за 30 днів (грубо): **{fmt_int(int(pred_30d))}**")


# =========================
# UI
# =========================
st.title("© Сашко Гарматний  Ютуба Дивисі🔥")

st.markdown("""
<style>
.kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-top: 6px; }
.kpi-grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 8px; }
.kpi { border: 1px solid rgba(255,255,255,0.10); border-radius: 12px; padding: 10px 12px; overflow: hidden; }
.kpi-label { font-size: 12px; color: #9aa0a6; margin-bottom: 2px; }
.kpi-value { font-size: 20px; font-weight: 700; line-height: 1.1; white-space: nowrap; }
.kpi-value.small { font-size: 18px; }
.kpi-value.tiny { font-size: 16px; }
hr { border: none; border-top: 1px solid rgba(255,255,255,0.10); margin: 14px 0; }
</style>
""", unsafe_allow_html=True)

db_init()

# --- session state (важливо: НЕ ставимо keywords override як порожній рядок, щоб не ламати пошук)
if "df" not in st.session_state:
    st.session_state.df = pd.DataFrame()
if "page" not in st.session_state:
    st.session_state.page = 1
if "topics_df" not in st.session_state:
    st.session_state.topics_df = pd.DataFrame()
if "shortlist" not in st.session_state:
    st.session_state.shortlist = set()

if "seed_pool" not in st.session_state:
    st.session_state.seed_pool = ["space exploration", "world war 2", "ai documentary", "psychology facts"]

# AI-generated keywords storage (None або непорожній рядок)
if "keywords_csv_ai" not in st.session_state:
    st.session_state.keywords_csv_ai = None

# чекбокс — чи використовувати AI ключі (за замовчуванням НІ, щоб не ламати стару поведінку)
if "use_ai_keywords" not in st.session_state:
    st.session_state.use_ai_keywords = False


with st.sidebar:
    st.header("Налаштування пошуку")

    api_key = st.text_input("YouTube API ключ", type="password")

    # --- Ключі: базові (ручні) + опційно AI
    base_default = "survival stories, true survival story, survival story reddit, i survived story"

    st.session_state.use_ai_keywords = st.checkbox(
        "Використовувати AI-згенеровані ключі",
        value=bool(st.session_state.use_ai_keywords),
        help="Якщо вимкнути — пошук працює як раніше (по ручним ключам)."
    )

    if st.session_state.use_ai_keywords and (st.session_state.keywords_csv_ai or "").strip():
        default_kw = st.session_state.keywords_csv_ai
    else:
        default_kw = base_default

    keywords_csv = st.text_area(
        "Ключові слова (через кому)",
        value=default_kw,
        height=120
    )

    st.caption("Порада: якщо щось зламалось — вимкни чекбокс AI і введи ручні ключі.")

    # ---------- AI keyword generator (multi) ----------
    st.divider()
    st.subheader("🧩 AI генератор ключових слів (multi)")

    niche_modes = st.multiselect(
        "Ніші (можна кілька)",
        ["Mixed", "Stories", "History", "Science", "Space", "Business", "Tech", "Gaming", "Health", "True Crime"],
        default=["Mixed"]
    )

    audience = st.selectbox(
        "Аудиторія",
        ["English (US/Global)", "English (UK)", "English (CA/AU)"],
        index=0
    )

    new_seed = st.text_input("Додати seed-тему/ключ", value="")
    add_seed = st.button("➕ Додати seed")

    if add_seed:
        s = (new_seed or "").strip()
        if s:
            if s.lower() not in {x.lower() for x in st.session_state.seed_pool}:
                st.session_state.seed_pool.append(s)
            st.success("Додано ✅")

    seed_topics = st.multiselect(
        "Seed-теми/ключі (можна кілька)",
        st.session_state.seed_pool,
        default=st.session_state.seed_pool[:2] if len(st.session_state.seed_pool) >= 2 else st.session_state.seed_pool
    )

    total_kw = st.slider("Скільки згенерувати (всього)", 20, 300, 120, 10)
    kw_seed = st.number_input("Seed (для повторюваності)", min_value=0, max_value=999999, value=123, step=1)

    colg1, colg2 = st.columns(2)
    with colg1:
        gen_kw = st.button("✨ Згенерувати AI-ключі")
    with colg2:
        reset_kw = st.button("♻️ Скинути AI-ключі")

    if reset_kw:
        st.session_state.keywords_csv_ai = None
        st.session_state.use_ai_keywords = False
        st.success("AI-ключі скинуто ✅ (пошук як раніше)")

    if gen_kw:
        try:
            if _get_openai_client() is None:
                raise RuntimeError("Нема OpenAI ключа/клієнта. Додай OPENAI_API_KEY в env.")

            kws = ai_generate_keywords_multi(
                seed_topics=seed_topics,
                niche_modes=niche_modes,
                audience=audience,
                total_n=int(total_kw),
                seed=int(kw_seed),
            )
            st.session_state.keywords_csv_ai = ", ".join(kws)
            st.session_state.use_ai_keywords = True
            st.success(f"Згенеровано: {len(kws)} ключів ✅")
            st.info("AI-ключі готові. Поле ключів зверху вже оновилось (бо чекбокс увімкнено).")
        except Exception as e:
            st.error(f"Помилка AI: {e}")

    # ---------- rest of search settings ----------
    st.divider()

    search_days_back = st.number_input(
        "Шукати відео за останні (днів)",
        min_value=1,
        max_value=365,
        value=30,
        step=1
    )

    region = st.selectbox("Регіон", ["US", "CA", "GB", "AU"], index=0)
    lang = st.selectbox("Мова", ["en"], index=0)

    st.divider()
    st.subheader("Порядок видачі YouTube")
    order_mode = st.selectbox("order", ["viewCount", "date", "relevance", "rating"], index=0)

    st.divider()
    st.subheader("Фільтри")

    age_min, age_max = st.slider(
        "Вік відео (днів тому опубліковано)",
        min_value=1,
        max_value=365,
        value=(1, min(30, int(search_days_back))),
        step=1
    )

    ch_age_min, ch_age_max = st.slider(
        "Вік каналу (днів)",
        min_value=0,
        max_value=5000,
        value=(0, 5000),
        step=10
    )

    min_minutes = st.slider("Мінімальна тривалість (хв)", 5, 60, 5, 1)
    max_minutes = st.slider("Максимальна тривалість (хв)", 10, 180, 30, 5)

    min_views_per_day = st.number_input("Мін. переглядів за день", min_value=0.0, value=2000.0, step=500.0)
    min_eng_pct = st.number_input("Мін. залучення (%)", min_value=0.0, value=0.5, step=0.1)
    max_subs = st.number_input("Макс. підписників (0 = без обмежень)", min_value=0, value=0, step=50000)

    st.divider()
    st.subheader("Сортування")

    sort_options = [
        ("⚡ Trend Score", "trend_score"),
        ("🔥 Рейтинг (0–100)", "rating"),
        ("💣 Score (raw)", "score"),
        ("📈 Переглядів/день", "views_per_day"),
        ("🚀 Темп росту", "growth_speed"),
        ("👁️ Перегляди (всього)", "views"),
        ("💬 Залучення (%)", "engagement_pct"),
        ("⚖️ Перегляди/підписники", "views_to_subs"),
    ]
    sort_label = st.selectbox("Сортувати за", [x[0] for x in sort_options], index=0)
    sort_by = dict(sort_options)[sort_label]

    st.divider()
    st.subheader("Оцінка доходу (приблизно)")
    rpm_low = st.number_input("RPM мін. ($/1000)", min_value=0.0, value=2.0, step=0.5)
    rpm_high = st.number_input("RPM макс. ($/1000)", min_value=0.0, value=6.0, step=0.5)

    st.divider()
    per_query = st.slider("Результатів на ключ", 10, 50, 25, 5)
    save_snap = st.checkbox("Зберігати snapshot (Movers)", value=True)

    run = st.button("Запустити пошук")


if run:
    if not api_key.strip():
        st.error("Встав YouTube API ключ.")
        st.stop()

    # ВАЖЛИВО: беремо ключі ТІЛЬКИ з текстового поля (як було “до правок”)
    keywords = [k.strip() for k in (keywords_csv or "").split(",") if k.strip()]
    if not keywords:
        st.error("Додай хоча б 1 ключове слово.")
        st.stop()

    published_after = iso_days_ago(int(search_days_back))

    with st.spinner("Шукаю відео і рахую метрики..."):
        df = fetch_videos_with_channels(
            api_key=api_key.strip(),
            keywords=keywords[:25],
            region=region,
            lang=lang,
            published_after=published_after,
            per_query=int(per_query),
            order=order_mode,
        )

    if df.empty:
        st.warning("Нічого не знайшов. Спробуй інші ключі або збільш дні/per_query.")
        st.stop()

    st.session_state.df = df
    st.session_state.page = 1
    st.session_state.topics_df = pd.DataFrame()

    if save_snap:
        snap_id = save_snapshot(df)
        if snap_id:
            st.toast(f"Snapshot saved: {snap_id}", icon="✅")


df = st.session_state.df
if df.empty:
    st.info("Введи API ключ, ключові слова і натисни **«Запустити пошук»**.")
    st.stop()

# Apply filters
df2 = df.copy()
df2 = df2[(df2["age_days"] >= float(age_min)) & (df2["age_days"] <= float(age_max))]
df2 = df2[(df2["duration_min"] >= min_minutes) & (df2["duration_min"] <= max_minutes)]
df2 = df2[df2["views_per_day"] >= float(min_views_per_day)]
df2 = df2[df2["engagement_pct"] >= float(min_eng_pct)]
if max_subs and int(max_subs) > 0:
    df2 = df2[df2["subs"] <= int(max_subs)]

if not (ch_age_min == 0 and ch_age_max == 5000):
    df2 = df2[df2["channel_age_days"].notna()]
    df2 = df2[
        (df2["channel_age_days"].astype(float) >= float(ch_age_min)) &
        (df2["channel_age_days"].astype(float) <= float(ch_age_max))
    ]

df2 = df2.sort_values(sort_by, ascending=False).reset_index(drop=True)

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 Overview", "⚡ Movers", "🧩 Clusters", "🧠 AI Topics", "📌 Shortlist"])

with tab1:
    st.subheader("Загальна аналітика")
    cA, cB, cC, cD, cE, cF = st.columns(6)
    cA.metric("Відео (після фільтрів)", f"{len(df2)}")
    cB.metric("Медіана переглядів/день", f"{df2['views_per_day'].median():.0f}" if len(df2) else "0")
    cC.metric("Медіана Trend", f"{df2['trend_score'].median():.0f}" if len(df2) else "0")
    cD.metric("Медіана залучення", f"{df2['engagement_pct'].median():.2f}%" if len(df2) else "0%")
    cE.metric("Медіана підписників", fmt_int(int(df2["subs"].median() if len(df2) else 0)))
    cF.metric("Топ-рейтинг", f"{df2['rating'].max():.1f}" if len(df2) else "0")

    with st.expander("Аналітика по ключових словах (розширена)"):
        render_keyword_analytics(df2, rpm_low=rpm_low, rpm_high=rpm_high)

    st.divider()
    st.subheader("Результати (картки) • 30 на сторінку • 2 в ряд")

    PAGE_SIZE = 30
    total = len(df2)
    total_pages = max(1, math.ceil(total / PAGE_SIZE))

    colp1, colp2, colp3, colp4 = st.columns([1, 2, 2, 1])
    with colp1:
        if st.button("◀ Назад", disabled=(st.session_state.page <= 1)):
            st.session_state.page -= 1
    with colp4:
        if st.button("Далі ▶", disabled=(st.session_state.page >= total_pages)):
            st.session_state.page += 1
    with colp2:
        st.write(f"Сторінка **{st.session_state.page}** / **{total_pages}**")
    with colp3:
        page = st.number_input("Перейти на сторінку", min_value=1, max_value=total_pages, value=st.session_state.page, step=1)
        st.session_state.page = int(page)

    start = (st.session_state.page - 1) * PAGE_SIZE
    end = start + PAGE_SIZE
    page_df = df2.iloc[start:end].copy()

    items = page_df.to_dict(orient="records")
    for i in range(0, len(items), 2):
        cols = st.columns(2)
        for j in range(2):
            idx = i + j
            if idx >= len(items):
                break
            v = items[idx]
            with cols[j]:
                with st.container(border=True):
                    if v.get("thumb_url"):
                        st.image(v["thumb_url"], use_container_width=True)
                    render_video_card(v, rpm_low=rpm_low, rpm_high=rpm_high)

    st.download_button(
        "Завантажити CSV (після фільтрів)",
        data=df2.to_csv(index=False).encode("utf-8"),
        file_name="yt_viral_finder_filtered.csv",
        mime="text/csv"
    )

with tab2:
    st.subheader("⚡ Movers (між 2 останніми snapshot)")
    movers = compute_movers(top_n=80)
    if movers.empty:
        st.info("Поки нема 2 snapshot. Увімкни 'Зберігати snapshot' і запусти пошук 2 рази.")
    else:
        show = movers[[
            "title", "channel_title", "keyword", "views_per_day", "vpd_delta", "views_delta",
            "engagement_pct", "eng_delta", "url"
        ]].copy()
        show.rename(columns={
            "title": "Title",
            "channel_title": "Channel",
            "keyword": "Keyword",
            "views_per_day": "VPD now",
            "vpd_delta": "Δ VPD",
            "views_delta": "Δ Views",
            "engagement_pct": "Eng %",
            "eng_delta": "Δ Eng",
            "url": "URL"
        }, inplace=True)
        st.dataframe(show, use_container_width=True, height=620)

with tab3:
    st.subheader("🧩 Title Clusters (формати)")
    k = st.slider("К-сть кластерів", 2, 16, 8, 1)
    clustered = cluster_titles(df2, k=k)
    if clustered.empty:
        st.info("Нема даних.")
    else:
        agg = clustered.groupby(["cluster", "cluster_terms"], as_index=False).agg(
            videos=("video_id", "count"),
            median_vpd=("views_per_day", "median"),
            median_trend=("trend_score", "median"),
            median_rating=("rating", "median"),
        ).sort_values("median_trend", ascending=False)
        st.dataframe(agg, use_container_width=True, height=360)

        pick = st.selectbox("Подивитись кластер", agg["cluster"].tolist(), index=0)
        sub = clustered[clustered["cluster"] == pick].sort_values("trend_score", ascending=False).head(60)
        st.dataframe(
            sub[["title", "channel_title", "keyword", "views_per_day", "trend_score", "rating", "url"]],
            use_container_width=True,
            height=620
        )

with tab4:
    st.subheader("🧠 AI Topics")

    top_kw_df = compute_top_keywords(df2, rpm_low=rpm_low, rpm_high=rpm_high, top_n=10)
    if top_kw_df.empty:
        st.info("Недостатньо даних для ТОП-ключів. Збільш дні/per_query або послаб фільтри.")
    else:
        st.dataframe(top_kw_df, use_container_width=True, height=320)

        log_box = st.empty()
        result_box = st.empty()

        with st.form("ai_topics_form", clear_on_submit=False):
            col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
            with col1:
                selected_kw = st.selectbox("Ключ для AI", top_kw_df["Ключове слово"].tolist(), index=0)
            with col2:
                final_n = st.number_input("К-сть фінальних (10–20)", min_value=10, max_value=20, value=15, step=1)
            with col3:
                seed = st.number_input("Seed", min_value=0, max_value=999999, value=123, step=1)
            with col4:
                bulk_n = st.number_input("Чернеток (bulk)", min_value=25, max_value=60, value=35, step=5)

            submitted = st.form_submit_button("🧠 Згенерувати пакети тем")

        if submitted:
            try:
                if _get_openai_client() is None:
                    raise RuntimeError("Нема OpenAI ключа/клієнта. Додай OPENAI_API_KEY в env.")

                log_box.info("1/3 Збираю референси…")
                sample_titles, patterns = extract_title_patterns(df2, selected_kw, top_k=25)

                log_box.info("2/3 Генерую чернетки…")
                drafts = ai_bulk_generate_topics(
                    keyword=selected_kw,
                    sample_titles=sample_titles,
                    patterns=patterns,
                    n_bulk=int(bulk_n),
                    seed=int(seed),
                )

                log_box.info("3/3 Ранжую та покращую…")
                final_topics = ai_refine_and_rank(
                    keyword=selected_kw,
                    drafts=drafts,
                    n_final=int(final_n),
                )

                topics_df = topics_to_df(final_topics, selected_kw)
                if "Оцінка" in topics_df.columns:
                    topics_df = topics_df.sort_values("Оцінка", ascending=False)

                st.session_state.topics_df = topics_df
                log_box.success("Готово ✅")
                result_box.dataframe(st.session_state.topics_df, use_container_width=True, height=650)

            except Exception as e:
                log_box.error(f"Помилка AI: {e}")

        if not st.session_state.topics_df.empty:
            st.dataframe(st.session_state.topics_df, use_container_width=True, height=650)
            st.download_button(
                "Завантажити AI пакети (CSV)",
                data=st.session_state.topics_df.to_csv(index=False).encode("utf-8"),
                file_name="topics_packages_ai.csv",
                mime="text/csv"
            )

with tab5:
    st.subheader("📌 Shortlist (Закріплені відео)")
    if not st.session_state.shortlist:
        st.info("Натисни 📌 Закріпити на картці відео, щоб додати сюди.")
    else:
        sl = df2[df2["video_id"].isin(list(st.session_state.shortlist))].copy()
        if sl.empty:
            st.warning("Закріплені відео не в поточній вибірці (змінив фільтри/ключі).")
        else:
            st.dataframe(
                sl[["title", "channel_title", "keyword", "views_per_day", "trend_score", "rating", "url"]]
                .sort_values("trend_score", ascending=False),
                use_container_width=True,
                height=620
            )
