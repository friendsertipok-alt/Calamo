FROM python:3.11-slim

# Установка системных зависимостей, необходимых для python-magic и базовой работы
RUN apt-get update && apt-get install -y \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Создаем пользователя без прав root
RUN useradd -m -r calamo_user

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip wheel && \
    pip install --no-cache-dir -r requirements.txt

# Копируем весь код бэкенда
COPY . .

# Создаем директорию для загрузок и даем права пользователю
RUN mkdir -p /app/uploads/support && chown -R calamo_user:calamo_user /app

# Переключаемся на безопасного пользователя
USER calamo_user

# Открываем порт
EXPOSE 8000

# Запускаем FastAPI через Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
