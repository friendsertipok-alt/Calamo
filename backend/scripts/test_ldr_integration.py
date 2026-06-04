import asyncio
import sys
import os

# Добавляем путь к backend, чтобы импорты работали
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.services.ldr_service import ldr_service

async def main():
    import logging
    from app.services import ldr_service as ldr_module
    print(f"DEBUG: ldr_service file: {ldr_module.__file__}", flush=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    print("=== ТЕСТ ИНТЕГРАЦИИ CALAMO + LDR ===")
    topic = "Учёт и аудит дебиторской задолженности в ПАО «Газпром» за 2023-2024 гг."
    print(f"Тема поиска: {topic}")
    
    if not ldr_service.enabled:
        print("ОШИБКА: LDR Service не активен. Проверьте пути и наличие GEMINI_API_KEY.")
        return

    print("Запуск исследования... Это может занять 1-2 минуты.")
    sources = await ldr_service.get_real_sources(topic, count=3)
    
    if not sources:
        print("Источники не найдены.")
        return

    print(f"\nНайдено источников: {len(sources)}")
    for i, s in enumerate(sources, 1):
        print(f"\n--- Источник №{i} ---")
        print(f"Название: {s['title']}")
        print(f"Ссылка: {s['url']}")
        print(f"Сниппет: {s['snippet'][:200]}...")
    
    print("\n=== ТЕСТ ЗАВЕРШЕН ===")

if __name__ == "__main__":
    asyncio.run(main())
