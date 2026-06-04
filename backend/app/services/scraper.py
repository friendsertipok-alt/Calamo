import httpx
from bs4 import BeautifulSoup
import logging
import asyncio

logger = logging.getLogger(__name__)

import socket
from ipaddress import ip_address

def is_internal_ip(hostname: str) -> bool:
    """Проверяет, разрешается ли хост во внутренний/приватный IP для защиты от SSRF."""
    try:
        ip = socket.gethostbyname(hostname)
        addr = ip_address(ip)
        return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
    except:
        return True # По умолчанию блокируем при ошибках резолва

async def fetch_and_extract_text(client: httpx.AsyncClient, url: str) -> str:
    """Загружает страницу и извлекает очищенный текст (защищено от SSRF)."""
    if not url:
        return ""
    
    try:
        parsed = urlparse(url)
        if is_internal_ip(parsed.hostname):
            logger.warning(f"SSRF Attempt blocked: {url}")
            return ""
            
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        res = await client.get(url, headers=headers, timeout=10.0, follow_redirects=True)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            # Удаляем мусор
            for element in soup(["script", "style", "nav", "header", "footer", "aside"]):
                element.extract()
            text = soup.get_text(separator=' ', strip=True)
            return text[:2500]
    except Exception as e:
        logger.warning(f"Ошибка чтения содержимого {url}: {e}")
    return ""

from urllib.parse import urlparse

async def gather_sources_content(urls: list[str]) -> str:
    """Асинхронно скачивает контент по всем URL и объединяет в единый текст (защищено)."""
    valid_urls = []
    for u in urls:
        if not u or not u.startswith("http"):
            continue
        try:
            parsed = urlparse(u)
            if not is_internal_ip(parsed.hostname):
                valid_urls.append(u)
        except: continue

    if not valid_urls:
        return ""
    
    async with httpx.AsyncClient(verify=True) as client:
        tasks = [fetch_and_extract_text(client, url) for url in valid_urls]
        results = await asyncio.gather(*tasks)
        
    combined = []
    for url, text in zip(valid_urls, results):
        if len(text) > 200:
            combined.append(f"--- ДАННЫЕ ИЗ ИСТОЧНИКА ({url}) ---\n{text}...\n")
            
    return "\n".join(combined)

