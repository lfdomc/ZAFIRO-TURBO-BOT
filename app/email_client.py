"""
Envío de correos vía Resend (resend.com) — hoy solo se usa para el
informe mensual. Requiere RESEND_API_KEY en Railway; RESEND_FROM es
opcional (por defecto usa el dominio de pruebas de Resend, que
funciona sin verificar un dominio propio, aunque a veces termina en
spam — para producción de verdad conviene verificar el dominio propio
en Resend y configurar RESEND_FROM con ese dominio).
"""
import httpx
import logging

from app.config import settings

logger = logging.getLogger(__name__)


async def enviar_email(destinatarios: list[str], asunto: str, html: str) -> bool:
    if not settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY no configurada — no se pudo enviar el correo.")
        return False
    if not destinatarios:
        logger.warning("Sin destinatarios — no se envió ningún correo.")
        return False

    payload = {"from": settings.RESEND_FROM, "to": destinatarios, "subject": asunto, "html": html}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            json=payload,
            headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}", "Content-Type": "application/json"},
        )
        if resp.status_code not in (200, 201, 202):
            logger.error(f"Error enviando correo con Resend: HTTP {resp.status_code}: {resp.text}")
            return False
        return True
