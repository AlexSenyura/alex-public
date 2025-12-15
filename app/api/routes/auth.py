from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user, get_password_hash, login_user, logout_user, verify_password
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import LoginForm, UserCreate
from app.core.templates import templates

router = APIRouter()


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...), db: AsyncSession = Depends(get_db)):
    stmt = select(User).where(User.email == email)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.hashed_password) or not user.is_active:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Невірні облікові дані"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    response = RedirectResponse("/dashboard", status_code=status.HTTP_302_FOUND)
    await login_user(response, user)
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse("/login")
    logout_user(response)
    return response


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(status_code=403)
    users = (await db.execute(select(User))).scalars().all()
    return templates.TemplateResponse("admin_users.html", {"request": request, "users": users})


@router.post("/admin/users")
async def create_user(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
    is_active: bool = Form(True),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_user),
):
    if admin.role != "admin":
        raise HTTPException(status_code=403)
    stmt = select(User).where(User.email == email)
    existing = await db.execute(stmt)
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email вже існує")
    user = User(email=email, hashed_password=get_password_hash(password), role=role, is_active=is_active)
    db.add(user)
    await db.commit()
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/toggle")
async def toggle_user(user_id: int, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_user)):
    if admin.role != "admin":
        raise HTTPException(status_code=403)
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404)
    user.is_active = not user.is_active
    await db.commit()
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/password")
async def change_password(
    user_id: int,
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_user),
):
    if admin.role != "admin":
        raise HTTPException(status_code=403)
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404)
    user.hashed_password = get_password_hash(password)
    await db.commit()
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/delete")
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db), admin: User = Depends(get_current_user)):
    if admin.role != "admin":
        raise HTTPException(status_code=403)
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404)
    await db.delete(user)
    await db.commit()
    return RedirectResponse("/admin/users", status_code=303)
