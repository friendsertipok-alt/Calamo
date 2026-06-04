from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.auth import get_current_user
from app.models import User, SupportTicket
from app.database import get_db
from app.pipeline.generator import orders_store
from app.config import settings
import os
import json
import logging

logger = logging.getLogger("FilesRouter")
router = APIRouter()

@router.get("/output/{filename}")
async def download_output(filename: str, user: User = Depends(get_current_user)):
    """Защищенное скачивание готовых работ."""
    # Находим заказ по имени файла в orders_store
    order_id = None
    for oid, order in orders_store.items():
        url = order.get("download_url", "")
        if url and filename in url:
            order_id = oid
            break
    
    if not order_id:
        # Если не нашли в store, попробуем поискать в БД (на случай если это старый заказ)
        from app.models import Order
        # Но в БД file_path может быть полным путем или именем файла
        # Для простоты и безопасности ориентируемся на orders_store, так как там вся текущая работа
        raise HTTPException(status_code=404, detail="Файл не найден в базе активных заказов")
    
    stored = orders_store[order_id]
    if stored.get("user_id") != user.id and not user.is_admin:
        logger.warning(f"Unauthorized access attempt to {filename} by user {user.email}")
        raise HTTPException(status_code=403, detail="Доступ запрещен: это не ваш файл")
        
    file_path = settings.OUTPUT_DIR / filename
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Файл на диске не найден")
        
    return FileResponse(file_path)

@router.get("/uploads/support/{filename}")
async def download_support_file(
    filename: str, 
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Защищенное скачивание вложений из техподдержки."""
    if user.is_admin:
        # Админам можно всё
        file_path = settings.UPLOADS_DIR / "support" / filename
        if os.path.exists(file_path):
            return FileResponse(file_path)
        raise HTTPException(status_code=404, detail="Файл не найден")

    # Для обычного пользователя проверяем, принадлежит ли ему тикет с этим файлом
    # Ищем тикеты пользователя по email
    stmt = select(SupportTicket).where(SupportTicket.user_email == user.email)
    result = await db.execute(stmt)
    tickets = result.scalars().all()
    
    found = False
    for ticket in tickets:
        if ticket.file_urls:
            try:
                urls = json.loads(ticket.file_urls)
                if any(filename in url for url in urls):
                    found = True
                    break
            except: continue
            
    if not found:
        logger.warning(f"User {user.email} tried to access support file {filename} without permission")
        raise HTTPException(status_code=403, detail="Доступ запрещен")
        
    file_path = settings.UPLOADS_DIR / "support" / filename
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Файл не найден")
        
    return FileResponse(file_path)
