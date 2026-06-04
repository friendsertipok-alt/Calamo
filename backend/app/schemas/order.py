"""
Calamo — Pydantic Schemas для заказов
"""
from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class WorkType(str, Enum):
    """Типы академических работ."""
    COURSEWORK = "курсовая"
    ESSAY = "реферат"
    DIPLOMA = "диплом"
    TEST = "контрольная"
    REPORT = "отчёт"


class OrderStatus(str, Enum):
    """Статусы заказа."""
    PENDING = "pending"
    GENERATING_OUTLINE = "generating_outline"
    GENERATING_SOURCES = "generating_sources"
    DRAFT_READY = "draft_ready"
    GENERATING_TEXT = "generating_text"
    GENERATING_CHARTS = "generating_charts"
    BUILDING_DOCX = "building_docx"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    STOPPED = "stopped"
    CANCELLED = "cancelled"


class OrderCreate(BaseModel):
    """Схема создания заказа."""
    work_type: WorkType = Field(..., description="Тип работы")
    topic: str = Field(..., min_length=5, max_length=500, description="Тема работы")
    subject: str = Field(..., min_length=2, max_length=200, description="Предмет/дисциплина")
    university: Optional[str] = Field(None, max_length=300, description="Название университета")
    
    # Дополнительные требования
    custom_outline: Optional[str] = Field(None, description="Своё содержание (если есть)")
    custom_sources: Optional[str] = Field(None, description="Свои источники (если есть)")
    additional_requirements: Optional[str] = Field(None, description="Доп. требования")
    pages_count: Optional[int] = Field(35, ge=15, le=60, description="Желаемое количество страниц")
    target_words: Optional[int] = Field(None, description="Целевое количество слов")
    figures_count: int = Field(2, ge=0, le=10, description="Количество рисунков/графиков")
    tables_count: int = Field(2, ge=0, le=13, description="Количество таблиц")

    
    # Контакты
    email: Optional[str] = Field(None, description="Email для уведомления")
    telegram: Optional[str] = Field(None, description="Telegram для уведомления")

    # Данные студента (для титульного листа)
    student_name: Optional[str] = Field(None, description="ФИО студента")
    student_group: Optional[str] = Field(None, description="Группа")
    teacher_name: Optional[str] = Field(None, description="ФИО преподавателя")
    teacher_title: Optional[str] = Field(None, description="Должность преподавателя")


class OrderResponse(BaseModel):
    """Схема ответа с информацией о заказе."""
    id: str
    work_type: WorkType
    topic: str
    subject: str
    status: OrderStatus
    progress: int = Field(0, ge=0, le=100, description="Прогресс в %")
    current_step: Optional[str] = None
    download_url: Optional[str] = None
    error_message: Optional[str] = None
    draft_outline: Optional[dict] = None
    draft_sources: Optional[list] = None
    created_at: Optional[str] = None



class GenerationProgress(BaseModel):
    """Прогресс генерации для отображения на фронте."""
    order_id: str
    status: OrderStatus
    progress: int
    current_step: str
    steps_completed: list[str] = []
    estimated_time_remaining: Optional[int] = None  # секунды


class OutlineSection(BaseModel):
    """Раздел плана работы."""
    number: str = Field(..., description="Номер раздела (1.1, 1.2, 2.1)")
    title: str = Field(..., description="Название раздела")
    subsections: list[OutlineSection] = Field(default_factory=list)
    description: Optional[str] = Field(None, description="Краткое описание содержания")


class PaperOutline(BaseModel):
    """Полный план работы."""
    title: str
    introduction: Optional[str] = Field("Введение ко всей работе", description="Краткое описание введения")
    chapters: list[OutlineSection]
    conclusion: Optional[str] = Field("Заключение ко всей работе", description="Краткое описание заключения")


class SourceItem(BaseModel):
    """Источник литературы."""
    number: Optional[int] = None
    type: Optional[str] = Field("book", description="Тип: law, book, article")
    title: str = Field(..., description="Полная библиографическая запись по ГОСТу")
    citation: Optional[str] = Field(None, description="Дубликат для фронтенда")
    url: Optional[str] = Field(None, description="URL если есть")
    year: Optional[int] = Field(None, description="Год издания")
    pages_total: Optional[int] = Field(None, description="Общее количество страниц")
    relevance: Optional[str] = Field(None, description="Обоснование выбора источника")

    def model_post_init(self, __context) -> None:
        """Синхронизируем title и citation после инициализации."""
        if self.title and not self.citation:
            self.citation = self.title
        elif self.citation and not self.title:
            self.title = self.citation


class ChartSpec(BaseModel):
    """Спецификация для генерации графика."""
    chart_type: str = Field(..., description="Тип: bar, line, pie, scatter, hbar")
    title: str = Field(..., description="Название рисунка")
    data: dict = Field(..., description="Данные для графика")
    x_label: Optional[str] = None
    y_label: Optional[str] = None
    figure_number: int = Field(..., description="Номер рисунка")
    source_note: Optional[str] = Field("Источник: составлено автором.", description="Ссылка на источник (например: Источник: [3])")


class TableSpec(BaseModel):
    """Спецификация для генерации таблицы."""
    title: str = Field(..., description="Название таблицы")
    headers: list[str] = Field(..., description="Заголовки столбцов")
    rows: list[list[str]] = Field(..., description="Данные строк")
    table_number: int = Field(..., description="Номер таблицы")
    source_note: Optional[str] = Field("Источник: составлено автором.", description="Ссылка на источник (например: Источник: [3])")



class DiagramSpec(BaseModel):
    """Спецификация для генерации диаграммы."""
    diagram_type: str = Field(..., description="Тип: flowchart, structure, hierarchy")
    title: str = Field(..., description="Название рисунка")
    nodes: list[dict] = Field(..., description="Узлы диаграммы")
    edges: list[dict] = Field(..., description="Связи между узлами")
    figure_number: int = Field(..., description="Номер рисунка")


class OrderConfirm(BaseModel):
    """Схема подтверждения черновика."""
    outline: PaperOutline
    sources: list[SourceItem]
    chapter_prompts: Optional[dict[str, str]] = Field(default_factory=dict, description="Индивидуальные инструкции для глав")


class VisualPlanItem(BaseModel):
    """Элемент плана визуализации для конкретного раздела."""
    section_number: str
    visual_type: str = Field(..., description="TABLE or CHART")
    topic: str = Field(..., description="Конкретная тема для визуализации в этом разделе")


class VisualPlan(BaseModel):
    """Полный план распределения визуалов по работе."""
    items: list[VisualPlanItem]
