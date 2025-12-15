from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.templates import templates
from app.services import jobs

router = APIRouter()


@router.get("/api/jobs/{job_id}")
async def job_status(job_id: str, request: Request):
    data = jobs.job_status(job_id)
    if "hx-request" in request.headers:
        return templates.TemplateResponse(
            "partials/job_status.html",
            {"request": request, "job_id": job_id, "status": data, "target": request.query_params.get("target")},
        )
    return JSONResponse(data)
