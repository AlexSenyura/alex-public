import asyncio

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import get_password_hash
from app.db.session import SessionLocal
from app.models.user import User


async def ensure_admin():
    settings = get_settings()
    async with SessionLocal() as db:
        result = await db.execute(select(User).where(User.role == "admin"))
        admin = result.scalar_one_or_none()
        if admin:
            return admin
        user = User(
            email=settings.admin_bootstrap_email,
            hashed_password=get_password_hash(settings.admin_bootstrap_password),
            role="admin",
            is_active=True,
        )
        db.add(user)
        await db.commit()
        return user


def main():
    asyncio.run(ensure_admin())


if __name__ == "__main__":
    main()
