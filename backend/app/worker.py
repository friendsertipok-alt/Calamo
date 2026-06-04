import asyncio
from arq.connections import RedisSettings
from app.pipeline.generator import run_draft_generation, run_full_generation
from app.config import settings
import os

async def startup(ctx):
    print("Worker starting up...")

async def shutdown(ctx):
    print("Worker shutting down...")

class WorkerSettings:
    redis_settings = RedisSettings(host=os.getenv("REDIS_HOST", "localhost"))
    functions = [run_draft_generation, run_full_generation]
    on_startup = startup
    on_shutdown = shutdown
    # Таймаут на одну задачу (например, 10 минут)
    job_timeout = 1800
    # Максимальное количество одновременных задач
    max_jobs = 2
