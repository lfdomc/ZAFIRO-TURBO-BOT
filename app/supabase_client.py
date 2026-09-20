"""
Cliente de Supabase (REST + RPC) usando httpx async.

Diseño: `properties.datos` guarda el JSON completo y verbatim de cada
propiedad (fuente de verdad para el sitio); `knowledge_chunks` es un
índice derivado, solo para que el bot busque por significado.
"""
import httpx
import logging
from datetime import datetime, timedelta, timezone
from app.config import settings

logger = logging.getLogger("supabase_client")


def _headers() -> dict:
    return {
        "apikey": settings.SUPABASE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def _base_url() -> str:
    return settings.SUPABASE_URL.rstrip("/")


async def buscar_contexto_semantico(
    pregunta: str,
    embedding: list[float],
    match_count: int = 5,
    audiencia: str = "admin",
    property_id: str | None = None,
) -> list[dict]:
    """Llama a busqueda_hibrida_propiedades (texto + vector combinados
    con RRF) — corre en milisegundos gracias a los índices gin/ivfflat,
    sin escanear toda la tabla."""
    url = f"{_base_url()}/rest/v1/rpc/busqueda_hibrida_propiedades"
    payload = {
        "query_text": pregunta,
        "query_embedding": embedding,
        "match_count": match_count,
        "rrf_k": 60,
        "p_audiencia": audiencia,
        "p_property_id": property_id,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, json=payload, headers=_headers())
        if resp.status_code == 200:
            return resp.json()
        logger.error(f"Error en busqueda_hibrida_propiedades: HTTP {resp.status_code}: {resp.text}")
        return []


# ------------------------------------------------------------
# Ingesta — usado por scripts/ingest_propiedades.py
# ------------------------------------------------------------

async def upsert_property(property_id: str, nombre: str, grupo: str | None, zona: str | None,
                           propietario: str | None, datos: dict) -> bool:
    """Upsert por id (mismo slug que ya usa el JSON) — reingestar el
    mismo archivo nunca duplica filas."""
    url = f"{_base_url()}/rest/v1/properties"
    payload = {
        "id": property_id, "nombre": nombre, "grupo": grupo, "zona": zona,
        "propietario": propietario, "datos": datos,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            url, json=payload,
            headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"}
        )
        if resp.status_code in (200, 201, 204):
            return True
        logger.error(f"Error en upsert_property({property_id}): HTTP {resp.status_code}: {resp.text}")
        return False


async def upsert_configuracion_general(clave: str, valor) -> bool:
    url = f"{_base_url()}/rest/v1/configuracion_general"
    payload = {"clave": clave, "valor": valor}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            url, json=payload,
            headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"}
        )
        if resp.status_code in (200, 201, 204):
            return True
        logger.error(f"Error en upsert_configuracion_general({clave}): HTTP {resp.status_code}: {resp.text}")
        return False


async def borrar_chunks_de_property(property_id: str | None) -> None:
    """property_id=None borra los fragmentos globales (info de
    compañía, no atada a una propiedad). Se llama antes de reingestar
    para no acumular fragmentos viejos duplicados."""
    if property_id is None:
        url = f"{_base_url()}/rest/v1/knowledge_chunks?property_id=is.null"
    else:
        url = f"{_base_url()}/rest/v1/knowledge_chunks?property_id=eq.{property_id}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.delete(url, headers={**_headers(), "Prefer": "return=minimal"})
        if resp.status_code not in (200, 204):
            logger.error(f"Error borrando chunks previos de {property_id}: HTTP {resp.status_code}: {resp.text}")


async def insertar_chunk(
    contenido: str,
    embedding: list[float],
    categoria: str,
    property_id: str | None = None,
    unit_id: str | None = None,
    idioma: str = "es",
    audiencia: str = "admin",
    metadata: dict | None = None,
) -> bool:
    url = f"{_base_url()}/rest/v1/knowledge_chunks"
    payload = [{
        "property_id": property_id,
        "unit_id": unit_id,
        "categoria": categoria,
        "contenido": contenido,
        "embedding": embedding,
        "idioma": idioma,
        "audiencia": audiencia,
        "metadata": metadata or {},
    }]
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            url, json=payload,
            headers={**_headers(), "Prefer": "return=minimal"}
        )
        if resp.status_code in (201, 204):
            return True
        logger.error(f"Error insertando chunk: HTTP {resp.status_code}: {resp.text}")
        return False


# ------------------------------------------------------------
# CRUD de propiedades — usado por el panel Admin (app/admin.py)
# ------------------------------------------------------------

async def listar_propiedades_con_datos() -> list[dict]:
    """Como listar_propiedades_resumen, pero incluye `datos` completo —
    lo usa el panel Admin para calcular qué falta cargar en cada una."""
    url = f"{_base_url()}/rest/v1/properties?select=id,nombre,zona,actualizado_en,datos&order=nombre.asc"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=_headers())
        if resp.status_code == 200:
            return resp.json()
        logger.error(f"Error listando propiedades con datos: HTTP {resp.status_code}: {resp.text}")
        return []


async def listar_propiedades_resumen() -> list[dict]:
    url = f"{_base_url()}/rest/v1/properties?select=id,nombre,zona,actualizado_en&order=nombre.asc"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=_headers())
        if resp.status_code == 200:
            return resp.json()
        logger.error(f"Error listando propiedades: HTTP {resp.status_code}: {resp.text}")
        return []


async def obtener_property(property_id: str) -> dict | None:
    url = f"{_base_url()}/rest/v1/properties?id=eq.{property_id}&select=datos"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=_headers())
        if resp.status_code == 200 and resp.json():
            return resp.json()[0]["datos"]
        return None


async def eliminar_property(property_id: str) -> bool:
    """Los fragmentos de knowledge_chunks se borran solos (FK ON DELETE
    CASCADE) — no hace falta borrarlos aparte."""
    url = f"{_base_url()}/rest/v1/properties?id=eq.{property_id}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.delete(url, headers={**_headers(), "Prefer": "return=minimal"})
        if resp.status_code in (200, 204):
            return True
        logger.error(f"Error eliminando propiedad {property_id}: HTTP {resp.status_code}: {resp.text}")
        return False


# ------------------------------------------------------------
# Campos personalizados — definiciones globales (panel Admin)
# ------------------------------------------------------------

async def listar_campos_personalizados() -> list[dict]:
    url = f"{_base_url()}/rest/v1/custom_field_defs?select=id,etiqueta,tipo,nivel&order=creado_en.asc"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=_headers())
        if resp.status_code == 200:
            return resp.json()
        return []


async def crear_campo_personalizado(field_id: str, etiqueta: str, tipo: str = "texto", nivel: str = "propiedad") -> bool:
    url = f"{_base_url()}/rest/v1/custom_field_defs"
    payload = {"id": field_id, "etiqueta": etiqueta, "tipo": tipo, "nivel": nivel}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            url, json=payload,
            headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"}
        )
        if resp.status_code in (200, 201, 204):
            return True
        logger.error(f"Error creando campo personalizado {field_id}: HTTP {resp.status_code}: {resp.text}")
        return False


async def eliminar_campo_personalizado(field_id: str) -> bool:
    url = f"{_base_url()}/rest/v1/custom_field_defs?id=eq.{field_id}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.delete(url, headers={**_headers(), "Prefer": "return=minimal"})
        return resp.status_code in (200, 204)


# ------------------------------------------------------------
# Reportes de mantenimiento/limpieza (ver app/reportes.py)
# ------------------------------------------------------------

async def obtener_config(clave: str):
    url = f"{_base_url()}/rest/v1/configuracion_general?clave=eq.{clave}&select=valor"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=_headers())
        if resp.status_code == 200 and resp.json():
            return resp.json()[0]["valor"]
        return None


async def obtener_numero_whatsapp(property_id: str | None, tipo: str) -> str | None:
    """Orden de prioridad: 1) campo personalizado de esa propiedad
    puntual (excepción, si algún día hace falta), 2) número guardado
    en Supabase desde el panel Admin (Configuración general — el caso
    normal), 3) variable de entorno en Railway (respaldo, útil antes
    de configurarlo por primera vez desde el panel)."""
    if property_id:
        datos = await obtener_property(property_id)
        if datos:
            campos = datos.get("camposPersonalizados") or {}
            numero_propio = campos.get(f"whatsapp_{tipo}")
            if numero_propio and numero_propio.strip():
                return numero_propio.strip()

    numero_config = await obtener_config(f"whatsapp_{tipo}_default")
    if numero_config:
        return numero_config

    if tipo == "mantenimiento":
        return settings.WHATSAPP_MANTENIMIENTO_DEFAULT or None
    if tipo == "limpieza":
        return settings.WHATSAPP_LIMPIEZA_DEFAULT or None
    return None


async def crear_reporte(
    telegram_chat_id: str, tipo: str, property_id: str | None, nombre_propiedad: str | None,
    detalle: str, numero_destino: str | None, minutos_espera: int,
) -> str | None:
    enviar_en = (datetime.now(timezone.utc) + timedelta(minutes=minutos_espera)).isoformat()
    url = f"{_base_url()}/rest/v1/reportes_incidencias"
    payload = {
        "telegram_chat_id": str(telegram_chat_id),
        "tipo": tipo,
        "property_id": property_id,
        "nombre_propiedad": nombre_propiedad,
        "detalle": detalle,
        "numero_destino": numero_destino,
        "estado": "pendiente" if numero_destino else "sin_destino",
        "enviar_en": enviar_en,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, json=payload, headers={**_headers(), "Prefer": "return=representation"})
        if resp.status_code == 201:
            return resp.json()[0]["id"]
        logger.error(f"Error creando reporte: HTTP {resp.status_code}: {resp.text}")
        return None


async def obtener_reporte(report_id: str) -> dict | None:
    url = f"{_base_url()}/rest/v1/reportes_incidencias?id=eq.{report_id}&select=*"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=_headers())
        if resp.status_code == 200 and resp.json():
            return resp.json()[0]
        return None


async def actualizar_estado_reporte(report_id: str, estado: str) -> None:
    url = f"{_base_url()}/rest/v1/reportes_incidencias?id=eq.{report_id}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.patch(url, json={"estado": estado}, headers={**_headers(), "Prefer": "return=minimal"})


async def obtener_reportes_pendientes() -> list[dict]:
    """Usado al arrancar el proceso, para no perder reportes que
    quedaron esperando si Railway se reinició."""
    url = f"{_base_url()}/rest/v1/reportes_incidencias?estado=eq.pendiente&select=*"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=_headers())
        if resp.status_code == 200:
            return resp.json()
        return []


# ------------------------------------------------------------
# Accesos temporales para huéspedes (ver app/public.py)
# ------------------------------------------------------------

async def crear_acceso_temporal(property_id: str, horas: int, unit_id: str | None = None) -> str | None:
    import secrets
    token = secrets.token_urlsafe(18)
    expira_en = (datetime.now(timezone.utc) + timedelta(hours=horas)).isoformat()
    url = f"{_base_url()}/rest/v1/accesos_temporales"
    payload = {"token": token, "property_id": property_id, "unit_id": unit_id, "expira_en": expira_en}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, json=payload, headers={**_headers(), "Prefer": "return=minimal"})
        if resp.status_code in (200, 201, 204):
            return token
        logger.error(f"Error creando acceso temporal para {property_id}: HTTP {resp.status_code}: {resp.text}")
        return None


async def obtener_acceso_temporal(token: str) -> dict | None:
    url = f"{_base_url()}/rest/v1/accesos_temporales?token=eq.{token}&select=*"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=_headers())
        if resp.status_code == 200 and resp.json():
            return resp.json()[0]
        return None


async def registrar_uso_acceso_temporal(token: str, usos_actuales: int) -> None:
    url = f"{_base_url()}/rest/v1/accesos_temporales?token=eq.{token}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.patch(url, json={"usos": usos_actuales + 1}, headers={**_headers(), "Prefer": "return=minimal"})


# ------------------------------------------------------------
# Guía externa sincronizada (ver app/fuente_externa.py) — se guarda
# aparte de los campos curados a mano, nunca los pisa.
# ------------------------------------------------------------

async def guardar_guia_externa(property_id: str, guia_data: dict) -> bool:
    prop = await obtener_property(property_id)
    if not prop:
        return False
    prop["guiaExterna"] = {**guia_data, "sincronizado_en": datetime.now(timezone.utc).isoformat()}
    return await upsert_property(
        property_id, prop.get("name", property_id), prop.get("group"),
        prop.get("zone"), prop.get("owner"), prop,
    )


async def borrar_historial_antiguo(dias: int) -> None:
    """Borra turnos de historial más viejos que `dias` — se llama al
    arrancar el proceso (ver main.py), así la tabla nunca crece sin
    límite. Es un no-op barato si no hay nada tan viejo todavía."""
    limite = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    url = f"{_base_url()}/rest/v1/historial_conversacion?creado_en=lt.{limite}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.delete(url, headers={**_headers(), "Prefer": "return=minimal"})
        if resp.status_code not in (200, 204):
            logger.error(f"Error limpiando historial antiguo: HTTP {resp.status_code}: {resp.text}")


# ------------------------------------------------------------
# Historial de conversación
# ------------------------------------------------------------

async def obtener_historial_conversacion(telegram_id: str, property_id: str | None, limite: int = 6) -> list[dict]:
    """Solo trae turnos anteriores de la MISMA propiedad detectada (o
    turnos igual de 'sin propiedad detectada' si property_id es None)
    — evita que el contexto de una casa se filtre a la respuesta de
    otra cuando el admin salta de un tema a otro en el mismo chat."""
    filtro_propiedad = f"property_id.eq.{property_id}" if property_id else "property_id.is.null"
    url = (
        f"{_base_url()}/rest/v1/historial_conversacion"
        f"?telegram_id=eq.{telegram_id}&{filtro_propiedad}&order=creado_en.desc&limit={limite}"
    )
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=_headers())
        if resp.status_code == 200:
            filas = resp.json()
            filas.reverse()
            return [
                {"role": f["role"], "parts": [{"text": f["contenido"]}]}
                for f in filas
            ]
        return []


async def guardar_mensaje_historial(telegram_id: str, rol: str, contenido: str, property_id: str | None = None) -> None:
    url = f"{_base_url()}/rest/v1/historial_conversacion"
    payload = {"telegram_id": telegram_id, "role": rol, "contenido": contenido, "property_id": property_id}
    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.post(url, json=payload, headers={**_headers(), "Prefer": "return=minimal"})


# ------------------------------------------------------------
# Export estático — usado por scripts/generar_json_estatico.py
# ------------------------------------------------------------

async def obtener_todo_para_export() -> dict:
    """Reconstruye exactamente la forma original del propiedades.json:
    {checkInGeneral, checkOutGeneral, general, masterTable, properties:[...]}
    — properties.datos ya es el objeto completo verbatim, así que el
    sitio en React recibe lo mismo que recibía antes, sin cambios.

    Si Supabase todavía no tiene nada cargado (antes de la primera
    importación), esto devuelve una forma "vacía pero segura" — nunca
    None/undefined en los campos que el sitio siempre espera poder leer
    (general.mensajesFrecuentes, general.contactos, etc.) — así el sitio
    no se cae mientras no haya datos, solo se ve vacío."""
    GENERAL_VACIO = {
        "formulario": {"texto": "", "link": "", "linkLabel": ""},
        "comunicacion": None,
        "reservaDirecta": None,
        "mensajesFrecuentes": [],
        "contactos": [],
        "faqs": [],
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp_config = await client.get(
            f"{_base_url()}/rest/v1/configuracion_general?select=clave,valor",
            headers=_headers()
        )
        resp_props = await client.get(
            f"{_base_url()}/rest/v1/properties?select=datos&order=id.asc",
            headers=_headers()
        )
        if resp_config.status_code != 200 or resp_props.status_code != 200:
            logger.error("Error exportando datos para el JSON estático")
            return {}

        config = {fila["clave"]: fila["valor"] for fila in resp_config.json()}
        propiedades = [fila["datos"] for fila in resp_props.json()]

        resultado = {
            "checkInGeneral": config.get("checkInGeneral") or "",
            "checkOutGeneral": config.get("checkOutGeneral") or "",
            "general": config.get("general") or GENERAL_VACIO,
            "masterTable": config.get("masterTable") or [],
            "properties": propiedades,
        }
        return resultado
