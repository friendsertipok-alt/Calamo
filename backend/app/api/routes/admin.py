"""
Calamo — Admin API Routes
Защищены через get_current_admin: доступ только для email-адресов из admin_emails.json.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query, BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from app.database import get_db
from app.models import User, Order, SupportTicket, LLMUsage, Transaction
from app.auth import get_current_admin
from app.pipeline.generator import orders_store
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


# ==========================================
# Pydantic Response Models
# ==========================================

class FunnelStats(BaseModel):
    """Воронка генерации."""
    total_orders: int = 0             # Всего создано заказов
    drafts_created: int = 0           # Дошли до черновика (plan+sources ready)
    drafts_confirmed: int = 0         # Подтвердили черновик
    completed: int = 0                # Успешно завершили и скачали
    failed: int = 0                   # Упали с ошибкой
    in_progress: int = 0              # Сейчас генерируются


class LLMStats(BaseModel):
    """Статистика нагрузки на LLM."""
    avg_generation_time_minutes: float = 0.0
    total_generations_today: int = 0
    total_generations_all_time: int = 0
    api_balance: Optional[float] = None     # Баланс в рублях
    api_used: Optional[float] = None        # Потрачено на ключе (всего)
    avg_cost_per_generation: float = 0.0    # Средняя себестоимость одной работы
    total_expenses_rub: float = 0.0         # Всего потрачено по нашей БД
    tokens_used: Optional[int] = None
    estimated_cost_usd: Optional[float] = None


class ModelHealth(BaseModel):
    """Здоровье моделей."""
    gemini_ok: bool = True
    openai_ok: bool = True
    last_gemini_error: Optional[str] = None
    last_openai_error: Optional[str] = None
    openai_balance: float = 0.0
    gemini_expenses: float = 0.0
    openai_expenses: float = 0.0

class RecentError(BaseModel):
    """Последние ошибки."""
    order_id: str
    error: str
    time: str

class DashboardResponse(BaseModel):
    """Полный ответ для дашборда."""
    total_users: int = 0
    new_users_today: int = 0
    new_users_week: int = 0
    total_logins_today: int = 0
    new_user_logins_today: int = 0
    funnel: FunnelStats = FunnelStats()
    llm: LLMStats = LLMStats()
    llm_health_status: ModelHealth = ModelHealth()
    recent_orders: list[dict] = []
    daily_expenses: list[dict] = []
    last_errors: list[RecentError] = []
    open_tickets: int = 0
    llm_strategy: str = "auto"
    chart_engine: str = "matplotlib"


class OrderAdminView(BaseModel):
    """Заказ глазами админа."""
    id: str
    user_id: Optional[int] = None
    topic: str = ""
    work_type: str = ""
    status: str = ""
    progress: int = 0
    current_step: str = ""
    download_url: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None


# ==========================================
# Endpoints
# ==========================================

@router.get("/queue")
async def get_generation_queue(admin: User = Depends(get_current_admin)):
    """Очередь текущих генераций."""
    active_orders = []
    for oid, order in orders_store.items():
        status = order.get("status", "")
        if status not in ("completed", "failed", "draft_ready", "pending", "pending_payment", "stopped", "cancelled"):
            data = order.get("data", {})
            active_orders.append({
                "id": oid,
                "user_id": order.get("user_id"),
                "topic": data.get("topic", "—") if isinstance(data, dict) else getattr(data, "topic", "—"),
                "work_type": data.get("work_type", "—") if isinstance(data, dict) else getattr(data, "work_type", "—"),
                "status": status,
                "progress": order.get("progress", 0),
                "current_step": order.get("current_step", ""),
                "created_at": order.get("created_at")
            })
            
    active_orders.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return active_orders

@router.get("/orders/{order_id}/logs")
async def get_order_logs(order_id: str, admin: User = Depends(get_current_admin)):
    """Получить логи конкретного заказа."""
    order = orders_store.get(order_id)
    if not order:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Заказ не найден")
    
    return {"id": order_id, "logs": order.get("logs", [])}

@router.get("/orders/all")
async def get_all_orders(admin: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    """Все заказы с инфо о пользователе и ошибках."""
    # Получаем маппинг user_id -> email
    users_result = await db.execute(select(User.id, User.email))
    user_map = {row[0]: row[1] for row in users_result}

    # Получаем стоимости заказов
    order_costs = {}
    try:
        cost_stmt = select(LLMUsage.order_id, func.sum(LLMUsage.estimated_cost_rub)).group_by(LLMUsage.order_id)
        cost_res = await db.execute(cost_stmt)
        for row in cost_res:
            if row[0]:
                order_costs[row[0]] = round(row[1] or 0.0, 2)
    except Exception:
        pass

    result = []
    for oid, order in orders_store.items():
        data = order.get("data", {})
        uid = order.get("user_id")
        result.append({
            "id": oid,
            "user_id": uid,
            "user_email": user_map.get(uid, "—"),
            "topic": data.get("topic", "—") if isinstance(data, dict) else getattr(data, "topic", "—"),
            "work_type": data.get("work_type", "—") if isinstance(data, dict) else getattr(data, "work_type", "—"),
            "status": order.get("status", ""),
            "progress": order.get("progress", 0),
            "current_step": order.get("current_step", ""),
            "error_message": order.get("error_message"),
            "download_url": order.get("download_url"),
            "cost_rub": order_costs.get(oid, 0.0),
            "created_at": order.get("created_at"),
        })
    result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return result

@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    from datetime import datetime, timedelta
    from app.services.llm_service import llm_health
    """Главная страница админ-панели: вся статистика в одном запросе."""

    # --- Читаем текущие настройки ---
    llm_strategy = "auto"
    chart_engine = "matplotlib"
    try:
        import json
        with open("app/llm_strategy.json", "r") as f:
            config = json.load(f)
            llm_strategy = config.get("llm_strategy", "auto")
            chart_engine = config.get("chart_engine", "matplotlib")
    except Exception:
        pass

    # --- 1. Пользователи ---
    total_users_result = await db.execute(select(func.count(User.id)))
    total_users = total_users_result.scalar() or 0

    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)

    new_today_result = await db.execute(
        select(func.count(User.id)).where(User.created_at >= today_start)
    )
    new_users_today = new_today_result.scalar() or 0

    new_week_result = await db.execute(
        select(func.count(User.id)).where(User.created_at >= week_start)
    )
    new_users_week = new_week_result.scalar() or 0

    # --- 1.5 Визиты (Логины) ---
    from app.models import UserVisit
    logins_today_result = await db.execute(
        select(func.count(UserVisit.id)).where(UserVisit.visited_at >= today_start)
    )
    total_logins_today = logins_today_result.scalar() or 0

    new_logins_today_result = await db.execute(
        select(func.count(UserVisit.id)).where(UserVisit.visited_at >= today_start, UserVisit.is_new_user == True)
    )
    new_user_logins_today = new_logins_today_result.scalar() or 0

    # --- 2. Воронка генерации ---
    funnel = FunnelStats()
    funnel.total_orders = len(orders_store)

    generation_times = []

    for oid, order in orders_store.items():
        status = order.get("status", "")
        if status in ("draft_ready",):
            funnel.drafts_created += 1
        elif status in ("generating_text", "generating_charts", "building_docx"):
            funnel.drafts_confirmed += 1
            funnel.in_progress += 1
        elif status == "completed":
            funnel.drafts_created += 1
            funnel.drafts_confirmed += 1
            funnel.completed += 1
            created = order.get("created_at")
            if created:
                try:
                    start = datetime.fromisoformat(created)
                    duration = (now - start).total_seconds() / 60.0
                    if duration < 120:
                        generation_times.append(duration)
                except Exception: pass
        elif status == "failed":
            funnel.failed += 1
        elif status in ("generating_outline", "generating_sources"):
            funnel.in_progress += 1

    # --- 3. LLM статистика ---
    llm = LLMStats()
    llm.total_generations_all_time = funnel.completed
    llm.avg_generation_time_minutes = round(sum(generation_times) / len(generation_times), 1) if generation_times else 0.0

    # Разделение расходов по моделям
    openai_balance = 0.0
    try:
        from app.config import settings
        import httpx
        if settings.PROXYAPI_KEY:
            async with httpx.AsyncClient(timeout=5.0) as client:
                headers = {"Authorization": f"Bearer {settings.PROXYAPI_KEY}"}
                res = await client.get("https://api.proxyapi.ru/proxyapi/balance", headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    openai_balance = data.get("balance", 0.0)
    except Exception as e:
        logger.error(f"Error fetching ProxyAPI balance: {e}")

    try:
        gemini_stmt = select(func.sum(LLMUsage.estimated_cost_rub)).where(LLMUsage.model.like("%gemini%"))
        gemini_res = await db.execute(gemini_stmt)
        gemini_cost = gemini_res.scalar() or 0.0
        
        openai_stmt = select(func.sum(LLMUsage.estimated_cost_rub)).where(LLMUsage.model.like("%gpt%"))
        openai_res = await db.execute(openai_stmt)
        openai_cost = openai_res.scalar() or 0.0
        
        llm.total_expenses_rub = round(gemini_cost + openai_cost, 2)
        llm_health["openai_balance"] = round(openai_balance, 2)
        llm_health["gemini_expenses"] = round(gemini_cost, 2)
        llm_health["openai_expenses"] = round(openai_cost, 2)
    except Exception as e:
        logger.error(f"Error calculating detailed LLM costs: {e}")

    # Расходы по дням
    daily_expenses = []
    try:
        cutoff = datetime.utcnow() - timedelta(days=14)
        # Определяем функцию форматирования даты в зависимости от типа БД
        is_sqlite = "sqlite" in str(db.bind.url)
        if is_sqlite:
            date_func = func.strftime('%d.%m', LLMUsage.created_at)
        else:
            date_func = func.to_char(LLMUsage.created_at, 'DD.MM')

        daily_stmt = select(
            date_func.label("day"),
            func.sum(LLMUsage.estimated_cost_rub).label("cost")
        ).where(LLMUsage.created_at >= cutoff).group_by("day")
        
        daily_res = await db.execute(daily_stmt)
        for row in daily_res:
            daily_expenses.append({"date": row[0], "cost": round(row[1] or 0.0, 2)})
    except Exception as e:
        logger.error(f"Error fetching daily expenses: {e}")
        # Если транзакция упала в Postgres, нужно сделать rollback, иначе следующие запросы упадут
        await db.rollback()
    except Exception: pass

    # Последние заказы и их стоимость
    order_costs = {}
    try:
        cost_stmt = select(LLMUsage.order_id, func.sum(LLMUsage.estimated_cost_rub)).group_by(LLMUsage.order_id)
        cost_res = await db.execute(cost_stmt)
        for row in cost_res:
            if row[0]:
                order_costs[row[0]] = round(row[1] or 0.0, 2)
    except Exception as e:
        logger.error(f"Error fetching order costs: {e}")

    recent = []
    sorted_orders = sorted(orders_store.items(), key=lambda x: x[1].get("created_at", ""), reverse=True)[:15]
    for oid, order in sorted_orders:
        data = order.get("data", {})
        recent.append({
            "id": oid,
            "topic": data.get("topic", "—") if isinstance(data, dict) else getattr(data, "topic", "—"),
            "work_type": data.get("work_type", "—") if isinstance(data, dict) else getattr(data, "work_type", "—"),
            "status": order.get("status", ""),
            "progress": order.get("progress", 0),
            "created_at": order.get("created_at"),
            "cost_rub": order_costs.get(oid, 0.0),
            "download_url": order.get("download_url")
        })

    # Ошибки
    errors = []
    try:
        errors = [RecentError(**err) for err in llm_health.get("recent_errors", [])]
    except Exception: pass

    # --- 5. Открытые тикеты ---
    open_tickets_result = await db.execute(
        select(func.count(SupportTicket.id)).where(SupportTicket.status.notin_(["Решена", "Закрыта"]))
    )
    open_tickets = open_tickets_result.scalar() or 0

    return DashboardResponse(
        total_users=total_users,
        new_users_today=new_users_today,
        new_users_week=new_users_week,
        total_logins_today=total_logins_today,
        new_user_logins_today=new_user_logins_today,
        funnel=funnel,
        llm=llm,
        llm_health_status=ModelHealth(**llm_health),
        recent_orders=recent,
        daily_expenses=daily_expenses,
        last_errors=errors,
        open_tickets=open_tickets,
        llm_strategy=llm_strategy,
        chart_engine=chart_engine
    )


@router.get("/orders")
async def list_all_orders(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, le=200),
):
    """Все заказы с фильтрацией по статусу."""
    # Получаем расходы по заказам
    order_costs = {}
    try:
        cost_stmt = select(LLMUsage.order_id, func.sum(LLMUsage.estimated_cost_rub)).group_by(LLMUsage.order_id)
        cost_res = await db.execute(cost_stmt)
        for row in cost_res:
            if row[0]:
                order_costs[row[0]] = round(row[1] or 0.0, 2)
    except Exception as e:
        logger.error(f"Error fetching order costs: {e}")

    result = []
    for oid, order in orders_store.items():
        if status_filter and order.get("status") != status_filter:
            continue
        data = order.get("data", {})
        result.append({
            "id": oid,
            "user_id": order.get("user_id"),
            "topic": data.get("topic", "—") if isinstance(data, dict) else getattr(data, "topic", "—"),
            "work_type": data.get("work_type", "—") if isinstance(data, dict) else getattr(data, "work_type", "—"),
            "status": order.get("status", ""),
            "progress": order.get("progress", 0),
            "current_step": order.get("current_step", ""),
            "download_url": order.get("download_url"),
            "error_message": order.get("error_message"),
            "created_at": order.get("created_at"),
            "cost_rub": order_costs.get(oid, 0.0)
        })

    result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return result[:limit]


@router.get("/users")
async def list_all_users(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, le=200),
):
    """Список всех зарегистрированных пользователей."""
    query = select(User).order_by(User.created_at.desc()).limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()
    
    user_generations = {}
    for oid, order in orders_store.items():
        uid = order.get("user_id")
        if uid is not None:
            user_generations[uid] = user_generations.get(uid, 0) + 1

    return [
        {
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name,
            "avatar_url": u.avatar_url,
            "balance": u.balance,
            "is_admin": u.is_admin,
            "provider": u.provider,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "generations_count": user_generations.get(u.id, 0),
        }
        for u in users
    ]


@router.get("/users/{user_id}")
async def get_user_details(
    user_id: int,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Детальная информация о пользователе: заказы, логины, баланс."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found")
        
    user_orders = []
    for oid, order in orders_store.items():
        if order.get("user_id") == user_id:
            data = order.get("data", {})
            user_orders.append({
                "id": oid,
                "topic": data.get("topic", "—") if isinstance(data, dict) else getattr(data, "topic", "—"),
                "work_type": data.get("work_type", "—") if isinstance(data, dict) else getattr(data, "work_type", "—"),
                "status": order.get("status", ""),
                "progress": order.get("progress", 0),
                "download_url": order.get("download_url"),
                "created_at": order.get("created_at"),
            })
            
    user_orders.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    
    # Визиты
    from app.models import UserVisit
    visits_res = await db.execute(select(UserVisit).where(UserVisit.user_id == user_id).order_by(UserVisit.visited_at.desc()).limit(10))
    visits = visits_res.scalars().all()
    
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "avatar_url": user.avatar_url,
        "balance": user.balance,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "orders": user_orders,
        "recent_visits": [v.visited_at.isoformat() for v in visits if v.visited_at]
    }


@router.get("/check")
async def check_admin(admin: User = Depends(get_current_admin)):
    """Быстрая проверка: является ли текущий пользователь админом."""
    return {"is_admin": True, "email": admin.email}

@router.get("/settings")
async def get_settings(admin: User = Depends(get_current_admin)):
    """Получить текущие глобальные настройки."""
    try:
        import json
        with open("app/llm_strategy.json", "r") as f:
            return json.load(f)
    except Exception:
        return {"llm_strategy": "auto"}

@router.post("/settings")
async def update_settings(update_data: dict, admin: User = Depends(get_current_admin)):
    """Обновить глобальные настройки (стратегию LLM или движок графиков)."""
    try:
        import json
        import os
        
        current_config = {}
        if os.path.exists("app/llm_strategy.json"):
            with open("app/llm_strategy.json", "r") as f:
                current_config = json.load(f)
        
        # Обновляем только присланные поля
        current_config.update(update_data)
        
        with open("app/llm_strategy.json", "w") as f:
            json.dump(current_config, f, indent=4)
            
        return {"status": "success", "config": current_config}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/orders/{order_id}/rescue")
async def rescue_order(order_id: str, admin: User = Depends(get_current_admin)):
    """Кнопка спасения: перезапустить упавший заказ с текущего этапа."""
    from app.api.routes.generation import get_redis_pool
    
    stored = orders_store.get(order_id)
    if not stored:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    
    # Сбрасываем ошибку
    stored["error_message"] = None
    stored["status"] = "pending" # Временный статус для перезапуска
    
    redis = await get_redis_pool()
    
    # Решаем, что запускать
    if stored.get("draft_outline") and stored.get("draft_sources"):
        # План уже есть, запускаем полную генерацию
        confirm_data = {
            "outline": stored["draft_outline"],
            "sources": stored["draft_sources"]
        }
        await redis.enqueue_job('run_full_generation', order_id, confirm_data)
        return {"message": "Заказ спасен: перезапущена полная генерация", "step": "full"}
    else:
        # Плана нет, начинаем с начала
        await redis.enqueue_job('run_draft_generation', order_id)
        return {"message": "Заказ спасен: перезапущена генерация черновика", "step": "draft"}

@router.post("/orders/{order_id}/pause")
async def pause_order(order_id: str, admin: User = Depends(get_current_admin)):
    """Поставить генерацию на паузу."""
    from app.pipeline.generator import _save_orders
    stored = orders_store.get(order_id)
    if not stored:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    
    if stored.get("status") in ("completed", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail="Невозможно поставить на паузу завершенный или отмененный заказ")
        
    stored["status"] = "paused"
    if "logs" not in stored: stored["logs"] = []
    stored["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] Администратор {admin.email} поставил на паузу.")
    
    _save_orders(orders_store)
    return {"message": "Заказ поставлен на паузу"}

@router.post("/orders/{order_id}/resume")
async def resume_order(order_id: str, admin: User = Depends(get_current_admin)):
    """Снять генерацию с паузы."""
    from app.pipeline.generator import _save_orders
    stored = orders_store.get(order_id)
    if not stored:
        raise HTTPException(status_code=404, detail="Заказ не найден")
        
    if stored.get("status") != "paused":
        raise HTTPException(status_code=400, detail="Заказ не на паузе")
        
    stored["status"] = "generating_text"
    if "logs" not in stored: stored["logs"] = []
    stored["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] Администратор {admin.email} возобновил генерацию.")
    
    _save_orders(orders_store)
    return {"message": "Генерация продолжена"}

@router.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: str, admin: User = Depends(get_current_admin)):
    """Полностью отменить генерацию."""
    from app.pipeline.generator import _save_orders
    stored = orders_store.get(order_id)
    if not stored:
        raise HTTPException(status_code=404, detail="Заказ не найден")
        
    stored["status"] = "cancelled"
    stored["error_message"] = "Отменено администратором"
    if "logs" not in stored: stored["logs"] = []
    stored["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] Администратор {admin.email} полностью отменил заказ.")
    
    _save_orders(orders_store)
    return {"message": "Заказ полностью отменен"}
class BalanceUpdate(BaseModel):
    amount: float
    description: Optional[str] = "Корректировка администратором"

@router.post("/users/{user_id}/balance")
async def update_user_balance(
    user_id: int,
    data: BalanceUpdate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Ручное изменение баланса пользователя администратором."""
    amount = data.amount
    description = data.description
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    try:
        user.balance += amount
        
        # Логируем операцию
        from app.models import Transaction
        new_transaction = Transaction(
            user_id=user.id,
            amount=amount,
            type="top-up" if amount > 0 else "withdrawal",
            status="completed",
            description=description,
            external_id=f"ADMIN_{admin.id}_{int(datetime.utcnow().timestamp())}"
        )
        db.add(new_transaction)
        await db.commit()
        
        return {
            "status": "success",
            "new_balance": user.balance,
            "message": f"Баланс пользователя {user.email} изменен на {amount} руб."
        }
    except Exception as e:
        logger.error(f"Error updating balance for user {user_id}: {str(e)}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
