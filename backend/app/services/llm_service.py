"""
Calamo — LLM Service (Google Gemini / ProxyAPI)
"""
import json
import re
import asyncio
import logging
from datetime import datetime
from typing import Optional
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
# from openai import AsyncOpenAI
# import openai
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type, before_sleep_log
import google.generativeai as genai

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LLMService")
from app.config import settings
from contextvars import ContextVar
from app.schemas.order import (
    PaperOutline, SourceItem, ChartSpec, TableSpec, WorkType, VisualPlan
)
from app.models import LLMUsage

# Контекст для автоматического логирования расходов
llm_context_db: ContextVar[Optional[AsyncSession]] = ContextVar("llm_context_db", default=None)
llm_context_order_id: ContextVar[Optional[int]] = ContextVar("llm_context_order_id", default=None)
llm_context_description: ContextVar[Optional[str]] = ContextVar("llm_context_description", default=None)

# ==========================================
# СИСТЕМНЫЙ ПРОМПТ (Глобальные правила)
# ==========================================

SYSTEM_PROMPT = """# ТВОЯ РОЛЬ
Ты — блестящий научный руководитель и профессиональный автор академических текстов для платформы Calamo. Твоя задача — генерировать безупречный научный контент.
ВАЖНО: Ты генерируешь ТОЛЬКО текст и смыслы. Любое визуальное форматирование (шрифты, отступы, разрывы страниц, написание заголовков) делает скрипт-сборщик. 

# ПРАВИЛА НАПИСАНИЯ АКАДЕМИЧЕСКОГО ТЕКСТА:
1. ЗАПРЕТ НА "ВОДУ": Никогда не используй вводные штампы ("В современном мире", "Ни для кого не секрет, что", "С развитием технологий"). Пиши сухо, объективно, от третьего лица.
2. СТРУКТУРА И ЗАПРЕТ СПИСКОВ: КАТЕГОРИЧЕСКИ ЗАПРЕЩАЕТСЯ использовать маркированные списки (буллиты). Пиши исключительно связными абзацами. Один абзац — одна завершенная мысль (от 4 до 8 предложений).
3. НАУЧНОСТЬ И КОНКРЕТИКА: Избегай общих слов ("выручка сильно выросла"). Опирайся на теории, терминологию, цифры и строгую логику.

# ТЕХНИЧЕСКИЕ ОГРАНИЧЕНИЯ (КРИТИЧЕСКИ ВАЖНО):
1. ПОЛНЫЙ ЗАПРЕТ MARKDOWN И КАПСЛОКА: 
   - Не используй символы `#`, `##`, `**` (жирный шрифт), `*` (курсив). Выдавай абсолютно чистый текст.
   - КАТЕГОРИЧЕСКИ ЗАПРЕЩАЕТСЯ писать какой-либо текст ЗАГЛАВНЫМИ БУКВАМИ (КАПСОМ). Весь текст, включая абзацы с анализом таблиц, должен быть написан в стандартном регистре (как обычные предложения).
2. КАВЫЧКИ: Используй ТОЛЬКО русские кавычки-ёлочки (« и »). Английские (" ") строго запрещены.
3. ОФОРМЛЕНИЕ СНОСОК (ДЛЯ СИСТЕМЫ ПАРСИНГА):
   - Ссылки указывай СТРОГО в формате [N] или [N, с. X]
   - КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО ставить две и более сносок подряд (например, [1][2]). Ставь только ОДНУ сноску на один факт.
   - ПОЗИЦИЯ: Сноска должна стоять СТРОГО ПОСЛЕ ТОЧКИ в конце предложения, без пробела.
   - ОГРАНИЧЕНИЕ: Ставь сноски КРАЙНЕ РЕДКО (макс. 1-2 на раздел), только для критически важных цифр или цитат.
   - Пример ПРАВИЛЬНО: ...выручка выросла на 20%.[3, с. 45]
   - Пример НЕПРАВИЛЬНО: ...рентабельность бизнеса[1].
4. МАРКЕРЫ ВИЗУАЛА:
   - СТРОГО ЗАПРЕЩЕНО использовать знаки доллара ($ или $$) для формул. Вместо них всегда используй теги [EQUATION]формула[/EQUATION].
   - КРИТИЧЕСКИ ВАЖНО: Все переменные и буквенные обозначения (например, CAC, Revenue_t, i, n) в тексте и особенно в пояснениях после слова «где» ТАКЖЕ должны быть обернуты в теги [EQUATION]...[/EQUATION].
   - КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО использовать кириллицу внутри тегов [EQUATION]. Только латиница, цифры и символы LaTeX.
    - Если в промпте тебя просят добавить график или таблицу, вставь маркер [ВСТАВИТЬ_ГРАФИК_N] или [ВСТАВИТЬ_ТАБЛИЦУ_N] с новой строки, где N — номер, указанный в инструкции к разделу.
    - СРАЗУ ПОСЛЕ маркера напиши абзац с детальным анализом того, что изображено на этом графике/таблице, описывая числовые тенденции словами. 
    - КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО самостоятельно писать слова «Рисунок N» или «Таблица N» в тексте анализа. Используй фразы: «согласно представленным данным», «из графика следует», «анализ таблицы показывает». Номера и подписи будут добавлены системой автоматически.
5. ОФОРМЛЕНИЕ ФОРМУЛ:
   - Математические формулы и уравнения оборачивай в теги [EQUATION]...[/EQUATION].
6. КРИТИЧЕСКИ ВАЖНО (ССЫЛКИ): Любая ссылка (URL) в тексте или в списке литературы ОБЯЗАТЕЛЬНО должна быть обернута в тег [HYPERLINK:ссылка], например: [HYPERLINK:https://cyberleninka.ru/article/n/audit]. Без этого ссылка не будет кликабельной!
   - Каждая формула [EQUATION] должна быть выделена в ОТДЕЛЬНЫЙ АБЗАЦ (окружена двойными переносами строк \n\n). 
   - СРАЗУ ПОСЛЕ формулы (в новом отдельном абзаце) должно идти пояснение переменных, начинающееся со слова «где» с маленькой буквы (например: «где P — прибыль...»).
   - Используй стандартный LaTeX. Все слеши (\) и фигурные скобки ({}) должны быть сохранены.
   - КАТЕГОРИЧЕСКИ ЗАПРЕЩАЕТСЯ ИСПОЛЬЗОВАТЬ РУССКИЕ СЛОВА В ФОРМУЛАХ! Используй ТОЛЬКО латинские или греческие буквы для переменных (например, P, TR, TC, ROMI, CAC). Команду \text{...} внутри формул использовать запрещено.
   - Пример правильного оформления (между блоками — пустая строка):
     
     [EQUATION]ROMI = \\frac{TR - TC}{TC} \\cdot 100\\%[/EQUATION]
     
      где [EQUATION]ROMI[/EQUATION] — рентабельность инвестиций; [EQUATION]TR[/EQUATION] — общий доход; [EQUATION]TC[/EQUATION] — общие затраты.
     
   - ПУНКТУАЦИЯ: КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО ставить пробел перед двоеточием (:), точкой с запятой (;) и запятой (,). Знак должен вплотную примыкать к слову.
   - Никогда не пиши формулы обычным текстом с использованием косой черты или простых символов. Только [EQUATION]."""


# ==========================================
# ШАБЛОНЫ ГЛАВ (СПЕЦИФИКАЦИЯ ПОЛЬЗОВАТЕЛЯ)
# ==========================================

PROMPT_CHAPTER_1 = """ШАГ ГЕНЕРАЦИИ: написание ГЛАВЫ 1 (ТЕОРЕТИЧЕСКАЯ БАЗА).
Твоя задача: сформировать фундаментальную теоретическую базу исследования.

КРИТИЧЕСКИЕ ТРЕБОВАНИЯ К СОДЕРЖАНИЮ:
1. АКТУАЛЬНОЕ ПРАВО: Самостоятельно определи перечень действующих нормативных актов, регулирующих тему исследования, опираясь на предоставленные источники. Используй только АКТУАЛЬНЫЕ редакции. Категорически запрещено использовать утратившие силу законы.
2. МЕТОДОЛОГИЯ: Адаптируй стиль и глубину анализа под конкретную научную область темы (юриспруденция, экономика, социология и т.д.). Используй терминологию и подходы, принятые в данной сфере.
3. РАБОТА С ИСТОЧНИКАМИ: Ссылайся СТРОГО на предоставленный список литературы. Не выдумывай авторов.
4. СТИЛЬ: Сухо, академично, СТРОГО БЕЗ СПИСКОВ. После двоеточия пиши с маленькой буквы."""

PROMPT_CHAPTER_2 = """ШАГ ГЕНЕРАЦИИ: написание ГЛАВЫ 2 (ПРАКТИЧЕСКИЙ АНАЛИЗ).
Твоя задача: представить глубокий практический анализ на примере конкретного объекта.

КРИТИЧЕСКИЕ ТРЕБОВАНИЯ К СОДЕРЖАНИЮ:
1. ДОСТОВЕРНОСТЬ ДАННЫХ: Категорически запрещено использовать идеально круглые цифры (100, 500, 1000). Все показатели должны быть реалистичными и опираться на предоставленные источники.
2. ФАКТЫ: Используй цифры, названия департаментов и конкретные показатели из источников. Если точных данных нет — проводи качественный институциональный анализ.
3. ТАБЛИЦЫ/РИСУНКИ: Все данные в них должны быть математически точными и логически связанными.
4. ФОРМУЛЫ: Если упоминаешь расчет показателей (рентабельность, ликвидность и т.д.), обязательно приводи сами математические формулы расчета в тегах [EQUATION]...[/EQUATION].
5. СТИЛЬ: Сухо, академично, СТРОГО БЕЗ СПИСКОВ. Привязка исключительно к данным исследуемого объекта."""

PROMPT_CHAPTER_3 = """ШАГ ГЕНЕРАЦИИ: написание ГЛАВЫ 3 (РЕКОМЕНДАЦИИ И ЭФФЕКТИВНОСТЬ).
Твоя задача: разработать проектную часть, направленную на решение выявленных проблем.

КРИТИЧЕСКИЕ ТРЕБОВАНИЯ К СОДЕРЖАНИЮ:
1. ПРОФИЛЬ ЭФФЕКТИВНОСТИ: Выбери метод оценки эффекта, строго соответствующий теме исследования. 
   - Если тема социально-правовая: оценивай социальную значимость и совершенствование институтов.
   - Если тема экономическая: используй финансовые коэффициенты и расчеты окупаемости.
2. ЛОГИКА РЕКОМЕНДАЦИЙ: Все предложения должны быть реалистичными и базироваться на данных, полученных в ходе анализа во второй главе.
3. ТОЧНОСТЬ ПРОГНОЗА: Прогнозы должны быть сдержанными и академически обоснованными.
4. РАСЧЕТНЫЕ ФОРМУЛЫ: Обязательно приводи 2-3 математические формулы, по которым производились расчеты эффективности или другие вычисления. Оформляй их строго в тегах [EQUATION]...[/EQUATION]."""


# Мониторинг здоровья для админки
llm_health = {
    "gemini_ok": True,
    "openai_ok": True,
    "last_gemini_error": None,
    "last_openai_error": None,
    "recent_errors": [] # List of {"order_id": str, "error": str, "time": str}
}

class LLMService:
    def __init__(self):
        # Модели-алиасы (Оптимизировано для платного тарифа Calamo)
        self.model_smart = "gemini-2.5-pro"       # Для плана, введения и визуалов
        self.model_fast = "gemini-2.5-flash-lite" # Для основного текста разделов
        self.model_search = "gemini-2.5-flash-lite"
        
        # Конкретные идентификаторы
        self.model_gemini_pro = "gemini-2.5-pro"
        self.model_gemini_flash = "gemini-2.5-flash-lite"
        
        # Инициализация Google
        genai.configure(api_key=settings.GEMINI_API_KEY)

    async def _generate_gemini(self, prompt: str, temperature: float = 0.8, model_name: str = "gemini-1.5-pro") -> str:
        """Генерация через Google Gemini."""
        current_date = datetime.now().strftime("%d.%m.%Y")
        system_context = f"ВНИМАНИЕ! СЕГОДНЯШНЯЯ ДАТА: {current_date}. СЕЙЧАС 2026 ГОД. При составлении прогнозов и анализе актуальных данных ориентируйся на текущий момент (2026 год).\n\n{SYSTEM_PROMPT}"
        
        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=system_context
            )
            
            # Настройки безопасности: отключаем лишние фильтры для научных тем
            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]

            response = await model.generate_content_async(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=temperature,
                    max_output_tokens=8192
                ),
                safety_settings=safety_settings
            )
            
            # Проверка на отказ модели по безопасности или иным причинам
            if not response.candidates or not response.candidates[0].content.parts:
                logger.warning(f"Gemini отказалась отвечать (reason: {response.candidates[0].finish_reason if response.candidates else 'unknown'})")
                raise Exception("Gemini_Refusal")

            await asyncio.sleep(2) # Защита от спама запросов
            llm_health["gemini_ok"] = True
            # Логируем расход токенов
            usage = {
                "prompt_tokens": response.usage_metadata.prompt_token_count,
                "completion_tokens": response.usage_metadata.candidates_token_count,
                "total_tokens": response.usage_metadata.total_token_count
            }
            await self._log_usage(model_name, usage)

            return response.text.strip()
        except Exception as e:
            llm_health["gemini_ok"] = False
            llm_health["last_gemini_error"] = str(e)
            # Добавляем в список последних ошибок
            oid = llm_context_order_id.get()
            if oid:
                llm_health["recent_errors"].insert(0, {
                    "order_id": oid,
                    "error": f"Gemini: {str(e)}",
                    "time": datetime.now().strftime("%H:%M:%S")
                })
                llm_health["recent_errors"] = llm_health["recent_errors"][:10]

            logger.error(f"Gemini Error: {e}")
            raise

    async def _generate_openai(self, prompt: str, temperature: float = 0.8, model: Optional[str] = None) -> str:
        """Старая логика через ProxyAPI (OpenAI)."""
        if model is None:
            model = self.model_fast
        url = "https://api.proxyapi.ru/openai/v1/chat/completions"
        api_key = getattr(settings, "PROXYAPI_KEY", "")
        headers = {"Authorization": f"Bearer {api_key}"}
        current_date = datetime.now().strftime("%d.%m.%Y")
        system_context = f"ВНИМАНИЕ! СЕГОДНЯШНЯЯ ДАТА: {current_date}. СЕЙЧАС 2026 ГОД. При составлении прогнозов и анализе актуальных данных ориентируйся на текущий момент (2026 год).\n\n{SYSTEM_PROMPT}"
        
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_context},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 8000
        }
        if "search" not in model:
            payload["temperature"] = temperature

        try:
            async with httpx.AsyncClient(verify=True, timeout=120.0) as client:
                res = await client.post(url, headers=headers, json=payload)
                if res.status_code != 200:
                    logger.error(f"API Error: {res.status_code} - {res.text}")
                    res.raise_for_status()
                data = res.json()
                
                usage = data.get("usage")
                if usage:
                    await self._log_usage(model, usage)
                
                llm_health["openai_ok"] = True
                return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            llm_health["openai_ok"] = False
            llm_health["last_openai_error"] = str(e)
            
            oid = llm_context_order_id.get()
            if oid:
                llm_health["recent_errors"].insert(0, {
                    "order_id": oid,
                    "error": f"OpenAI: {str(e)}",
                    "time": datetime.now().strftime("%H:%M:%S")
                })
                llm_health["recent_errors"] = llm_health["recent_errors"][:10]

            logger.error(f"OpenAI Error: {e}")
            raise

    @retry(
        retry=retry_if_exception_type((json.JSONDecodeError, httpx.HTTPStatusError, httpx.RequestError, Exception)),
        wait=wait_exponential(multiplier=2, min=10, max=60),
        stop=stop_after_attempt(5),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    async def _generate(self, prompt: str, temperature: float = 0.8, model: Optional[str] = None) -> str:
        """Умный балансировщик (Hybrid Generation)."""
        
        # Читаем глобальную стратегию из файла
        strategy = "auto"
        try:
            import json
            with open("app/llm_strategy.json", "r") as f:
                config = json.load(f)
                strategy = config.get("llm_strategy", "auto")
        except Exception:
            pass

        # Если модель не указана, используем Flash Lite по умолчанию
        target_model = model if model else self.model_fast
        
        # Принудительная стратегия
        if strategy == "openai_only" and "gemini" in target_model.lower():
            logger.info("Стратегия: OpenAI Only. Переключаю с Gemini на gpt-4o-mini...")
            target_model = "gpt-4o-mini"
        
        try:
            if "gemini" in target_model.lower():
                logger.info(f"Генерация через Gemini ({target_model})...")
                return await self._generate_gemini(prompt, temperature, target_model)
            else:
                return await self._generate_openai(prompt, temperature, target_model)
        except Exception as e:
            if strategy == "gemini_only":
                logger.error(f"Сбой Gemini, а стратегия 'Gemini Only'. Ошибка: {e}")
                raise e
                
            logger.warning(f"Критический сбой модели {target_model}: {e}. Переход на резерв (ProxyAPI)...")
            # Если упал даже Flash, пробуем GPT-4o-mini как самый стабильный и дешевый вариант
            fallback_model = self.model_fast if "gemini" not in self.model_fast else "gpt-4o-mini"
            return await self._generate_openai(prompt, temperature, fallback_model)

    async def _log_usage(self, model: str, usage: dict):
        """Логирование расхода токенов в БД."""
        db = llm_context_db.get()
        if not db:
            return

        order_id = llm_context_order_id.get()
        description = llm_context_description.get()
        
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        
        # Расчет стоимости (примерный для ProxyAPI в рублях за 1М токенов)
        # GPT-4o: ~500р / ~1500р
        # GPT-4o-mini: ~15р / ~60р
        if "mini" in model:
            cost = (prompt_tokens * 15 + completion_tokens * 60) / 1_000_000
        else:
            cost = (prompt_tokens * 500 + completion_tokens * 1500) / 1_000_000
            
        try:
            log_entry = LLMUsage(
                order_id=order_id,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=usage.get("total_tokens", 0),
                estimated_cost_rub=cost,
                description=description
            )
            db.add(log_entry)
            await db.commit()
        except Exception as e:
            logger.error(f"Failed to log LLM usage: {e}")

    def _parse_json(self, text: str):
        """Robust JSON parser that handles markdown code fences and trailing garbage."""
        text = re.sub(r'```json\s*|\s*```', '', text).strip()
        start_curly = text.find('{')
        start_bracket = text.find('[')
        if start_curly == -1 and start_bracket == -1:
            raise ValueError(f"No JSON structure found in response. Raw: {text[:100]}")
        s = start_curly if start_curly != -1 and (start_bracket == -1 or start_curly < start_bracket) else start_bracket
        json_str = text[s:]
        decoder = json.JSONDecoder()
        try:
            obj, _ = decoder.raw_decode(json_str)
            return obj
        except json.JSONDecodeError:
            e_idx = text.rfind('}' if s == start_curly else ']')
            if e_idx != -1:
                return json.loads(text[s:e_idx + 1])
            raise

    # ==========================================
    # ГЕНЕРАТОР ПЛАНА
    # ==========================================

    async def generate_outline(self, topic, work_type, subject, custom_outline=None, pages_count=35, pages_range=None) -> PaperOutline:
        if pages_count <= 25:
            structure_rule = "Базовый состав: ВВЕДЕНИЕ, строго 2 Главы (в каждой строго по 2 параграфа), ЗАКЛЮЧЕНИЕ, СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ И ЛИТЕРАТУРЫ."
        elif pages_count <= 35:
            structure_rule = "Базовый состав: ВВЕДЕНИЕ, строго 3 Главы (в каждой строго по 2 параграфа), ЗАКЛЮЧЕНИЕ, СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ И ЛИТЕРАТУРЫ."
        elif pages_count <= 45:
            structure_rule = "Базовый состав: ВВЕДЕНИЕ, строго 3 Главы (в каждой строго по 3 параграфа), ЗАКЛЮЧЕНИЕ, СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ И ЛИТЕРАТУРЫ."
        else:
            structure_rule = "Базовый состав: ВВЕДЕНИЕ, строго 3 Главы (в каждой строго по 4 параграфа), ЗАКЛЮЧЕНИЕ, СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ И ЛИТЕРАТУРЫ."

        prompt = f"""Разработай подробный план для академической работы типа «{work_type.value}» на тему: «{topic}» по дисциплине «{subject}».
Целевой объем работы: {pages_count} страниц.

ПРАВИЛА СТРУКТУРЫ И ЛОГИКИ:
1. {structure_rule}
2. Логика исследования: Глава 1 должна носить теоретико-методологический характер (понятия, сущность, нормативная база). Глава 2 должна носить практико-аналитический характер (анализ на примере, оценка состояния). Если есть Глава 3, она должна содержать рекомендации и пути решения проблем.
3. Ограничения вложенности: Категорически запрещен 3-й уровень (никаких 1.1.1). Разрешены только уровни Глав (Глава 1...) и их подпунктов (1.1...).
4. Стиль названий: Строгий академический язык. Названия глав и подпунктов не должны дословно дублировать друг друга или саму тему работы. В конце названий точки не ставятся.
5. ВРЕМЕННОЙ ПЕРИОД (КРИТИЧЕСКИ ВАЖНО): Сейчас 2026 год. Если в названиях глав или параграфов используются временные интервалы, они ОБЯЗАТЕЛЬНО должны включать 2026 год (например: «2021–2026 гг.», «2024–2026 гг.», «прогноз до 2027 г.»). КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО заканчивать периоды 2024 или 2025 годом.

ТРЕБОВАНИЯ К ФОРМАТУ ВЫВОДА (КРИТИЧЕСКИ ВАЖНО):
Верни ТОЛЬКО валидный JSON-массив объектов. Не используй Markdown-разметку (не пиши json в начале и в конце). Не добавляй никаких приветствий, пояснений или комментариев. Вывод должен начинаться с символа [ и заканчиваться символом ].

Формат объектов:
[
  {{ "title": "ВВЕДЕНИЕ", "level": 1 }},
  {{ "title": "Глава 1. Название первой главы", "level": 1 }},
  {{ "title": "1.1. Название первого параграфа", "level": 2 }},
  {{ "title": "1.2. Название второго параграфа", "level": 2 }},
  {{ "title": "Глава 2. Название второй главы", "level": 1 }},
  {{ "title": "2.1. Название параграфа", "level": 2 }},
  {{ "title": "ЗАКЛЮЧЕНИЕ", "level": 1 }},
  {{ "title": "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ И ЛИТЕРАТУРЫ", "level": 1 }}
]"""
        text = await self._generate(prompt, model=self.model_smart)
        return self._parse_flat_outline(text, topic)

    def _parse_flat_outline(self, text: str, topic: str) -> PaperOutline:
        """Parse flat [{title, level}] array into PaperOutline with nested chapters."""
        try:
            items = self._parse_json(text)
            if not isinstance(items, list):
                # LLM returned a dict — fall back to old nested parser
                return self._parse_outline(text, topic)
        except Exception as e:
            logger.error(f"JSON parse failed for flat outline: {e}")
            raise

        skip_keywords = ["ВВЕДЕНИЕ", "ЗАКЛЮЧЕНИЕ", "СПИСОК", "ПРИЛОЖЕНИЕ"]
        chapters = []
        current_chapter = None
        chapter_idx = 0

        for item in items:
            level = item.get("level", 1)
            item_title = item.get("title", "").strip()
            if not item_title:
                continue
            # Skip service sections (intro, conclusion, references)
            if any(kw in item_title.upper() for kw in skip_keywords):
                continue

            if level == 1:
                chapter_idx += 1
                # Parse "Глава N. Title" pattern
                m = re.match(r'Глава\s+(\d+)[.\s]+(.+)', item_title, re.IGNORECASE)
                if m:
                    ch_number = m.group(1)
                    ch_title = m.group(2).strip()
                else:
                    ch_number = str(chapter_idx)
                    ch_title = item_title
                current_chapter = {
                    "number": ch_number,
                    "title": ch_title.upper(),
                    "subsections": []
                }
                chapters.append(current_chapter)

            elif level == 2 and current_chapter is not None:
                # Parse "1.1. Title" or "1.1 Title" pattern
                m = re.match(r'(\d+\.\d+)[.\s]+(.+)', item_title)
                if m:
                    sub_number = m.group(1)
                    sub_title = m.group(2).strip()
                else:
                    parent_num = current_chapter["number"]
                    sub_idx = len(current_chapter["subsections"]) + 1
                    sub_number = f"{parent_num}.{sub_idx}"
                    sub_title = item_title
                current_chapter["subsections"].append({
                    "number": sub_number,
                    "title": sub_title,
                    "subsections": []
                })

        if not chapters:
            # Total parse failure — fall back to old parser
            logger.warning("_parse_flat_outline: no chapters found, falling back to _parse_outline")
            return self._parse_outline(text, topic)

        return PaperOutline(title=topic, chapters=chapters)

    def _parse_outline(self, text: str, topic: str) -> PaperOutline:
        """Legacy nested-JSON outline parser (fallback)."""
        try:
            raw = self._parse_json(text)
        except Exception as e:
            logger.error(f"JSON parse failed for outline: {e}")
            raise

        if "chapters" not in raw:
            for alt in ("sections", "parts", "content", "structure"):
                if alt in raw:
                    raw["chapters"] = raw.pop(alt)
                    break

        if isinstance(raw.get("chapters"), dict):
            raw["chapters"] = list(raw["chapters"].values())

        if "title" not in raw or not raw["title"]:
            raw["title"] = topic

        chapters = []
        for i, ch in enumerate(raw.get("chapters", [])):
            if isinstance(ch, str):
                ch = {"number": str(i + 1), "title": ch, "subsections": []}
            ch.setdefault("number", str(i + 1))
            ch.setdefault("title", f"Глава {i + 1}")
            ch.setdefault("subsections", [])
            subs = []
            for j, sub in enumerate(ch.get("subsections", [])):
                if isinstance(sub, str):
                    sub = {"number": f"{i + 1}.{j + 1}", "title": sub, "subsections": []}
                sub.setdefault("number", f"{i + 1}.{j + 1}")
                sub.setdefault("title", f"Раздел {i + 1}.{j + 1}")
                sub.setdefault("subsections", [])
                subs.append(sub)
            ch["subsections"] = subs
            chapters.append(ch)

        raw["chapters"] = chapters
        return PaperOutline(**raw)

    async def _get_academic_sources_context(self, topic: str, count: int = 10) -> str:
        """
        Получает расширенные метаданные реальных статей из OpenAlex API.
        Двойной запрос: оригинальная тема + русскоязычный фокус.
        Извлекает: автор, название, журнал, том, номер, страницы, DOI.
        """
        all_works = []
        
        async def _fetch_openalex(search_query: str, extra_filter: str = "") -> list:
            try:
                url = "https://api.openalex.org/works"
                base_filter = "has_fulltext:true"
                if extra_filter:
                    base_filter += f",{extra_filter}"
                params = {
                    "search": search_query,
                    "per_page": count,
                    "sort": "relevance_score:desc",
                    "filter": base_filter,
                    "mailto": "calamo@calamo.lol"  # OpenAlex polite pool
                }
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(url, params=params)
                    if resp.status_code == 200:
                        return resp.json().get("results", [])
            except Exception as e:
                logger.warning(f"OpenAlex fetch failed for '{search_query[:50]}': {e}")
            return []

        try:
            # Параллельный запрос: общий + с фильтром по русскому языку
            results = await asyncio.gather(
                _fetch_openalex(topic),
                _fetch_openalex(topic, extra_filter="language:ru"),
                return_exceptions=True
            )
            
            for res in results:
                if isinstance(res, list):
                    all_works.extend(res)
            
            # Дедупликация по DOI/title
            seen_ids = set()
            unique_works = []
            for w in all_works:
                work_id = w.get("doi") or w.get("title", "")
                if work_id and work_id not in seen_ids:
                    seen_ids.add(work_id)
                    unique_works.append(w)
            
            if not unique_works:
                return ""
            
            context_lines = []
            for w in unique_works[:count]:
                title = w.get("title")
                if not title:
                    continue
                    
                # Авторы (до 3-х)
                authorships = w.get("authorships", [])
                authors = ", ".join([
                    a.get("author", {}).get("display_name", "") 
                    for a in authorships[:3]
                ])
                
                year = w.get("publication_year")
                doi = w.get("doi")
                
                # Расширенные метаданные журнала
                primary_loc = w.get("primary_location") or {}
                source_info = primary_loc.get("source") or {}
                journal_name = source_info.get("display_name", "")
                
                # Библиографические детали (том, номер, страницы)
                biblio = w.get("biblio") or {}
                volume = biblio.get("volume", "")
                issue = biblio.get("issue", "")
                first_page = biblio.get("first_page", "")
                last_page = biblio.get("last_page", "")
                pages = f"{first_page}-{last_page}" if first_page and last_page else ""
                
                # Ссылка (приоритет: landing page > DOI)
                landing_url = primary_loc.get("landing_page_url")
                best_link = landing_url or doi
                
                # Язык работы
                lang = (w.get("language") or "").lower()
                lang_label = "RU" if lang == "ru" else "EN" if lang == "en" else lang.upper()
                
                # Формируем строку с максимумом метаданных для ГОСТ-оформления
                line = f"- [{lang_label}] АВТОР: {authors} | НАЗВАНИЕ: {title} | ГОД: {year}"
                if journal_name:
                    line += f" | ЖУРНАЛ: {journal_name}"
                if volume:
                    line += f" | Т. {volume}"
                if issue:
                    line += f", № {issue}"
                if pages:
                    line += f" | С. {pages}"
                if doi:
                    line += f" | DOI: {doi}"
                elif best_link:
                    line += f" | URL: {best_link}"
                
                context_lines.append(line)
            
            if context_lines:
                # Подсчитаем баланс языков для инструкции
                ru_count = sum(1 for l in context_lines if l.startswith("- [RU]"))
                en_count = sum(1 for l in context_lines if l.startswith("- [EN]"))
                
                header = (
                    f"\n\nСПИСОК РЕАЛЬНО СУЩЕСТВУЮЩИХ НАУЧНЫХ СТАТЕЙ ПО ДАННОЙ ТЕМЕ "
                    f"(найдено: {ru_count} русскоязычных, {en_count} англоязычных). "
                    f"Используй их метаданные как основу для статейной части выборки, "
                    f"оформляя строго по ГОСТу. Англоязычные источники оформляй на языке оригинала. "
                    f"НЕ ВЫДУМЫВАЙ URL — система добавит их автоматически:\n"
                )
                return header + "\n".join(context_lines)
                
        except Exception as e:
            logger.warning(f"OpenAlex context build failed: {e}")
        return ""

    # ==========================================
    # ГЕНЕРАТОР ИСТОЧНИКОВ
    # ==========================================

    async def generate_sources(self, topic, work_type, subject, count=None, custom_sources=None, exclude_titles: list[str] = None) -> list[SourceItem]:
        current_date = datetime.now().strftime("%d.%m.%Y")
        n = count or 22
        
        # Получаем реальный контекст из OpenAlex
        academic_context = await self._get_academic_sources_context(topic, count=12)

        exclude_str = ""
        if exclude_titles:
            exclude_str = "\n\nКАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО ПОВТОРЯТЬ СЛЕДУЮЩИЕ ИСТОЧНИКИ (ОНИ УЖЕ ЕСТЬ В СПИСКЕ):\n" + "\n".join([f"- {t}" for t in exclude_titles])

        prompt = f"""Сгенерируй РОВНО {n} релевантных, абсолютно реальных и высококачественных академических источников для исследовательской работы на тему: «{topic}» по дисциплине «{subject}».{academic_context}{exclude_str}

КРИТИЧЕСКИЕ ПРАВИЛА ПОДБОРА И ВЕРИФИКАЦИИ ИСТОЧНИКОВ:
1. Выдай СТРОГО {n} источников в сумме. Не больше и не меньше.
2. Кросс-проверка реальности: Каждый автор, название книги или статьи должны реально существовать. Проведи внутреннюю валидацию.
3. Используй метаданные из списка реальных статей (если он предоставлен выше): выбери из них самые подходящие и оформи строго по ГОСТу. Не нужно брать все, возьми только лучшие, чтобы уложиться в лимит {n} источников.
3. Актуальность: Строго последние 3-5 лет (для законов — действующие редакции).

КРИТИЧЕСКИ ВАЖНО — ЗАПРЕТ НА ГЕНЕРАЦИЮ URL:
⛔ КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО выдумывать или генерировать какие-либо URL-адреса, ссылки, DOI или теги [HYPERLINK:...].
⛔ НЕ ПИШИ строки вида «URL:...» или «– URL:...» в gost_format.
✅ Система автоматически добавит верифицированные ссылки к нужным источникам после твоей генерации.
✅ Твоя задача — только точное ГОСТ-оформление текстовой части записи (автор, название, журнал, год, страницы).

СТРУКТУРА И ПРОПОРЦИИ ВЫБОРКИ (СТРОГО СОБЛЮДАТЬ):
- Нормативные акты / ГОСТы (type=law): ровно от 1 до 3 источников (в зависимости от темы).
- Научные статьи из журналов (type=article): не менее 8-10 источников. Приоритет отдавай русскоязычным статьям, но если качественных материалов по узкой теме не хватает — используй в качестве страховки 2-3 англоязычные статьи (оформляй их на языке оригинала). Для всех статей система сама добавит URL.
- Монографии и учебники (type=book): 5-7 классических печатных изданий (без URL).
- Аналитические отчеты (type=report): 1-2 актуальных отчета реальных компаний или агентств (Data Insight, РБК, НИУ ВШЭ, Росстат). Для них система сама добавит URL.

# ПРАВИЛА ОФОРМЛЕНИЯ (УПРОЩЕННЫЙ ГОСТ БЕЗ URL):
- Диапазоны страниц: Указывай реалистичные данные. Для научной статьи это обычно 5-15 страниц (например, С. 112-120). Для целой книги/монографии указывай общий объем (например, 350 с.).
- Для статей: Фамилия, И. О. Название статьи / И. О. Фамилия // Название журнала. – Год. – Т. Х, № Y. – С. XX-YY.
- Для книг: Фамилия, И. О. Название книги: учебник / И. О. Фамилия. – Москва: Издательство, Год. – XXX с.
- Для нормативных актов: Название акта от ДД.ММ.ГГГГ № ХХ-ФЗ (ред. от ДД.ММ.ГГГГ) // Собрание законодательства РФ. – Год. – № Х. – Ст. ХХ.
- Для отчетов компаний: Название отчета за Год год / Название компании. – Год. – С. XX-YY (если есть).

ТРЕБОВАНИЯ К ФОРМАТУ ВЫВОДА:
Верни ТОЛЬКО валидный JSON-массив объектов. Не используй Markdown-разметку (никаких ```json). Никаких приветствий и лишнего текста.
НАПОМИНАНИЕ: ПОЛЕ url ОСТАВЛЯЙ ПУСТЫМ ("") — система заполнит его сама верифицированными ссылками.

Формат вывода:
[
  {{
    "id": 1,
    "gost_format": "Иванов, И. И. Трансформация ритейла в условиях цифровизации / И. И. Иванов // Экономика и предпринимательство. – 2023. – № 5. – С. 112-119.",
    "url": "",
    "type": "article"
  }},
  {{
    "id": 2,
    "gost_format": "Петров, А. А. Стратегический менеджмент: учебник / А. А. Петров. – Москва: КноРус, 2022. – 410 с.",
    "url": "",
    "type": "book"
  }},
  {{
    "id": 3,
    "gost_format": "Анализ рынка e-commerce в России за 2023 год / Data Insight. – 2024.",
    "url": "",
    "type": "report"
  }}
]"""
        text = await self._generate(prompt, model=self.model_search)
        try:
            data = self._parse_json(text)
            if isinstance(data, dict):
                data = list(data.values())
            # Дедупликация на лету
            unique_citations = set()
            items = []
            for item in data:
                gost = (item.get("gost_format") or item.get("title") or "").strip()
                if not gost:
                    continue
                
                # Приводим к нижнему регистру для сравнения, убирая лишние пробелы
                normalized_gost = gost.lower()
                if normalized_gost in unique_citations:
                    continue
                
                unique_citations.add(normalized_gost)
                
                # Экстракция URL из текста, если поле url пустое (подстраховка от ошибок LLM)
                url = item.get("url")
                
                # Если ИИ засунул тег [HYPERLINK:...] в чистое поле url, вырезаем его
                if url and "[HYPERLINK:" in url:
                    url = re.sub(r'\[HYPERLINK:(.*?)\]', r'\1', url).strip()
                
                if not url or url.strip() == "":
                    # Улучшенная экстракция URL (подстраховка от ошибок LLM)
                    # 1. Сначала ищем внутри тега [HYPERLINK:...]
                    tag_match = re.search(r'\[HYPERLINK:(?P<url>https?://[^\s\]]+)\]', gost, re.IGNORECASE)
                    if tag_match:
                        url = tag_match.group('url').strip()
                    else:
                        # 2. Ищем после маркера URL:
                        url_marker_match = re.search(r'URL:\s*(https?://[^\s\)\],]+)', gost, re.IGNORECASE)
                        if url_marker_match:
                            url = url_marker_match.group(1).strip()
                        else:
                            # 3. Крайний случай: ищем любую ссылку в тексте
                            any_url_match = re.search(r'(https?://[^\s\)\],]+)', gost, re.IGNORECASE)
                            if any_url_match:
                                url = any_url_match.group(1).strip()

                items.append(SourceItem(
                    number=len(items) + 1,
                    title=gost,
                    citation=gost,
                    url=url or None,
                    type=item.get("type", "book"),
                    year=item.get("year"),
                ))
            return items[:n]
        except Exception as e:
            logger.error(f"Sources parse failed: {e}. Raw: {text[:200]}")
            return []

    # ==========================================
    # ГЕНЕРАТОР РАЗДЕЛА (Смыслы и Сноски)
    # ==========================================

    async def generate_section(
        self, topic, work_type, section_number, section_title,
        outline, sources, sources_content="", target_words=1500, figures_instruction="",
        chapter_instruction: str = "", citation_usage: dict[int, int] = None
    ) -> str:
        """Трехшаговая генерация раздела: Исследование -> Написание -> Полировка."""
        if citation_usage is None:
            citation_usage = {}
            
        # Подготовка контекста источников
        context_sources = "\n".join(
            f"[{s.number}] {s.citation or s.title}" for s in sources
        ) if sources else "Источники не предоставлены."

        # Список исчерпанных источников
        exhausted = [n for n, count in citation_usage.items() if count >= 3]
        exhausted_instr = ""
        if exhausted:
            exhausted_instr = f"\nВНИМАНИЕ: Следующие источники уже были использованы максимально допустимое количество раз, КАТЕГОРИЧЕСКИ НЕ ИСПОЛЬЗУЙ ИХ ДЛЯ СНОСОК: {exhausted}. Выбирай другие источники из списка."

        # Выбор специфического стиля главы
        chapter_match = re.match(r'^(\d+)', section_number)
        chapter_num = chapter_match.group(1) if chapter_match else "1"
        chapter_specific = {
            "1": PROMPT_CHAPTER_1,
            "2": PROMPT_CHAPTER_2,
            "3": PROMPT_CHAPTER_3
        }.get(chapter_num, "ШАГ ГЕНЕРАЦИИ: написание раздела академической работы.")

        if chapter_instruction:
            chapter_specific += f"\n\nДОПОЛНИТЕЛЬНАЯ ИНСТРУКЦИЯ:\n{chapter_instruction}"

        # --- ШАГ 1: ИЗВЛЕЧЕНИЕ ФАКТОВ (RESEARCH) ---
        research_prompt = f"""На основе предоставленного контента источников выдели ключевые факты, цифры, определения и тезисы, которые СТРОГО относятся к теме раздела: «{section_number} {section_title}».
        
        Тема работы: «{topic}»
        
        КОНТЕНТ ИСТОЧНИКОВ:
        {sources_content[:12000]} # Ограничиваем контекст для стабильности
        
        Верни только список фактов и тезисов для использования в тексте. Без приветствий."""
        
        logger.info(f"[{section_number}] Шаг 1: Анализ источников...")
        extracted_facts = await self._generate(research_prompt, temperature=0.3)

        # --- ШАГ 2: НАПИСАНИЕ ЧЕРНОВИКА (DRAFTING) ---
        draft_prompt = f"""{chapter_specific}
        
        Тема работы: «{topic}»
        Раздел: «{section_number} {section_title}»
        ОГРАНИЧЕНИЕ ПО ОБЪЕМУ (КРИТИЧЕСКИ ВАЖНО):
        Твоя задача — написать СТРОГО около {target_words} слов (примерно {max(1, target_words // 250)} страниц чистого текста для этого конкретного раздела). 
        Ты ОБЯЗАН следить за объемом: не лей воду, если запрошен маленький объем, и пиши ОЧЕНЬ подробно и развернуто, если запрошен большой объем!
        
        ПОДГОТОВЛЕННЫЕ ФАКТЫ ИЗ ИСТОЧНИКОВ:
        {extracted_facts}
        
        СПИСОК ИСТОЧНИКОВ ДЛЯ ЦИТИРОВАНИЯ (ИСПОЛЬЗУЙ НОМЕРА [N]):
        {context_sources}{exhausted_instr}
        
        {figures_instruction}
        
        ЗАДАЧА: Напиши развернутый академический текст раздела. 
        - Используй факты выше.
        - СТРОГО БЕЗ СПИСКОВ (только связные абзацы).
        - ОГРАНИЧЕНИЕ ПО СНОСКАМ: Вставляй ссылки на источники [N] или [N, с. X] КРАЙНЕ РЕДКО. Делай максимум 2-3 сноски на весь раздел! Ставь их только после реальных фактов, цифр или цитат. Не ставь сноски после каждого предложения.
        - Если есть инструкция по визуалам — ОБЯЗАТЕЛЬНО выполни её (подводка, вставка маркера, затем СТРОГО 180-230 слов глубокого анализа цифр и тенденций).
        
        Начинай сразу с текста, без заголовка раздела."""
        
        logger.info(f"[{section_number}] Шаг 2: Написание черновика...")
        draft_text = await self._generate(draft_prompt, temperature=0.7)

        # --- ШАГ 3: ГОСТ-ПОЛИРОВКА (POLISHING) ---
        polish_prompt = f"""Ты — строгий академический редактор. Твоя задача — довести текст до идеала по ГОСТ.
        
        ИСХОДНЫЙ ТЕКСТ:
        {draft_text}
        
        ПРАВИЛА ПРАВКИ (КРИТИЧЕСКИ ВАЖНО):
        1. КАТЕГОРИЧЕСКИ УДАЛИ любую Markdown-разметку (никаких #, **, *). Только чистый текст.
        2. ФОРМУЛЫ И АБЗАЦЫ: Каждая формула [EQUATION] должна быть выделена в ОТДЕЛЬНЫЙ АБЗАЦ (окружена \n\n). Пояснение переменных (начинающееся со слова «где») должно идти СЛЕДУЮЩИМ отдельным абзацем.
        3. ФОРМУЛЫ И ПЕРЕМЕННЫЕ: ЗАМЕНИ ВСЕ знаки доллара ($$...$$ и $...$) вокруг формул и переменных на теги [EQUATION]...[/EQUATION].
           ВАЖНО: В абзаце с пояснением переменных (после слова «где») ВСЕ буквенные обозначения переменных (например, Revenue_t, CAC, i и т.д.) ОБЯЗАТЕЛЬНО оберни в теги [EQUATION]...[/EQUATION].
        4. НЕ ТРОГАЙ ЛАТЕКС И ПЕРЕМЕННЫЕ: Внутри тегов [EQUATION]...[/EQUATION] КАТЕГОРИЧЕСКИ ЗАПРЕЩАЕТСЯ удалять обратные слеши (\) и фигурные скобки ({{}}). Формулы должны содержать ТОЛЬКО латинские/греческие буквы. Русские слова и команду \text{{...}} внутри LaTeX использовать СТРОГО ЗАПРЕЩЕНО.
        5. ЗАМЕНИ все кавычки на «ёлочки».
        6. ПОЗИЦИЯ СНОСОК И ПУНКТУАЦИЯ: 
           - Сноска [N] должна стоять СТРОГО ПОСЛЕ ТОЧКИ в конце предложения.
           - КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО оставлять две сноски подряд ([1][2]) — удаляй дубли, оставляй только одну.
           - КАТЕГОРИЧЕСКИ УДАЛИ любые пробелы перед двоеточиями (:) и точками с запятой (;).
           - ВАЖНО: Все ссылки (URL) ОБЯЗАТЕЛЬНО оберни в [HYPERLINK:ссылка].
        7. УБЕРИ любые маркированные или нумерованные списки — перепиши их в связные абзацы.
        8. СОХРАНИ все маркеры [ВСТАВИТЬ_ТАБЛИЦУ_...] и [ВСТАВИТЬ_ГРАФИК_...], присутствующие в исходном тексте. НЕ МЕНЯЙ В НИХ НОМЕРА.
        
        Верни только очищенный финальный текст."""
        
        logger.info(f"[{section_number}] Шаг 3: ГОСТ-полировка...")
        final_text = await self._generate(polish_prompt, temperature=0.1)
        
        # Финальная программная чистка (страховка)
        # Переносим сноски [N] за точку, если ИИ ошибся
        final_text = re.sub(r'\s*\[(\d+)\]\.', r'.[\1]', final_text)
        # Переносим сноски с страницами [N, с. X] за точку
        final_text = re.sub(r'\s*\[(\d+),\s*с\.\s*(\d+)\]\.', r'.[\1, с. \2]', final_text)
        # Убираем пробелы перед сносками, если они остались
        final_text = re.sub(r'\s+(\[\d+\])', r'\1', final_text)
        
        # Ограничиваем количество сносок программно
        final_text, updated_usage = self._apply_citation_limits(final_text, citation_usage)
        citation_usage.update(updated_usage)
        
        return final_text

    def _apply_citation_limits(self, text: str, usage: dict[int, int], per_source_limit: int = 3, per_section_limit: int = 6) -> tuple[str, dict[int, int]]:
        """Программное ограничение количества сносок."""
        new_usage = usage.copy()
        section_total = 0
        
        # Паттерн для поиска [N] или [N, M] или [N, с. X]
        pattern = r'\[(?P<ids>\d+(?:\s*,\s*\d+)*)(?:,\s*с\.\s*(?P<page>\d+(?:-\d+)?))?\]'
        
        def replace_func(match):
            nonlocal section_total
            ids_str = match.group('ids')
            page_info = match.group('page')
            source_ids = [s.strip() for s in ids_str.split(',')]
            
            valid_ids = []
            for source_num_str in source_ids:
                source_num = int(source_num_str)
                if section_total < per_section_limit and new_usage.get(source_num, 0) < per_source_limit:
                    section_total += 1
                    new_usage[source_num] = new_usage.get(source_num, 0) + 1
                    valid_ids.append(source_num_str)
            
            if not valid_ids:
                return ""
            
            new_ids = ", ".join(valid_ids)
            if page_info:
                return f"[{new_ids}, с. {page_info}]"
            return f"[{new_ids}]"
            
        cleaned_text = re.sub(pattern, replace_func, text)
        cleaned_text = cleaned_text.replace("..", ".")
        cleaned_text = re.sub(r'\s+\.', '.', cleaned_text)
        return cleaned_text, new_usage

    # ==========================================
    # ГЕНЕРАТОР ВВЕДЕНИЯ
    # ==========================================

    async def generate_introduction(self, topic, work_type, subject, outline, target_words=800) -> str:
        outline_str = "\n".join(
            f"Глава {ch.number}. {ch.title}\n" +
            "\n".join(f"  {sub.number}. {sub.title}" for sub in ch.subsections)
            for ch in outline.chapters
        )
        prompt = f"""Тема работы: «{topic}».
Тип работы: {work_type.value}.
Дисциплина: {subject}.
Структура работы:
{outline_str}

Напиши академическое введение для данной работы объёмом примерно {target_words} слов.

ОБЯЗАТЕЛЬНАЯ СТРУКТУРА (всё — связными абзацами, без заголовков и без списков):
Первый абзац — актуальность темы: объясни, почему данная тема важна для науки и практики в текущий период. Используй 1-2 ссылки на источники в формате .[N] строго после точки.
Второй абзац — степень научной разработанности: опиши 2-3 ключевых теоретических подхода или научные школы, изучающие данную проблематику. Фокусируйся на сути концепций, СТРОГО БЕЗ упоминания конкретных фамилий ученых.
Третий абзац — объект и предмет исследования: чётко сформулируй объект (конкретное явление или процесс) и предмет (конкретные свойства или аспекты объекта).
Четвёртый абзац — цель и задачи: начни со слов «Целью настоящей работы является...». Затем перечисли 4-5 задач В ВИДЕ СВЯЗНОГО ТЕКСТА используя обороты «для достижения указанной цели необходимо: рассмотреть...; проанализировать...; выявить...; разработать...».
Пятый абзац — методология: кратко укажи теоретические и практические методы исследования (анализ, синтез, сравнительный метод, статистический анализ и т.д.).
Шестой абзац — структура работы: начни со слов «Структура работы обусловлена её целью и задачами». Кратко опиши, из каких разделов состоит работа и что в них рассматривается.

СТРОГО ЗАПРЕЩЕНО:
- Писать слово «ВВЕДЕНИЕ» или любой другой заголовок в тексте.
- Использовать маркированные или нумерованные списки.
- Использовать Markdown-разметку (#, **, *).
- Использовать вводные клише («В современном мире», «Актуальность данной темы обусловлена...»).
- Упоминать фамилии конкретных ученых («в трудах Иванова», «как отмечает Смит» и т.д.). Используй только названия подходов и школ.

Выдай чистый академический текст абзацами."""
        return await self._generate(prompt, temperature=0.72, model=self.model_smart)

    # ==========================================
    # ГЕНЕРАТОР ЗАКЛЮЧЕНИЯ
    # ==========================================

    async def generate_conclusion(self, topic, work_type, outline, sections_summary, target_words=600) -> str:
        outline_str = "\n".join(
            f"Глава {ch.number}. {ch.title}" for ch in outline.chapters
        )
        prompt = f"""Тема работы: «{topic}».
Тип работы: {work_type.value}.
Структура работы:
{outline_str}

Краткое содержание написанных разделов:
{sections_summary}

Напиши академическое заключение для данной работы объёмом примерно {target_words} слов.

ОБЯЗАТЕЛЬНАЯ СТРУКТУРА (всё — связными абзацами, без заголовков и без списков):
Первый абзац — вводная фраза: начни со слов «В ходе проведённого исследования...» и обозначь, какие вопросы были рассмотрены в работе в целом.
Далее — по одному абзацу на каждую главу работы: сформулируй ключевой вывод по каждой главе, опираясь на её содержание. Каждый абзац должен начинаться со слов «По результатам изучения [темы главы] установлено, что...» или аналогичным оборотом.
Предпоследний абзац — общий итог: начни со слов «Таким образом, цель работы достигнута». Кратко укажи, все ли поставленные задачи были решены.
Последний абзац — практическая значимость и перспективы: укажи, где могут быть применены результаты работы и какие направления требуют дальнейшего исследования.

СТРОГО ЗАПРЕЩЕНО:
- Писать слово «ЗАКЛЮЧЕНИЕ» или любой другой заголовок в тексте.
- Использовать маркированные или нумерованные списки.
- Использовать Markdown-разметку (#, **, *).
- Вставлять сноски [N] (заключение пишется на основе уже изложенного материала).

Выдай чистый академический текст абзацами."""
        return await self._generate(prompt, temperature=0.72, model=self.model_smart)

    # ==========================================
    # ГЕНЕРАТОР ГРАФИКОВ
    # ==========================================

    async def plan_visuals(self, topic: str, outline: PaperOutline, tables_count: int, charts_count: int) -> VisualPlan:
        outline_str = "\n".join([f"{ch.number}. {ch.title}\n" + "\n".join([f"  {sub.number}. {sub.title}" for sub in ch.subsections]) for ch in outline.chapters])
        
        prompt = f"""Тема работы: «{topic}».
Структура работы:
{outline_str}

Задание: Составь оптимальный план размещения {tables_count} ТАБЛИЦ и {charts_count} ГРАФИКОВ/РИСУНКОВ по разделам работы.

ПРАВИЛА ПЛАНИРОВАНИЯ:
1. Глава 1 (теория): Минимум визуалов. Можно 1 диаграмму или таблицу с классификациями.
2. Глава 2 (анализ): Основное место для визуалов. Здесь должны быть таблицы с данными и графики динамики.
3. Глава 3 (проект): Таблицы с расчетами эффективности, ROI, бюджета.
4. ВЫБОР ВИДА: Используй ТАБЛИЦЫ только для сложной структурированной информации (сравнения, финансовые показатели, технические данные), которую трудно описать обычным связным текстом.
5. УМЕСТНОСТЬ: Выбирай только те разделы, где визуал ДЕЙСТВИТЕЛЬНО УМЕСТЕН по названию.

ФОРМАТ JSON:
{{
  "items": [
    {{ "section_number": "2.1", "visual_type": "CHART", "topic": "Динамика объема рынка за 2021-2024 гг." }},
    {{ "section_number": "2.2", "visual_type": "TABLE", "topic": "Сравнительный анализ финансовых показателей компании X и Y" }}
  ]
}}

Верни ТОЛЬКО JSON."""
        data = self._parse_json(await self._generate(prompt, model=self.model_smart))
        return VisualPlan(**data)

    async def generate_chart_specs(self, topic, work_type, outline, sources_content="", count=None, chapter_num: str = "2", full_bibliography: str = "", specific_topic: str = "") -> list[ChartSpec]:
        context_instr = f"График должен быть на тему: «{specific_topic}»." if specific_topic else "Графики должны носить аналитический характер."
        
        prompt = f"""Тема работы: «{topic}».
{context_instr}
Данные из источников для анализа:
{sources_content[:4000]}

Задание: Сгенерируй спецификации для {count or 1} ГРАФИКОВ для ГЛАВЫ {chapter_num}.
{context_instr}

ПРАВИЛА ДОСТОВЕРНОСТИ (КРИТИЧЕСКИ ВАЖНО):
1. ПРИОРИТЕТ №1: Используй РЕАЛЬНЫЕ цифры и показатели из источников. Если в источнике есть число — ты ОБЯЗАН перенести его без искажений.
2. ЕСЛИ ТОЧНЫХ ЦИФР НЕТ: Категорически запрещено оставлять график пустым или заполнять его нулями. Только в этом случае ТЫ ДОЛЖЕН сгенерировать ЛОГИЧЕСКИЕ ЭКСПЕРТНЫЕ ОЦЕНКИ (цифры), которые соответствуют качественному описанию в тексте.
3. ЗАПРЕТ НА НУЛИ: Генерация графиков с нулевыми значениями или текстом "нет данных" СТРОГО ЗАПРЕЩЕНА. График ДОЛЖЕН содержать осмысленные данные в 100% случаев.
4. ТЕКУЩИЙ ГОД: Сейчас 2026 год. Все данные за 2026 год и ранее — это РЕАЛЬНЫЕ данные (не прогноз!). Слово «прогноз» допустимо ТОЛЬКО для 2027 года и далее. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать «прогноз» рядом с 2024, 2025 или 2026 годом.

ПРАВИЛА ТИПОВ (КРИТИЧЕСКИ ВАЖНО):
Строго выбирай тип графика, который идеально подходит под СМЫСЛ данных. Не выбирай случайные типы!
Доступные типы:
- "donut": современная кольцевая диаграмма для долевой структуры (сумма = 100%). Выглядит очень стильно.
- "area": график с закрашенной областью под кривой. Идеально для показа роста объемов рынка во времени.
- "pie": классическая круговая диаграмма для структуры.
- "line": для временной динамики и трендов (выручка по годам).
- "bar": столбчатая диаграмма для сравнения абсолютных значений.
- "hbar": горизонтальные столбцы (отлично для рейтингов или длинных названий).
- "scatter": для показа зависимости (корреляции) двух переменных.

ПРАВИЛА РАЗНООБРАЗИЯ (КРИТИЧЕСКИ ВАЖНО):
1. ЗАПРЕТ НА ПОВТОРЫ ТИПОВ: СТРОГО комбинируй разные типы графиков! Категорически запрещено использовать один и тот же тип (например, "bar" или "line") дважды в одном ответе. Если первый график "bar", второй делай "donut" или "area". Все графики должны быть визуально разными.
2. РАЗНЫЕ АСПЕКТЫ: Каждый график должен раскрывать новую грань темы. Например: 1-й график — динамика рынка (line), 2-й — структура игроков (pie), 3-й — рейтинг факторов (hbar).
3. НЕ ДУБЛИРОВАТЬ ДАННЫЕ: Использование одних и тех же цифр в разных графиках запрещено.

СПИСОК ЛИТЕРАТУРЫ ДЛЯ ФОРМИРОВАНИЯ ИСТОЧНИКОВ:
{full_bibliography}

ПРАВИЛА ОФОРМЛЕНИЯ (КРИТИЧЕСКИ ВАЖНО):
1. ВИЗУАЛ: График должен быть четким и понятным. Избегай перегруженности.
2. КАРТЫ ПОЗИЦИОНИРОВАНИЯ (scatter): Если ты делаешь карту (например, "Карта конкурентов"), используй расширенный формат данных с "x_values", "y_values" и "labels". Шкала для осей должна быть от 1 до 10. Распределяй точки по всему полю, не ставь всех в одну линию.
3. НАЗВАНИЕ: Начинай с большой буквы, далее строго СТРОЧНЫМИ буквами (не капсом!). КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать внутри названия текст об источнике (например "Источник: ..."). Название должно содержать только саму суть!
4. ИСТОЧНИК (source_note): В этом поле НЕ ПИШИ слово «Источник:». Сразу начинай с фразы: «составлено автором на основе: [Полная запись из списка литературы выше]».
5. ПРОВЕРКА: Убедись, что в названии и данных нет грамматических ошибок.

ФОРМАТ JSON (СТРОГО):
[
  {{
    "chart_type": "line",
    "title": "Динамика объемов рынка электронной коммерции в РФ",
    "data": {{
        "labels": ["2022", "2023", "2024", "2025"],
        "values": [12.5, 14.8, 18.2, 21.5]
    }},
    "x_label": "Годы",
    "y_label": "Ед. изм.",
    "figure_number": 1,
    "source_note": "составлено автором на основе: Аакер, Д. Создание сильных брендов / Д. Аакер. – М.: Гребенников, 2021. – 440 с."
  }},
  {{
    "chart_type": "scatter",
    "title": "Карта позиционирования ключевых игроков рынка",
    "data": {{
        "x_values": [8.5, 4.2, 7.8, 2.5],
        "y_values": [7.2, 8.9, 3.5, 5.1],
        "labels": ["Сбер", "Яндекс", "ВТБ", "Т-Банк"]
    }},
    "x_label": "Широта экосистемы (1-10)",
    "y_label": "Уровень инноваций (1-10)",
    "figure_number": 2,
    "source_note": "составлено автором на основе: Аакер, Д. Создание сильных брендов / Д. Аакер. – М.: Гребенников, 2021. – 440 с."
  }}
]

КРИТИЧЕСКИ ВАЖНО: Поле "data" ДОЛЖНО быть объектом. Для scatter используй ключи "x_values", "y_values" и "labels". Для остальных — "labels" и "values".

Верни ТОЛЬКО валидный JSON-массив."""
        data = self._parse_json(await self._generate(prompt, model=self.model_smart))
        if isinstance(data, dict):
            data = [data]
            
        # Исправление структуры данных для Pydantic (list -> dict)
        cleaned_data = []
        for item in data:
            if not isinstance(item, dict): continue
            
            # Если это scatter (Карта позиционирования)
            if item.get("chart_type") == "scatter":
                chart_data = item.get("data", {})
                if isinstance(chart_data, dict):
                    labels = chart_data.get("labels", [])
                    x_vals = chart_data.get("x_values") or chart_data.get("values_x")
                    y_vals = chart_data.get("y_values") or chart_data.get("values_y") or chart_data.get("values")
                    
                    # Если ИИ прислал неполные данные для карты, чиним их
                    if not x_vals or not y_vals or all(v == 0 for v in x_vals) or all(v == 0 for v in y_vals):
                        import random
                        # Генерируем распределенные экспертные оценки от 1 до 10
                        item["data"]["x_values"] = [round(random.uniform(2, 9), 1) for _ in labels]
                        item["data"]["y_values"] = [round(random.uniform(2, 9), 1) for _ in labels]
                        item["data"]["labels"] = labels
            
            # Если ИИ прислал список точек вместо словаря labels/values для обычных графиков
            elif isinstance(item.get("data"), list):
                points = item["data"]
                labels = []
                values = []
                for pt in points:
                    if isinstance(pt, dict):
                        name = pt.get("name") or pt.get("label") or pt.get("x") or "Point"
                        val = pt.get("value") or pt.get("val") or pt.get("y") or 0
                        labels.append(str(name))
                        values.append(val)
                item["data"] = {"labels": labels, "values": values}
            
            cleaned_data.append(item)
            
        return [ChartSpec(**item) for item in cleaned_data]

    # ==========================================
    # ГЕНЕРАТОР ТАБЛИЦ
    # ==========================================

    async def generate_table_specs(self, topic, work_type, outline, sources_content="", count=1, chapter_num: str = "2", full_bibliography: str = "", specific_topic: str = "") -> list[TableSpec]:
        context_instr = f"Таблица должна быть на тему: «{specific_topic}»." if specific_topic else "Таблицы должны содержать финансово-экономические показатели."

        prompt = f"""Тема: «{topic}».
{context_instr}
Данные из источников:
{sources_content[:4000]}

Задание: Сгенерируй данные для {count} аналитических ТАБЛИЦ для ГЛАВЫ {chapter_num}.

ПРАВИЛА ДОСТОВЕРНОСТИ (КРИТИЧЕСКИ ВАЖНО):
1. ПРИОРИТЕТ №1: Используй РЕАЛЬНЫЕ цифры и показатели из источников. Если число есть в тексте — переноси его без изменений.
2. ЕСЛИ ТОЧНЫХ ЦИФР НЕТ: Категорически запрещено оставлять таблицу пустой. Только в этом случае сгенерируй ЛОГИЧЕСКИЕ ЭКСПЕРТНЫЕ ОЦЕНКИ (цифры), соответствующие описанию в тексте.
3. ЗАПРЕТ НА НУЛИ: Таблица ДОЛЖНА содержать осмысленные данные. Вывод "нет данных" СТРОГО ЗАПРЕЩЕН.

ПРАВИЛА РАЗНООБРАЗИЯ (КРИТИЧЕСКИ ВАЖНО):
1. ЗАПРЕТ НА ПОВТОРЫ: Все таблицы в выдаче должны быть абсолютно разными по смыслу и содержанию. 
2. РАЗНЫЕ АСПЕКТЫ: Каждая таблица должна раскрывать новую грань темы. Например, если первая таблица — это финансовые результаты (выручка/прибыль), то вторая должна быть — структура затрат, а третья — расчет эффективности или прогнозные показатели.
3. НЕ ДУБЛИРОВАТЬ ДАННЫЕ: Использование одних и тех же цифр в разных таблицах строго запрещено. Модель должна искать разные наборы данных в предоставленных источниках.

СПИСОК ЛИТЕРАТУРЫ ДЛЯ ФОРМИРОВАНИЯ ИСТОЧНИКОВ:
{full_bibliography}

ПРАВИЛА ОФОРМЛЕНИЯ (КРИТИЧЕСКИ ВАЖНО):
1. РАЗМЕР: Оптимальный размер таблицы — от 3х3 до 5х7 (столбцы х строки). КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО делать огромные таблицы на всю страницу. Если данных много — выбирай самые важные.
2. ЛАКОНИЧНОСТЬ: В одной ячейке должно быть не более 3-5 слов. Таблица — это краткая сводка цифр и параметров, а не место для длинных предложений.
3. НАЗВАНИЕ: Начинай с большой буквы, далее строго СТРОЧНЫМИ буквами (не капсом!). КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать внутри названия текст об источнике (например "Источник: ..."). Название должно содержать только саму суть!
2. ИСТОЧНИК (source_note): В этом поле НЕ ПИШИ слово «Источник:». Сразу начинай с фразы:
   «составлено автором на основе: [Полная запись из списка литературы выше]».
   Используй СТРОГО ОДНУ реальную полную библиографическую запись из предоставленного списка. Не перечисляй несколько источников.
3. ПРОВЕРКА: Убедись, что в названии и данных нет грамматических ошибок.

ФОРМАТ JSON (СТРОГО):
[
  {{
    "title": "Сравнительный анализ финансовой устойчивости предприятия в 2024-2026 гг.",
    "headers": ["Параметр", "2025 г.", "2026 г.", "2027 г. (прогноз)", "Отклонение", "Темп роста"],
    "rows": [
      ["Показатель 1", "val", "val", "val", "diff", "%"],
      ["Показатель 2", "val", "val", "val", "diff", "%"]
    ],
    "table_number": 1,
    "source_note": "составлено автором на основе источников: Аакер, Д. Создание сильных брендов / Д. Аакер. – М.: Гребенников, 2021. – 440 с.; Манн, И. Маркетинг на 100%: ремикс / И. Манн. – М.: Манн, Иванов и Фербер, 2022. – 320 с."
  }}
]

Верни ТОЛЬКО валидный JSON-массив."""
        data = self._parse_json(await self._generate(prompt, model=self.model_smart))
        if isinstance(data, dict):
            data = [data]
            
        # Очистка данных для TableSpec
        cleaned_data = []
        for item in data:
            if not isinstance(item, dict): continue
            
            # Гарантируем, что заголовки — это список строк
            if "headers" in item and isinstance(item["headers"], list):
                item["headers"] = [str(h) for h in item["headers"]]
            
            # Гарантируем, что строки — это список списков строк
            if "rows" in item and isinstance(item["rows"], list):
                new_rows = []
                for row in item["rows"]:
                    if isinstance(row, list):
                        new_rows.append([str(cell) for cell in row])
                    elif isinstance(row, dict):
                        # Если ИИ прислал словарь вместо списка (бывает)
                        new_rows.append([str(v) for v in row.values()])
                item["rows"] = new_rows
            
            cleaned_data.append(item)
            
        return [TableSpec(**item) for item in cleaned_data]

    # ==========================================
    # ГЕНЕРАТОР ДИАГРАММ
    # ==========================================

    async def generate_diagram_specs(self, topic, work_type, outline, count=1, chapter_num: str = "1") -> list[dict]:
        if chapter_num == "1":
            task_instr = "Сгенерируй иерархическую структуру или классификацию (например, классификация видов инноваций)."
        else:
            task_instr = "Сгенерируй схему бизнес-процесса или структуру управления компанией."

        prompt = f"""Тема: «{topic}».
Задание: Сгенерируй спецификацию для {count} ДИАГРАММЫ (блок-схемы) для ГЛАВЫ {chapter_num}.
{task_instr}

ФОРМАТ JSON (СТРОГО):
[
  {{
    "diagram_type": "hierarchy",
    "title": "СТРУКТУРА ПРЕДМЕТА ИССЛЕДОВАНИЯ",
    "nodes": [
      {{"id": "root", "label": "Центральное понятие"}},
      {{"id": "child1", "label": "Подтип 1"}},
      {{"id": "child2", "label": "Подтип 2"}}
    ],
    "edges": [
      {{"from": "root", "to": "child1"}},
      {{"from": "root", "to": "child2"}}
    ],
    "figure_number": 1
  }}
]

Верни ТОЛЬКО валидный JSON-массив."""
        data = self._parse_json(await self._generate(prompt, model=self.model_smart))
        if isinstance(data, dict):
            data = [data]
        return data

    async def fix_empty_chart_spec(self, topic: str, spec: ChartSpec) -> ChartSpec:
        """Если график пришел без данных, просим ИИ наполнить его."""
        prompt = f"""Тема работы: «{topic}».
Данные для графика «{spec.title}» (тип: {spec.chart_type}) отсутствуют или некорректны.
Твоя задача: Сгенерируй реалистичные научные данные для этого графика.

ТРЕБОВАНИЯ К ДАННЫМ:
1. Если тип {spec.chart_type} — это динамика (line/bar), выдай список меток (годы) и 1-2 ряда значений.
2. Если тип scatter (карта позиционирования), выдай x_values, y_values и labels для 4-6 объектов.
3. Данные должны быть академически достоверными (не круглыми).

Верни ТОЛЬКО JSON объект с полями:
- labels: [список строк]
- values: [список чисел] (для line/bar)
- values2: [список чисел] (опционально для сравнения)
- x_values: [список чисел] (для scatter)
- y_values: [список чисел] (для scatter)

Формат вывода (БЕЗ MARKDOWN):
{{
  "labels": ["2021", "2022", "2023"],
  "values": [10.5, 12.3, 15.1]
}}"""
        try:
            res_text = await self._generate(prompt, model=self.model_smart, temperature=0.5)
            data = self._parse_json(res_text)
            spec.data = data
            return spec
        except Exception as e:
            logger.error(f"Failed to fix empty chart spec: {e}")
            # Возвращаем хоть какие-то данные, чтобы не упал рендерер
            spec.data = {"labels": ["2024", "2025", "2026"], "values": [10, 15, 20]}
            return spec

    async def fix_empty_table_spec(self, topic: str, spec: TableSpec) -> TableSpec:
        """Если таблица пришла без строк, просим ИИ наполнить её."""
        prompt = f"""Тема работы: «{topic}».
Таблица «{spec.title}» пуста.
Твоя задача: Сгенерируй реалистичные научные данные для этой таблицы.

ТРЕБОВАНИЯ:
1. Заголовки: {spec.headers or 'придумай сам'}
2. Количество строк: 3-5.
3. Данные должны быть академически достоверными.

Верни ТОЛЬКО JSON объект с полями:
- headers: [список строк]
- rows: [[строка1], [строка2], ...]

Формат вывода (БЕЗ MARKDOWN):
{{
  "headers": ["Параметр", "Значение"],
  "rows": [["Показатель 1", "100"], ["Показатель 2", "200"]]
}}"""
        try:
            res_text = await self._generate(prompt, model=self.model_smart, temperature=0.5)
            data = self._parse_json(res_text)
            spec.headers = data.get("headers", spec.headers)
            spec.rows = data.get("rows", [])
            return spec
        except Exception as e:
            logger.error(f"Failed to fix empty table spec: {e}")
            spec.rows = [["Нет данных", "0"]]
            return spec

    def _format_outline(self, outline): return str(outline)
    def _format_sources(self, sources): return str(sources)

    async def fix_empty_table_spec(self, topic: str, spec: TableSpec, outline: PaperOutline, sources_content: str, full_bibliography: str) -> TableSpec:
        """Исправить пустую спецификацию таблицы."""
        try:
            # Повторно вызываем генерацию для одной таблицы с полным контекстом
            new_specs = await self.generate_table_specs(
                topic, "курсовая", outline, sources_content, 1, 1, full_bibliography, spec.title
            )
            return new_specs[0] if new_specs else spec
        except:
            return spec

    async def fix_empty_chart_spec(self, topic: str, spec: ChartSpec, outline: PaperOutline, sources_content: str, full_bibliography: str) -> ChartSpec:
        """Исправить пустую спецификацию графика."""
        try:
            # Повторно вызываем генерацию для одного графика с полным контекстом
            new_specs = await self.generate_chart_specs(
                topic, "курсовая", outline, sources_content, 1, 1, full_bibliography, spec.title
            )
            return new_specs[0] if new_specs else spec
        except:
            return spec

llm_service = LLMService()
