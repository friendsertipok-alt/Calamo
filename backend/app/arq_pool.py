from arq import create_pool
from arq.connections import RedisSettings
from app.config import settings
import os

async def get_redis_pool():
    redis_host = os.getenv("REDIS_HOST", "localhost")
    return await create_pool(RedisSettings(host=redis_host))
