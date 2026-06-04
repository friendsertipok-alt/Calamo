import httpx
import logging
from app.config import settings
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import TelegramMessageMapping

logger = logging.getLogger(__name__)

async def send_telegram_message(text: str, chat_id: Optional[str] = None, reply_to_message_id: Optional[int] = None):
    """Отправить простое текстовое сообщение в Telegram."""
    target_id = chat_id or settings.TELEGRAM_CHAT_ID
    if not settings.TELEGRAM_BOT_TOKEN or not target_id:
        return False

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": target_id,
        "text": text,
        "parse_mode": "HTML"
    }
    
    # Если мы пишем в нашу группу и там настроены топики
    if str(target_id) == str(settings.TELEGRAM_CHAT_ID) and settings.TELEGRAM_TOPIC_ID:
        payload["message_thread_id"] = settings.TELEGRAM_TOPIC_ID

    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=payload, timeout=10.0)
            return res.json() if res.status_code == 200 else False
    except Exception as e:
        logger.error(f"Failed to send telegram message: {e}")
        return False

async def notify_new_ticket(ticket_id: int, email: str, message: str, file_urls: Optional[List[str]] = None):
    """Сформировать и отправить уведомление о новом тикете (с сайта) в группу."""
    text = (
        f"<b>🔔 Новое обращение в поддержку!</b>\n\n"
        f"<b>ID:</b> #{ticket_id}\n"
        f"<b>От:</b> {email}\n\n"
        f"<b>Сообщение:</b>\n<i>{message}</i>\n"
    )
    
    if file_urls:
        text += f"\n<b>📎 Прикреплено файлов:</b> {len(file_urls)}\n"
        for i, url in enumerate(file_urls):
            full_url = f"https://calamo.lol{url}"
            text += f"<a href='{full_url}'>Файл {i+1}</a>\n"

    text += f"\n<a href='https://calamo.lol/admin'>Перейти в админку</a>"
    
    return await send_telegram_message(text)

async def notify_new_order(order_id: str, email: str, work_type: str, topic: str):
    """Отправить уведомление о создании нового заказа."""
    text = (
        f"<b>🚀 Создан новый заказ!</b>\n\n"
        f"<b>ID:</b> #{order_id}\n"
        f"<b>Пользователь:</b> {email}\n"
        f"<b>Тип работы:</b> {work_type}\n\n"
        f"<b>Тема:</b>\n<i>{topic}</i>\n\n"
        f"<a href='https://calamo.lol/admin'>Перейти в админку</a>"
    )
    return await send_telegram_message(text)

async def notify_order_error(order_id: str, email: str, work_type: str, topic: str, error_message: str):
    """Отправить уведомление об ошибке генерации заказа."""
    text = (
        f"<b>❌ Ошибка генерации заказа!</b>\n\n"
        f"<b>ID:</b> #{order_id}\n"
        f"<b>Пользователь:</b> {email}\n"
        f"<b>Тип работы:</b> {work_type}\n\n"
        f"<b>Тема:</b>\n<i>{topic}</i>\n\n"
        f"<b>Текст ошибки:</b>\n<code>{error_message}</code>\n\n"
        f"<a href='https://calamo.lol/admin'>Перейти в админку для спасения</a>"
    )
    return await send_telegram_message(text)

async def handle_telegram_update(update: dict, db: AsyncSession):
    """Основной обработчик входящих обновлений от Telegram (Webhook)."""
    if "message" not in update:
        return

    msg = update["message"]
    chat_id = msg["chat"]["id"]
    text = msg.get("text") or msg.get("caption") or ""
    user = msg.get("from", {})
    
    # Форматируем имя пользователя
    username = user.get("username")
    display_name = f"@{username}" if username else user.get("first_name", "User")
    user_id = user.get("id")

    # 1. Если это сообщение из нашей админ-группы
    if str(chat_id) == str(settings.TELEGRAM_CHAT_ID):
        # Проверяем, является ли это ответом на сообщение бота
        if "reply_to_message" in msg:
            reply_to = msg["reply_to_message"]
            admin_msg_id = reply_to["message_id"]
            
            # Ищем в базе, кому принадлежит это сообщение
            stmt = select(TelegramMessageMapping).where(TelegramMessageMapping.admin_message_id == admin_msg_id)
            result = await db.execute(stmt)
            mapping = result.scalar_one_or_none()
            
            if mapping:
                # Пересылаем ответ админа пользователю в личку
                # Используем sendMessage для текста
                await send_telegram_message(text, chat_id=mapping.user_chat_id)
        return

    # 2. Если это сообщение от пользователя боту в личку (Private Chat)
    if msg["chat"]["type"] == "private":
        if text == "/start":
            await send_telegram_message(
                f"<b>👋 Привет, {display_name}! Это поддержка Calamo.</b>\n\n"
                "Напиши свой вопрос здесь, и наши менеджеры ответят тебе в ближайшее время. "
                "Ты можешь присылать текст, фото или документы.",
                chat_id=chat_id
            )
            return

        # Сначала отправляем инфо-сообщение админам
        info_text = (
            f"<b>📩 Сообщение от клиента</b>\n"
            f"<b>Пользователь:</b> {display_name}\n"
            f"<b>ID:</b> <code>{chat_id}</code>"
        )
        await send_telegram_message(info_text)

        # Затем КОПИРУЕМ оригинальное сообщение в группу (это сохранит фото, файлы и т.д.)
        copy_url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/copyMessage"
        copy_payload = {
            "chat_id": settings.TELEGRAM_CHAT_ID,
            "from_chat_id": chat_id,
            "message_id": msg["message_id"]
        }
        if settings.TELEGRAM_TOPIC_ID:
            copy_payload["message_thread_id"] = settings.TELEGRAM_TOPIC_ID
        
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(copy_url, json=copy_payload, timeout=10.0)
                copy_res = res.json()
                
                if copy_res.get("ok"):
                    admin_side_msg_id = copy_res["result"]["message_id"]
                    
                    # Сохраняем маппинг в БД (чтобы можно было ответить на это сообщение)
                    new_mapping = TelegramMessageMapping(
                        admin_message_id=admin_side_msg_id,
                        user_chat_id=chat_id,
                        user_name=display_name
                    )
                    db.add(new_mapping)
                    await db.commit()
                    
                    # Подтверждаем пользователю
                    await send_telegram_message("✅ Сообщение отправлено менеджерам. Ожидайте ответа.", chat_id=chat_id)
        except Exception as e:
            logger.error(f"Error copying message to group: {e}")
            await send_telegram_message("❌ Ошибка при отправке сообщения. Попробуйте позже.", chat_id=chat_id)

async def set_webhook(url: str, secret_token: str = ""):
    """Зарегистрировать вебхук в Telegram."""
    if not settings.TELEGRAM_BOT_TOKEN:
        return False
    webhook_url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/setWebhook"
    payload = {"url": url}
    if secret_token:
        payload["secret_token"] = secret_token
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(webhook_url, json=payload, timeout=10.0)
            return res.json()
    except Exception as e:
        return {"error": str(e)}

async def get_latest_updates():
    """Получить последние обновления от бота (для отладки)."""
    if not settings.TELEGRAM_BOT_TOKEN:
        return {"error": "Token not configured"}
        
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(url, timeout=10.0)
            return res.json()
    except Exception as e:
        return {"error": str(e)}
