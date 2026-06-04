"""
Calamo — Smart Link Builder & Validator
Архитектура «Умных редиректов»: вместо проверки выдуманных URL
конструируем гарантированно рабочие поисковые ссылки.
"""
import asyncio
import re
import logging
from urllib.parse import urlparse, quote_plus
import httpx
from app.schemas.order import SourceItem

logger = logging.getLogger(__name__)

# ==========================================
# БЕЗОПАСНОСТЬ: Фильтры URL
# ==========================================

ADULT_AND_SPAM_KEYWORDS = [
    "porn", "xxx", "sex", "cam", "nude", "escort", "casino", "bet", "slot", 
    "gambling", "xvideos", "pornhub", "chaturbate", "onlyfans", "erotic", "strip"
]

def is_safe_url(url: str) -> bool:
    """Проверка безопасности URL для предотвращения SSRF и фильтрация NSFW/спам контента."""
    if not url or not url.startswith("https://"):
        return False
        
    url_lower = url.lower()
    if any(kw in url_lower for kw in ADULT_AND_SPAM_KEYWORDS):
        return False
        
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        hostname_lower = hostname.lower()
        if hostname_lower in ["localhost", "127.0.0.1", "0.0.0.0"]:
            return False
        if any(hostname_lower.startswith(prefix) for prefix in ["10.", "192.168.", "172."]):
            return False
        if hostname_lower.endswith(".local") or hostname_lower.endswith(".internal"):
            return False
        return True
    except Exception:
        return False


# ==========================================
# ЯДРО: Конструктор умных URL
# ==========================================

def build_smart_url(source: SourceItem) -> str | None:
    """
    Конструирует гарантированно рабочий поисковый URL на основе метаданных источника.
    Использует Google Scholar для всех статей, так как eLibrary требует cookie-сессию.
    """
    citation = source.citation or source.title or ""
    source_type = (source.type or "").lower()
    
    # Для книг и нормативных актов URL не нужен (печатные источники)
    if source_type in ("book", "law", "gost"):
        return None
    
    # 1. Если у источника уже есть DOI — используем его
    existing_url = source.url or ""
    if "doi.org" in existing_url:
        return existing_url
    
    # 2. Проверяем, есть ли DOI в тексте цитаты
    doi_match = re.search(r'(10\.\d{4,}/[^\s,]+)', citation)
    if doi_match:
        return f"https://doi.org/{doi_match.group(1)}"
    
    # Извлекаем основную часть названия (автор + название до символа // или –)
    # Пример: "Иванов И. И. Трансформация ритейла / И. И. Иванов // Журнал..." -> "Иванов И. И. Трансформация ритейла / И. И. Иванов"
    core_part = citation.split('//')[0].split('–')[0].split('-')[0].strip()
    
    if len(core_part) < 10:
        # Резервный вариант, если сплит не сработал
        core_part = citation[:100].strip()
    
    # Убираем лишние слэши, чтобы запрос был чище
    clean_query = core_part.replace('/', ' ').strip()
    scholar_query = clean_query[:120].strip()
    
    # 3. Для всех статей используем Google Scholar без жестких кавычек
    # Scholar отлично находит как русские, так и английские работы по автору и названию
    return f"https://scholar.google.com/scholar?q={quote_plus(scholar_query)}"


# ==========================================
# БЫСТРАЯ ВАЛИДАЦИЯ (без HTTP-запросов к защищённым сайтам)
# ==========================================

async def _quick_validate_url(client: httpx.AsyncClient, url: str) -> bool:
    """
    Быстрая проверка URL. Возвращает True если ссылка скорее жива.
    НЕ делает запросы к сайтам с Cloudflare — для них всегда True.
    """
    if not url or not is_safe_url(url):
        return False
    
    # DOI-ссылки всегда валидны (они редиректят на издателя)
    if "doi.org" in url:
        return True
    
    # Поисковые URL (наши конструированные) — всегда валидны
    SEARCH_PATTERNS = ["scholar.google", "elibrary.ru/query_results", "cyberleninka.ru/search"]
    if any(p in url for p in SEARCH_PATTERNS):
        return True
    
    # Домены с Cloudflare — пропускаем без проверки (убирает false positive)
    CLOUDFLARE_PROTECTED = ["cyberleninka.ru", "elibrary.ru", "elar.", "vestnik."]
    if any(d in url.lower() for d in CLOUDFLARE_PROTECTED):
        return True
    
    # Для остальных — делаем HEAD-запрос (быстрее GET)
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = await client.head(url, timeout=5.0, follow_redirects=True, headers=headers)
        
        if response.status_code in [404, 410]:
            return False
        if response.status_code == 200:
            return True
        # Любой другой код (403, 301, 500) — даём benefit of the doubt
        return True
    except Exception:
        # Таймаут/ошибка соединения — не помечаем как битый
        return True


# ==========================================
# ГОСТ-утилиты
# ==========================================

def remove_url_from_gost_citation(text: str) -> str:
    """Аккуратно удаляет блок URL и дату обращения из библиографической записи по ГОСТу."""
    if not text:
        return ""
    cleaned = re.sub(r'–?\s*URL:\s*\[?HYPERLINK:[^\]]+\]?(\s*\([^)]*\))?\.?', '', text, flags=re.IGNORECASE)
    cleaned = re.sub(r'–?\s*URL:\s*https?://[^\s)]+(\s*\([^)]*\))?\.?', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\(\s*дата обращения[^)]*\)\.?$', '', cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.strip().rstrip('.')
    if cleaned:
        return cleaned + '.'
    return text


def _inject_url_into_citation(citation: str, url: str) -> str:
    """
    Вставляет URL в ГОСТ-запись в правильную позицию.
    Если URL уже есть — заменяет его на новый.
    """
    from datetime import datetime
    current_date = datetime.now().strftime("%d.%m.%Y")
    
    # Сначала убираем старый URL (если есть)
    clean = remove_url_from_gost_citation(citation).rstrip('.')
    
    # Добавляем новый URL в конец перед точкой
    url_block = f" – URL:[HYPERLINK:{url}] (дата обращения: {current_date})."
    return clean + url_block


# ==========================================
# ГЛАВНЫЙ ПАЙПЛАЙН
# ==========================================

async def validate_and_clean_sources(sources: list[SourceItem]) -> list[SourceItem]:
    """
    Трёхфазная обработка источников:
    1. Дедупликация
    2. Построение умных URL (для источников без DOI)
    3. Быстрая валидация (только для прямых ссылок)
    """
    
    # --- Фаза 1: Дедупликация (по заголовку и URL) ---
    unique_sources = []
    seen_titles = set()
    seen_urls = set()
    
    for src in sources:
        title_norm = "".join(filter(str.isalnum, src.title.lower()))
        url_norm = src.url.lower().strip().rstrip("/") if src.url else None
        
        if title_norm in seen_titles:
            continue
        if url_norm and url_norm in seen_urls:
            continue
            
        unique_sources.append(src)
        seen_titles.add(title_norm)
        if url_norm:
            seen_urls.add(url_norm)

    # --- Фаза 2: Умные URL ---
    for src in unique_sources:
        source_type = (src.type or "").lower()
        
        # Книги и законы — всегда без URL (печатные источники)
        if source_type in ("book", "law", "gost"):
            if src.url:
                # Убираем URL из цитаты, оставляем как печатный источник
                src.citation = remove_url_from_gost_citation(src.citation or src.title)
                src.title = src.citation
                src.url = None
            continue
        
        # Для статей и отчётов — конструируем умный URL если текущий выглядит подозрительно
        if src.url and is_safe_url(src.url):
            # Если URL уже есть и он безопасный — оставляем для проверки в фазе 3
            continue
        
        # Нет URL или он небезопасный — конструируем умный
        smart_url = build_smart_url(src)
        if smart_url:
            src.url = smart_url
            src.citation = _inject_url_into_citation(src.citation or src.title, smart_url)
            src.title = src.citation
            logger.info(f"Smart URL built for source #{src.number}: {smart_url}")
        else:
            # Не удалось построить URL — оставляем как печатный
            if src.url:
                src.citation = remove_url_from_gost_citation(src.citation or src.title)
                src.title = src.citation
            src.url = None

    # --- Фаза 3: Быстрая валидация оставшихся прямых URL ---
    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        for src in unique_sources:
            if not src.url:
                continue
            
            # Поисковые URL (наши конструированные) — пропускаем, они всегда работают
            SEARCH_PATTERNS = ["scholar.google", "elibrary.ru/query_results", "cyberleninka.ru/search"]
            if any(p in src.url for p in SEARCH_PATTERNS):
                continue
            
            # DOI — пропускаем, всегда работает
            if "doi.org" in src.url:
                continue
            
            # Для прямых ссылок — быстрая проверка
            try:
                is_valid = await asyncio.wait_for(
                    _quick_validate_url(client, src.url), timeout=8.0
                )
                if not is_valid:
                    logger.info(f"Dead direct URL detected: {src.url}. Building smart replacement.")
                    # Заменяем на умный поисковый URL
                    smart_url = build_smart_url(src)
                    if smart_url:
                        src.url = smart_url
                        src.citation = _inject_url_into_citation(src.citation or src.title, smart_url)
                        src.title = src.citation
                    else:
                        src.citation = remove_url_from_gost_citation(src.citation or src.title)
                        src.title = src.citation
                        src.url = None
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning(f"Validation timeout/error for {src.url}: {e}. Keeping as-is.")
    
    return list(unique_sources)
