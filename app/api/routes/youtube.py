from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.core.templates import templates
from app.schemas.youtube import YouTubeQuery
from app.services import jobs

router = APIRouter()


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse("dashboard.html", {"request": request})


@router.post("/api/youtube/analyze")
async def start_analysis(
    request: Request,
    api_key: str = Form(...),
    keywords: str = Form(...),
    days_back: int = Form(7),
    region: str = Form("US"),
    language: str = Form("en"),
    order: str = Form("viewCount"),
    per_query: int = Form(5),
    age_min: int | None = Form(None),
    age_max: int | None = Form(None),
    channel_age_min: int | None = Form(None),
    channel_age_max: int | None = Form(None),
    min_minutes: int | None = Form(None),
    max_minutes: int | None = Form(None),
    min_views_per_day: float | None = Form(None),
    min_eng_pct: float | None = Form(None),
    max_subs: int | None = Form(None),
    rpm_low: float | None = Form(None),
    rpm_high: float | None = Form(None),
    user=Depends(get_current_user),
):
    query = YouTubeQuery(
        api_key=api_key,
        keywords=[k.strip() for k in keywords.split(",") if k.strip()],
        days_back=days_back,
        region=region,
        language=language,
        order=order,
        per_query=per_query,
        age_min=age_min,
        age_max=age_max,
        channel_age_min=channel_age_min,
        channel_age_max=channel_age_max,
        min_minutes=min_minutes,
        max_minutes=max_minutes,
        min_views_per_day=min_views_per_day,
        min_eng_pct=min_eng_pct,
        max_subs=max_subs,
        rpm_low=rpm_low,
        rpm_high=rpm_high,
    )
    job = jobs.enqueue_job(jobs.youtube_job, query.model_dump())
    if "hx-request" in request.headers:
        return templates.TemplateResponse(
            "partials/job_status.html",
            {"request": request, "job_id": job.id, "target": "analysis"},
        )
    return JSONResponse({"job_id": job.id})
