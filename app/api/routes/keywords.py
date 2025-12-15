from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.db.session import get_db
from app.core.templates import templates
from app.services.analytics import top_keywords

router = APIRouter()


@router.get("/api/keywords/top")
async def api_top_keywords(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    scores = await top_keywords(db)
    return [s.model_dump() for s in scores]


@router.get("/keywords", response_class=HTMLResponse)
async def keywords_page(request: Request, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    scores = await top_keywords(db)
    return templates.TemplateResponse("keywords.html", {"request": request, "scores": scores})
