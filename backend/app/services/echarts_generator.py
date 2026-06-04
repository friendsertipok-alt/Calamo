"""
Calamo — ECharts Generator (Premium)
Генерация современных графиков через ECharts + Playwright.
"""
import json
import asyncio
import logging
from pathlib import Path
from typing import Optional
import base64

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

from app.schemas.order import ChartSpec

logger = logging.getLogger(__name__)

# Глобальный семафор для ограничения количества одновременно работающих браузеров (защита от OOM)
# На 1 ГБ RAM допускается строго 1 процесс Chromium одновременно.
_chromium_semaphore = asyncio.Semaphore(1)

class EChartsGenerator:
    """Генератор премиальных графиков через ECharts."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._browser_context = None

    async def generate_chart(self, spec: ChartSpec) -> Path:
        """Сгенерировать график через ECharts и сохранить как PNG."""
        if not PLAYWRIGHT_AVAILABLE:
            logger.error("Playwright not installed. Falling back to Matplotlib logic should be handled by caller.")
            raise ImportError("Playwright is required for ECharts engine")

        filepath = self.output_dir / f"figure_{spec.figure_number}.png"
        
        # Подготовка данных для ECharts
        chart_config = self._get_echarts_config(spec)
        
        # HTML шаблон с ECharts
        html_content = f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="utf-8">
            <meta http-equiv="Content-Type" content="text/html; charset=utf-8">
            <link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;700&display=swap" rel="stylesheet">
            <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
            <style>
                body {{ 
                    margin: 0; 
                    padding: 0; 
                    background: white; 
                    display: flex; 
                    justify-content: center; 
                    align-items: center; 
                    height: 100vh;
                    font-family: 'Roboto', 'Arial', sans-serif;
                }}
                #chart {{ 
                    width: 900px; 
                    height: 600px; 
                }}
            </style>
        </head>
        <body>
            <div id="chart"></div>
            <script>
                window.onload = function() {{
                    document.fonts.ready.then(function() {{
                        var chart = echarts.init(document.getElementById('chart'), null, {{
                            devicePixelRatio: 2
                        }});
                        var option = {json.dumps(chart_config, ensure_ascii=False)};
                        chart.setOption(option);
                        // Даем время на финальную отрисовку анимаций
                        setTimeout(function() {{
                            window.chartReady = true;
                        }}, 500);
                    }});
                }};
            </script>
        </body>
        </html>
        """

        try:
            b64_html = base64.b64encode(html_content.encode('utf-8')).decode('utf-8')
            data_url = f"data:text/html;charset=utf-8;base64,{b64_html}"

            # Захватываем семафор: если другой график уже рисуется, ждем очереди
            async with _chromium_semaphore:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    page = await browser.new_page(viewport={"width": 1000, "height": 700})
                    
                    # Загружаем через Data URL для гарантии кодировки
                    await page.goto(data_url)
                    
                    # Ждем, пока всё загрузится и отрисуется
                    await page.wait_for_function("window.chartReady === true", timeout=10000)
                    await asyncio.sleep(0.5) 
                    
                    # Скриншот именно элемента графика
                    chart_element = await page.query_selector("#chart")
                    await chart_element.screenshot(path=str(filepath))
                    await browser.close()
                
            return filepath
        except Exception as e:
            logger.error(f"ECharts generation failed: {e}")
            raise

    def _get_echarts_config(self, spec: ChartSpec) -> dict:
        """Преобразует ChartSpec в конфигурацию ECharts."""
        data = spec.data
        labels = data.get("labels", [])
        values = data.get("values", [])
        
        # Академическая палитра Calamo (с градиентами)
        colors = [
            "#2E5090", "#C0392B", "#27AE60", "#F39C12", 
            "#8E44AD", "#16A085", "#D35400", "#2C3E50"
        ]

        # Проверка на наличие данных
        data = spec.data
        # Извлечение всех возможных серий данных
        series_data = []
        x_vals = data.get("x_values", [])
        y_vals = data.get("y_values", [])
        
        # 1. Проверка на вложенные массивы в values
        if isinstance(values, list) and len(values) > 0 and isinstance(values[0], list):
            for i, v_list in enumerate(values):
                series_data.append({
                    "name": f"Показатель {i+1}",
                    "data": v_list
                })
        # 2. Проверка на values1, values2...
        elif any(f"values{i}" in data for i in range(1, 5)):
            for i in range(1, 6):
                v_key = f"values{i}"
                if v_key in data:
                    name_key = f"name{i}" or f"label{i}"
                    series_data.append({
                        "name": data.get(name_key, f"Показатель {i}"),
                        "data": data[v_key]
                    })
        # 3. Обычный одиночный values
        elif values:
            series_data.append({
                "name": spec.y_label or "Значение",
                "data": values
            })

        # Если данных нет вообще (для scatter проверяем x_vals и y_vals)
        has_data = (labels and series_data) or (x_vals and y_vals)
        
        if not has_data:
            return {
                "title": {
                    "text": spec.title,
                    "left": "center",
                    "top": "middle",
                    "textStyle": { "color": "#999", "fontSize": 16 }
                },
                "graphic": [{
                    "type": "text",
                    "left": "center",
                    "top": "60%",
                    "style": {
                        "text": "Недостаточно данных для построения визуализации",
                        "fill": "#ccc",
                        "fontSize": 14
                    }
                }]
            }

        base_config = {
            "backgroundColor": "transparent",
            "title": {
                "show": False
            },
            "tooltip": { "trigger": "axis" },
            "grid": {
                "left": "5%",
                "right": "5%",
                "bottom": "10%",
                "top": "30",
                "containLabel": True
            }
        }

        if spec.chart_type == "bar":
            series_list = []
            for i, s in enumerate(series_data):
                series_list.append({
                    "name": s["name"],
                    "data": s["data"],
                    "type": "bar",
                    "itemStyle": {
                        "color": colors[i % len(colors)],
                        "borderRadius": [4, 4, 0, 0]
                    },
                    "label": { "show": True, "position": "top", "fontSize": 10 }
                })
            
            base_config.update({
                "legend": { "show": len(series_list) > 1, "bottom": "2%", "left": "center" },
                "xAxis": { "type": "category", "data": labels, "name": spec.x_label or "", "axisLabel": { "interval": 0, "rotate": 15 } },
                "yAxis": { "type": "value", "name": spec.y_label or "" },
                "series": series_list
            })
        elif spec.chart_type == "line":
            series_list = []
            for i, s in enumerate(series_data):
                series_list.append({
                    "name": s["name"],
                    "data": s["data"],
                    "type": "line",
                    "smooth": True,
                    "lineStyle": { "width": 4, "color": colors[i % len(colors)] },
                    "symbol": "circle",
                    "symbolSize": 8,
                    "itemStyle": { "color": colors[i % len(colors)], "borderWidth": 2, "borderColor": "#fff" },
                    "label": { "show": True, "position": "top", "fontSize": 10, "formatter": "{c}" }
                })

            base_config.update({
                "legend": { "show": len(series_list) > 1, "bottom": "2%", "left": "center" },
                "xAxis": { "type": "category", "data": labels, "name": spec.x_label or "" },
                "yAxis": { "type": "value", "name": spec.y_label or "" },
                "series": series_list
            })
        elif spec.chart_type == "area":
            base_config.update({
                "xAxis": { "type": "category", "boundaryGap": False, "data": labels, "name": spec.x_label or "" },
                "yAxis": { "type": "value", "name": spec.y_label or "" },
                "series": [{
                    "data": values,
                    "type": "line",
                    "smooth": True,
                    "symbol": "circle",
                    "symbolSize": 8,
                    "label": { "show": True, "position": "top", "fontSize": 10, "formatter": "{c}" },
                    "areaStyle": {
                        "color": {
                            "type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1,
                            "colorStops": [
                                { "offset": 0, "color": "rgba(255, 123, 0, 0.4)" },
                                { "offset": 1, "color": "rgba(255, 123, 0, 0)" }
                            ]
                        }
                    },
                    "lineStyle": { "width": 3, "color": "#ff7b00" },
                    "itemStyle": { "color": "#ff7b00" }
                }]
            })
        elif spec.chart_type == "pie" or spec.chart_type == "donut":
            pie_data = [{"name": l, "value": v} for l, v in zip(labels, values)]
            is_donut = spec.chart_type == "donut"
            base_config.update({
                "legend": {
                    "orient": "horizontal",
                    "bottom": "5%",
                    "left": "center",
                    "textStyle": { "fontSize": 12 }
                },
                "series": [{
                    "type": "pie",
                    "radius": ["35%", "60%"] if is_donut else "60%",
                    "center": ["50%", "50%"],
                    "avoidLabelOverlap": True,
                    "itemStyle": {
                        "borderRadius": 6,
                        "borderColor": "#fff",
                        "borderWidth": 2
                    },
                    "label": {
                        "show": True,
                        "position": "outside",
                        "formatter": "{b}: {d}%",
                        "fontSize": 12
                    },
                    "labelLine": {
                        "show": True,
                        "length": 15,
                        "length2": 10
                    },
                    "emphasis": {
                        "label": { "show": True, "fontSize": 16, "fontWeight": "bold" }
                    },
                    "data": pie_data,
                    "color": colors
                }]
            })
        elif spec.chart_type == "hbar":
             base_config.update({
                "yAxis": { "type": "category", "data": labels, "name": spec.y_label or "" },
                "xAxis": { "type": "value", "name": spec.x_label or "" },
                "series": [{
                    "data": values,
                    "type": "bar",
                    "itemStyle": {
                        "color": {
                            "type": "linear", "x": 0, "y": 0, "x2": 1, "y2": 0,
                            "colorStops": [
                                { "offset": 0, "color": "#ff7b00" },
                                { "offset": 1, "color": "#ffa726" }
                            ]
                        },
                        "borderRadius": [0, 4, 4, 0]
                    },
                    "label": { "show": True, "position": "right" }
                }]
            })
        elif spec.chart_type == "scatter":
            x_vals = data.get("x_values", [])
            y_vals = data.get("y_values", [])
            labels = data.get("labels", [])
            scatter_data = [[x, y, l] for x, y, l in zip(x_vals, y_vals, labels)]
            
            base_config.update({
                "xAxis": { 
                    "type": "value",
                    "name": spec.x_label or "Показатель X",
                    "splitLine": { "lineStyle": { "type": "dashed" } },
                    "axisLine": { "onZero": True, "lineStyle": { "color": "#999" } }
                },
                "yAxis": { 
                    "type": "value",
                    "name": spec.y_label or "Показатель Y",
                    "splitLine": { "lineStyle": { "type": "dashed" } },
                    "axisLine": { "onZero": True, "lineStyle": { "color": "#999" } }
                },
                "series": [{
                    "data": scatter_data,
                    "type": "scatter",
                    "symbolSize": 25,
                    "label": {
                        "show": True,
                        "formatter": "{@[2]}",
                        "position": "top",
                        "fontSize": 11,
                        "fontWeight": "bold",
                        "backgroundColor": "rgba(255,255,255,0.7)",
                        "padding": [2, 4],
                        "borderRadius": 3
                    },
                    "itemStyle": {
                        "color": colors[0],
                        "shadowBlur": 10,
                        "shadowColor": "rgba(0, 0, 0, 0.3)"
                    }
                }]
            })

        return base_config

def create_echarts_generator(output_dir: Path) -> EChartsGenerator:
    return EChartsGenerator(output_dir)
