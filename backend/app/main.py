"""
Calamo — FastAPI Main Application
"""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from pathlib import Path
from app.config import settings
from app.api.routes import auth, orders, generation, reviews, support, admin, payment, files
from app.database import engine, Base
import logging

import os
from contextlib import asynccontextmanager
import mimetypes

# Fix for Docker environments where mimetypes db might be missing
mimetypes.add_type("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx")

logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.APP_NAME,
    description="Автоматизированная платформа генерации академических работ",
    version="1.0.0",
)

# Инициализация Rate Limiter
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Создаем таблицы при старте (PostgreSQL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Очистка истёкших токенов из блеклиста (предотвращает рост таблицы)
    try:
        from app.auth import cleanup_expired_blacklist
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            await cleanup_expired_blacklist(db)
    except Exception as e:
        logger.warning(f"Blacklist cleanup on startup skipped: {e}")
    
    # --- SEED MOCK USER AND ORDER ---
    try:
        from app.database import AsyncSessionLocal
        from app.models import User
        from app.pipeline.generator import orders_store, _save_orders
        from sqlalchemy import select
        import shutil

        # 1. Проверяем / создаем пользователя
        async with AsyncSessionLocal() as session:
            stmt = select(User).where(User.email == "pobedonosec756@gmail.com")
            res = await session.execute(stmt)
            user = res.scalars().first()
            if not user:
                user = User(
                    email="pobedonosec756@gmail.com",
                    full_name="Мария Плоткина",
                    provider="google",
                    provider_id="pobedonosec756_google_id",
                    balance=0.0
                )
                session.add(user)
                await session.commit()
                await session.refresh(user)
                logger.info(f"Mock user created with ID {user.id}")
            else:
                logger.info(f"Mock user already exists with ID {user.id}")
            
            user_id = user.id

        # 1.5 Проверяем / создаем записи стоимости в LLMUsage
        from app.models import LLMUsage
        from datetime import datetime, timedelta
        async with AsyncSessionLocal() as session:
            # Заказ 1: 5634fhwj
            stmt1 = select(LLMUsage).where(LLMUsage.order_id == "5634fhwj")
            res1 = await session.execute(stmt1)
            usage1 = res1.scalars().first()
            if not usage1:
                usage1 = LLMUsage(
                    order_id="5634fhwj",
                    model="gemini-1.5-pro",
                    prompt_tokens=45000,
                    completion_tokens=15000,
                    total_tokens=60000,
                    estimated_cost_rub=48.50, # в пределах 45-50р
                    description="Полная генерация курсового проекта",
                    created_at=datetime.utcnow() - timedelta(days=2)
                )
                session.add(usage1)
                logger.info("Seeded LLMUsage for 5634fhwj (48.50 RUB)")

            # Заказ 2: f0984s7g
            stmt2 = select(LLMUsage).where(LLMUsage.order_id == "f0984s7g")
            res2 = await session.execute(stmt2)
            usage2 = res2.scalars().first()
            if not usage2:
                usage2 = LLMUsage(
                    order_id="f0984s7g",
                    model="gemini-1.5-pro",
                    prompt_tokens=42000,
                    completion_tokens=14000,
                    total_tokens=56000,
                    estimated_cost_rub=46.80, # в пределах 45-50р
                    description="Полная генерация курсового проекта",
                    created_at=datetime.utcnow() - timedelta(days=3)
                )
                session.add(usage2)
                logger.info("Seeded LLMUsage for f0984s7g (46.80 RUB)")
                
            await session.commit()

        # 2. Копируем файлы
        for mock_id, filename in [("5634fhwj", "Paper_5634fhwj.docx"), ("f0984s7g", "Paper_f0984s7g.docx")]:
            src_file = settings.UPLOADS_DIR / filename
            dest_dir = settings.OUTPUT_DIR / mock_id
            dest_file = dest_dir / filename
            if src_file.exists():
                dest_dir.mkdir(parents=True, exist_ok=True)
                if not dest_file.exists() or dest_file.stat().st_size != src_file.stat().st_size:
                    shutil.copy2(src_file, dest_file)
                    logger.info(f"Copied mock file to {dest_file}")
            else:
                logger.warning(f"Mock source file not found at {src_file}")

        # 3. Инжектим заказы в orders_store
        if "plotsmar" in orders_store:
            del orders_store["plotsmar"]

        orders_store["5634fhwj"] = {
            "id": "5634fhwj",
            "user_id": user_id,
            "data": {
                "topic": "Влияние корпоративной культуры на эффективность работы компании (на примере ООО «Яндекс»)",
                "work_type": "курсовая",
                "subject": "Менеджмент",
                "university": "Финансовый университет при Правительстве Российской Федерации",
                "student_name": "Плоткина Мария",
                "student_group": "Высшая школа управления",
                "teacher_name": "Иванов И.И.",
                "teacher_title": "доцент, к.э.н.",
                "pages_count": 35,
                "tables_count": 2,
                "figures_count": 2,
                "target_words": 7500
            },
            "status": "completed",
            "progress": 100,
            "current_step": "Работа готова!",
            "steps_completed": [
                "Введение написано",
                "Материалы источников изучены",
                "Проектирование таблиц и графиков",
                "Глава 1 написана",
                "Глава 2 написана",
                "Заключение написано",
                "Документ собран и готов к скачиванию"
            ],
            "download_url": f"/output/5634fhwj/Paper_5634fhwj.docx",
            "error_message": None,
            "logs": [
                "[10:00:00] Заказ создан. Ожидание запуска.",
                "[10:05:00] Генерация плана работы...",
                "[10:10:00] Подбор источников...",
                "[10:15:00] Написание введения...",
                "[10:20:00] Генерация графиков и таблиц...",
                "[10:35:00] Написание глав...",
                "[10:50:00] Написание заключения...",
                "[10:55:00] Сборка документа Word...",
                "[11:00:00] Документ успешно собран: Paper_5634fhwj.docx",
                "[11:00:00] Статус: Работа готова! (100%)"
            ],
            "created_at": "2026-06-17T11:00:00.000000"
        }

        orders_store["f0984s7g"] = {
            "id": "f0984s7g",
            "user_id": user_id,
            "data": {
                "topic": "Разработка продуктовой стратегии компании в сфере онлайн-ритейла",
                "work_type": "курсовая",
                "subject": "Электронная коммерция",
                "university": "Финансовый университет при Правительстве Российской Федерации",
                "student_name": "Алексеев А.А.",
                "student_group": "Высшая школа управления",
                "teacher_name": "Петров П.П.",
                "teacher_title": "доцент, к.э.н.",
                "pages_count": 30,
                "tables_count": 2,
                "figures_count": 2,
                "target_words": 6500
            },
            "status": "completed",
            "progress": 100,
            "current_step": "Работа готова!",
            "steps_completed": [
                "Введение написано",
                "Материалы источников изучены",
                "Проектирование таблиц и графиков",
                "Глава 1 написана",
                "Глава 2 написана",
                "Заключение написано",
                "Документ собран и готов к скачиванию"
            ],
            "download_url": f"/output/f0984s7g/Paper_f0984s7g.docx",
            "error_message": None,
            "logs": [
                "[14:00:00] Заказ создан. Ожидание запуска.",
                "[14:05:00] Генерация плана работы...",
                "[14:10:00] Подбор источников...",
                "[14:15:00] Написание введения...",
                "[14:20:00] Генерация графиков и таблиц...",
                "[14:35:00] Написание глав...",
                "[14:50:00] Написание заключения...",
                "[14:55:00] Сборка документа Word...",
                "[15:00:00] Документ успешно собран: Paper_f0984s7g.docx",
                "[15:00:00] Статус: Работа готова! (100%)"
            ],
            "created_at": "2026-06-16T15:30:00.000000"
        }

        _save_orders(orders_store)
        logger.info("Mock orders 5634fhwj and f0984s7g injected/updated in orders_store")

    except Exception as e:
        logger.error(f"Failed to seed mock user/order: {e}", exc_info=True)
    
    yield

app.router.lifespan_context = lifespan

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # Маскируем чувствительные данные (пароли и т.д.) в логах
    body = exc.body
    if isinstance(body, bytes):
        try:
            import json
            body = json.loads(body)
        except:
            body = str(body)
            
    if isinstance(body, dict):
        body = body.copy()
        for sensitive_key in ["password", "token", "secret", "new_password"]:
            if sensitive_key in body:
                body[sensitive_key] = "********"
    
    logger.error(f"Validation error payload: {body}")
    logger.error(f"Validation error headers: {dict(request.headers)}")
    logger.error(f"Validation error details: {exc.errors()}")
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )

import json
@app.exception_handler(json.JSONDecodeError)
async def json_exception_handler(request: Request, exc: json.JSONDecodeError):
    logger.error(f"GLOBAL JSON ERROR: {str(exc)}")
    return JSONResponse(
        status_code=400,
        content={"error": "JSON_ERROR", "message": str(exc)},
    )

# CORS — разрешаем фронтенд
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,  # Ограниченный список доменов из настроек
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Прокси заголовки (важно для HTTPS за Nginx)
# Доверяем только Nginx (Docker) и localhost (локальная разработка)
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["127.0.0.1", "nginx", "calamo_nginx"])

# Безопасность и CSRF Middleware
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # 1. Защита от CSRF (Custom Header Check)
    # Требуем кастомный заголовок для всех мутирующих запросов к API
    if request.method in ["POST", "PUT", "DELETE", "PATCH"] and request.url.path.startswith("/api/"):
        # Исключения для вебхуков и эндпоинтов авторизации
        if not request.url.path.startswith("/api/payment/callback") and not request.url.path.startswith("/api/auth/"):
            if request.headers.get("x-requested-with") != "XMLHttpRequest":
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=403, content={"detail": "CSRF verification failed. Missing X-Requested-With header."})
    
    response = await call_next(request)
    
    # 2. Заголовки безопасности и скрытие метаданных
    response.headers["Server"] = "Calamo Engine"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    
    # Базовый CSP: защищает от загрузки сторонних вредоносных скриптов
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://www.googletagmanager.com https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https://lh3.googleusercontent.com https://avatars.githubusercontent.com https://cdn-icons-png.flaticon.com https://*.googleusercontent.com; "
        "connect-src 'self' https://www.google-analytics.com https://accounts.google.com https://oauth2.googleapis.com https://*.googleapis.com;"
    )
    return response

# Маршруты API
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(orders.router, prefix="/api/orders", tags=["orders"])
app.include_router(generation.router, prefix="/api/generation", tags=["generation"])
app.include_router(reviews.router, prefix="/api/reviews", tags=["reviews"])
app.include_router(support.router, prefix="/api/support", tags=["support"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(payment.router, prefix="/api/payment", tags=["payment"])
app.include_router(files.router, tags=["files"]) # Без префикса, чтобы ловить /output и /uploads

# Статические файлы (Публичные папки)
settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
settings.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


# Документы (политики, соглашения)
docs_path = Path(__file__).resolve().parent.parent.parent / "frontend" / "docs"
if docs_path.exists():
    app.mount("/docs", StaticFiles(directory=str(docs_path)), name="docs")


# -------------------------
# HEALTH & UTILS (Must be above catch-all)
@app.get("/api/health")
async def health_check():
    """Проверка работоспособности сервиса."""
    return {
        "status": "ok",
        "service": settings.APP_NAME,
        "version": "1.0.0",
    }

@app.get("/api/work-types")
async def get_work_types():
    """Получить доступные типы работ с параметрами."""
    return {
        work_type: {
            "pages": settings.DEFAULT_WORK_PAGES[work_type],
            "sources": settings.DEFAULT_SOURCES_COUNT[work_type],
            "tables": settings.DEFAULT_TABLES_COUNT[work_type],
            "figures": settings.DEFAULT_FIGURES_COUNT[work_type],
        }
        for work_type in settings.DEFAULT_WORK_PAGES
    }

@app.get("/manage-calamo-system")
async def serve_admin():
    # Явный поиск от корня проекта в Docker
    admin_path = Path("/app/frontend/admin.html")
    if not admin_path.exists():
        # Если не Docker, ищем локально
        base_dir = Path(__file__).resolve().parent.parent
        admin_path = base_dir / "frontend" / "admin.html"
    
    logger.info(f"Admin access attempt. Path: {admin_path}, Exists: {admin_path.exists()}")
    
    if admin_path.exists():
        return HTMLResponse(content=open(admin_path, "r", encoding="utf-8").read())
    return HTMLResponse(content="<h1>Admin not found</h1>", status_code=404)

# Явно запрещаем старый путь, чтобы не путаться
@app.get("/admin")
async def old_admin():
    raise HTTPException(status_code=404, detail="Not Found")

@app.get("/{path:path}")
async def serve_static_or_frontend(path: str):
    base_dir = Path(__file__).resolve().parent.parent
    frontend_dir = base_dir / "frontend"
    if not frontend_dir.exists():
        frontend_dir = base_dir.parent / "frontend"
    
    # Если путь пустой — отдаем index.html
    if not path:
        index_path = frontend_dir / "index.html"
        return HTMLResponse(content=open(index_path, "r", encoding="utf-8").read())

    # Защита от Path Traversal: проверяем, что путь остаётся внутри frontend_dir
    file_path = (frontend_dir / path).resolve()
    if not str(file_path).startswith(str(frontend_dir.resolve())):
        return HTMLResponse(content="<h1>Forbidden</h1>", status_code=403)

    if file_path.exists() and file_path.is_file():
        return FileResponse(file_path)

    # Если это не файл и не API — отдаем index.html (для SPA роутинга в будущем)
    index_path = frontend_dir / "index.html"
    if index_path.exists():
        return HTMLResponse(content=open(index_path, "r", encoding="utf-8").read())
    
    return HTMLResponse(content="<h1>Not Found</h1>", status_code=404)

logging.basicConfig(level=logging.INFO)
