"""
Calamo — Chart Generator
Генерация академических графиков через matplotlib.
"""
import io
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # Без GUI

import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

from app.schemas.order import ChartSpec


# Настройка шрифтов для кириллицы
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["DejaVu Sans", "Arial", "Helvetica"]
plt.rcParams["axes.unicode_minus"] = False

# Академическая цветовая палитра
COLORS = [
    "#2E5090",  # тёмно-синий
    "#C0392B",  # тёмно-красный
    "#27AE60",  # зелёный
    "#F39C12",  # оранжевый
    "#8E44AD",  # фиолетовый
    "#16A085",  # бирюзовый
    "#D35400",  # тёмно-оранжевый
    "#2C3E50",  # графитовый
]


class ChartGenerator:
    """Генератор академических графиков."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_chart(self, spec: ChartSpec) -> Path:
        """Сгенерировать график по спецификации и сохранить как PNG."""
        method_map = {
            "bar": self._bar_chart,
            "line": self._line_chart,
            "pie": self._pie_chart,
            "hbar": self._hbar_chart,
            "scatter": self._scatter_chart,
            "area": self._area_chart,
            "donut": self._donut_chart,
        }

        generator = method_map.get(spec.chart_type, self._bar_chart)
        fig = generator(spec)

        filepath = self.output_dir / f"figure_{spec.figure_number}.png"
        fig.savefig(str(filepath), dpi=200, bbox_inches="tight", facecolor="white")
        plt.close(fig)

        return filepath

    def generate_all(self, specs: list[ChartSpec]) -> list[Path]:
        """Сгенерировать все графики."""
        return [self.generate_chart(spec) for spec in specs]

    def _setup_figure(self, figsize: tuple[float, float] = (8, 5)) -> tuple:
        """Базовая настройка фигуры в академическом стиле."""
        fig, ax = plt.subplots(figsize=figsize)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.tick_params(labelsize=10)
        return fig, ax

    def _clean_val(self, val):
        """Пытается превратить значение в число (float)."""
        try:
            if isinstance(val, str):
                # Убираем пробелы, проценты и меняем запятую на точку
                val = val.replace(" ", "").replace("%", "").replace(",", ".")
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    def _get_data(self, spec: ChartSpec) -> tuple[list, list]:
        """Унифицированное извлечение меток и значений."""
        data = spec.data
        labels = data.get("labels", [])
        # Проверяем также x_values/y_values для обратной совместимости
        if not labels:
            labels = data.get("x_values", [])
            
        values = data.get("values", [])
        if not values:
            values = data.get("y_values", [])
            
        # Принудительная очистка
        clean_labels = [str(l) for l in labels]
        clean_values = [self._clean_val(v) for v in values]
        
        # Выравнивание длины (защита от ошибок LLM)
        min_len = min(len(clean_labels), len(clean_values))
        return clean_labels[:min_len], clean_values[:min_len]

    def _bar_chart(self, spec: ChartSpec) -> plt.Figure:
        """Столбчатая диаграмма."""
        fig, ax = self._setup_figure()
        labels, values = self._get_data(spec)

        if not values:
            ax.text(0.5, 0.5, "Нет данных для отображения", ha="center", va="center")
            return fig

        import textwrap
        wrapped_labels = [textwrap.fill(str(l), width=12) for l in labels]
        y_pos = range(len(labels))

        bars = ax.bar(y_pos, values, color=COLORS[0], edgecolor="white", width=0.6)
        
        ax.set_xticks(y_pos)
        ax.set_xticklabels(wrapped_labels, rotation=15 if len(labels) <= 4 else 45, ha='right', fontsize=9)

        # Значения над столбцами
        max_v = max(values) if values else 1
        for bar, val in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max_v * 0.02,
                f"{val:g}",
                ha="center",
                va="bottom",
                fontsize=9,
            )

        ax.set_xticks(y_pos)
        ax.set_xticklabels(wrapped_labels, rotation=15 if len(labels) <= 4 else 45, ha="right", fontsize=9)
        
        if spec.x_label:
            ax.set_xlabel(spec.x_label, fontsize=11)
        if spec.y_label:
            ax.set_ylabel(spec.y_label, fontsize=11)

        ax.grid(axis="y", alpha=0.3)
        fig.tight_layout()
        return fig

    def _line_chart(self, spec: ChartSpec) -> plt.Figure:
        """Линейный график."""
        fig, ax = self._setup_figure()
        labels, values = self._get_data(spec)

        if not values:
            ax.text(0.5, 0.5, "Нет данных для отображения", ha="center", va="center")
            return fig

        import textwrap
        wrapped_labels = [textwrap.fill(str(l), width=12) for l in labels]
        y_pos = range(len(labels))

        ax.plot(
            y_pos, values,
            color=COLORS[0],
            linewidth=2,
            marker="o",
            markersize=6,
            markerfacecolor="white",
            markeredgecolor=COLORS[0],
            markeredgewidth=2,
        )

        # Значения у точек
        for i, (x, y) in enumerate(zip(y_pos, values)):
            ax.annotate(
                f"{y:g}",
                (x, y),
                textcoords="offset points",
                xytext=(0, 10),
                ha="center",
                fontsize=8,
            )

        ax.set_xticks(y_pos)
        ax.set_xticklabels(wrapped_labels, rotation=15 if len(labels) <= 4 else 45, ha="right", fontsize=9)
        
        if spec.x_label:
            ax.set_xlabel(spec.x_label, fontsize=11)
        if spec.y_label:
            ax.set_ylabel(spec.y_label, fontsize=11)

        ax.grid(alpha=0.3)
        fig.tight_layout()
        return fig

    def _pie_chart(self, spec: ChartSpec) -> plt.Figure:
        """Круговая диаграмма."""
        fig, ax = self._setup_figure(figsize=(7, 7))
        labels, values = self._get_data(spec)

        if not values:
            ax.text(0.5, 0.5, "Нет данных для отображения", ha="center", va="center")
            return fig

        colors = COLORS[:len(values)]

        wedges, texts, autotexts = ax.pie(
            values,
            labels=labels,
            colors=colors,
            autopct="%1.1f%%",
            startangle=90,
            pctdistance=0.85,
            textprops={"fontsize": 10},
        )

        for autotext in autotexts:
            autotext.set_fontsize(9)
            autotext.set_color("white")
            autotext.set_fontweight("bold")

        ax.set_aspect("equal")
        fig.tight_layout()
        return fig

    def _hbar_chart(self, spec: ChartSpec) -> plt.Figure:
        """Горизонтальная столбчатая диаграмма."""
        fig, ax = self._setup_figure()
        labels, values = self._get_data(spec)

        if not values:
            ax.text(0.5, 0.5, "Нет данных для отображения", ha="center", va="center")
            return fig

        import textwrap
        wrapped_labels = [textwrap.fill(str(l), width=15) for l in labels]

        y_pos = range(len(labels))
        bars = ax.barh(y_pos, values, color=COLORS[0], edgecolor="white", height=0.5)
        ax.set_yticks(y_pos)
        ax.set_yticklabels(wrapped_labels, fontsize=9)

        max_v = max(values) if values else 1
        for bar, val in zip(bars, values):
            ax.text(
                bar.get_width() + max_v * 0.02,
                bar.get_y() + bar.get_height() / 2,
                f"{val:g}",
                va="center",
                fontsize=9,
            )
        
        if spec.x_label:
            ax.set_xlabel(spec.x_label, fontsize=11)

        ax.grid(axis="x", alpha=0.3)
        fig.tight_layout()
        return fig

    def _scatter_chart(self, spec: ChartSpec) -> plt.Figure:
        """Точечная диаграмма (Карта позиционирования)."""
        fig, ax = self._setup_figure()
        data = spec.data
        
        # Проверяем наличие расширенного формата (x_values, y_values, labels)
        x_vals = data.get("x_values") or data.get("values_x")
        y_vals = data.get("y_values") or data.get("values_y") or data.get("values")
        labels = data.get("labels") or data.get("names")
        
        if x_vals and y_vals and labels and len(x_vals) == len(y_vals) == len(labels):
            # Режим "Карта": X и Y - числа, labels - подписи к точкам
            x_clean = [self._clean_val(x) for x in x_vals]
            y_clean = [self._clean_val(y) for y in y_vals]
            
            ax.scatter(x_clean, y_clean, color=COLORS[0], s=80, alpha=0.7, edgecolors="white")
            
            # Добавляем подписи к точкам
            for i, txt in enumerate(labels):
                ax.annotate(
                    str(txt), 
                    (x_clean[i], y_clean[i]),
                    xytext=(5, 5), 
                    textcoords='offset points',
                    fontsize=9,
                    fontweight='bold'
                )
        else:
            # Старый режим (категории по X, значения по Y)
            labels, values = self._get_data(spec)
            if not values:
                ax.text(0.5, 0.5, "Нет данных для отображения", ha="center", va="center")
                return fig
            ax.scatter(labels, values, color=COLORS[0], s=60, alpha=0.7, edgecolors="white")

        if spec.x_label:
            ax.set_xlabel(spec.x_label, fontsize=11)
        if spec.y_label:
            ax.set_ylabel(spec.y_label, fontsize=11)

        ax.grid(alpha=0.3)
        fig.tight_layout()
        return fig


    def _area_chart(self, spec: ChartSpec) -> plt.Figure:
        """Диаграмма с областями (Area Chart)."""
        fig, ax = self._setup_figure()
        labels, values = self._get_data(spec)

        if not values:
            ax.text(0.5, 0.5, "Нет данных для отображения", ha="center", va="center")
            return fig

        import textwrap
        wrapped_labels = [textwrap.fill(str(l), width=12) for l in labels]
        y_pos = range(len(labels))

        ax.fill_between(y_pos, values, color=COLORS[0], alpha=0.4)
        ax.plot(y_pos, values, color=COLORS[0], linewidth=2, marker='o')

        ax.set_xticks(y_pos)
        ax.set_xticklabels(wrapped_labels, rotation=15 if len(labels) <= 4 else 45, ha="right", fontsize=9)

        if spec.x_label:
            ax.set_xlabel(spec.x_label, fontsize=11)
        if spec.y_label:
            ax.set_ylabel(spec.y_label, fontsize=11)

        ax.grid(alpha=0.3)
        fig.tight_layout()
        return fig

    def _donut_chart(self, spec: ChartSpec) -> plt.Figure:
        """Кольцевая диаграмма (Donut Chart)."""
        fig, ax = self._setup_figure(figsize=(7, 7))
        labels, values = self._get_data(spec)

        if not values:
            ax.text(0.5, 0.5, "Нет данных для отображения", ha="center", va="center")
            return fig

        colors = COLORS[:len(values)]
        
        wedges, texts, autotexts = ax.pie(
            values,
            labels=labels,
            colors=colors,
            autopct="%1.1f%%",
            startangle=90,
            pctdistance=0.75,
            textprops={"fontsize": 10},
            wedgeprops=dict(width=0.4, edgecolor='white')
        )

        for autotext in autotexts:
            autotext.set_fontsize(9)
            autotext.set_color("white")
            autotext.set_fontweight("bold")

        ax.set_aspect("equal")
        fig.tight_layout()
        return fig


# Фабрика
def create_chart_generator(output_dir: Path) -> ChartGenerator:
    return ChartGenerator(output_dir)
