"""
Calamo — Diagram Generator
Генерация простых блок-схем и иерархий через matplotlib.
"""
import matplotlib.pyplot as plt
from pathlib import Path
import logging
import math

logger = logging.getLogger(__name__)

class DiagramGenerator:
    """Генератор академических диаграмм."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_diagram(self, spec: dict) -> Path:
        """Сгенерировать диаграмму по спецификации."""
        title = spec.get("title", "Диаграмма")
        nodes = spec.get("nodes", [])
        edges = spec.get("edges", [])
        fig_num = spec.get("figure_number", 1)

        fig, ax = plt.subplots(figsize=(10, 6))
        ax.set_title(title.upper(), fontsize=12, fontweight='bold', pad=20)
        ax.axis('off')

        # Простая логика размещения узлов (уровневая для иерархии)
        # Для MVP просто разместим их по кругу или сетке, если нет координат
        num_nodes = len(nodes)
        node_positions = {}
        
        # Если это иерархия, попробуем примитивное дерево
        if spec.get("diagram_type") == "hierarchy":
            # root на самом верху, остальные ниже
            node_positions[nodes[0]["id"]] = (0.5, 0.9)
            for i, node in enumerate(nodes[1:], 1):
                x = (i / num_nodes) + (0.5 / num_nodes)
                node_positions[node["id"]] = (x, 0.5)
        else:
            # Сетка
            import math
            cols = math.ceil(math.sqrt(num_nodes))
            for i, node in enumerate(nodes):
                r = i // cols
                c = i % cols
                node_positions[node["id"]] = (0.2 + c * 0.6 / cols, 0.8 - r * 0.6 / cols)

        # Рисуем связи (стрелки)
        for edge in edges:
            start_pos = node_positions.get(edge["from"])
            end_pos = node_positions.get(edge["to"])
            if start_pos and end_pos:
                ax.annotate("", 
                            xy=end_pos, xycoords='data',
                            xytext=start_pos, textcoords='data',
                            arrowprops=dict(arrowstyle="->", color="#2C3E50", lw=1.5))

        # Рисуем узлы (блоки с текстом)
        for node in nodes:
            pos = node_positions.get(node["id"])
            ax.text(pos[0], pos[1], node["label"], 
                    ha="center", va="center",
                    bbox=dict(boxstyle="round,pad=0.5", facecolor='#ECF0F1', edgecolor='#2C3E50', lw=1.5),
                    fontsize=10)

        filepath = self.output_dir / f"diagram_{fig_num}.png"
        fig.savefig(str(filepath), dpi=200, bbox_inches="tight")
        plt.close(fig)
        return filepath

def create_diagram_generator(output_dir: Path) -> DiagramGenerator:
    return DiagramGenerator(output_dir)
