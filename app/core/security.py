from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, URLSafeSerializer
from passlib.context import CryptContext

from app.core.config import SessionData, get_settings
from app.db.session import get_db
from app.models.user import User
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_serializer() -> URLSafeSerializer:
    settings = get_settings()
    return URLSafeSerializer(settings.secret_key)


async def get_current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    require_admin: bool = False,
) -> User:
    settings = get_settings()
    cookie = request.cookies.get(settings.session_cookie_name)
    if not cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    serializer = get_serializer()
    try:
        data = serializer.loads(cookie)
    except BadSignature:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    session_data = SessionData.model_validate(data)
    stmt = select(User).where(User.id == session_data.user_id)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    if require_admin and user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user


async def login_user(response: Response, user: User) -> None:
    settings = get_settings()
    serializer = get_serializer()
    payload = SessionData(user_id=user.id, role=user.role).model_dump()
    token = serializer.dumps(payload)
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        domain=settings.session_cookie_domain,
    )


def logout_user(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(settings.session_cookie_name)


async def ensure_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return user
