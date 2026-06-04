import re
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from app.database import get_db
from app.models import SupportTicket
from app.services.telegram_service import notify_new_ticket

router = APIRouter()

# Schema for creating a ticket
class TicketCreate(BaseModel):
    user_email: str
    message: str

    @field_validator('user_email')
    @classmethod
    def validate_email(cls, v):
        if not re.match(r"[^@]+@[^@]+\.[^@]+", v):
            raise ValueError('Некорректный формат email')
        return v

import os
import uuid
import json
from fastapi import File, UploadFile, Form

# Schema for ticket response
class TicketResponse(BaseModel):
    id: int
    user_email: str
    message: str
    status: str
    file_urls: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

# Schema for status update
class StatusUpdate(BaseModel):
    status: str

@router.post("/tickets", response_model=TicketResponse)
async def create_ticket(
    background_tasks: BackgroundTasks,
    user_email: str = Form(...),
    message: str = Form(...),
    files: List[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db)
):
    """Создать новую заявку в техподдержку с файлами."""
    # Лимит на количество файлов (защита от CWE-400)
    MAX_FILES = 5
    if files and len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Слишком много файлов. Максимум {MAX_FILES} вложений.")

    # Валидация email вручную
    if not re.match(r"[^@]+@[^@]+\.[^@]+", user_email):
        raise HTTPException(status_code=400, detail="Некорректный формат email")

    saved_file_urls = []
    if files:
        from app.config import settings
        upload_dir = settings.UPLOADS_DIR / "support"
        os.makedirs(upload_dir, exist_ok=True)
        
        import magic
        ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".doc", ".docx", ".txt", ".xlsx", ".xls"}
        ALLOWED_MIME_TYPES = {
            "image/jpeg", "image/png", "application/pdf", 
            "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/plain", "application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        }
        MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
        
        for file in files:
            if not file.filename:
                continue
                
            ext = os.path.splitext(file.filename)[1].lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise HTTPException(status_code=400, detail=f"Недопустимое расширение файла: {ext}")
                
            # Проверка Magic Bytes
            first_chunk = await file.read(2048)
            mime_type = magic.from_buffer(first_chunk, mime=True)
            if mime_type not in ALLOWED_MIME_TYPES:
                raise HTTPException(status_code=400, detail=f"Недопустимый тип файла по содержимому: {mime_type}")
            
            # Сбрасываем указатель файла обратно в начало для последующего чтения
            await file.seek(0)
                
            if file.size is not None and file.size > MAX_FILE_SIZE:
                raise HTTPException(status_code=400, detail="Файл слишком большой. Максимальный размер 10 МБ")
                
            unique_filename = f"{uuid.uuid4()}{ext}"
            file_full_path = upload_dir / unique_filename
            
            # Читаем чанками, чтобы не убить оперативную память (OOM)
            file_size_read = 0
            with open(file_full_path, "wb") as f:
                while chunk := await file.read(1024 * 1024):  # Читаем по 1 МБ
                    file_size_read += len(chunk)
                    if file_size_read > MAX_FILE_SIZE:
                        # Удаляем файл, если он превысил лимит во время чтения
                        os.remove(file_full_path)
                        raise HTTPException(status_code=400, detail="Файл слишком большой. Максимальный размер 10 МБ")
                    f.write(chunk)
            
            saved_file_urls.append(f"/uploads/support/{unique_filename}")

    from app.utils.security import sanitize_html
    new_ticket = SupportTicket(
        user_email=sanitize_html(user_email),
        message=sanitize_html(message),
        file_urls=json.dumps(saved_file_urls) if saved_file_urls else None
    )
    db.add(new_ticket)
    await db.commit()
    await db.refresh(new_ticket)
    
    # Отправляем уведомление в Telegram в фоне
    background_tasks.add_task(
        notify_new_ticket, 
        ticket_id=new_ticket.id, 
        email=user_email, 
        message=message, 
        file_urls=saved_file_urls if saved_file_urls else None
    )
    
    return new_ticket

from app.auth import get_current_admin

@router.get("/tickets", response_model=List[TicketResponse])
async def list_tickets(
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    status: Optional[str] = Query(None)
):
    """Получить список всех заявок (Админ-панель)."""
    query = select(SupportTicket).order_by(SupportTicket.created_at.desc())
    if status:
        query = query.where(SupportTicket.status == status)
    
    result = await db.execute(query)
    return result.scalars().all()

@router.patch("/tickets/{ticket_id}/status")
async def update_ticket_status(
    ticket_id: int, 
    update_data: StatusUpdate,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Обновить статус заявки."""
    # Map old UI status to new backend status if needed, or just accept
    status_map = {"Закрыта": "Решена", "Решена": "Решена", "Новая": "Новая", "В работе": "В работе"}
    new_status = status_map.get(update_data.status, update_data.status)
    
    await db.execute(
        update(SupportTicket)
        .where(SupportTicket.id == ticket_id)
        .values(status=new_status)
    )
    await db.commit()
    return {"status": "success", "message": "Статус обновлен"}

@router.delete("/tickets/{ticket_id}")
async def delete_ticket(
    ticket_id: int,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Удалить заявку (только для админов)."""
    ticket = await db.get(SupportTicket, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    
    await db.delete(ticket)
    await db.commit()
    return {"status": "success", "message": "Заявка удалена"}

from app.services.telegram_service import get_latest_updates, handle_telegram_update, set_webhook

@router.get("/telegram/check-updates")
async def check_tg_updates(admin: dict = Depends(get_current_admin)):
    """Помощник для поиска Chat ID группы."""
    return await get_latest_updates()

@router.post("/telegram/webhook")
async def telegram_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """Эндпоинт для приема обновлений от Telegram с проверкой секрета (защита от CWE-288)."""
    from app.config import settings
    
    # Проверка секретного токена от Telegram
    if settings.TELEGRAM_WEBHOOK_SECRET:
        received_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if received_token != settings.TELEGRAM_WEBHOOK_SECRET:
            logger.warning(f"Unauthorized webhook attempt from IP: {request.client.host}")
            raise HTTPException(status_code=403, detail="Forbidden")
            
    update = await request.json()
    await handle_telegram_update(update, db)
    return {"status": "ok"}

@router.get("/telegram/set-webhook")
async def register_webhook(admin: dict = Depends(get_current_admin)):
    """Зарегистрировать вебхук (вызвать один раз после деплоя)."""
    from app.config import settings
    webhook_url = "https://calamo.lol/api/support/telegram/webhook"
    return await set_webhook(webhook_url, secret_token=settings.TELEGRAM_WEBHOOK_SECRET)
