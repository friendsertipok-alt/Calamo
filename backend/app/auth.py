from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from .config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from .database import get_db
from .models import User

# oauth2_scheme оставлен для Swagger UI, но реально токен берется из куки
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login", auto_error=False)

def decode_access_token(token: str):
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        return None

async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    token = request.cookies.get("access_token")
    if not token:
        # Фолбэк для Swagger или скриптов
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ")[1]
        else:
            raise credentials_exception

    payload = decode_access_token(token)
    if payload is None:
        raise credentials_exception

    from app.models import BlacklistedToken
    bl_check = await db.execute(select(BlacklistedToken).where(BlacklistedToken.token == token))
    if bl_check.scalars().first():
        raise credentials_exception
    email: str = payload.get("sub")
    if email is None:
        raise credentials_exception
    
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()
    if user is None:
        raise credentials_exception
    return user


async def get_current_user_optional(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        return await get_current_user(request, db)
    except Exception:
        return None


# --- Админ-защита ---
import json
from pathlib import Path

from functools import lru_cache

@lru_cache(maxsize=1)
def load_admin_emails() -> list[str]:
    """Загружает список email-адресов администраторов из admin_emails.json. Кэширует результат."""
    config_path = Path(__file__).resolve().parent.parent / "admin_emails.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            emails = [e.lower().strip() for e in data.get("admin_emails", [])]
            return emails
    except Exception as e:
        import logging
        logging.getLogger("uvicorn").error(f"Error loading admin_emails.json: {e}")
        return []

async def get_current_admin(user: User = Depends(get_current_user)):
    """Проверяет, что текущий пользователь — администратор."""
    admin_emails = load_admin_emails()
    if user.is_admin or user.email.lower() in admin_emails:
        return user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Доступ запрещен: требуются права администратора",
    )


async def cleanup_expired_blacklist(db: AsyncSession):
    """Удаляет из блеклиста токены, срок действия которых уже истёк.
    
    JWT-токены с истёкшим exp и так не пройдут проверку подписи,
    поэтому хранить их в блеклисте бессмысленно — это лишняя нагрузка на БД.
    """
    from app.models import BlacklistedToken
    try:
        result = await db.execute(select(BlacklistedToken))
        tokens = result.scalars().all()
        now = datetime.utcnow()
        deleted = 0
        for bt in tokens:
            try:
                # Декодируем без проверки подписи, чтобы получить exp
                payload = jwt.decode(
                    bt.token, settings.SECRET_KEY, 
                    algorithms=[settings.ALGORITHM],
                    options={"verify_exp": False}
                )
                exp = payload.get("exp")
                if exp and datetime.utcfromtimestamp(exp) < now:
                    await db.delete(bt)
                    deleted += 1
            except JWTError:
                # Повреждённый токен — тоже удаляем
                await db.delete(bt)
                deleted += 1
        if deleted > 0:
            await db.commit()
        import logging
        logging.getLogger(__name__).info(f"Blacklist cleanup: удалено {deleted} истёкших токенов")
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Blacklist cleanup error: {e}")
        await db.rollback()
