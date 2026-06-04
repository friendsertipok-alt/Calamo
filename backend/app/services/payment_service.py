import hmac
import hashlib
import json
import uuid
import logging
import httpx
from datetime import datetime
from app.config import settings

logger = logging.getLogger("PaymentService")

class PaymentService:
    def __init__(self):
        self.api_key = settings.ROLLYPAY_API_KEY
        self.secret_key = settings.ROLLYPAY_SECRET_KEY
        self.base_url = settings.ROLLYPAY_BASE_URL

    async def create_checkout_session(self, order_id: str, amount: float, description: str) -> dict:
        """Создает платеж в RollyPay и возвращает URL для оплаты."""
        path = "/payments"
        request_id = str(uuid.uuid4())
        
        payload = {
            "amount": f"{amount:.2f}",
            "payment_currency": "RUB",
            "order_id": order_id,
            "description": description
        }
        
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
            "X-Nonce": request_id
        }
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}{path}",
                    json=payload,
                    headers=headers,
                    timeout=30.0
                )
                
                if response.status_code not in (200, 201):
                    logger.error(f"RollyPay Error: {response.status_code} - {response.text}")
                    return {"error": f"Bank error: {response.status_code}"}
                
                data = response.json()
                # RollyPay возвращает pay_url
                return {
                    "payment_url": data.get("pay_url"),
                    "external_id": data.get("payment_id") or data.get("id")
                }
            except Exception as e:
                logger.error(f"RollyPay request failed: {e}")
                return {"error": str(e)}

    def verify_webhook(self, raw_body: bytes, signature: str) -> bool:
        """
        Проверка подписи входящего вебхука X-Signature.
        Обычно это HMAC-SHA256(SecretKey, raw_body)
        """
        if not signature:
            logger.warning("No X-Signature header provided in webhook")
            return False
            
        expected_signature = hmac.new(
            self.secret_key.encode('utf-8'),
            raw_body,
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(expected_signature, signature):
            logger.warning(f"Signature mismatch! Expected: {expected_signature}, Got: {signature}")
            return False
            
        return True

payment_service = PaymentService()
