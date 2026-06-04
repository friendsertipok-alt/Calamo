import re
import html

def sanitize_html(text: str) -> str:
    """
    Тщательно очищает текст от HTML-тегов и скриптов для защиты от XSS.
    """
    if not text:
        return ""
    
    # 1. Удаляем скрипты и их содержимое целиком
    text = re.sub(r'<script\b[^>]*>([\s\S]*?)<\/script>', '', text, flags=re.IGNORECASE)
    
    # 2. Удаляем все остальные теги
    clean_text = re.sub(r'<[^>]*>', '', text)
    
    # 3. Экранируем спецсимволы (на случай если что-то осталось)
    clean_text = html.escape(clean_text)
    
    return clean_text.strip()
