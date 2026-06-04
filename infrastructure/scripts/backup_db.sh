#!/bin/bash

# Настройки
BACKUP_DIR="/opt/calamo/backups"
DB_CONTAINER="calamo_db"
DB_USER="calamo_admin"
DB_NAME="calamo"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/backup_$TIMESTAMP.sql.gz.enc"
PASS_FILE="/opt/calamo/backend/secrets/backup_password.txt"

# Создаем папку для бэкапов, если ее нет
mkdir -p "$BACKUP_DIR"

echo "🚀 Запуск зашифрованного бэкапа базы данных $DB_NAME..."

# Делаем дамп, сжимаем и шифруем через openssl
docker exec $DB_CONTAINER pg_dump -U $DB_USER $DB_NAME | \
    gzip | \
    openssl enc -aes-256-cbc -salt -pbkdf2 -pass "file:$PASS_FILE" -out "$BACKUP_FILE"

if [ $? -eq 0 ]; then
    echo "✅ Зашифрованный бэкап успешно создан: $BACKUP_FILE"
    
    # Отправка в Telegram (если настроено в .env бэкенда)
    if [ -f "/opt/calamo/backend/.env" ]; then
        # Извлекаем токены из .env
        TG_TOKEN=$(grep "TELEGRAM_BOT_TOKEN=" /opt/calamo/backend/.env | cut -d '=' -f2)
        TG_CHAT=$(grep "TELEGRAM_CHAT_ID=" /opt/calamo/backend/.env | cut -d '=' -f2)
        
        if [ ! -z "$TG_TOKEN" ] && [ ! -z "$TG_CHAT" ]; then
            echo "📤 Отправка бэкапа в Telegram..."
            curl -F document=@"$BACKUP_FILE" \
                 -F caption="📦 Encrypted Backup: $(basename $BACKUP_FILE) ($(date))" \
                 "https://api.telegram.org/bot$TG_TOKEN/sendDocument?chat_id=$TG_CHAT" > /dev/null
        fi
    fi

    # Удаляем бэкапы старше 7 дней
    find "$BACKUP_DIR" -type f -name "*.sql.gz.enc" -mtime +7 -delete
    echo "🧹 Старые бэкапы удалены."
else
    echo "❌ Ошибка при создании зашифрованного бэкапа!"
    exit 1
fi

