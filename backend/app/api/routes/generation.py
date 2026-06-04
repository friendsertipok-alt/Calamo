"""
Calamo — API Routes: Generation
"""
import asyncio
from fastapi import APIRouter, HTTPException, BackgroundTasks
from app.schemas.order import GenerationProgress, OrderConfirm, OrderStatus
from app.pipeline.generator import pipeline, orders_store
from app.auth import get_current_user
from app.models import User, Transaction
from app.database import get_db, AsyncSessionLocal
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from fastapi import Depends
from app.arq_pool import get_redis_pool

router = APIRouter()


@router.post("/{order_id}/start")
async def start_generation(order_id: str, background_tasks: BackgroundTasks, user: User = Depends(get_current_user)):
    """Запустить генерацию черновика (план + источники) в фоне."""
    stored = orders_store.get(order_id)
    if not stored:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    if stored.get("user_id") != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="Доступ запрещен: это не ваш заказ")

    if stored["status"] not in ("pending", "failed"):
        raise HTTPException(
            status_code=400,
            detail=f"Генерация уже в процессе или завершена (статус: {stored['status']})",
        )

    # --- ЗАЩИТА ОТ СПАМА (DDoS Wallet) ---
    # Лимит на бесплатные черновики: 3 в час для обычных пользователей.
    # Админы (из белого списка) — безлимитно.
    from app.auth import load_admin_emails
    is_admin = user.is_admin or user.email.lower() in load_admin_emails()
    
    if not is_admin:
        from datetime import datetime, timedelta
        one_hour_ago = datetime.now() - timedelta(hours=1)
        
        user_drafts_count = 0
        for oid, order in orders_store.items():
            if order.get("user_id") == user.id:
                try:
                    created_at = datetime.fromisoformat(order["created_at"])
                    if created_at >= one_hour_ago:
                        user_drafts_count += 1
                except (ValueError, KeyError):
                    continue
        
        MAX_DRAFTS_PER_HOUR = 3
        if user_drafts_count >= MAX_DRAFTS_PER_HOUR:
             raise HTTPException(
                status_code=429, 
                detail=f"Лимит генерации черновиков исчерпан ({MAX_DRAFTS_PER_HOUR} в час). Подождите немного или обратитесь в поддержку."
            )

    # Запускаем генерацию черновика в фоне через ARQ
    redis = await get_redis_pool()
    await redis.enqueue_job('run_draft_generation', order_id)

    return {
        "message": "Генерация черновика запущена",
        "order_id": order_id,
    }


@router.post("/{order_id}/confirm")
async def confirm_generation(order_id: str, confirmation: OrderConfirm, background_tasks: BackgroundTasks, user: User = Depends(get_current_user)):
    """Подтвердить черновик и запустить полную генерацию."""
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Confirming order {order_id}")
    
    stored = orders_store.get(order_id)
    if not stored:
        logger.warning(f"Order {order_id} not found")
        raise HTTPException(status_code=404, detail="Заказ не найден")

    if stored.get("user_id") != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="Доступ запрещен: это не ваш заказ")

    if stored["status"] in ("generating_text", "completed"):
        logger.info(f"Order {order_id} already in progress")
        return {"message": "Генерация уже идет", "order_id": order_id}

    if stored["status"] != "draft_ready":
        error_msg = f"Заказ не в состоянии готовности черновика (статус: {stored['status']})"
        logger.warning(error_msg)
        raise HTTPException(
            status_code=400,
            detail=error_msg,
        )

    logger.info(f"Starting background generation for {order_id}")
    
    # --- БЛОК ОПЛАТЫ ---
    from app.auth import load_admin_emails
    is_admin = user.is_admin or user.email.lower() in load_admin_emails()
    
    if not stored.get("is_paid") and not is_admin:
        # Цены на работы
        ORDER_PRICES = {
            "курсовая": 3500.0,
            "реферат": 1000.0,
            "диплом": 8000.0,
            "контрольная": 500.0,
            "отчёт": 1500.0
        }

        
        async with AsyncSessionLocal() as db_session:
            # 1. Получаем пользователя заказа
            user_id = stored.get("user_id")
            if not user_id:
                raise HTTPException(status_code=400, detail="Заказ должен быть привязан к аккаунту для оплаты")
                
            user_res = await db_session.execute(select(User).where(User.id == user_id))
            user = user_res.scalar_one_or_none()
            
            if not user:
                raise HTTPException(status_code=404, detail="Пользователь не найден")
                
            # 2. Определяем цену на основе типа работы и количества страниц
            work_type = str(stored["data"].get("work_type", "курсовая")).lower().strip()
            base_price = ORDER_PRICES.get(work_type, 3500.0)
            
            # Учитываем количество страниц (добавочная стоимость, если страниц больше базы)
            pages = int(stored["data"].get("pages_count", 35))
            base_pages = {
                "курсовая": 30,
                "реферат": 10,
                "диплом": 60,
                "контрольная": 5,
                "отчёт": 20
            }.get(work_type, 30)
            
            extra_pages = max(0, pages - base_pages)
            price_per_extra_page = 100.0 # 100 руб за каждую доп страницу
            
            price = base_price + (extra_pages * price_per_extra_page)
            
            logger.info(f"Deduction attempt: User {user.id} ({user.email}), Balance: {user.balance}, Price: {price}, Work: {work_type}, Pages: {pages}")
            
            from sqlalchemy import update
            
            # 3. Проверяем баланс и списываем атомарно (защита от race condition)
            stmt = (
                update(User)
                .where(User.id == user.id)
                .where(User.balance >= price)
                .values(balance=User.balance - price)
                .execution_options(synchronize_session="fetch")
            )
            
            result = await db_session.execute(stmt)
            
            if result.rowcount == 0:
                # Списание не удалось: либо нет денег, либо гонка
                await db_session.refresh(user)
                logger.warning(f"Insufficient funds for user {user.id}: needs {price}, has {user.balance}")
                raise HTTPException(
                    status_code=402, 
                    detail=f"Недостаточно средств. Стоимость: {price} руб. Ваш баланс: {user.balance} руб."
                )
            
            # 5. Логируем транзакцию списания
            new_transaction = Transaction(
                user_id=user.id,
                amount=-float(price),
                type="withdrawal",
                status="completed",
                description=f"Списание за генерацию: {work_type} ({order_id})",
                external_id=order_id
            )
            db_session.add(new_transaction)
            
            await db_session.commit()
            await db_session.refresh(user)
            
            logger.info(f"✅ SUCCESSFULLY DEDUCTED {price} RUB from user {user.id}. New balance: {user.balance}")
            stored["is_paid"] = True
    else:
        logger.info(f"Order {order_id} is already paid, skipping deduction.")


    # Запускаем полную генерацию в фоне через ARQ
    redis = await get_redis_pool()
    await redis.enqueue_job('run_full_generation', order_id, confirmation.model_dump())

    return {
        "message": "Оплата прошла успешно, полная генерация запущена",
        "order_id": order_id,
        "is_paid": True
    }


@router.post("/{order_id}/confirm_balance")
async def confirm_balance(
    order_id: str, 
    user: User = Depends(get_current_user),
    db: AsyncSessionLocal = Depends(get_db)
):
    """Списать средства с баланса пользователя за заказ."""
    stored = orders_store.get(order_id)
    if not stored:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    
    if stored.get("is_paid"):
        # Если уже оплачен, просто перезапускаем генерацию на случай сбоя
        from app.arq_pool import get_redis_pool
        redis = await get_redis_pool()
        confirmation_data = {
            "outline": stored.get("draft_outline"),
            "sources": stored.get("draft_sources")
        }
        await redis.enqueue_job('run_full_generation', order_id, confirmation_data)
        return {"status": "success", "message": "Генерация перезапущена (заказ уже оплачен)", "new_balance": user.balance}
    
    # Цены
    ORDER_PRICES = {
        "курсовая": 3500.0,
        "реферат": 1000.0,
        "диплом": 8000.0,
        "контрольная": 500.0,
        "отчёт": 1500.0
    }
    work_type = str(stored["data"].get("work_type", "курсовая")).lower().strip()
    price = ORDER_PRICES.get(work_type, 3500.0)

    if float(user.balance) < float(price):
        raise HTTPException(status_code=402, detail=f"Недостаточно средств. Стоимость: {price} руб. Ваш баланс: {user.balance} руб.")

    # Атомарное списание (защита от двойного списания при одновременных запросах)
    stmt = (
        update(User)
        .where(User.id == user.id)
        .where(User.balance >= price)
        .values(balance=User.balance - price)
    )
    result = await db.execute(stmt)
    
    if result.rowcount == 0:
        raise HTTPException(status_code=402, detail="Недостаточно средств (параллельное списание)")
    
    # Транзакция
    new_transaction = Transaction(
        user_id=user.id,
        amount=-float(price),
        type="withdrawal",
        status="completed",
        description=f"Оплата заказа {order_id}",
        external_id=order_id
    )
    db.add(new_transaction)
    await db.commit()
    
    # Помечаем заказ как оплаченный
    stored["is_paid"] = True
    stored["user_id"] = user.id
    
    # Получаем обновлённый баланс
    await db.refresh(user)

    # Запускаем полную генерацию в фоне
    from app.arq_pool import get_redis_pool
    redis = await get_redis_pool()
    confirmation_data = {
        "outline": stored.get("draft_outline"),
        "sources": stored.get("draft_sources")
    }
    await redis.enqueue_job('run_full_generation', order_id, confirmation_data)
    
    return {"status": "success", "message": "Оплата прошла успешно, генерация запущена", "new_balance": user.balance}





@router.get("/{order_id}/progress", response_model=GenerationProgress)
async def get_progress(order_id: str, user: User = Depends(get_current_user)):
    """Получить прогресс генерации."""
    stored = orders_store.get(order_id)
    if not stored:
        raise HTTPException(status_code=404, detail="Заказ не найден")
        
    if stored.get("user_id") != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="Доступ запрещен: это не ваш заказ")
        
    progress = pipeline.get_progress(order_id)
    if not progress:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    return progress
