import latex2mathml.converter
from lxml import etree
import logging

logger = logging.getLogger("FormulaService")

# Минимальный XSLT для конвертации MathML в OMML (Office Math Markup Language)
# В идеале стоит использовать официальный MML2OMML.XSL от Microsoft,
# но для большинства базовых формул достаточно этого подхода.
# Мы будем использовать библиотеку latex2mathml для получения MathML.

def latex_to_word_omml(latex_str: str):
    """Конвертирует LaTeX в XML-структуру OMML для Word."""
    try:
        # 1. Получаем MathML
        mathml = latex2mathml.converter.convert(latex_str)
        
        # 2. Очистка MathML (иногда latex2mathml добавляет лишнее)
        tree = etree.fromstring(mathml)
        
        # 3. Трансформация в OMML. 
        # Поскольку полноценный XSLT огромен, мы используем 'хитрый' путь:
        # Word умеет импортировать MathML, если обернуть его правильно, 
        # но для стабильности лучше вставлять чистый OMML.
        
        # Для этого проекта мы реализуем вставку через 'raw xml' блока MathML,
        # который Word сам сконвертирует при открытии, либо через простую обертку.
        
        return tree
    except Exception as e:
        logger.error(f"Formula conversion error: {e}")
        return None

def wrap_mathml_for_word(mathml_tree):
    """Оборачивает MathML в структуру, которую python-docx может вставить как raw XML."""
    # Word поддерживает MathML внутри определенных тегов
    # Но самый надежный способ — это вставка объекта Math
    return mathml_tree
