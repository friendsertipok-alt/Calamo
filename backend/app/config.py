import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    APP_NAME: str = "Calamo"
    API_V1_STR: str = "/api"
    GEMINI_API_KEY: str = ""
    PROXYAPI_KEY: str = ""
    DEBUG: bool = False
    
    # CORS
    CORS_ORIGINS: list[str] = [
        "https://calamo.lol", 
        "http://calamo.lol",
        "http://localhost:8000", 
        "http://127.0.0.1:8000",
    ]
    
    # Postgres
    POSTGRES_USER: str = ""
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = ""
    
    # Пути
    BASE_DIR: Path = Path(__file__).resolve().parent.parent
    OUTPUT_DIR: Path = BASE_DIR / "output"
    UPLOADS_DIR: Path = BASE_DIR / "uploads"
    
    # Настройки генерации по умолчанию
    DEFAULT_WORK_PAGES: dict = {
        "курсовая": (30, 40),
        "реферат": (10, 15),
        "диплом": (60, 80),
        "контрольная": (5, 10),
        "отчёт": (20, 30)
    }
    
    DEFAULT_SOURCES_COUNT: dict = {
        "курсовая": 15,
        "реферат": 5,
        "диплом": 40,
        "контрольная": 3,
        "отчёт": 5
    }
    
    DEFAULT_TABLES_COUNT: dict = {
        "курсовая": 2,
        "реферат": 0,
        "диплом": 5,
        "контрольная": 0,
        "отчёт": 3
    }
    
    DEFAULT_FIGURES_COUNT: dict = {
        "курсовая": 2,
        "реферат": 1,
        "диплом": 8,
        "контрольная": 0,
        "отчёт": 5
    }
    # БД
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./calamo.db")
    
    # Auth
    SECRET_KEY: str = "" # Значение перенесено в .env
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7 # Неделя
    
    # OAuth (Заполни в .env)
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "https://calamo.lol/api/auth/callback/google"
    APPLE_CLIENT_ID: str = ""
    
    # Telegram
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    TELEGRAM_TOPIC_ID: Optional[int] = 2
    TELEGRAM_WEBHOOK_SECRET: Optional[str] = None



    # RollyPay
    ROLLYPAY_API_KEY: str = ""
    ROLLYPAY_SECRET_KEY: str = ""
    ROLLYPAY_BASE_URL: str = "https://rollypay.io/api/v1"

    def _get_secret(self, secret_name: str, default: str = "") -> str:
        """Вспомогательная функция для чтения секретов Docker."""
        secret_path = f"/run/secrets/{secret_name}"
        if os.path.exists(secret_path):
            with open(secret_path, "r") as f:
                content = f.read().strip()
                if content and not content.startswith("dummy"):
                    return content
        return default

    def __init__(self, **values):
        super().__init__(**values)
        # Приоритет секретам Docker над .env
        self.GEMINI_API_KEY = self._get_secret("gemini_api_key", self.GEMINI_API_KEY)
        self.PROXYAPI_KEY = self._get_secret("proxyapi_key", self.PROXYAPI_KEY)
        self.POSTGRES_PASSWORD = self._get_secret("postgres_password", self.POSTGRES_PASSWORD)
        
        # Валидация критических настроек
        if not self.SECRET_KEY or len(self.SECRET_KEY) < 16:
            import secrets
            if not self.DEBUG:
                # В продакшне мы НЕ ДОЛЖНЫ запускаться без ключа. 
                # Но чтобы не ломать всё сразу, если админ забыл — логируем ошибку.
                import logging
                logging.getLogger("uvicorn").error("CRITICAL: SECRET_KEY is missing or too short! Auth is INSECURE.")
            else:
                # В дебаге можем сгенерировать временный
                self.SECRET_KEY = secrets.token_urlsafe(32)


    model_config = {
        "case_sensitive": True,
        "env_file": os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
        "extra": "ignore"
    }

settings = Settings()
