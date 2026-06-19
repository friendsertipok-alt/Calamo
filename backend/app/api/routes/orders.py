from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.schemas.order import OrderCreate, OrderResponse
from app.pipeline.generator import pipeline, orders_store, refresh_orders_store
from app.auth import get_current_user, get_current_user_optional, oauth2_scheme
from app.models import User
from app.database import get_db
from app.services.telegram_service import notify_new_order
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("/", response_model=OrderResponse)
async def create_order(
    order: OrderCreate, 
    background_tasks: BackgroundTasks,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db)
):
    """Создать новый заказ на генерацию."""
    user_id = user.id if user else None
    
    from app.utils.security import sanitize_html
    order.topic = sanitize_html(order.topic)
    order.subject = sanitize_html(order.subject)

    try:
        order_id = await pipeline.create_order(order, user_id=user_id)
        stored = orders_store[order_id]
        
        data = stored["data"]
        # Handle both dict and Pydantic object
        if isinstance(data, dict):
            work_type = data.get("work_type")
            topic = data.get("topic")
            subject = data.get("subject")
        else:
            work_type = data.work_type
            topic = data.topic
            subject = data.subject

        background_tasks.add_task(
            notify_new_order,
            order_id,
            user.email if user else "guest@calamo.lol",
            str(work_type),
            str(topic)
        )

        return OrderResponse(
            id=order_id,
            work_type=work_type,
            topic=topic,
            subject=subject,
            status=stored["status"],
            progress=stored["progress"],
            current_step=stored["current_step"]
        )
    except Exception as e:
        logger.error(f"Error creating order: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(order_id: str, user: Optional[User] = Depends(get_current_user_optional)):
    """Получить информацию о заказе."""
    stored = await pipeline.check_and_claim_guest_order(order_id, user)
    
    data = stored["data"]
    # Handle both dict and Pydantic object
    if isinstance(data, dict):
        work_type = data.get("work_type")
        topic = data.get("topic")
        subject = data.get("subject")
    else:
        work_type = data.work_type
        topic = data.topic
        subject = data.subject

    return OrderResponse(
        id=order_id,
        work_type=work_type,
        topic=topic,
        subject=subject,
        status=stored["status"],
        progress=stored["progress"],
        current_step=stored["current_step"],
        download_url=stored.get("download_url"),
        error_message=stored.get("error_message"),
        draft_outline=stored.get("draft_outline"),
        draft_sources=stored.get("draft_sources"),
        created_at=stored.get("created_at")
    )

@router.get("/user/me", response_model=list[OrderResponse])
async def get_my_orders(user: User = Depends(get_current_user)):
    """Получить список всех заказов текущего пользователя."""
    user_orders = pipeline.list_user_orders(user.id, user_email=user.email)
    
    responses = []
    for stored in user_orders:
        data = stored["data"]
        # Handle dict/model consistency
        if isinstance(data, dict):
            work_type = data.get("work_type")
            topic = data.get("topic")
            subject = data.get("subject")
        else:
            work_type = data.work_type
            topic = data.topic
            subject = data.subject

        responses.append(OrderResponse(
            id=stored["id"],
            work_type=work_type,
            topic=topic,
            subject=subject,
            status=stored["status"],
            progress=stored["progress"],
            current_step=stored["current_step"],
            download_url=stored.get("download_url"),
            error_message=stored.get("error_message"),
            created_at=stored.get("created_at")
        ))
    return responses

@router.delete("/{order_id}")
async def delete_order(order_id: str, user: User = Depends(get_current_user)):
    """Удалить заказ пользователя."""
    success = pipeline.delete_order(order_id, user.id)
    if not success:
        raise HTTPException(status_code=404, detail="Заказ не найден или недостаточно прав")
    return {"status": "success", "message": "Заказ удален"}
