from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.core.security import get_current_user
from app.core.templates import templates
from app.schemas.topics import TopicRequest
from app.services import jobs

router = APIRouter()


@router.get("/topics", response_class=HTMLResponse)
async def topics_page(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse("topics.html", {"request": request})


@router.post("/api/topics/generate")
async def generate_topics(
    request: Request,
    keyword: str = Form(...),
    bulk_n: int = Form(20),
    final_n: int = Form(10),
    seed: str | None = Form(None),
    user=Depends(get_current_user),
):
    req = TopicRequest(keyword=keyword, bulk_n=bulk_n, final_n=final_n, seed=seed)
    job = jobs.enqueue_job(jobs.topics_job, req.model_dump())
    if "hx-request" in request.headers:
        return templates.TemplateResponse(
            "partials/job_status.html",
            {"request": request, "job_id": job.id, "target": "topics"},
        )
    return JSONResponse({"job_id": job.id})
