from fastapi import APIRouter, Depends, HTTPException, Request, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update, or_
from typing import Optional
import logging
import json
from datetime import datetime

from app.database import get_db
from app.models import User, Transaction
from app.auth import get_current_user
from app.services.payment_service import payment_service

router = APIRouter()
logger = logging.getLogger("PaymentRouter")

@router.post("/create")
async def create_payment(
    amount: float, 
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Создание сессии пополнения баланса через RollyPay."""
    if amount < 100:
        raise HTTPException(status_code=400, detail="Минимальная сумма пополнения — 100 рублей")
    if amount > 100000:
        raise HTTPException(status_code=400, detail="Максимальная сумма пополнения — 100 000 рублей")
    amount = round(amount, 2)

    # 1. Создаем транзакцию в статусе pending
    order_id = f"TOPUP_{current_user.id}_{int(datetime.utcnow().timestamp())}"
    
    new_transaction = Transaction(
        user_id=current_user.id,
        amount=amount,
        type="top-up",
        status="pending",
        description=f"Пополнение баланса на {amount} руб.",
        external_id=None # Заполнится после ответа API
    )
    db.add(new_transaction)
    await db.commit()
    await db.refresh(new_transaction)

    # 2. Запрашиваем ссылку у RollyPay
    result = await payment_service.create_checkout_session(
        order_id=order_id,
        amount=amount,
        description=f"Пополнение баланса (ID: {current_user.id})"
    )

    if "error" in result:
        new_transaction.status = "failed"
        await db.commit()
        raise HTTPException(status_code=500, detail=result["error"])

    # 3. Сохраняем ID платежа
    new_transaction.external_id = result["external_id"]
    await db.commit()

    return {"payment_url": result["payment_url"]}

@router.post("/callback")
async def payment_callback(
    request: Request,
    x_signature: Optional[str] = Header(None, alias="X-Signature"),
    db: AsyncSession = Depends(get_db)
):
    """Обработка вебхука от RollyPay."""
    raw_body = await request.body()
    
    # 1. Проверка подписи
    if not payment_service.verify_webhook(raw_body, x_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # 2. Парсинг данных RollyPay
    try:
        data = json.loads(raw_body)
        status = data.get("status")
        event_type = data.get("event_type")
        payment_id = data.get("payment_id")
        order_id = data.get("order_id")

        if event_type == "payment.paid" and status == "paid":
            # 3. Находим транзакцию и атомарно меняем статус (защита от двойного вызова вебхука)
            stmt = (
                update(Transaction)
                .where(Transaction.status == "pending")
                .where(or_(Transaction.external_id == payment_id, Transaction.external_id == order_id))
                .values(status="completed")
                .execution_options(synchronize_session="fetch")
            )
            result = await db.execute(stmt)
            
            if result.rowcount > 0:
                # Транзакция успешно переведена в completed, мы первые!
                # 4. Начисляем деньги пользователю (атомарно)
                
                # Сначала получаем user_id из транзакции
                tx_res = await db.execute(select(Transaction).where(or_(Transaction.external_id == payment_id, Transaction.external_id == order_id)))
                tx = tx_res.scalars().first()
                
                if tx:
                    user_stmt = (
                        update(User)
                        .where(User.id == tx.user_id)
                        .values(balance=User.balance + tx.amount)
                    )
                    await db.execute(user_stmt)
                    await db.commit()
                    logger.info(f"SUCCESS: User {tx.user_id} balance +{tx.amount} via RollyPay")
            else:
                # Либо транзакция не найдена, либо уже обработана (status != pending)
                logger.warning(f"WARNING: Transaction not found or already processed for {payment_id} / {order_id}")
                await db.rollback()

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"CRITICAL: Webhook processing failed: {e}")
        await db.rollback()
        # НЕ возвращаем str(e) наружу, чтобы не сливать кишки системы
        return {"status": "error", "message": "Internal error processing webhook"}
