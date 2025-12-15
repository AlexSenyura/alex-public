from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from app.api.routes import auth, jobs, keywords, movers, snapshots, topics, youtube
from app.core.config import get_settings
from app.core.security import get_current_user
from app.core.templates import templates
from app.models.user import User
from app.scripts.bootstrap_admin import ensure_admin

settings = get_settings()

app = FastAPI(title=settings.app_name, debug=settings.debug)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def add_user(request: Request, call_next):
    try:
        request.state.user = await get_current_user(request)
    except Exception:
        request.state.user = None
    response = await call_next(request)
    return response


@app.get("/", include_in_schema=False)
async def root(request: Request):
    if not request.state.user:
        return RedirectResponse("/login")
    return RedirectResponse("/dashboard")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def startup_event():
    await ensure_admin()


app.include_router(auth.router)
app.include_router(youtube.router)
app.include_router(jobs.router)
app.include_router(keywords.router)
app.include_router(topics.router)
app.include_router(snapshots.router)
app.include_router(movers.router)
