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


async def _mapa_etiquetas_unidad() -> dict:
    """Arma {(property_id, unit_id): 'Nombre legible'} — combina
    propiedad + unidad (ej. 'Urban Escalante — 1411 (Vistas de
    Volcanes)') solo cuando la propiedad tiene varias unidades; si
    tiene una sola, el nombre de la propiedad ya alcanza."""
    filas = await listar_propiedades_con_datos()
    mapa = {}
    for f in filas:
        pid = f["id"]
        datos = f.get("datos") or {}
        nombre_prop = datos.get("name", f["nombre"])
        unidades = datos.get("units", []) or []
        mapa[(pid, None)] = nombre_prop
        for u in unidades:
            uid = u.get("id")
            nombre_u = u.get("name") or uid
            mapa[(pid, uid)] = f"{nombre_prop} — {nombre_u}" if len(unidades) > 1 else nombre_prop
    return mapa


def _color_confianza(pct_alta: float) -> str:
    if pct_alta >= 70:
        return "verde"
    if pct_alta >= 40:
        return "amarillo"
    return "rojo"


def _color_sentimiento(pct_no_negativo: float) -> str:
    if pct_no_negativo >= 80:
        return "verde"
    if pct_no_negativo >= 60:
        return "amarillo"
    return "rojo"


async def generar_informe_mensual(anio: int, mes: int) -> dict:
    """Lee historial_archivo del mes pedido (solo filas role='user', que
    son las que llevan tipo_consulta/sentimiento/confianza) y arma los
    indicadores agregados: cuántas consultas de cada tipo, de cada
    sentimiento, de cada nivel de confianza del bot, desglosado por
    propiedad Y por unidad puntual (ej. 'Urban 1411' en vez de solo
    'Urban Escalante'), más el FCR (First Contact Resolution) de
    reportes de mantenimiento/limpieza — la base del informe mensual."""
    desde = f"{anio:04d}-{mes:02d}-01T00:00:00Z"
    if mes == 12:
        hasta = f"{anio + 1:04d}-01-01T00:00:00Z"
    else:
        hasta = f"{anio:04d}-{mes + 1:02d}-01T00:00:00Z"

    url = (
        f"{_base_url()}/rest/v1/historial_archivo"
        f"?creado_en=gte.{desde}&creado_en=lt.{hasta}&role=eq.user"
        f"&select=property_id,unit_id,tipo_consulta,sentimiento,confianza,contenido"
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers())
        if resp.status_code != 200:
            logger.error(f"Error generando informe mensual: HTTP {resp.status_code}: {resp.text}")
            return {
                "anio": anio, "mes": mes, "total_consultas": 0, "por_tipo": {}, "por_sentimiento": {},
                "por_confianza": {}, "por_propiedad": {}, "fcr": None,
                "confianza_baja_por_propiedad": {}, "sentimiento_negativo_por_propiedad": {},
                "ejemplos_baja_confianza": [], "rendimiento_por_unidad": [],
            }
        filas = resp.json()

    propiedades = {p["id"]: p["nombre"] for p in await listar_propiedades_resumen()}
    etiquetas_unidad = await _mapa_etiquetas_unidad()

    por_tipo, por_sentimiento, por_confianza, por_propiedad = {}, {}, {}, {}
    confianza_baja_por_propiedad, sentimiento_negativo_por_propiedad = {}, {}
    ejemplos_baja_confianza = []
    unidades_datos: dict[tuple, dict] = {}
    for f in filas:
        t = f.get("tipo_consulta") or "sin_clasificar"
        s = f.get("sentimiento") or "sin_clasificar"
        c = f.get("confianza") or "sin_clasificar"
        pid = f.get("property_id")
        uid = f.get("unit_id")
        nombre_prop = propiedades.get(pid, pid) or "Sin propiedad identificada"

        por_tipo[t] = por_tipo.get(t, 0) + 1
        por_sentimiento[s] = por_sentimiento.get(s, 0) + 1
        por_confianza[c] = por_confianza.get(c, 0) + 1
        por_propiedad.setdefault(nombre_prop, {})
        por_propiedad[nombre_prop][t] = por_propiedad[nombre_prop].get(t, 0) + 1

        if c == "baja":
            confianza_baja_por_propiedad[nombre_prop] = confianza_baja_por_propiedad.get(nombre_prop, 0) + 1
            if len(ejemplos_baja_confianza) < 10:
                ejemplos_baja_confianza.append({"propiedad": nombre_prop, "mensaje": (f.get("contenido") or "")[:200]})
        if s == "negativo":
            sentimiento_negativo_por_propiedad[nombre_prop] = sentimiento_negativo_por_propiedad.get(nombre_prop, 0) + 1

        clave_unidad = (pid, uid)
        registro = unidades_datos.setdefault(clave_unidad, {"total": 0, "confianza": {}, "sentimiento": {}})
        registro["total"] += 1
        registro["confianza"][c] = registro["confianza"].get(c, 0) + 1
        registro["sentimiento"][s] = registro["sentimiento"].get(s, 0) + 1

    rendimiento_por_unidad = []
    for (pid, uid), datos_u in unidades_datos.items():
        total_u = datos_u["total"]
        alta = datos_u["confianza"].get("alta", 0)
        negativo = datos_u["sentimiento"].get("negativo", 0)
        pct_confianza = round(alta / total_u * 100) if total_u else 0
        pct_sentimiento = round((total_u - negativo) / total_u * 100) if total_u else 0
        etiqueta = etiquetas_unidad.get((pid, uid)) or propiedades.get(pid, pid) or "Sin propiedad identificada"
        rendimiento_por_unidad.append({
            "unidad": etiqueta, "total_consultas": total_u,
            "confianza_pct": pct_confianza, "confianza_color": _color_confianza(pct_confianza),
            "sentimiento_pct": pct_sentimiento, "sentimiento_color": _color_sentimiento(pct_sentimiento),
        })
    rendimiento_por_unidad.sort(key=lambda r: r["total_consultas"], reverse=True)

    fcr = await _calcular_fcr_mes(anio, mes)

    return {
        "anio": anio, "mes": mes, "total_consultas": len(filas),
        "por_tipo": por_tipo, "por_sentimiento": por_sentimiento, "por_confianza": por_confianza,
        "por_propiedad": por_propiedad, "fcr": fcr,
        "confianza_baja_por_propiedad": confianza_baja_por_propiedad,
        "sentimiento_negativo_por_propiedad": sentimiento_negativo_por_propiedad,
        "ejemplos_baja_confianza": ejemplos_baja_confianza,
        "rendimiento_por_unidad": rendimiento_por_unidad,
    }


async def _calcular_fcr_mes(anio: int, mes: int) -> dict | None:
    """Aproxima el First Contact Resolution (un estándar internacional
    de atención al cliente): de los reportes de mantenimiento/limpieza
    del mes, qué % NO tuvo un reporte de seguimiento del mismo tipo en
    la misma propiedad dentro de los 7 días siguientes — una proxy de
    'se resolvió a la primera', ya que hoy no hay un botón de marcar
    un reporte como resuelto."""
    desde_dt = datetime.fromisoformat(f"{anio:04d}-{mes:02d}-01T00:00:00+00:00")
    hasta_dt = datetime(anio + 1, 1, 1, tzinfo=timezone.utc) if mes == 12 else datetime(anio, mes + 1, 1, tzinfo=timezone.utc)
    # Margen de 7 días después del mes, para detectar seguimientos que
    # caen justo después del cierre de mes.
    hasta_con_margen = hasta_dt + timedelta(days=7)

    url = (
        f"{_base_url()}/rest/v1/reportes_incidencias"
        f"?creado_en=gte.{desde_dt.isoformat()}&creado_en=lt.{hasta_con_margen.isoformat()}"
        f"&select=property_id,tipo,creado_en&order=creado_en.asc"
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=_headers())
        if resp.status_code != 200:
            logger.error(f"Error calculando FCR: HTTP {resp.status_code}: {resp.text}")
            return None
        filas = resp.json()

    grupos: dict[tuple, list] = {}
    for f in filas:
        clave = (f.get("property_id"), f.get("tipo"))
        grupos.setdefault(clave, []).append(datetime.fromisoformat(f["creado_en"].replace("Z", "+00:00")))

    total_del_mes = 0
    resueltos_primera_vez = 0
    for fechas in grupos.values():
        fechas.sort()
        for fecha in fechas:
            if not (desde_dt <= fecha < hasta_dt):
                continue
            total_del_mes += 1
            tiene_seguimiento = any(timedelta(0) < (f2 - fecha) <= timedelta(days=7) for f2 in fechas)
            if not tiene_seguimiento:
                resueltos_primera_vez += 1

    if total_del_mes == 0:
        return {"total_reportes": 0, "resueltos_primera_vez": 0, "fcr_pct": None}
    return {
        "total_reportes": total_del_mes,
        "resueltos_primera_vez": resueltos_primera_vez,
        "fcr_pct": round(resueltos_primera_vez / total_del_mes * 100),
    }


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


async def contar_reportes_recientes(property_id: str, tipo: str, dias: int) -> int:
    """Cuenta reportes del mismo tipo en la misma propiedad, en los
    últimos `dias` — para detectar problemas recurrentes (ej. el mismo
    aire acondicionado fallando varias veces en la semana)."""
    if not property_id:
        return 0
    desde = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    url = (
        f"{_base_url()}/rest/v1/reportes_incidencias"
        f"?property_id=eq.{property_id}&tipo=eq.{tipo}&creado_en=gte.{desde}&select=id"
    )
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=_headers())
        if resp.status_code == 200:
            return len(resp.json())
        logger.error(f"Error contando reportes recientes: HTTP {resp.status_code}: {resp.text}")
        return 0


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
    """Borra del historial 'caliente' lo más viejo que `dias` — ya no
    hace falta archivar antes, porque cada turno ya se guardó en
    historial_archivo al momento de crearse (ver
    guardar_mensaje_historial). Se llama al arrancar el proceso."""
    limite = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    url = f"{_base_url()}/rest/v1/historial_conversacion?creado_en=lt.{limite}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.delete(url, headers={**_headers(), "Prefer": "return=minimal"})
        if resp.status_code not in (200, 204):
            logger.error(f"Error limpiando historial antiguo: HTTP {resp.status_code}: {resp.text}")


async def borrar_historial_archivo_antiguo(dias: int) -> None:
    """El histórico permanente también se purga eventualmente (default
    ~4 meses) — borrado final, sin copiar a ningún otro lado. Se llama
    al arrancar el proceso."""
    limite = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    url = f"{_base_url()}/rest/v1/historial_archivo?creado_en=lt.{limite}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.delete(url, headers={**_headers(), "Prefer": "return=minimal"})
        if resp.status_code not in (200, 204):
            logger.error(f"Error limpiando historial_archivo antiguo: HTTP {resp.status_code}: {resp.text}")


async def borrar_reportes_antiguos(dias: int) -> None:
    """reportes_incidencias también se purga (default ~4 meses) — son
    registros operativos de corto plazo, no hace falta conservarlos
    para siempre (el conteo de recurrencia solo mira los últimos 7
    días de todos modos). Se llama al arrancar el proceso."""
    limite = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    url = f"{_base_url()}/rest/v1/reportes_incidencias?creado_en=lt.{limite}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.delete(url, headers={**_headers(), "Prefer": "return=minimal"})
        if resp.status_code not in (200, 204):
            logger.error(f"Error limpiando reportes_incidencias antiguos: HTTP {resp.status_code}: {resp.text}")


async def borrar_accesos_temporales_antiguos(dias: int) -> None:
    """accesos_temporales (links de 24h para huéspedes) también se
    purga (default ~4 meses) — mucho después de que cualquier token
    haya vencido, así que es solo limpieza de la tabla. Se llama al
    arrancar el proceso."""
    limite = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    url = f"{_base_url()}/rest/v1/accesos_temporales?creado_en=lt.{limite}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.delete(url, headers={**_headers(), "Prefer": "return=minimal"})
        if resp.status_code not in (200, 204):
            logger.error(f"Error limpiando accesos_temporales antiguos: HTTP {resp.status_code}: {resp.text}")


# ------------------------------------------------------------
# Historial de conversación
# ------------------------------------------------------------

async def borrar_historial_de_unidad(telegram_id: str, property_id: str, unit_id: str | None) -> None:
    """Se llama cuando un huésped se despide/avisa que ya salió: borra
    el historial 'caliente' de esa propiedad/unidad puntual — no hace
    falta archivar antes, porque cada turno ya quedó guardado en
    historial_archivo al momento de crearse. Así la conversación del
    próximo huésped en esa misma casa no arrastra reclamos o
    situaciones de quien ya se fue, y nada se pierde para siempre."""
    filtro_unidad = f"unit_id.eq.{unit_id}" if unit_id else "unit_id.is.null"
    url = (
        f"{_base_url()}/rest/v1/historial_conversacion"
        f"?telegram_id=eq.{telegram_id}&property_id=eq.{property_id}&{filtro_unidad}"
    )
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.delete(url, headers={**_headers(), "Prefer": "return=minimal"})
        if resp.status_code not in (200, 204):
            logger.error(f"Error borrando historial de unidad tras despedida: HTTP {resp.status_code}: {resp.text}")


async def obtener_historial_conversacion(telegram_id: str, property_id: str | None, unit_id: str | None = None, limite: int = 6) -> list[dict]:
    """Solo trae turnos anteriores de la MISMA propiedad Y MISMA unidad
    detectada (o turnos igual de 'sin propiedad/unidad detectada' si
    vienen en None) — evita que el contexto de una casa (o de una
    unidad puntual dentro de un condominio) se filtre a la respuesta
    de otra cuando el admin salta de un tema a otro en el mismo chat."""
    filtro_propiedad = f"property_id.eq.{property_id}" if property_id else "property_id.is.null"
    filtro_unidad = f"unit_id.eq.{unit_id}" if unit_id else "unit_id.is.null"
    url = (
        f"{_base_url()}/rest/v1/historial_conversacion"
        f"?telegram_id=eq.{telegram_id}&{filtro_propiedad}&{filtro_unidad}&order=creado_en.desc&limit={limite}"
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


async def guardar_mensaje_historial(
    telegram_id: str, rol: str, contenido: str,
    property_id: str | None = None, unit_id: str | None = None,
    tipo_consulta: str | None = None, sentimiento: str | None = None, confianza: str | None = None,
) -> None:
    """Guarda el turno en LAS DOS tablas al mismo tiempo — el 'caliente'
    (para el contexto en vivo del bot, se borra a los 4 días) y el
    'histórico' (para análisis/informe mensual, se borra a los 4
    meses). Así nunca depende de que algo se "evicte" del caliente
    para quedar respaldado — queda archivado desde el primer momento."""
    ahora = datetime.now(timezone.utc).isoformat()
    payload_base = {
        "telegram_id": telegram_id, "role": rol, "contenido": contenido,
        "property_id": property_id, "unit_id": unit_id,
        "tipo_consulta": tipo_consulta, "sentimiento": sentimiento, "confianza": confianza,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_base_url()}/rest/v1/historial_conversacion", json=payload_base,
            headers={**_headers(), "Prefer": "return=minimal"},
        )
        if resp.status_code not in (200, 201, 204):
            logger.error(f"Error guardando turno de historial (caliente): HTTP {resp.status_code}: {resp.text}")

        resp2 = await client.post(
            f"{_base_url()}/rest/v1/historial_archivo", json={**payload_base, "creado_en": ahora},
            headers={**_headers(), "Prefer": "return=minimal"},
        )
        if resp2.status_code not in (200, 201, 204):
            logger.error(f"Error guardando turno de historial (histórico): HTTP {resp2.status_code}: {resp2.text}")


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

        # masterTable se DERIVA de properties[] en cada export — nunca se
        # guarda por separado — así nunca puede quedar desactualizada
        # cuando se agrega/edita una propiedad (antes era una copia plana
        # mantenida a mano, que se desincronizaba justo en esos casos).
        masterTable_derivado = []
        for p in propiedades:
            for u in p.get("units", []) or []:
                masterTable_derivado.append({
                    "property": p.get("name", ""),
                    "unit": f"{u.get('name', '')} ({u.get('num', '')})".strip(),
                    "pax": u.get("pax", ""),
                    "parqueo": u.get("parqueo", ""),
                    "forms": u.get("forms", "NO"),
                    "correo": u.get("correo", "NO"),
                    "whatsapp": u.get("whatsapp", "NO"),
                    "app": u.get("app", "NO"),
                    "propertyId": p.get("id", ""),
                })

        resultado = {
            "checkInGeneral": config.get("checkInGeneral") or "",
            "checkOutGeneral": config.get("checkOutGeneral") or "",
            "general": config.get("general") or GENERAL_VACIO,
            "masterTable": masterTable_derivado,
            "properties": propiedades,
        }
        return resultado
