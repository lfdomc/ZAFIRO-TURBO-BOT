"""
Orquesta el flujo de reportes de mantenimiento/limpieza: crea el
reporte en Supabase, manda el mensaje con botones a Telegram, programa
el envío automático por WhatsApp si nadie actúa a tiempo, y permite
cancelarlo desde el botón "Ya avisé".

El estado real vive en Supabase (tabla reportes_incidencias), no solo
en memoria — así, si Railway reinicia el proceso mientras un reporte
está esperando su ventana de tiempo, `rehidratar_reportes_pendientes()`
(llamado al arrancar) lo recupera en vez de perderlo en silencio.
"""
import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import quote

from app import supabase_client, telegram_client, whatsapp_client
from app.config import settings

logger = logging.getLogger("reportes")

# Tareas de espera en curso, por si hace falta cancelarlas cuando se
# aprieta "Ya avisé" en el mismo proceso que las programó.
_tareas_programadas: dict[str, asyncio.Task] = {}


def _saludo_por_hora() -> str:
    from datetime import datetime, timezone, timedelta
    hora_cr = datetime.now(timezone(timedelta(hours=-6))).hour  # Costa Rica no usa horario de verano
    if hora_cr < 12:
        return "Buenos días"
    if hora_cr < 19:
        return "Buenas tardes"
    return "Buenas noches"


def _texto_reporte(tipo: str, propiedad: str, unidad: str, detalle: str) -> str:
    lugar = f"{propiedad} ({unidad})" if unidad else propiedad
    return f"*Reporte de {tipo}*\n\n{_saludo_por_hora()}, nos reportan de {lugar}:\n{detalle}"


def _link_wa_me(numero: str, texto: str) -> str:
    numero_limpio = numero.lstrip("+").replace(" ", "").replace("-", "")
    return f"https://wa.me/{numero_limpio}?text={quote(texto)}"


def _whatsapp_api_configurada() -> bool:
    return bool(settings.WHATSAPP_PHONE_NUMBER_ID and settings.WHATSAPP_ACCESS_TOKEN)


async def crear_y_programar_reporte(
    chat_id: int, tipo: str, property_id: str | None, nombre_propiedad: str | None,
    detalle: str, numero_destino: str | None, unit_id: str | None = None,
) -> None:
    minutos = settings.AUTO_ENVIO_WHATSAPP_MINUTOS
    report_id = await supabase_client.crear_reporte(
        chat_id, tipo, property_id, nombre_propiedad, detalle, numero_destino, minutos, unit_id
    )
    if not report_id:
        await telegram_client.enviar_mensaje(chat_id, "⚠️ Detecté un posible reporte pero no lo pude registrar (error de base de datos).")
        return

    # Seguimiento de recurrencia — se arma el texto ahora pero se manda
    # MÁS ABAJO, combinado con el mensaje de destino (envío automático,
    # WhatsApp, o falta de número/chat configurado), para no llenar el
    # chat de mensajes sueltos.
    nota_seguimiento = ""
    if property_id:
        DIAS_VENTANA = 7
        cantidad = await supabase_client.contar_reportes_recientes(property_id, tipo, DIAS_VENTANA, unit_id)
        if cantidad >= 2:
            nota_seguimiento = (
                f"\n\n📋 Seguimiento (interno): este es el {cantidad}° reporte de *{tipo}* en "
                f"{nombre_propiedad or 'esta propiedad'} en los últimos {DIAS_VENTANA} días — "
                f"vale la pena revisar si es un problema recurrente."
            )

    # Si esa unidad (o la propiedad completa) tiene un grupo de Telegram
    # configurado, el reporte se manda ahí mismo, de una — sin botón, sin
    # esperar nada. Esto reemplaza al flujo de WhatsApp solo para esa
    # unidad/propiedad puntual; el resto sigue funcionando como antes.
    chat_destino = await supabase_client.obtener_chat_telegram_reportes(property_id, unit_id, tipo)
    if chat_destino:
        texto_grupo = _texto_reporte(tipo, nombre_propiedad or "propiedad sin identificar", "", detalle)
        try:
            await telegram_client.enviar_mensaje(int(chat_destino), texto_grupo)
            await supabase_client.actualizar_estado_reporte(report_id, "enviado")
            await telegram_client.enviar_mensaje(chat_id, f"✅ Reporte de *{tipo}* enviado automáticamente al grupo de Telegram de {nombre_propiedad or 'esta propiedad'}.{nota_seguimiento}")
        except Exception as e:
            logger.warning(f"No se pudo enviar el reporte {report_id} al grupo de Telegram {chat_destino}: {e}")
            await supabase_client.actualizar_estado_reporte(report_id, "error_envio")
            await telegram_client.enviar_mensaje(chat_id, f"⚠️ Intenté mandar el reporte al grupo de Telegram configurado y falló — revisá que el bot siga siendo miembro de ese chat.{nota_seguimiento}")
        return

    if not numero_destino:
        await telegram_client.enviar_mensaje(
            chat_id,
            f"⚠️ Parece un reporte de *{tipo}* para {nombre_propiedad or 'esta propiedad'}, pero no hay número de "
            f"WhatsApp de {tipo} configurado ahí. Agregalo en Admin → Configuración general (o como campo "
            f"personalizado 'whatsapp_{tipo}' en esa propiedad si necesita uno distinto).{nota_seguimiento}"
        )
        return

    texto_wa = _texto_reporte(tipo, nombre_propiedad or "propiedad sin identificar", "", detalle)
    link = _link_wa_me(numero_destino, texto_wa)

    if not _whatsapp_api_configurada():
        # El envío automático (Meta Cloud API) todavía no está activado —
        # no prometemos una cuenta regresiva que no va a pasar. Solo el
        # botón para reenviarlo a mano, como ya hacías antes.
        await supabase_client.actualizar_estado_reporte(report_id, "manual")
        botones = [[{"text": "📲 Enviar yo por WhatsApp", "url": link}]]
        texto_telegram = f"🔧 Reporte de *{tipo}* en {nombre_propiedad or 'una propiedad'}\n\n{detalle}{nota_seguimiento}"
        await telegram_client.enviar_mensaje_con_botones(chat_id, texto_telegram, botones)
        return

    botones = [
        [{"text": "📲 Enviar yo por WhatsApp", "url": link}],
        [{"text": "✅ Ya avisé / no hace falta", "callback_data": f"cancelar_reporte:{report_id}"}],
    ]
    texto_telegram = (
        f"🔧 Reporte de *{tipo}* en {nombre_propiedad or 'una propiedad'}\n\n{detalle}\n\n"
        f"Si nadie lo envía en {minutos} min, lo mando yo automáticamente por WhatsApp.{nota_seguimiento}"
    )
    await telegram_client.enviar_mensaje_con_botones(chat_id, texto_telegram, botones)

    tarea = asyncio.create_task(_esperar_y_enviar(report_id, minutos * 60))
    _tareas_programadas[report_id] = tarea


async def _esperar_y_enviar(report_id: str, segundos_espera: float):
    try:
        await asyncio.sleep(segundos_espera)
    except asyncio.CancelledError:
        return
    await _procesar_reporte_pendiente(report_id)


async def _procesar_reporte_pendiente(report_id: str):
    if not _whatsapp_api_configurada():
        # Defensivo: si quedó algún reporte viejo en 'pendiente' de antes
        # de desactivar esto, no reintentes ni avises de nuevo — solo
        # archivalo en silencio.
        await supabase_client.actualizar_estado_reporte(report_id, "cancelado")
        return

    reporte = await supabase_client.obtener_reporte(report_id)
    if not reporte or reporte["estado"] != "pendiente":
        return  # ya fue cancelado, ya se envió, o nunca tuvo destino

    ok = await whatsapp_client.enviar_reporte(
        reporte["numero_destino"], reporte["tipo"], reporte.get("nombre_propiedad") or "", "", reporte["detalle"],
    )
    await supabase_client.actualizar_estado_reporte(report_id, "enviado" if ok else "error_envio")

    if reporte.get("telegram_chat_id"):
        texto = (
            f"📤 Envié automáticamente el reporte de {reporte['tipo']} por WhatsApp (nadie lo hizo a tiempo)."
            if ok else
            f"⚠️ Intenté enviar automáticamente el reporte de {reporte['tipo']} por WhatsApp y falló "
            f"(revisá WHATSAPP_ACCESS_TOKEN/plantilla en Railway). Avisá a mano por ahora."
        )
        try:
            await telegram_client.enviar_mensaje(int(reporte["telegram_chat_id"]), texto)
        except Exception as e:
            logger.warning(f"No se pudo notificar el resultado del reporte {report_id} en Telegram: {e}")

    _tareas_programadas.pop(report_id, None)


async def cancelar_reporte(report_id: str) -> bool:
    reporte = await supabase_client.obtener_reporte(report_id)
    if not reporte or reporte["estado"] != "pendiente":
        return False
    await supabase_client.actualizar_estado_reporte(report_id, "cancelado")
    tarea = _tareas_programadas.pop(report_id, None)
    if tarea:
        tarea.cancel()
    return True


async def rehidratar_reportes_pendientes():
    """Se llama una vez al arrancar el proceso (ver main.py). Sin esto,
    un reinicio de Railway mientras un reporte esperaba su ventana de
    tiempo lo dejaría pendiente para siempre en Supabase, sin que nadie
    lo mande nunca."""
    if not _whatsapp_api_configurada():
        # El envío automático está desactivado — no tiene sentido reintentar
        # reportes viejos (fallarían igual y volverían a avisar por error).
        return

    try:
        pendientes = await supabase_client.obtener_reportes_pendientes()
    except Exception as e:
        logger.error(f"No se pudieron leer los reportes pendientes al arrancar: {e}")
        return

    ahora = datetime.now(timezone.utc)
    for r in pendientes:
        try:
            enviar_en = datetime.fromisoformat(r["enviar_en"].replace("Z", "+00:00"))
        except Exception:
            continue
        restante = (enviar_en - ahora).total_seconds()
        if restante <= 0:
            asyncio.create_task(_procesar_reporte_pendiente(r["id"]))
        else:
            _tareas_programadas[r["id"]] = asyncio.create_task(_esperar_y_enviar(r["id"], restante))

    if pendientes:
        logger.info(f"Rehidratados {len(pendientes)} reporte(s) pendiente(s) tras (re)inicio.")
