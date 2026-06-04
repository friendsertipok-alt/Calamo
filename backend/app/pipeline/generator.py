import re
import uuid
import asyncio
import logging
from pathlib import Path
from datetime import datetime
import json as json_lib
from typing import Optional, List, Dict, Any
import os
import tempfile
import sys

from app.config import settings
from app.schemas.order import (
    OrderCreate,
    OrderResponse,
    OrderStatus,
    GenerationProgress,
    PaperOutline,
    WorkType,
    SourceItem,
    TableSpec,
    ChartSpec,
)
from app.services.llm_service import llm_service
from app.services.docx_builder import create_docx_builder
from app.services.chart_generator import create_chart_generator
from app.services.echarts_generator import create_echarts_generator, PLAYWRIGHT_AVAILABLE
from app.services.diagram_generator import create_diagram_generator
from app.services.link_checker import validate_and_clean_sources
from app.services.scraper import gather_sources_content

logger = logging.getLogger(__name__)

def _strip_markdown(text: str) -> str:
    """Удаляет типичные маркдаун-артефакты из текста LLM перед вставкой в Word."""
    if not text:
        return text
    # Убираем заголовки markdown (## Текст -> Текст)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Убираем жирный и курсив (**текст**, __текст__, *текст*, _текст_)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'(?<!\w)\*(.+?)\*(?!\w)', r'\1', text)
    text = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'\1', text)
    # Убираем зачеркивание (~~текст~~)
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    # Убираем инлайн-код (`текст`)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Убираем маркеры списков в начале строк (- , * , + )
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    return text

def _gs(spec, key, default=None):
    """Get spec attribute - works with both dict and Pydantic model."""
    if isinstance(spec, dict):
        return spec.get(key, default)
    return getattr(spec, key, default)

ORDERS_DB_PATH = settings.OUTPUT_DIR / "orders_db.json"

def _load_orders():
    if ORDERS_DB_PATH.exists():
        try:
            with open(ORDERS_DB_PATH, "r", encoding="utf-8") as f:
                return json_lib.load(f)
        except Exception:
            return {}
    return {}

def _save_orders(orders):
    try:
        settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        # Атомарная запись для предотвращения потери данных при сбое (Crash Resilience)
        fd, temp_path = tempfile.mkstemp(dir=settings.OUTPUT_DIR, prefix="orders_db_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            # Превращаем все Pydantic модели в дикты перед сохранением (на всякий случай)
            def pydantic_encoder(obj):
                if hasattr(obj, "dict"):
                    return obj.dict()
                if hasattr(obj, "model_dump"):
                    return obj.model_dump()
                return str(obj)

            # logger.info("DEBUG: Dumping orders to JSON...")
            json_lib.dump(orders, f, ensure_ascii=False, default=pydantic_encoder)
            f.flush()
            os.fsync(f.fileno())  # Гарантируем сброс буферов ОС на диск
        os.replace(temp_path, ORDERS_DB_PATH)
        # logger.info("DEBUG: Orders saved successfully")
    except Exception as e:
        logger.error(f"Failed to save orders DB: {e}", exc_info=True)
        # Пытаемся удалить временный файл в случае ошибки, если он существует
        if 'temp_path' in locals() and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass

# Хранилище заказов (с персистентностью)
orders_store: dict[str, dict] = _load_orders()

def refresh_orders_store():
    global orders_store
    orders_store.update(_load_orders())


class GenerationCancelledError(Exception):
    """Исключение, выбрасываемое при отмене генерации администратором."""
    pass

class PipelineOrchestrator:
    """Главный оркестратор генерации академических работ."""

    async def _check_status(self, order_id: str):
        """Проверка статуса заказа. Пауза (sleep) или Отмена (Exception)."""
        stored = orders_store.get(order_id)
        if not stored:
            return
            
        while stored.get("status") == "paused":
            await asyncio.sleep(5)
            # Перечитываем статус после сна
            stored = orders_store.get(order_id)
            if not stored:
                return
                
        if stored.get("status") == "cancelled":
            raise GenerationCancelledError("Генерация полностью отменена администратором.")

    async def create_order(self, order_data: OrderCreate, user_id: Optional[int] = None) -> str:
        """Создать заказ и вернуть его ID."""
        order_id = str(uuid.uuid4())[:8]

        orders_store[order_id] = {
            "id": order_id,
            "user_id": user_id,
            "data": order_data.dict(), # Сейвим как dict для JSON
            "status": OrderStatus.PENDING,
            "progress": 0,
            "current_step": "Ожидание запуска",
            "steps_completed": [],
            "download_url": None,
            "error_message": None,
            "logs": [f"[{datetime.now().strftime('%H:%M:%S')}] Заказ создан. Ожидание запуска."],
            "created_at": datetime.now().isoformat(),
        }
        _save_orders(orders_store)
        return order_id

    async def generate_draft(self, order_id: str, topic: str, subject: str, work_type: WorkType, pages_count: int = 35) -> tuple[PaperOutline, list[SourceItem]]:
        """Генерация первой части работы (План и Источники - Черновик)."""
        logger.info(f"Начало генерации черновика для заказа {order_id}")
        
        # 1. План работы
        self._update_status(order_id, OrderStatus.GENERATING_OUTLINE, 5, "Генерация плана работы...")
        try:
            await self._check_status(order_id)
            outline = await llm_service.generate_outline(
                topic=topic,
                subject=subject,
                work_type=work_type,
                pages_count=pages_count
            )
            orders_store[order_id]["draft_outline"] = outline.model_dump()
        except Exception as e:
            logger.error(f"Ошибка при генерации плана: {e}")
            raise

        # 2. Источники (в 3 этапа по 7-8 штук для гарантированного обхода лимитов)
        self._update_status(order_id, OrderStatus.GENERATING_SOURCES, 15, "Подбор источников (этап 1/3)...")
        try:
            await self._check_status(order_id)
            # Первый батч (8 штук)
            batch1 = await llm_service.generate_sources(
                topic=topic,
                subject=subject,
                work_type=work_type,
                count=8
            )
            
            self._update_status(order_id, OrderStatus.GENERATING_SOURCES, 17, "Подбор источников (этап 2/3)...")
            exclude = [s.title for s in batch1 if s.title]
            await self._check_status(order_id)
            batch2 = await llm_service.generate_sources(
                topic=topic,
                subject=subject,
                work_type=work_type,
                count=7,
                exclude_titles=exclude
            )
            
            self._update_status(order_id, OrderStatus.GENERATING_SOURCES, 19, "Подбор источников (этап 3/3)...")
            exclude.extend([s.title for s in batch2 if s.title])
            await self._check_status(order_id)
            batch3 = await llm_service.generate_sources(
                topic=topic,
                subject=subject,
                work_type=work_type,
                count=7,
                exclude_titles=exclude
            )
            
            sources = batch1 + batch2 + batch3
            
            sources = await validate_and_clean_sources(sources)
            
            # Перенумеруем источники для порядка (ПОСЛЕ очистки)
            for i, s in enumerate(sources):
                s.number = i + 1
                
            orders_store[order_id]["draft_sources"] = [s.model_dump() for s in sources]
            self._add_log(order_id, f"Черновик готов: план ({len(outline.chapters)} глав) и {len(sources)} источников.")
        except Exception as e:
            logger.error(f"Ошибка при генерации источников: {e}")
            self._add_log(order_id, f"КРИТИЧЕСКАЯ ОШИБКА: {str(e)}")
            raise
            
        self._update_status(order_id, OrderStatus.DRAFT_READY, 20, "Ожидание утверждения черновика")
        return outline, sources

    def delete_order(self, order_id: str, user_id: int):
        """Удалить заказ, если он принадлежит пользователю."""
        if order_id in orders_store:
            if orders_store[order_id].get("user_id") == user_id:
                del orders_store[order_id]
                _save_orders(orders_store)
                return True
        return False

    async def generate_full(self, order_id: str, topic: str, subject: str, work_type: WorkType, outline: PaperOutline, sources: list[SourceItem], chapter_prompts: dict[str, str] = None):
        """Продолжение генерации работы после утверждения плана."""
        if chapter_prompts is None:
            chapter_prompts = {}
        
        order = orders_store.get(order_id)
        raw_data = order["data"]
        # Convert dict to model if loaded from JSON
        if isinstance(raw_data, dict):
            data = OrderCreate(**raw_data)
        else:
            data = raw_data
        
        # Инициализируем хранилища в заказе, если их нет
        stored = order
        if "sections" not in stored: stored["sections"] = {}
        if "all_table_specs" not in stored: stored["all_table_specs"] = []
        if "all_chart_specs" not in stored: stored["all_chart_specs"] = []
        if "all_diagram_specs" not in stored: stored["all_diagram_specs"] = []
        if "visuals_registry" not in stored: stored["visuals_registry"] = {} # (type, id) -> (spec, path)
        if "section_visuals" not in stored: stored["section_visuals"] = {}
        if "introduction" not in stored: stored["introduction"] = None
        if "sources_content" not in stored: stored["sources_content"] = ""
        if "conclusion" not in stored: stored["conclusion"] = None
        if "citation_usage" not in stored: stored["citation_usage"] = {}

        sections = stored["sections"]
        all_table_specs = stored["all_table_specs"]
        all_chart_specs = stored["all_chart_specs"]
        all_diagram_specs = stored["all_diagram_specs"]
        section_visuals = stored["section_visuals"]
        
        # Для visuals_registry нужно восстановить ключи-кортежи из JSON-строк (если были сохранены)
        visuals_registry = {}
        raw_reg = stored.get("visuals_registry", {})
        for k, v in raw_reg.items():
            # JSON ключи всегда строки, превращаем '["ГРАФИК", 1]' обратно в ("ГРАФИК", 1)
            try:
                tuple_key = tuple(json_lib.loads(k))
                visuals_registry[tuple_key] = v
            except:
                pass
            
        output_dir = settings.OUTPUT_DIR / order_id
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            logger.info(f"DEBUG: Entering generate_full for {order_id}")
            # --- ШАГ 3: Генерация текста по разделам ---
            if not stored.get("introduction"):
                self._update_status(order_id, OrderStatus.GENERATING_TEXT, 15, "Написание введения...")
                introduction = await llm_service.generate_introduction(
                    topic=topic,
                    work_type=work_type,
                    subject=subject,
                    outline=outline,
                )
                stored["introduction"] = introduction
                _save_orders(orders_store)
                self._add_log(order_id, "Введение успешно сгенерировано.")
                self._add_completed_step(order_id, "Введение написано")
            
            introduction = stored["introduction"]
            logger.info(f"DEBUG: Introduction handled")

            # --- Инициализация счетчика сносок (ГЛОБАЛЬНЫЙ ЛИМИТ) ---
            if not stored.get("citation_usage"):
                logger.info(f"DEBUG: Initializing citation_usage...")
                citation_usage = {s.number: 0 for s in sources}
                # Учитываем сноски из введения
                for match in re.finditer(r'\[(\d+)\]', introduction):
                    s_num = int(match.group(1))
                    if s_num in citation_usage:
                        citation_usage[s_num] += 1
                stored["citation_usage"] = citation_usage
            
            citation_usage = stored["citation_usage"]
            logger.info(f"DEBUG: Citation usage: {len(citation_usage)} items")

            if not sections:
                sections = {}
            total_sections = sum(len(ch.subsections) for ch in outline.chapters)
            section_idx = 0
            
            # Расчет объема текста (умный расчет с учетом ГОСТ и визуала)
            total_target_words = data.target_words
            if not total_target_words and data.pages_count:
                # 250-270 слов на страницу А4 (1.5 интервал, 14pt)
                total_capacity = data.pages_count * 260 
                
                # Вычитаем "фиксированный" объем
                intro_vol = 800
                concl_vol = 500
                visuals_vol = (data.tables_count + data.figures_count) * 200 # Визуал занимает место ~200 слов
                
                total_target_words = max(2000, total_capacity - intro_vol - concl_vol - visuals_vol)
                
            if not total_target_words:
                total_target_words = 7500 # По умолчанию
                
            words_per_section = total_target_words // total_sections if total_sections > 0 else 1500
            logger.info(f"DEBUG: Words per section: {words_per_section}")

            if not stored.get("sources_content"):
                self._update_status(order_id, OrderStatus.GENERATING_TEXT, 22, "Изучение литературы (чтение источников)...")
                source_urls = [s.url for s in sources if s.url]
                logger.info(f"DEBUG: Gathering content for {len(source_urls)} urls...")
                sources_content = await gather_sources_content(source_urls)
                stored["sources_content"] = sources_content
                _save_orders(orders_store)
            else:
                sources_content = stored["sources_content"]
                self._add_log(order_id, "Материалы источников загружены из кеша.")
            
            logger.info(f"DEBUG: Processing bibliography for {len(sources)} sources...")
            full_bib_str = "\n".join([f"[{s.number}] {s.citation}" for s in sources])
            logger.info(f"DEBUG: Bibliography string created")
            self._add_completed_step(order_id, "Материалы источников изучены")

            # --- ШАГ 4: Планирование и генерация визуала (УМНОЕ РАСПРЕДЕЛЕНИЕ) ---
            if not all_table_specs and not all_chart_specs:
                self._update_status(order_id, OrderStatus.GENERATING_CHARTS, 25,
                                    "Проектирование таблиц и графиков...")
            
            if not section_visuals:
                section_visuals = {}
            chart_paths = []
            stored["section_visuals"] = section_visuals
            
            logger.info("DEBUG: Choosing chart engine...")
            chart_engine = "echarts"
            try:
                strat_path = Path("app/llm_strategy.json")
                if strat_path.exists():
                    with open(strat_path, "r") as f:
                        config = json_lib.load(f)
                        chart_engine = config.get("chart_engine", "matplotlib")
            except Exception as e: 
                logger.warning(f"DEBUG: Strategy read error: {e}")

            logger.info(f"DEBUG: Final engine: {chart_engine}. Playwright: {PLAYWRIGHT_AVAILABLE}")
            
            if chart_engine == "echarts" and PLAYWRIGHT_AVAILABLE:
                logger.info("DEBUG: Init ECharts...")
                chart_gen = create_echarts_generator(output_dir / "figures")
            else:
                logger.info("DEBUG: Init Matplotlib...")
                chart_gen = create_chart_generator(output_dir / "figures")
            
            logger.info("DEBUG: Init Diagram...")
            diag_gen = create_diagram_generator(output_dir / "figures")
            logger.info("DEBUG: All generators init DONE")

            if not all_table_specs and not all_chart_specs:
                logger.info(f"DEBUG: No specs found. Calling plan_visuals for {order_id}...")
                await self._check_status(order_id)
                v_plan = await llm_service.plan_visuals(
                    topic=topic, outline=outline, 
                    tables_count=getattr(data, "tables_count", 2), 
                    charts_count=getattr(data, "figures_count", 2)
                )
                logger.info(f"DEBUG: v_plan received: {len(v_plan.items) if v_plan else 'NONE'}")

                # 2. Генерируем спецификации согласно плану
                t_counter = 1
                f_counter = 1
                
                # Сортировка плана по номеру раздела
                def sort_key(s):
                    try:
                        return [int(part) for part in s.split('.')]
                    except:
                        return [999]

                v_plan.items.sort(key=lambda x: sort_key(x.section_number))

                # 3. Сначала обрабатываем Главу 1 (обязательная диаграмма)
                ch1_secs = [sub.number for ch in outline.chapters if ch.number == "1" for sub in ch.subsections]
                has_ch1_visual = any(item.section_number in ch1_secs for item in v_plan.items)
                
                if ch1_secs and not has_ch1_visual:
                    diag_specs = await llm_service.generate_diagram_specs(
                        topic=topic, work_type=work_type, outline=outline, count=1, chapter_num="1"
                    )
                    for ds in diag_specs:
                        ds["figure_number"] = f_counter
                        num = f_counter
                        f_counter += 1
                        all_diagram_specs.append(ds)
                        stored["all_diagram_specs"] = all_diagram_specs
                        path = diag_gen.generate_diagram(ds)
                        s_num = ch1_secs[0]
                        if s_num not in section_visuals: section_visuals[s_num] = []
                        section_visuals[s_num].append(("DIAGRAM", num))
                        visuals_registry[("ГРАФИК", num)] = (ds, str(path))
                        visuals_registry[("РИСУНОК", num)] = (ds, str(path))

                # 4. Обрабатываем остальной план
                for item in v_plan.items:
                    s_num = item.section_number
                    if s_num not in section_visuals: section_visuals[s_num] = []
                    
                    ch_num = s_num.split('.')[0] if '.' in s_num else "2"
                    
                    if item.visual_type == "TABLE":
                        specs = await llm_service.generate_table_specs(
                            topic=topic, work_type=work_type, outline=outline,
                            sources_content=sources_content, count=1, 
                            chapter_num=ch_num, full_bibliography=full_bib_str,
                            specific_topic=item.topic
                        )
                        for s in specs:
                            # [SELF-CORRECTION] Проверка на пустые данные
                            if not s.rows or len(s.rows) == 0:
                                s = await llm_service.fix_empty_table_spec(topic, s, outline, sources_content, full_bib_str)
                                
                            s.table_number = t_counter
                            num = t_counter
                            t_counter += 1
                            all_table_specs.append(s)
                            section_visuals[s_num].append(("TABLE", num))
                            visuals_registry[("ТАБЛИЦУ", num)] = (s.dict() if hasattr(s, 'dict') else s, None)
                    
                    elif item.visual_type == "CHART":
                        specs = await llm_service.generate_chart_specs(
                            topic=topic, work_type=work_type, outline=outline,
                            sources_content=sources_content, count=1, 
                            chapter_num=ch_num, full_bibliography=full_bib_str,
                            specific_topic=item.topic
                        )
                        for s in specs:
                            # [SELF-CORRECTION] Проверка на пустые данные
                            labels = s.data.get("labels", [])
                            values = s.data.get("values", [])
                            x_vals = s.data.get("x_values", [])
                            
                            if (not labels and not x_vals) or (not values and not x_vals):
                                s = await llm_service.fix_empty_chart_spec(topic, s, outline, sources_content, full_bib_str)
                                
                            s.figure_number = f_counter
                            num = f_counter
                            f_counter += 1
                            all_chart_specs.append(s)
                            # В зависимости от движка вызываем async или sync метод
                            if hasattr(chart_gen, 'generate_chart') and asyncio.iscoroutinefunction(chart_gen.generate_chart):
                                path = await chart_gen.generate_chart(s)
                            else:
                                path = chart_gen.generate_chart(s)
                                
                            section_visuals[s_num].append(("CHART", num))
                            visuals_registry[("ГРАФИК", num)] = (s.dict() if hasattr(s, 'dict') else s, str(path))
                            visuals_registry[("РИСУНОК", num)] = (s.dict() if hasattr(s, 'dict') else s, str(path))
                
                stored["section_visuals"] = section_visuals
                _save_orders(orders_store)

            # --- ШАГ 5: Генерация текста разделов ---
            for chapter in outline.chapters:
                for sub in chapter.subsections:
                    # Проверка на паузу или полную остановку
                    current_status = orders_store.get(order_id, {}).get("status")
                    if current_status in (OrderStatus.STOPPED, "stopped"):
                        self._add_log(order_id, "Генерация полностью остановлена администратором.")
                        return outline, sources
                    
                    while orders_store.get(order_id, {}).get("status") in (OrderStatus.PAUSED, "paused"):
                        await asyncio.sleep(5)
                        if orders_store.get(order_id, {}).get("status") in (OrderStatus.STOPPED, "stopped"):
                            self._add_log(order_id, "Генерация полностью остановлена администратором.")
                            return outline, sources
                            
                    section_idx += 1
                    progress = 30 + int((section_idx / total_sections) * 40)
                    self._update_status(
                        order_id, OrderStatus.GENERATING_TEXT, progress,
                        f"Написание раздела {sub.number} {sub.title}..."
                    )

                    fig_instr = ""

                    if sub.number in section_visuals:
                        visuals_in_sec = section_visuals[sub.number]
                        fig_instr = "# КРИТИЧЕСКОЕ ТРЕБОВАНИЕ: ВСТАВЬ СЛЕДУЮЩИЕ ВИЗУАЛЫ В ДАННЫЙ РАЗДЕЛ\n"
                        fig_instr += "Для каждого указанного ниже объекта ты ОБЯЗАН вставить маркер и написать анализ. НАЗВАНИЕ и ИСТОЧНИК писать НЕ НУЖНО (они будут добавлены скриптом автоматически).\n\n"
                        
                        for v_type, v_id in visuals_in_sec:
                            if v_type == "TABLE":
                                spec, _ = visuals_registry.get(("ТАБЛИЦУ", v_id), (None, None))
                                if not spec: continue
                                data_str = f"Заголовки: {_gs(spec, 'headers', [])} | Строки: {_gs(spec, 'rows', [])[:5]}"
                                t_num = _gs(spec, 'table_number', 0)
                                t_title = _gs(spec, 'title', '')
                                fig_instr += f"--- ТАБЛИЦА №{t_num} ---\n"
                                fig_instr += f"ТЕМА: {t_title}\n"
                                fig_instr += f"ШАГ 1 (ДАННЫЕ ДЛЯ АНАЛИЗА): {data_str}\n"
                                fig_instr += f"ШАГ 2 (Маркер): Строго напиши [ВСТАВИТЬ_ТАБЛИЦУ_{t_num}] с новой строки.\n"
                                fig_instr += f"ШАГ 3 (Анализ): Сразу после маркера напиши СТРОГО 180-230 слов (3-4 объемных абзаца) глубочайшего анализа данных из ШАГА 1. Описывай тенденции, сравнивай показатели. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать 'Таблица {t_num}' или самостоятельно нумеровать её в тексте анализа — это сделает система!\n\n"
                            elif v_type == "CHART":
                                spec, _ = visuals_registry.get(("ГРАФИК", v_id), (None, None))
                                if not spec: continue
                                chart_data = _gs(spec, 'data', {})
                                f_num = _gs(spec, 'figure_number', 0)
                                f_title = _gs(spec, 'title', '')
                                fig_instr += f"--- РИСУНОК №{f_num} ---\n"
                                fig_instr += f"ТЕМА: {f_title}\n"
                                fig_instr += f"ШАГ 1 (ДАННЫЕ ДЛЯ АНАЛИЗА): {chart_data}\n"
                                fig_instr += f"ШАГ 2 (Маркер): Строго напиши [ВСТАВИТЬ_ГРАФИК_{f_num}] с новой строки.\n"
                                fig_instr += f"ШАГ 3 (Анализ): Сразу после маркера напиши СТРОГО 180-230 слов (3-4 объемных абзаца) глубочайшего анализа данных из ШАГА 1. Описывай тренды, динамику, делай выводы. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО писать 'Рисунок {f_num}' или самостоятельно нумеровать его в тексте анализа — это сделает система!\n\n"
                            elif v_type == "DIAGRAM":
                                spec, _ = visuals_registry.get(("ГРАФИК", v_id), (None, None))
                                if not spec: continue
                                fig_instr += f"--- РИСУНОК №{spec['figure_number']} ---\n"
                                fig_instr += f"ТЕМА: {spec['title']}\n"
                                fig_instr += f"ШАГ 1 (Маркер): Строго напиши [ВСТАВИТЬ_ГРАФИК_{spec['figure_number']}]\n"
                                fig_instr += "ШАГ 2 (Анализ): Сразу после маркера развернуто опиши структуру, представленную на схеме. КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО самостоятельно писать номер рисунка в тексте анализа — это сделает система!\n\n"
                        
                        if len(visuals_in_sec) > 1:
                            fig_instr += "ВАЖНО: Распредели эти маркеры и абзацы анализа равномерно по разделу. Между ними должно быть много обычного текста.\n"
                        
                        if sub.number.startswith("1."):
                            fig_instr += "ВАЖНО ДЛЯ ГРАФИКИ: Рисунки должны быть в стиле 'строго минималистичная, плоская 2D векторная графика в корпоративном стиле, без 3D-эффектов'.\n"
                        
                        fig_instr += "Используй эти инструкции.\n"

                    self._add_log(order_id, f"Генерация текста для раздела {sub.number}: {sub.title}...")
                    
                    if sub.number in sections and sections[sub.number]:
                        self._add_log(order_id, f"Раздел {sub.number} уже есть в памяти, пропускаем генерацию.")
                        continue

                    await self._check_status(order_id)
                    section_text = await llm_service.generate_section(
                        topic=topic,
                        work_type=work_type,
                        section_number=sub.number,
                        section_title=sub.title,
                        outline=outline,
                        sources=sources,
                        sources_content=sources_content,
                        target_words=words_per_section,
                        figures_instruction=fig_instr,
                        chapter_instruction=chapter_prompts.get(chapter.number, ""),
                        citation_usage=citation_usage
                    )
                    self._add_log(order_id, f"Раздел {sub.number} успешно написан ({len(section_text.split())} слов).")
                    sys.stdout.flush()
                    
                    # Принудительная защита от "слипшихся" визуалов (Правило №7)
                    if sub.number in section_visuals and len(section_visuals[sub.number]) > 1:
                        section_text = re.sub(
                            r'(Источник:[^\n]+)(?=\n+(?:Таблица|Рисунок)\s+\d+\s*—)',
                            r'\1\n\nПредставленные выше данные требуют дальнейшего рассмотрения в контексте дополнительных аналитических показателей.\n\n',
                            section_text,
                            flags=re.IGNORECASE
                        )

                    # Сохраняем состояние счетчика и раздела
                    stored["citation_usage"] = citation_usage
                    sections[sub.number] = section_text
                    stored["sections"] = sections
                    # Сохраняем только в конце каждой главы, чтобы не перегружать диск
                _save_orders(orders_store)

            if not stored.get("conclusion"):
                self._update_status(order_id, OrderStatus.GENERATING_TEXT, 68,
                                    "Написание заключения...")
                
                sections_summary = "\n".join(
                    f"{k}: {v[:200]}..." for k, v in sections.items()
                )
                await self._check_status(order_id)
                conclusion_obj = await llm_service.generate_conclusion(
                    topic=data.topic,
                    work_type=data.work_type,
                    outline=outline,
                    sections_summary=sections_summary,
                )
                stored["conclusion"] = conclusion_obj
                _save_orders(orders_store)
                self._add_completed_step(order_id, "Заключение написано")
            
            conclusion = stored["conclusion"]

            # --- ШАГ 4: Сборка .docx ---
            self._update_status(order_id, OrderStatus.BUILDING_DOCX, 85,
                                "Сборка документа Word...")

            builder = create_docx_builder()
            builder.set_sources(sources)

            # Титульный лист
            work_type_map = {
                WorkType.COURSEWORK: "КУРСОВАЯ РАБОТА",
                WorkType.ESSAY: "РЕФЕРАТ",
                WorkType.DIPLOMA: "ВЫПУСКНАЯ КВАЛИФИКАЦИОННАЯ РАБОТА",
                WorkType.TEST: "КОНТРОЛЬНАЯ РАБОТА",
                WorkType.REPORT: "ОТЧЁТ",
            }
            builder.add_title_page(
                university=data.university,
                work_type=work_type_map.get(data.work_type, "КУРСОВАЯ РАБОТА"),
                topic=data.topic,
                subject=data.subject,
                student_name=data.student_name,
                student_group=data.student_group,
                teacher_name=data.teacher_name,
                teacher_title=data.teacher_title,
            )

            # Содержание
            builder.add_table_of_contents()

            # Введение
            builder.add_section_title("ВВЕДЕНИЕ", page_break=True)
            builder.add_text(_strip_markdown((introduction or "").strip()))

            used_visuals = set()

            for ch_idx, chapter in enumerate(outline.chapters):
                builder.add_heading_chapter(chapter.number, chapter.title)
                num_subsections = len(chapter.subsections)
                for sub_idx, sub in enumerate(chapter.subsections):
                    builder.add_heading_section(sub.number, sub.title)
                    section_text = sections.get(sub.number, "")

                    # --- Маркер-ориентированная сборка ---
                    # Паттерн ищет [ВСТАВИТЬ_ТАБЛИЦУ_N], [ВСТАВИТЬ_ГРАФИК_N] или [ВСТАВИТЬ_РИСУНОК_N]
                    marker_pattern = re.compile(
                        r'\[ВСТАВИТЬ_(ТАБЛИЦУ|ГРАФИК|РИСУНОК)_(\d+|N)\]',
                        re.IGNORECASE
                    )
                    last_pos = 0
                    found_any_marker = False
                    for m in marker_pattern.finditer(section_text):
                        found_any_marker = True
                        kind = m.group(1).upper()
                        num_str = m.group(2)
                        
                        # Обработка "N" - пытаемся угадать по неиспользованным в этом разделе
                        if num_str.upper() == 'N':
                            num = None
                            if sub.number in section_visuals:
                                for v_type, v_id in section_visuals[sub.number]:
                                    if v_type in ["CHART", "DIAGRAM"] and kind in ["ГРАФИК", "РИСУНОК"]:
                                        if (kind, v_id) not in used_visuals:
                                            num = v_id
                                            break
                                    elif v_type == "TABLE" and kind == "ТАБЛИЦУ":
                                        if (kind, v_id) not in used_visuals:
                                            num = v_id
                                            break
                        else:
                            num = int(num_str)

                        # Текст ДО маркера
                        chunk_before = section_text[last_pos:m.start()].strip()
                        if chunk_before:
                            builder.add_text(_strip_markdown(chunk_before))

                        # Вставляем реальный визуал из реестра
                        if num is not None and (kind, num) in visuals_registry and (kind, num) not in used_visuals:
                            spec, path = visuals_registry[(kind, num)]
                            if kind == "ТАБЛИЦУ":
                                builder.add_table_data(
                                    table_number=_gs(spec, 'table_number', 0),
                                    title=_gs(spec, 'title', ''),
                                    headers=_gs(spec, 'headers', []),
                                    rows=_gs(spec, 'rows', []),
                                    source=_gs(spec, 'source_note', 'составлено автором'),
                                    skip_header=False,
                                )
                            else:
                                builder.add_figure(
                                    image_path=Path(path) if path else path,
                                    figure_number=spec.get("figure_number") if isinstance(spec, dict) else spec.figure_number,
                                    title=spec.get("title") if isinstance(spec, dict) else spec.title,
                                    source=spec.get("source_note", "составлено автором") if isinstance(spec, dict) else spec.source_note,
                                    skip_header=False,
                                )
                            used_visuals.add((kind, num))
                            # Если это был ГРАФИК, помечаем и РИСУНОК как использованный (и наоборот)
                            if kind == "ГРАФИК": used_visuals.add(("РИСУНОК", num))
                            if kind == "РИСУНОК": used_visuals.add(("ГРАФИК", num))

                        last_pos = m.end()

                    # Оставшийся текст после последнего маркера (или весь текст если маркеров нет)
                    tail = section_text[last_pos:].strip()
                    if tail:
                        builder.add_text(_strip_markdown(tail))

                    # Fallback: если в разделе были запланированы визуалы, но не все вставились через маркеры
                    if sub.number in section_visuals:
                        for v_type, v_id in section_visuals[sub.number]:
                            kind_key = "ТАБЛИЦУ" if v_type == "TABLE" else "ГРАФИК"
                            if (kind_key, v_id) not in used_visuals:
                                spec, path = visuals_registry.get((kind_key, v_id), (None, None))
                                if not spec: continue
                                
                                if kind_key == "ТАБЛИЦУ":
                                    builder.add_table_data(
                                        table_number=_gs(spec, 'table_number', 0),
                                        title=_gs(spec, 'title', ''),
                                        headers=_gs(spec, 'headers', []),
                                        rows=_gs(spec, 'rows', []),
                                        source=_gs(spec, 'source_note', 'составлено автором'),
                                    )
                                else:
                                    builder.add_figure(
                                        image_path=Path(path) if path else path,
                                        figure_number=spec.get("figure_number") if isinstance(spec, dict) else spec.figure_number,
                                        title=spec.get("title") if isinstance(spec, dict) else spec.title,
                                        source=spec.get("source_note", "составлено автором") if isinstance(spec, dict) else spec.source_note,
                                    )
                                used_visuals.add((kind_key, v_id))
                                if kind_key == "ГРАФИК": used_visuals.add(("РИСУНОК", v_id))
                                if kind_key == "РИСУНОК": used_visuals.add(("ГРАФИК", v_id))
                    
                    # УМНЫЙ РАЗРЫВ: убираем принудительный разрыв после каждого подраздела.
                    # Теперь только главы начинаются с новой страницы (настраивается в docx_builder).
                    pass

            # Заключение
            builder.add_section_title("ЗАКЛЮЧЕНИЕ", page_break=True)
            builder.add_text(_strip_markdown((conclusion or "Заключение в процессе подготовки...").strip()))

            # Список литературы
            builder.add_section_title("СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ", page_break=True)
            builder.add_sources_list_gost(sources)

            # Нумерация страниц
            builder.add_page_numbers()

            # Сохранение
            filename = f"Paper_{order_id}.docx"
            docx_path = output_dir / filename
            builder.save(docx_path)
            
            self._add_log(order_id, f"Документ успешно собран: {filename}")

            self._update_status(order_id, OrderStatus.COMPLETED, 100,
                                "Работа готова!")
            orders_store[order_id]["download_url"] = f"/output/{order_id}/{filename}"
            self._add_completed_step(order_id, "Документ собран и готов к скачиванию")

            return docx_path

        except Exception as e:
            import traceback
            traceback.print_exc()
            self._update_status(order_id, OrderStatus.FAILED, 0, f"Ошибка: {str(e)}")
            orders_store[order_id]["error_message"] = str(e)
            raise

    def _update_status(
        self, order_id: str, status: OrderStatus, progress: int, step: str
    ):
        """Обновить статус заказа."""
        if order_id in orders_store:
            orders_store[order_id]["status"] = status
            orders_store[order_id]["progress"] = progress
            orders_store[order_id]["current_step"] = step
            self._add_log(order_id, f"Статус: {step} ({progress}%)")
            _save_orders(orders_store)

    def _add_log(self, order_id: str, message: str):
        """Добавить запись в лог заказа."""
        if order_id in orders_store:
            if "logs" not in orders_store[order_id]:
                orders_store[order_id]["logs"] = []
            
            timestamp = datetime.now().strftime("%H:%M:%S")
            log_entry = f"[{timestamp}] {message}"
            orders_store[order_id]["logs"].append(log_entry)
            
            # Держим только последние 100 логов для экономии места
            if len(orders_store[order_id]["logs"]) > 100:
                orders_store[order_id]["logs"] = orders_store[order_id]["logs"][-100:]

    def _add_completed_step(self, order_id: str, step: str):
        """Добавить завершённый шаг (без дубликатов)."""
        if order_id in orders_store:
            steps = orders_store[order_id].get("steps_completed", [])
            if step not in steps:
                steps.append(step)
                orders_store[order_id]["steps_completed"] = steps
                _save_orders(orders_store)

    def get_progress(self, order_id: str) -> GenerationProgress | None:
        """Получить прогресс генерации."""
        order = orders_store.get(order_id)
        if not order:
            return None

        return GenerationProgress(
            order_id=order_id,
            status=order["status"],
            progress=order["progress"],
            current_step=order["current_step"],
            steps_completed=order["steps_completed"],
        )

    def list_user_orders(self, user_id: int) -> list[dict]:
        """Получить все заказы пользователя."""
        refresh_orders_store()
        user_orders = []
        for order_id, order in orders_store.items():
            if order.get("user_id") == user_id:
                user_orders.append(order)
        # Сортировка по дате (свежие сверху)
        return sorted(user_orders, key=lambda x: x.get("created_at", ""), reverse=True)


# Синглтон
pipeline = PipelineOrchestrator()


async def run_draft_generation(ctx, order_id: str):
    """Фоновая задача генерации черновика."""
    refresh_orders_store()
    from app.services.llm_service import llm_context_db, llm_context_order_id, llm_context_description
    from app.database import AsyncSessionLocal
    from app.schemas.order import OrderCreate, OrderStatus

    async with AsyncSessionLocal() as db:
        # Устанавливаем контекст для логирования расходов
        db_token = llm_context_db.set(db)
        id_token = llm_context_order_id.set(order_id)
        desc_token = llm_context_description.set("Черновик (план + источники)")
        
        try:
            stored = orders_store.get(order_id)
            if not stored: return
            raw_data = stored["data"]
            data = OrderCreate(**raw_data) if isinstance(raw_data, dict) else raw_data
            
            await pipeline.generate_draft(
                order_id=order_id,
                topic=data.topic,
                subject=data.subject,
                work_type=data.work_type,
                pages_count=data.pages_count
            )
        except GenerationCancelledError as e:
            logger.info(f"Генерация {order_id} отменена администратором.")
            pipeline._update_status(order_id, OrderStatus.CANCELLED, 0, str(e))
            if order_id in orders_store:
                orders_store[order_id]["error_message"] = str(e)
                _save_orders(orders_store)
            return
        except Exception as e:
            logger.error(f"[ERROR] Генерация черновика {order_id} провалилась: {e}", exc_info=True)
            pipeline._update_status(order_id, OrderStatus.FAILED, 0, f"Ошибка: {str(e)}")
            if order_id in orders_store:
                orders_store[order_id]["error_message"] = f"DraftError: {str(e)}"
                _save_orders(orders_store)
            
            # Уведомление об ошибке в Telegram
            email = "Неизвестно"
            work_type = "Неизвестно"
            topic = "Неизвестно"
            stored = orders_store.get(order_id)
            if stored:
                raw_data = stored.get("data", {})
                work_type = raw_data.get("work_type", "Неизвестно") if isinstance(raw_data, dict) else getattr(raw_data, "work_type", "Неизвестно")
                topic = raw_data.get("topic", "Неизвестно") if isinstance(raw_data, dict) else getattr(raw_data, "topic", "Неизвестно")
                user_id = stored.get("user_id")
                if user_id:
                    from app.models import User
                    from sqlalchemy import select
                    try:
                        user_res = await db.execute(select(User.email).where(User.id == user_id))
                        email = user_res.scalar_one_or_none() or "Неизвестно"
                    except Exception: pass
            from app.services.telegram_service import notify_order_error
            try:
                await notify_order_error(order_id, email, str(work_type), str(topic), str(e))
            except Exception as err:
                print(f"Error sending telegram error notification: {err}")
        finally:
            llm_context_db.reset(db_token)
            llm_context_order_id.reset(id_token)
            llm_context_description.reset(desc_token)


async def run_full_generation(ctx, order_id: str, confirmation_data: dict):
    """Фоновая задача полной генерации."""
    import logging
    logger = logging.getLogger("arq.worker")
    logger.info(f"DEBUG: Starting run_full_generation for {order_id}")
    refresh_orders_store()
    logger.info(f"DEBUG: Orders store refreshed")
    from app.services.llm_service import llm_context_db, llm_context_order_id, llm_context_description
    from app.database import AsyncSessionLocal
    from app.schemas.order import OrderCreate, OrderConfirm, OrderStatus
    logger.info(f"DEBUG: Imports successful")

    async with AsyncSessionLocal() as db:
        logger.info(f"DEBUG: DB session opened")
        # Устанавливаем контекст для логирования расходов
        db_token = llm_context_db.set(db)
        id_token = llm_context_order_id.set(order_id)
        desc_token = llm_context_description.set("Полная генерация (текст + графика)")
        
        # СРАЗУ очищаем старую ошибку на диске
        stored = orders_store.get(order_id)
        if stored:
            stored['error_message'] = None
            stored['status'] = OrderStatus.GENERATING_TEXT
            _save_orders(orders_store)
            logger.info(f"DEBUG: Status cleared and persisted for {order_id}")

        try:
            logger.info(f"DEBUG: Validating confirmation_data...")
            confirmation = OrderConfirm(**confirmation_data)
            logger.info(f"DEBUG: Confirmation validated")
            
            stored = orders_store.get(order_id)
            if not stored:
                logger.error(f"DEBUG: Order {order_id} not found in store!")
                return
            
            raw_data = stored["data"]
            logger.info(f"DEBUG: Raw data retrieved: {type(raw_data)}")
            data = OrderCreate(**raw_data) if isinstance(raw_data, dict) else raw_data
            logger.info(f"DEBUG: Order data validated")
            
            logger.info(f"DEBUG: Calling pipeline.generate_full...")
            await pipeline.generate_full(
                order_id=order_id,
                topic=data.topic,
                subject=data.subject,
                work_type=data.work_type,
                outline=confirmation.outline,
                sources=confirmation.sources,
                chapter_prompts=confirmation.chapter_prompts
            )
        except GenerationCancelledError as e:
            logger.info(f"Генерация {order_id} отменена администратором.")
            pipeline._update_status(order_id, OrderStatus.CANCELLED, 0, str(e))
            if order_id in orders_store:
                orders_store[order_id]["error_message"] = str(e)
                _save_orders(orders_store)
            return
        except Exception as e:
            logger.error(f"DEBUG: [ERROR] Полная генерация {order_id} провалилась: {e}", exc_info=True)
            pipeline._update_status(order_id, OrderStatus.FAILED, 0, f"Ошибка: {str(e)}")
            if order_id in orders_store:
                orders_store[order_id]["error_message"] = f"RuntimeError: {str(e)}"
                _save_orders(orders_store)
            
            # Уведомление об ошибке в Telegram
            email = "Неизвестно"
            work_type = "Неизвестно"
            topic = "Неизвестно"
            stored = orders_store.get(order_id)
            if stored:
                raw_data = stored.get("data", {})
                work_type = raw_data.get("work_type", "Неизвестно") if isinstance(raw_data, dict) else getattr(raw_data, "work_type", "Неизвестно")
                topic = raw_data.get("topic", "Неизвестно") if isinstance(raw_data, dict) else getattr(raw_data, "topic", "Неизвестно")
                user_id = stored.get("user_id")
                if user_id:
                    from app.models import User
                    from sqlalchemy import select
                    try:
                        user_res = await db.execute(select(User.email).where(User.id == user_id))
                        email = user_res.scalar_one_or_none() or "Неизвестно"
                    except Exception: pass
            from app.services.telegram_service import notify_order_error
            try:
                await notify_order_error(order_id, email, str(work_type), str(topic), str(e))
            except Exception as err:
                print(f"Error sending telegram error notification: {err}")
        finally:
            llm_context_db.reset(db_token)
            llm_context_order_id.reset(id_token)
            llm_context_description.reset(desc_token)
