import httpx
import logging
from app.config import settings

logger = logging.getLogger("telegram_client")


async def indicar_escribiendo(chat_id: int) -> None:
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendChatAction"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(url, json={"chat_id": chat_id, "action": "typing"})
            if resp.status_code != 200:
                logger.warning(f"Falló indicar_escribiendo: {resp.text}")
        except Exception as e:
            logger.warning(f"Excepción en indicar_escribiendo: {e}")


async def enviar_mensaje(chat_id: int, texto: str) -> None:
    """Reintenta en texto plano si el Markdown rompe el parser de Telegram."""
    if len(texto) > 4096:
        texto = texto[:4090] + "…"

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, json={"chat_id": chat_id, "text": texto, "parse_mode": "Markdown"})
        if resp.status_code == 200:
            return
        logger.warning(f"Falló envío con Markdown, reintentando en texto plano: {resp.text}")
        await client.post(url, json={"chat_id": chat_id, "text": texto})


async def enviar_mensaje_con_botones(chat_id: int, texto: str, botones: list[list[dict]]) -> int | None:
    """botones: filas de botones inline, ej.
    [[{"text": "Abrir link", "url": "https://..."}],
     [{"text": "Cancelar", "callback_data": "cancelar:123"}]]
    Devuelve el message_id (para poder editarlo después) o None si falló."""
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": texto, "reply_markup": {"inline_keyboard": botones}}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code == 200:
            return resp.json().get("result", {}).get("message_id")
        logger.warning(f"Falló enviar_mensaje_con_botones: {resp.text}")
        return None


async def responder_callback(callback_query_id: str, texto: str | None = None) -> None:
    """Obligatorio llamarlo tras cualquier botón inline presionado —
    si no, Telegram deja el reloj de carga girando en el botón del
    usuario indefinidamente."""
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if texto:
        payload["text"] = texto
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(url, json=payload)


async def editar_mensaje(chat_id: int, message_id: int, texto: str) -> None:
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/editMessageText"
    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.post(url, json={"chat_id": chat_id, "message_id": message_id, "text": texto})
