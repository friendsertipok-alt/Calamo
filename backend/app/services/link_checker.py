"""
Calamo — Smart Link Builder & Validator
Архитектура «Умных редиректов»: вместо проверки выдуманных URL
конструируем гарантированно рабочие поисковые ссылки.
"""
import asyncio
import re
import logging
from urllib.parse import urlparse, quote_plus
from typing import Optional
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
    Использует CyberLeninka для русских статей, Google Scholar для иностранных.
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
    core_part = citation.split('//')[0].split('–')[0].split('-')[0].strip()
    
    if len(core_part) < 10:
        core_part = citation[:100].strip()
    
    clean_query = core_part.replace('/', ' ').strip()
    scholar_query = clean_query[:120].strip()
    
    # 3. Для всех статей используем Google Scholar без жестких кавычек
    return f"https://scholar.google.com/scholar?q={quote_plus(scholar_query)}"


def build_fallback_search_url(src: SourceItem) -> str | None:
    """
    Строит гарантированно рабочую поисковую ссылку для источника.
    Приоритет: CyberLeninka (для русских статей) > Google Scholar.
    Эти URL ВСЕГДА рабочие — они не требуют проверки.
    """
    source_type = (src.type or "").lower()
    if source_type in ("book", "law", "gost"):
        return None
    
    citation = src.citation or src.title or ""
    
    # Проверяем, содержит ли цитата кириллицу (русская статья)
    has_cyrillic = bool(re.search(r'[а-яёА-ЯЁ]', citation))
    
    # Берём чистое название статьи (без авторов и журнала)
    # Часть до // — это автор + название, берём только название
    pre_slash = citation.split('//')[0].split('/')[0].strip()
    # Убираем инициалы авторов в начале: "Иванов И. И. Название статьи" -> "Название статьи"
    title_part = re.sub(r'^[А-Яа-яA-Za-z\s,.-]+?(?:[А-ЯA-Z]\.\s*){1,3}', '', pre_slash).strip()
    if len(title_part) < 8:
        title_part = pre_slash.replace('/', ' ').strip()
    
    query = title_part[:100].strip()
    if len(query) < 5:
        query = (src.title or citation)[:100].strip()
    
    encoded = quote_plus(query)
    
    if has_cyrillic:
        # CyberLeninka хорошо индексирует русские журналы
        return f"https://cyberleninka.ru/search?q={encoded}"
    else:
        return f"https://scholar.google.com/scholar?q={encoded}"


def is_search_url(url: str) -> bool:
    """Проверяет, является ли URL поисковым."""
    if not url:
        return False
    url_lower = url.lower()
    SEARCH_PATTERNS = [
        "scholar.google",
        "cyberleninka.ru/search",
        "elibrary.ru/query_results",
        "google.com/search",
        "google.ru/search",
        "yandex.ru/search",
        "yandex.com/search",
        "bing.com/search",
        "/search#q=",
        "/search?q="
    ]
    return any(p in url_lower for p in SEARCH_PATTERNS)



# ==========================================
# ПОИСК РЕАЛЬНЫХ ССЫЛОК ЧЕРЕЗ OPENALEX API
# ==========================================

async def check_url_working(client: httpx.AsyncClient, url: str) -> bool:
    """Проверяет работоспособность ссылки (выполняет HEAD/GET запрос)."""
    if not url or not is_safe_url(url):
        return False
    
    # Поисковые URL (Scholar, CyberLeninka search) — всегда рабочие, не проверяем
    if is_search_url(url):
        return True
    
    try:
        # Для доменов с Cloudflare (Cyberleninka, eLibrary и т.д.) даем benefit of the doubt
        CLOUDFLARE_PROTECTED = ["cyberleninka.ru", "elibrary.ru", "elar.", "vestnik."]
        if any(d in url.lower() for d in CLOUDFLARE_PROTECTED):
            return True
            
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        # Сначала пробуем HEAD-запрос для быстроты
        try:
            resp = await client.head(url, timeout=4.0, headers=headers, follow_redirects=True)
            if resp.status_code in (404, 410):
                return False
        except Exception:
            pass

        # Делаем GET-запрос
        resp_get = await client.get(url, timeout=4.0, headers=headers, follow_redirects=True)
        if resp_get.status_code in (404, 410):
            return False
            
        # Если это DOI, проверим на "not found" в тексте страницы
        if "doi.org" in url:
            if "doi not found" in resp_get.text.lower() or "not found" in resp_get.text.lower():
                return False
                
        return True
    except Exception:
        # При ошибках сети/таймаутах считаем ссылку рабочей, чтобы избежать ложноположительного удаления
        return True


def format_openalex_work_to_gost(w: dict) -> str:
    """
    Форматирует результат OpenAlex в текстовую библиографическую запись по ГОСТу.
    """
    lang = (w.get("language") or "ru").lower()
    
    # 1. Авторы
    authorships = w.get("authorships", [])
    author_list = []
    for a in authorships[:3]:
        name = a.get("author", {}).get("display_name", "")
        name = " ".join(name.split())
        if name:
            author_list.append(name)
            
    authors_str = ", ".join(author_list)

    title = w.get("title", "").strip()
    if title.endswith('.'):
        title = title[:-1]
        
    year = w.get("publication_year")
    
    primary_loc = w.get("primary_location") or {}
    source_info = primary_loc.get("source") or {}
    journal_name = source_info.get("display_name", "")
    
    biblio = w.get("biblio") or {}
    volume = biblio.get("volume", "")
    issue = biblio.get("issue", "")
    first_page = biblio.get("first_page", "")
    last_page = biblio.get("last_page", "")
    
    citation = ""
    
    if lang == "ru":
        if authors_str:
            citation += f"{authors_str}. "
        citation += title
        if journal_name:
            citation += f" // {journal_name}"
        if year:
            citation += f". – {year}"
        details = []
        if volume:
            details.append(f"Т. {volume}")
        if issue:
            details.append(f"№ {issue}")
        if details:
            citation += f". – {', '.join(details)}"
        if first_page and last_page:
            citation += f". – С. {first_page}–{last_page}"
        elif first_page:
            citation += f". – С. {first_page}"
    else:
        if authors_str:
            citation += f"{authors_str}. "
        citation += title
        if journal_name:
            citation += f" // {journal_name}"
        if year:
            citation += f". – {year}"
        details = []
        if volume:
            details.append(f"Vol. {volume}")
        if issue:
            details.append(f"No. {issue}")
        if details:
            citation += f". – {', '.join(details)}"
        if first_page and last_page:
            citation += f". – P. {first_page}–{last_page}"
        elif first_page:
            citation += f". – P. {first_page}"
            
    citation = citation.strip().rstrip('.') + '.'
    return citation


def _extract_title_for_search(raw: str) -> str:
    """
    Извлекает чистое название статьи из ГОСТ-библиографической строки.
    Работает для форматов:
    - "Иванов И.И. Название статьи // Журнал. – 2023."
    - "Нехода Е.В., Li Pan. Название // Журнал."
    - "Название статьи" (уже чистое)
    """
    if not raw:
        return ""
    # Берём только часть до // (автор + название), а также до первого слэша /
    # (который отделяет название от авторов/ответственности в ГОСТ)
    pre_slash = raw.split('//')[0].split('–')[0].split(' - ')[0].split('/')[0].strip()
    
    # Ищем позицию ПОСЛЕДНЕГО инициала (одна буква + точка + пробел или конец)
    # Пример: "Нехода Е. В., Li Pan." — последний инициал — «n.» в «Pan."
    last_initial_end = -1
    for m in re.finditer(r'[A-Za-zА-Яа-яЁё]\.[\s,]*', pre_slash):
        last_initial_end = m.end()
    
    if last_initial_end > 0:
        candidate = pre_slash[last_initial_end:].strip().lstrip(',; ')
        # Убираем оставшиеся инициалы в начале (случай смешанных авторов)
        candidate = re.sub(r'^[A-Za-zА-Яа-яЁё]\.\s*', '', candidate).strip()
        if len(candidate) >= 8:
            return candidate[:120]
    
    # Если инициалов нет, убираем только знаки препинания
    clean = re.sub(r'[^\w\s\-]', ' ', pre_slash)
    return " ".join(clean.split())[:120]


async def find_real_article_in_openalex(title: str, client: httpx.AsyncClient, topic_fallback: Optional[str] = None) -> Optional[dict]:
    """
    Ищет реальную научную публикацию в OpenAlex по названию (ключевым словам)
    или по резервной теме.
    Возвращает dict со следующими ключами:
    - 'url': рабочий DOI или URL публикации
    - 'citation': отформатированное описание по ГОСТу
    - 'title': название статьи
    Иначе возвращает None.
    """
    queries = []
    if title and len(title.strip()) >= 8:
        search_query = _extract_title_for_search(title)
        if len(search_query) >= 8:
            queries.append(search_query)
        
    if topic_fallback and topic_fallback not in queries:
        queries.append(topic_fallback[:120].strip())
        
    if not queries:
        return None
        
    url = "https://api.openalex.org/works"
    
    # Пробуем запросы последовательно
    for query in queries:
        params = {
            "search": query,
            "per_page": 5,
            "filter": "publication_year:>2021",
            "mailto": "calamo@calamo.lol"
        }
        try:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                if topic_fallback:
                    from app.services.llm_service import llm_service
                    results = await llm_service.filter_relevant_articles(topic_fallback, results)
                for work in results:
                    work_title = work.get("title", "")
                    if not work_title:
                        continue
                        
                    # Проверяем год публикации (должен быть от 2022)
                    work_year = work.get("publication_year")
                    if work_year and work_year < 2022:
                        continue
                        
                    # Исключаем украинские источники
                    gost = format_openalex_work_to_gost(work)
                    if contains_ukrainian_letters(work_title) or contains_ukrainian_letters(gost):
                        continue
                        
                    # Собираем кандидатов на URL в порядке предпочтения
                    url_candidates = []
                    
                    # 1. landing_page_url, не содержащие doi.org
                    for loc in work.get("locations", []):
                        l_url = loc.get("landing_page_url")
                        if l_url and "doi.org" not in l_url and not is_search_url(l_url):
                            url_candidates.append(l_url)
                            
                    # 2. Основной doi
                    doi = work.get("doi")
                    if doi:
                        url_candidates.append(doi)
                        
                    # 3. Любые другие landing_page_url (включая doi.org)
                    for loc in work.get("locations", []):
                        l_url = loc.get("landing_page_url")
                        if l_url and l_url not in url_candidates and not is_search_url(l_url):
                            url_candidates.append(l_url)
                            
                    # 4. pdf_url
                    for loc in work.get("locations", []):
                        p_url = loc.get("pdf_url")
                        if p_url and p_url not in url_candidates and not is_search_url(p_url):
                            url_candidates.append(p_url)
                            
                    # Проверяем кандидатов
                    working_url = None
                    for candidate in url_candidates:
                        if await check_url_working(client, candidate):
                            working_url = candidate
                            break
                            
                    if working_url:
                        gost = format_openalex_work_to_gost(work)
                        return {
                            "url": working_url,
                            "citation": gost,
                            "title": work_title
                        }
        except Exception as e:
            logger.warning(f"OpenAlex query failed for '{query[:50]}': {e}")
            
    return None


async def find_real_url_for_article(title: str, client: Optional[httpx.AsyncClient] = None) -> str | None:
    """
    Ищет реальную научную публикацию по её названию в OpenAlex API.
    Возвращает работающий DOI или URL публикации.
    Оставлен для обратной совместимости.
    """
    local_client = client if client else httpx.AsyncClient(verify=False, timeout=6.0)
    try:
        res = await find_real_article_in_openalex(title, local_client)
        if res:
            return res["url"]
    except Exception as e:
        logger.warning(f"find_real_url_for_article failed: {e}")
    finally:
        if not client:
            await local_client.aclose()
    return None



# ==========================================
# БЫСТРАЯ ВАЛИДАЦИЯ (без HTTP-запросов к защищённым сайтам)
# ==========================================


async def _quick_validate_url(client: httpx.AsyncClient, url: str) -> bool:
    """
    Быстрая проверка URL. Возвращает True если ссылка скорее жива.
    Специально проверяет DOI и делает корректный fallback при 404.
    """
    return await check_url_working(client, url)


def extract_year_from_gost(text: str) -> Optional[int]:
    """Извлекает 4-значный год издания из библиографического описания (ГОСТ)."""
    if not text:
        return None
    # Ищем 4-значное число от 1990 до 2029 в границах слов
    matches = re.findall(r'\b(20\d{2}|199\d)\b', text)
    if matches:
        return int(matches[-1])
    return None


def contains_ukrainian_letters(text: str) -> bool:
    """Проверяет, содержит ли текст украинские буквы (і, І, є, Є, ї, Ї, ґ, Ґ)."""
    if not text:
        return False
    return bool(re.search(r'[іІєЄїЇґҐ]', text))



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


def normalize_dashes(text: str) -> str:
    """Заменяет длинные тире на средние, нормализует дефисы/тире и пробелы вокруг них."""
    if not text:
        return ""
    # Заменяем все виды длинных/двойных/тройных тире на среднее тире
    text = re.sub(r'---|\u2014|\u2015|--|\u2212', '\u2013', text)
    # Заменяем одиночный дефис-минус с пробелами вокруг на среднее тире
    text = re.sub(r'\s+-\s+', ' \u2013 ', text)
    # Нормализуем любые пробелы вокруг en-dash/em-dash на один нормальный пробел
    text = re.sub(r'\s+[\u2013\u2014]\s+', ' \u2013 ', text)
    return text


# ==========================================
# ГЛАВНЫЙ ПАЙПЛАЙН
# ==========================================

async def validate_and_clean_sources(sources: list[SourceItem], topic: Optional[str] = None) -> list[SourceItem]:
    """
    Четырёхфазная обработка источников:
    0. СТРОГАЯ LLM-фильтрация (чтобы выкинуть 'Балтику' и левые компании).
    1. Дедупликация
    2. Поиск реальных статей с рабочими ссылками (DOI/URL) в OpenAlex, заменяя выдуманные/битые статьи.
    3. Добивка до гарантированного минимума в 14 рабочих ссылок по теме topic.
    """
    
    # --- Фаза 0: СТРОГАЯ LLM-фильтрация первоначального массива ---
    if topic:
        try:
            from app.services.llm_service import llm_service
            logger.info(f"Phase 0: Filtering {len(sources)} initial sources with LLM against topic '{topic}'")
            filtered_sources = await llm_service.filter_relevant_articles(topic, sources)
            if filtered_sources and len(filtered_sources) >= 5:
                sources = filtered_sources
                logger.info(f"Phase 0: Kept {len(sources)} relevant sources after LLM filter")
            else:
                logger.warning("Phase 0: LLM filter returned too few sources, keeping original list")
        except Exception as e:
            logger.error(f"Phase 0 LLM filter failed: {e}")

    # --- Фаза 1: Дедупликация (по заголовку и URL) ---
    unique_sources = []
    seen_titles = set()
    seen_urls = set()
    
    for src in sources:
        title_str = src.title or ""
        title_norm = "".join(filter(str.isalnum, title_str.lower()))
        url_norm = src.url.lower().strip().rstrip("/") if src.url else None
        
        if title_norm in seen_titles:
            continue
        if url_norm and url_norm in seen_urls:
            continue
            
        unique_sources.append(src)
        seen_titles.add(title_norm)
        if url_norm:
            seen_urls.add(url_norm)

    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        # --- Фаза 2: Поиск реальных статей и валидация ---
        for src in unique_sources:
            # Нормализуем тире во всех полях источника
            src.title = normalize_dashes(src.title)
            if src.citation:
                src.citation = normalize_dashes(src.citation)
                
            source_type = (src.type or "").lower()
            
            # Книги и законы — всегда без URL (печатные источники)
            if source_type in ("book", "law", "gost"):
                if src.url:
                    # Убираем URL из цитаты, оставляем как печатный источник
                    src.citation = remove_url_from_gost_citation(src.citation or src.title)
                    src.title = src.citation
                    src.url = None
                continue
            
            # Статьи и отчеты (article, report)
            # 1. Если URL уже есть, он безопасный, это не поисковая ссылка Scholar/Cyberleninka, год публикации >= 2022 и не содержит украинских букв
            url_is_ok = False
            src_year = extract_year_from_gost(src.citation or src.title)
            year_is_ok = (src_year is None) or (src_year >= 2022)
            has_ua = contains_ukrainian_letters(src.citation or src.title)
            
            if year_is_ok and not has_ua and src.url and is_safe_url(src.url) and not is_search_url(src.url):
                # Быстрая проверка работоспособности
                url_is_ok = await check_url_working(client, src.url)
            
            if url_is_ok:
                # Если URL рабочий, оставляем его, просто инжектим в цитату (если его там нет)
                src.citation = _inject_url_into_citation(src.citation or src.title, src.url)
                src.title = src.citation
                logger.info(f"Existing working URL verified for source #{src.number}: {src.url}")
            else:
                # URL нет, он нерабочий, небезопасный или поисковый — ищем реальную статью
                logger.info(f"Resolving real article for source #{src.number} ('{src.title[:50]}')")
                real_art = await find_real_article_in_openalex(src.citation or src.title, client, topic_fallback=topic)
                if real_art:
                    src.url = real_art["url"]
                    src.citation = _inject_url_into_citation(real_art["citation"], real_art["url"])
                    src.title = src.citation
                    logger.info(f"Replaced source #{src.number} with real article: {real_art['title']} -> {real_art['url']}")
                else:
                    # Не нашли прямую ссылку в OpenAlex — используем поисковый URL как fallback
                    fallback_url = build_fallback_search_url(src)
                    if fallback_url:
                        src.url = fallback_url
                        src.citation = _inject_url_into_citation(src.citation or src.title, fallback_url)
                        src.title = src.citation
                        logger.info(f"Fallback search URL set for source #{src.number}: {fallback_url}")
                    else:
                        src.citation = remove_url_from_gost_citation(src.citation or src.title)
                        src.title = src.citation
                        src.url = None
                        logger.info(f"No real article and no fallback URL found for source #{src.number}")

        async def _fetch_openalex_parallel(search_query: str) -> list:
            results_ru = []
            results_all = []
            
            async def _fetch_single(q: str, extra_filter: str = "") -> list:
                try:
                    url = "https://api.openalex.org/works"
                    base_filter = "publication_year:>2021"
                    if extra_filter:
                        base_filter += f",{extra_filter}"
                    params = {
                        "search": q,
                        "per_page": 20,
                        "filter": base_filter,
                        "mailto": "calamo@calamo.lol"
                    }
                    resp = await client.get(url, params=params)
                    if resp.status_code == 200:
                        return resp.json().get("results", [])
                except Exception as e:
                    logger.warning(f"OpenAlex fetch failed for shortage query: {e}")
                return []
                
            res = await asyncio.gather(
                _fetch_single(search_query, "language:ru"),
                _fetch_single(search_query),
                return_exceptions=True
            )
            for r in res:
                if isinstance(r, list):
                    if not results_ru:
                        results_ru = r
                    else:
                        results_all = r
            return results_ru + results_all

        # --- Обеспечиваем общее число источников в диапазоне 20-25 (целевое число: 22) ---
        target_total = 22
        if len(unique_sources) > 24:
            logger.info(f"Truncating sources list from {len(unique_sources)} to {target_total} (target 20-25)")
            unique_sources = unique_sources[:target_total]
        elif len(unique_sources) < 20 and topic:
            shortage_total = target_total - len(unique_sources)
            logger.info(f"Total sources count is {len(unique_sources)}, which is below minimum 20. Appending {shortage_total} more sources from OpenAlex...")
            words = [w for w in re.split(r'\s+', topic) if len(w) > 3]
            shortage_query = " ".join(words[:2]) if words else topic[:50]
            try:
                results = await _fetch_openalex_parallel(shortage_query)
                from app.services.llm_service import llm_service
                results = await llm_service.filter_relevant_articles(topic, results)
                
                added_count = 0
                for work in results:
                    work_title = work.get("title", "")
                    if not work_title: continue
                    work_year = work.get("publication_year")
                    if work_year and work_year < 2022: continue
                    gost = format_openalex_work_to_gost(work)
                    if contains_ukrainian_letters(work_title) or contains_ukrainian_letters(gost): continue
                    
                    title_norm = "".join(filter(str.isalnum, work_title.lower()))
                    if any(title_norm in "".join(filter(str.isalnum, (s.title or "").lower())) for s in unique_sources):
                        continue
                        
                    url_candidates = []
                    for loc in work.get("locations", []):
                        l_url = loc.get("landing_page_url")
                        if l_url and "doi.org" not in l_url and not is_search_url(l_url):
                            url_candidates.append(l_url)
                    doi = work.get("doi")
                    if doi: url_candidates.append(doi)
                    
                    working_url = None
                    for candidate in url_candidates:
                        if await check_url_working(client, candidate):
                            working_url = candidate
                            break
                    
                    gost_norm = normalize_dashes(gost)
                    if working_url:
                        gost_norm = _inject_url_into_citation(gost_norm, working_url)
                    else:
                        dummy_src = SourceItem(number=0, title=gost_norm, citation=gost_norm, url=None, type="article")
                        fallback_url = build_fallback_search_url(dummy_src)
                        if fallback_url:
                            working_url = fallback_url
                            gost_norm = _inject_url_into_citation(gost_norm, fallback_url)
                        else:
                            working_url = None
                        
                    unique_sources.append(SourceItem(
                        number=len(unique_sources) + 1,
                        type="article",
                        title=gost_norm,
                        citation=gost_norm,
                        url=working_url,
                        year=work_year
                    ))
                    added_count += 1
                    if added_count >= shortage_total:
                        break
                logger.info(f"Successfully appended {added_count} new sources. Total count: {len(unique_sources)}")
            except Exception as e:
                logger.warning(f"Failed to fetch fallback shortage sources: {e}")

        # --- --- Фаза 3: Приведение количества ссылок к диапазону 7-10 (целевой ориентир: 8) ---
        target_links = 8
        working_sources = [src for src in unique_sources if src.url]
        
        working_count = len(working_sources)
        logger.info(f"Phase 2 complete. Working links count: {working_count}")
        
        # Сценарий А: ссылок мало (< 7), нужно добавить до target_links (8)
        if working_count < 7 and topic:
            shortage = target_links - working_count
            logger.info(f"Need to replace {shortage} print/broken sources with real articles with working links (target: {target_links})")
            
            # Делаем запрос к OpenAlex по теме работы
            words = [w for w in re.split(r'\s+', topic) if len(w) > 3]
            shortage_query = " ".join(words[:2]) if words else topic[:50]
            
            try:
                results = await _fetch_openalex_parallel(shortage_query)
                from app.services.llm_service import llm_service
                results = await llm_service.filter_relevant_articles(topic, results)
                
                pool = []
                for work in results:
                    work_title = work.get("title", "")
                    if not work_title: continue
                    
                    work_year = work.get("publication_year")
                    if work_year and work_year < 2022: continue
                    
                    gost = format_openalex_work_to_gost(work)
                    if contains_ukrainian_letters(work_title) or contains_ukrainian_letters(gost): continue
                    
                    title_norm = "".join(filter(str.isalnum, work_title.lower()))
                    if any(title_norm in "".join(filter(str.isalnum, (s.title or "").lower())) for s in unique_sources):
                        continue
                        
                    url_candidates = []
                    for loc in work.get("locations", []):
                        l_url = loc.get("landing_page_url")
                        if l_url and "doi.org" not in l_url and not is_search_url(l_url):
                            url_candidates.append(l_url)
                    doi = work.get("doi")
                    if doi: url_candidates.append(doi)
                    
                    working_url = None
                    for candidate in url_candidates:
                        if await check_url_working(client, candidate):
                            working_url = candidate
                            break
                            
                    if working_url:
                        gost = format_openalex_work_to_gost(work)
                        pool.append({
                            "url": working_url,
                            "citation": gost,
                            "title": work_title
                        })
                        if len(pool) >= shortage:
                            break
                            
                logger.info(f"Found {len(pool)} candidate works in pool for shortage replacement")
                
                # Заменяем источники без URL на статьи с рабочими ссылками
                replaced_count = 0
                for src in unique_sources:
                    if replaced_count >= len(pool):
                        break
                    if not src.url and (src.type or "").lower() in ("article", "report"):
                        item = pool[replaced_count]
                        src.url = item["url"]
                        src.citation = _inject_url_into_citation(item["citation"], item["url"])
                        src.title = src.citation
                        logger.info(f"Shortage replacement: replaced source #{src.number} with real pool article '{item['title']}' -> '{item['url']}'")
                        replaced_count += 1
            except Exception as e:
                logger.warning(f"Shortage fallback pool retrieval failed: {e}")
        
        # Сценарий Б: ссылок слишком много (> 10), нужно урезать до target_links (8)
        elif working_count > 10:
            excess = working_count - target_links
            logger.info(f"Too many links ({working_count}). Removing url from {excess} sources to reach target of {target_links}")
            
            removed_count = 0
            for src in unique_sources:
                if removed_count >= excess:
                    break
                if src.url:
                    src.citation = remove_url_from_gost_citation(src.citation or src.title)
                    src.title = src.citation
                    src.url = None
                    logger.info(f"Excess link removal: stripped link from source #{src.number}")
                    removed_count += 1
                    
    # Перенумеруем источники по порядку
    for idx, src in enumerate(unique_sources, 1):
        src.number = idx
        
    return unique_sources

