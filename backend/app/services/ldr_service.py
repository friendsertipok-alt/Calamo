import sys
import os
import asyncio
import logging
from typing import List, Dict, Any

# Импортируем конфиг Calamo
try:
    from app.config import settings
except ImportError:
    sys.path.append(os.path.join(os.path.dirname(__file__), ".."))
    from app.config import settings

# Добавляем путь к LDR (в начало, чтобы перекрыть установленную версию)
LDR_PATH = "/Users/georgijistomin/Desktop/проекты кодинг/источники/src"
if os.path.exists(LDR_PATH):
    if LDR_PATH not in sys.path:
        sys.path.insert(0, LDR_PATH)

print(f"DEBUG: LDR_SERVICE.PY LOADED FROM {__file__}", file=sys.stderr, flush=True)

# Прямой импорт без try-except для отладки
from local_deep_research.api import quick_summary, create_settings_snapshot
import google.generativeai as genai

LDR_AVAILABLE = True
logger = logging.getLogger(__name__)

class LDRService:
    def __init__(self):
        self.api_key = settings.GEMINI_API_KEY
        self.enabled = LDR_AVAILABLE and bool(self.api_key)
        print(f"DEBUG: LDRService initialized. Enabled: {self.enabled}", file=sys.stderr, flush=True)
        if self.enabled:
            genai.configure(api_key=self.api_key)
            
            # Регистрируем кастомный русский ретривер, если он еще не зарегистрирован
            try:
                from local_deep_research.web_search_engines.retriever_registry import retriever_registry
                from langchain_community.utilities import DuckDuckGoSearchAPIWrapper
                
                class RussianAcademicRetriever:
                    def __init__(self):
                        self.ddg = DuckDuckGoSearchAPIWrapper(region="ru-ru", max_results=10)
                        
                    def get_relevant_documents(self, query):
                        print(f"DEBUG: CUSTOM RETRIEVER CALLED WITH QUERY: {query}", file=sys.stderr, flush=True)
                        from langchain_core.documents import Document
                        docs = []
                        
                        # Список доверенных доменов
                        trusted_domains = ["cyberleninka.ru", "consultant.ru", "garant.ru", "elibrary.ru", "gazprom.ru", "gov.ru"]
                        
                        # Делаем последовательные запросы по разным базам
                        search_targets = [
                            f"{query} site:cyberleninka.ru",
                            f"{query} site:consultant.ru",
                            f"{query} отчетность site:gazprom.ru"
                        ]
                        
                        for target_query in search_targets:
                            print(f"DEBUG: SEARCHING FOR: {target_query}", file=sys.stderr, flush=True)
                            try:
                                results = self.ddg.results(target_query, max_results=10)
                                print(f"DEBUG: FOUND {len(results)} RESULTS FOR {target_query}", file=sys.stderr, flush=True)
                                for r in results:
                                    link = r.get("link", "").lower()
                                    print(f"DEBUG: CHECKING LINK: {link}", file=sys.stderr, flush=True)
                                    # ЖЕСТКАЯ ПРОВЕРКА: только если ссылка содержит доверенный домен
                                    if any(domain in link for domain in trusted_domains):
                                        print(f"DEBUG: LINK ACCEPTED: {link}", file=sys.stderr, flush=True)
                                        docs.append(Document(
                                            page_content=r.get("snippet", ""),
                                            metadata={
                                                "title": r.get("title", ""),
                                                "link": r.get("link", ""),
                                                "source": r.get("link", "")
                                            }
                                        ))
                                if len(docs) >= 10: break
                            except Exception as e: 
                                print(f"DEBUG: SEARCH ERROR: {e}", file=sys.stderr, flush=True)
                                continue
                        
                        # Если совсем ничего не нашли по доверенным, ищем просто по .ru
                        if not docs:
                            try:
                                results = self.ddg.results(f"{query} lang:ru", max_results=10)
                                for r in results:
                                    link = r.get("link", "").lower()
                                    if ".ru" in link or ".su" in link:
                                        docs.append(Document(
                                            page_content=r.get("snippet", ""),
                                            metadata={
                                                "title": r.get("title", ""),
                                                "link": r.get("link", ""),
                                                "source": r.get("link", "")
                                            }
                                        ))
                            except: pass
                            
                        return docs

                if "russian_academic" not in retriever_registry.list_registered():
                    retriever_registry.register("russian_academic", RussianAcademicRetriever())
                    print("DEBUG: Registered custom russian_academic retriever", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"DEBUG: Failed to register retriever: {e}", file=sys.stderr, flush=True)

    async def get_real_sources(self, topic: str, count: int = 5) -> List[Dict[str, Any]]:
        print(f"DEBUG: Entering get_real_sources: {topic}", file=sys.stderr, flush=True)
        if not self.enabled:
            return []

        settings_override = {
            "llm.provider": "GOOGLE_NATIVE",
            "llm.google.api_key": self.api_key,
            "llm.model": "gemini-2.5-flash",
            "search.tool": "russian_academic", # Используем наш кастомный ретривер
            "search.max_results": count * 3,
            "research.iterations": 1,
            "research.instruction": "Ты — российский эксперт. Ищи информацию ТОЛЬКО через инструмент russian_academic. Пиши отчет СТРОГО на русском языке, опираясь на найденные ГОСТы, законы РФ и финансовую отчетность.",
            "programmatic_mode": True
        }

        try:
            snapshot = create_settings_snapshot(overrides=settings_override)
            loop = asyncio.get_event_loop()
            
            print(f"DEBUG: Running quick_summary...", file=sys.stderr, flush=True)
            result = await loop.run_in_executor(
                None, 
                lambda: quick_summary(
                    query=f"Научные источники и литература по теме: {topic}",
                    settings_snapshot=snapshot
                )
            )
            # 1. Сначала берем реальные веб-источники, если они есть
            sources_list = result.get("sources", [])
            summary = result.get("summary", "")
            
            formatted_sources = []
            
            if sources_list:
                for i, src in enumerate(sources_list[:count]):
                    title = src.get("title") or src.get("name") or f"Источник {i+1}"
                    url = src.get("link") or src.get("url") or ""
                    snippet = src.get("snippet") or ""
                    
                    # Для первого источника добавим общий summary исследования
                    content_summary = summary if i == 0 else snippet
                    
                    formatted_sources.append({
                        "title": title,
                        "url": url,
                        "author": src.get("author", ""),
                        "snippet": snippet,
                        "content_summary": content_summary
                    })
            
            # 2. Если реальных источников мало или нет, дополняем данными из findings
            if len(formatted_sources) < count:
                findings = result.get("findings", [])
                for i, finding in enumerate(findings):
                    if len(formatted_sources) >= count:
                        break
                        
                    if isinstance(finding, dict):
                        content = finding.get("content", "") or finding.get("summary", "") or str(finding)
                        title = finding.get("title", f"Исследовательский блок {i+1}")
                        url = finding.get("url", "")
                    else:
                        content = str(finding)
                        title = f"Исследовательский блок {i+1}"
                        url = ""
                    
                    # Проверяем, нет ли уже такого заголовка
                    if any(s["title"] == title for s in formatted_sources):
                        continue
                        
                    formatted_sources.append({
                        "title": title,
                        "url": url,
                        "author": "",
                        "snippet": content[:1000],
                        "content_summary": content[:2000]
                    })
            
            # 3. Если совсем пусто, но есть summary — отдаем хотя бы его
            if not formatted_sources and summary:
                formatted_sources.append({
                    "title": f"Результаты исследования по теме: {topic}",
                    "url": "",
                    "author": "LDR Agent",
                    "snippet": summary[:1000],
                    "content_summary": summary
                })
            
            return formatted_sources

        except Exception as e:
            print(f"ERROR in get_real_sources: {e}", file=sys.stderr, flush=True)
            return []

ldr_service = LDRService()
