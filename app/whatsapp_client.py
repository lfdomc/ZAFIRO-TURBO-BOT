"""
Cliente de WhatsApp Cloud API (Meta) — envía la plantilla de reporte de
mantenimiento/limpieza. Una plantilla es exactamente lo que hace falta
acá: un mensaje de una sola vía (no una conversación), que Meta permite
enviar sin que el destinatario haya escrito primero.
"""
import httpx
import logging
from app.config import settings

logger = logging.getLogger("whatsapp_client")

GRAPH_API_VERSION = "v21.0"


async def enviar_reporte(numero_destino: str, tipo: str, propiedad: str, unidad: str, detalle: str) -> bool:
    if not settings.WHATSAPP_PHONE_NUMBER_ID or not settings.WHATSAPP_ACCESS_TOKEN:
        logger.error("WhatsApp Cloud API no configurada (faltan WHATSAPP_PHONE_NUMBER_ID / WHATSAPP_ACCESS_TOKEN).")
        return False

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": numero_destino.lstrip("+").replace(" ", ""),
        "type": "template",
        "template": {
            "name": settings.WHATSAPP_TEMPLATE_NAME,
            "language": {"code": settings.WHATSAPP_TEMPLATE_LANG},
            "components": [{
                "type": "body",
                "parameters": [
                    {"type": "text", "text": tipo.capitalize()},
                    {"type": "text", "text": propiedad or "N/D"},
                    {"type": "text", "text": unidad or "N/D"},
                    {"type": "text", "text": (detalle or "")[:1000]},
                ],
            }],
        },
    }
    headers = {"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code == 200:
            return True
        logger.error(f"Error enviando reporte por WhatsApp: HTTP {resp.status_code}: {resp.text}")
        return False
