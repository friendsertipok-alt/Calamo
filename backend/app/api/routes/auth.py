from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.database import get_db
from app.models import User
from app.schemas.user import UserResponse, LoginRequest, Token
from app.auth import create_access_token, get_current_user
from app.config import settings
import httpx
import json
import secrets
from fastapi.responses import JSONResponse
import logging
from urllib.parse import urlencode, quote

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/google/url")
async def get_google_auth_url(return_to: str = "/"):
    """Возвращает URL для перенаправления пользователя в Google"""
    logger.info(f"Generating Google auth URL. Client ID: '{settings.GOOGLE_CLIENT_ID[:5] if settings.GOOGLE_CLIENT_ID else ''}...', Redirect URI: {settings.GOOGLE_REDIRECT_URI}")
    
    if not settings.GOOGLE_CLIENT_ID:
        logger.error("GOOGLE_CLIENT_ID is missing in settings!")

    csrf_token = secrets.token_urlsafe(32)
    state_value = f"{return_to}||{csrf_token}"
    
    client_id = settings.GOOGLE_CLIENT_ID.strip() if settings.GOOGLE_CLIENT_ID else ""
    redirect_uri = settings.GOOGLE_REDIRECT_URI.strip() if settings.GOOGLE_REDIRECT_URI else ""

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "select_account",
        "state": state_value
    }
    
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    
    response = JSONResponse({"url": url})
    response.set_cookie(
        key="oauth_csrf_token",
        value=csrf_token,
        httponly=True,
        max_age=600, # 10 минут на логин
        secure=True,
        samesite="none"
    )
    return response

@router.get("/callback/google")
async def google_callback(request: Request, code: str, state: str = "/", db: AsyncSession = Depends(get_db)):
    """Обрабатывает код от Google и авторизует пользователя"""
    logger.info(f"Incoming Google Callback: code='{code[:10]}...', state='{state[:20]}...'")
    
    # 0. Проверка CSRF токена
    parts = state.split("||")
    return_to = parts[0] if len(parts) > 0 else "/"
    csrf_token = parts[1] if len(parts) > 1 else ""
    
    cookie_token = request.cookies.get("oauth_csrf_token")
    
    # Мягкая проверка: если куки нет (блокировка браузером), проверяем только токен в state
    if not cookie_token:
        logger.warning(f"CSRF Cookie missing, but valid state token found. Proceeding. State: {state}")
    elif cookie_token != csrf_token:
        logger.error(f"CSRF Token Mismatch: cookie={cookie_token}, state={csrf_token}")
        raise HTTPException(status_code=400, detail="CSRF Validation Failed. Ошибка безопасности.")

    # 1. Обмениваем код на токены и получаем данные профиля
    client_id = settings.GOOGLE_CLIENT_ID.strip() if settings.GOOGLE_CLIENT_ID else ""
    client_secret = settings.GOOGLE_CLIENT_SECRET.strip() if settings.GOOGLE_CLIENT_SECRET else ""
    redirect_uri = settings.GOOGLE_REDIRECT_URI.strip() if settings.GOOGLE_REDIRECT_URI else ""
    
    # Формируем тело запроса
    exchange_body = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    # Формируем заголовок Basic Auth (на случай если Google требует его)
    import base64
    auth_str = f"{client_id}:{client_secret}"
    auth_b64 = base64.b64encode(auth_str.encode()).decode()

    try:
        async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:
            token_res = await client.post(
                "https://oauth2.googleapis.com/token",
                data=exchange_body,
                headers={
                    "Authorization": f"Basic {auth_b64}",
                    "Content-Type": "application/x-www-form-urlencoded"
                }
            )
            token_data = token_res.json()
            
            if token_res.status_code != 200:
                logger.error(f"Google Token Exchange FAILED. Status: {token_res.status_code}")
                logger.error(f"Response Headers: {dict(token_res.headers)}")
                logger.error(f"Response Body: {token_res.text}")
                raise HTTPException(status_code=400, detail=f"Google Error: {token_data.get('error_description', 'Bad Request')}")
            
            if "error" in token_data:
                logger.error(f"Google Token Exchange Error Response: {token_data}")
                raise HTTPException(status_code=400, detail=f"Google Error: {token_data.get('error_description')}")
                
            access_token = token_data.get("access_token")
            
            # Затем профиль
            user_res = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            user_info = user_res.json()
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Google Auth Communication Error: {str(e)}")
        raise HTTPException(
            status_code=502, 
            detail=f"Не удалось связаться с Google для авторизации. Техническая ошибка: {type(e).__name__}"
        )
        
    email = user_info.get("email")
    full_name = user_info.get("name")
    google_id = user_info.get("sub")
    avatar = user_info.get("picture")

    if not email:
        raise HTTPException(status_code=400, detail="Google did not provide email")

    # 3. Синхронизируем с БД
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()
    
    is_new = False
    if not user:
        user = User(
            email=email,
            full_name=full_name or "Пользователь Google",
            provider="google",
            provider_id=google_id,
            avatar_url=avatar
        )
        db.add(user)
        is_new = True
    else:
        # Обновляем имя и аватар если изменились
        user.full_name = full_name or user.full_name
        user.avatar_url = avatar or user.avatar_url
        
    await db.commit()
    await db.refresh(user)

    # 3.5 Логируем визит
    from app.models import UserVisit
    visit = UserVisit(user_id=user.id, is_new_user=is_new)
    db.add(visit)
    await db.commit()
    
    # 4. Создаем наш внутренний JWT
    our_token = create_access_token(data={"sub": user.email, "user_id": user.id})
    
    # 5. Перенаправляем обратно на нужную страницу (ТОЛЬКО КУКИ, без токена в URL)
    redirect_path = return_to if return_to.startswith("/") else "/"
    url = f"https://calamo.lol{redirect_path}"
        
    response = RedirectResponse(url=url)
    
    # Ставим HttpOnly куку с токеном (недоступно для JS/XSS)
    response.set_cookie(
        key="access_token",
        value=our_token,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )
    # Ставим публичную куку для UI, чтобы он знал, что мы вошли (но без самого токена)
    response.set_cookie(
        key="logged_in_status",
        value="true",
        httponly=False,
        secure=True,
        samesite="lax",
        path="/",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )
    # Удаляем временную куку CSRF
    response.delete_cookie("oauth_csrf_token")
    
    return response



@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    return user

@router.post("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get("access_token")
    if token:
        from app.models import BlacklistedToken
        bl_token = BlacklistedToken(token=token)
        db.add(bl_token)
        try:
            await db.commit()
        except Exception:
            await db.rollback()

    response = JSONResponse({"message": "Logged out successfully"})
    response.delete_cookie("access_token", secure=True, httponly=True, samesite="lax", path="/")
    response.delete_cookie("logged_in_status", secure=True, samesite="lax", path="/")
    return response
