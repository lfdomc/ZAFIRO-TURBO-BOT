import asyncio
import logging
import httpx
from fastapi import APIRouter, Header, HTTPException

from app.config import settings
from app import supabase_client, property_service, state, fuente_externa

logger = logging.getLogger("admin")
router = APIRouter()


def _verificar_admin_key(x_admin_key: str | None):
    if not settings.ADMIN_API_KEY:
        raise HTTPException(status_code=500, detail="ADMIN_API_KEY no está configurada en el backend.")
    if x_admin_key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="X-Admin-Key inválida o ausente.")


async def _disparar_redeploy_sitio():
    """Fire-and-forget: si hay un Deploy Hook de Vercel configurado, lo
    llama para que el sitio se regenere solo (trae el JSON fresco desde
    /export/propiedades.json). Un fallo acá no debe tumbar la
    respuesta al panel Admin — el guardado en Supabase ya es válido
    igual, el redeploy es un paso aparte."""
    if not settings.VERCEL_DEPLOY_HOOK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(settings.VERCEL_DEPLOY_HOOK_URL)
            if resp.status_code not in (200, 201, 202):
                logger.warning(f"Deploy hook respondió HTTP {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.warning(f"No se pudo disparar el redeploy de Vercel: {e}")


# ------------------------------------------------------------
# Propiedades
# ------------------------------------------------------------

@router.get("/admin/propiedades")
async def listar_propiedades(x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    return await supabase_client.listar_propiedades_resumen()


@router.get("/admin/propiedades/{property_id}")
async def obtener_propiedad(property_id: str, x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    datos = await supabase_client.obtener_property(property_id)
    if datos is None:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada.")
    return datos


@router.post("/admin/propiedades")
async def guardar_propiedad(prop: dict, x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    if not prop.get("id") or not prop.get("name"):
        raise HTTPException(status_code=400, detail="La propiedad necesita al menos 'id' y 'name'.")
    try:
        await property_service.guardar_y_reindexar_property(prop)
    except Exception as e:
        logger.error(f"Error guardando propiedad {prop.get('id')}: {e}")
        raise HTTPException(status_code=500, detail=f"No se pudo guardar: {e}")
    await _disparar_redeploy_sitio()
    return {"ok": True}


@router.delete("/admin/propiedades/{property_id}")
async def eliminar_propiedad(property_id: str, x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    ok = await supabase_client.eliminar_property(property_id)
    if not ok:
        raise HTTPException(status_code=500, detail="No se pudo eliminar la propiedad.")
    await _disparar_redeploy_sitio()
    return {"ok": True}


# ------------------------------------------------------------
# Campos personalizados
# ------------------------------------------------------------

@router.get("/admin/campos")
async def listar_campos(x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    return await supabase_client.listar_campos_personalizados()


@router.post("/admin/campos")
async def crear_campo(campo: dict, x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    field_id = (campo.get("id") or "").strip()
    etiqueta = (campo.get("etiqueta") or "").strip()
    nivel = campo.get("nivel", "propiedad")
    if nivel not in ("propiedad", "unidad"):
        nivel = "propiedad"
    if not field_id or not etiqueta:
        raise HTTPException(status_code=400, detail="El campo necesita 'id' y 'etiqueta'.")
    ok = await supabase_client.crear_campo_personalizado(field_id, etiqueta, campo.get("tipo", "texto"), nivel)
    if not ok:
        raise HTTPException(status_code=500, detail="No se pudo crear el campo.")
    return {"ok": True}


@router.delete("/admin/campos/{field_id}")
async def eliminar_campo(field_id: str, x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    await supabase_client.eliminar_campo_personalizado(field_id)
    return {"ok": True}


# ------------------------------------------------------------
# Export estático — lo consume scripts/generar-propiedades.mjs en el
# build de Vercel (server-side, nunca desde el navegador del visitante)
# ------------------------------------------------------------

@router.get("/export/propiedades.json")
async def exportar_propiedades(x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    datos = await supabase_client.obtener_todo_para_export()
    if not datos:
        raise HTTPException(status_code=500, detail="No se pudo exportar desde Supabase.")
    return datos


# ------------------------------------------------------------
# Importación masiva — carga un propiedades.json completo sin tocar
# una terminal. Corre en segundo plano (puede tardar varios minutos
# generando un embedding por fragmento) — el navegador solo dispara y
# consulta el progreso.
# ------------------------------------------------------------

async def _correr_importacion(datos: dict):
    total = 1 + len(datos.get("properties", []))  # +1 por la info general
    state.iniciar_importacion(total)
    try:
        await property_service.reindexar_general(datos)
        state.avanzar_importacion("Información general")
        for prop in datos.get("properties", []):
            await property_service.guardar_y_reindexar_property(prop)
            state.avanzar_importacion(prop.get("name", prop.get("id", "")))
        state.finalizar_importacion(error=None)
        await _disparar_redeploy_sitio()
    except Exception as e:
        logger.error(f"Error en importación masiva: {e}")
        state.finalizar_importacion(error=str(e))


@router.post("/admin/importar")
async def importar_propiedades(datos: dict, x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    if state.obtener_estado_importacion()["corriendo"]:
        raise HTTPException(status_code=409, detail="Ya hay una importación en curso.")
    if not isinstance(datos.get("properties"), list) or not datos["properties"]:
        raise HTTPException(status_code=400, detail="El JSON no tiene un array 'properties' con datos.")
    asyncio.create_task(_correr_importacion(datos))
    return {"ok": True, "mensaje": "Importación iniciada en segundo plano."}


@router.get("/admin/importar/estado")
async def estado_importacion(x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    return state.obtener_estado_importacion()


# ------------------------------------------------------------
# Configuración general editable desde el panel — hoy: los números de
# WhatsApp de los coordinadores de mantenimiento/limpieza. Guardado en
# Supabase (configuracion_general), con las variables de entorno de
# Railway como respaldo si todavía no se configuró nada acá.
# ------------------------------------------------------------

CLAVES_CONFIG_EDITABLE = ["whatsapp_mantenimiento_default", "whatsapp_limpieza_default"]


@router.get("/admin/configuracion")
async def obtener_configuracion(x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    resultado = {}
    for clave in CLAVES_CONFIG_EDITABLE:
        resultado[clave] = await supabase_client.obtener_config(clave)
    return resultado


@router.post("/admin/configuracion")
async def guardar_configuracion(payload: dict, x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    for clave in CLAVES_CONFIG_EDITABLE:
        if clave in payload:
            await supabase_client.upsert_configuracion_general(clave, payload[clave])
    return {"ok": True}


# ------------------------------------------------------------
# Guía pública externa (guia.zafiropm.com) — previsualizar (con
# comparación si la propiedad ya existe) y aplicar solo los cambios
# que el admin eligió a mano. Nunca escribe nada en el primer paso.
# ------------------------------------------------------------

@router.post("/admin/previsualizar-guia")
async def previsualizar_guia(payload: dict, x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    url = (payload.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="Falta 'url'.")

    try:
        html = await fuente_externa.obtener_html(url)
        guia = fuente_externa.parsear_guia_zafiropm(html)
    except Exception as e:
        logger.error(f"Error leyendo guía externa ({url}): {e}")
        raise HTTPException(status_code=502, detail=f"No se pudo leer la guía externa: {e}")

    property_id = (payload.get("property_id") or "").strip() or None
    comparacion = None
    campos_prellenado = None

    if property_id:
        prop_actual = await supabase_client.obtener_property(property_id)
        if prop_actual:
            comparacion = fuente_externa.comparar_con_actual(guia, prop_actual)
            # Guarda también una copia de referencia — informativo, no pisa lo curado.
            await supabase_client.guardar_guia_externa(property_id, guia)
    else:
        campos_prellenado = fuente_externa.mapear_a_property_nueva(
            guia, payload.get("id_sugerido", "") or "", url
        )

    return {"guia": guia, "comparacion": comparacion, "campos_prellenado": campos_prellenado}


@router.post("/admin/aplicar-cambios-guia/{property_id}")
async def aplicar_cambios_guia(property_id: str, payload: dict, x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    campos = payload.get("campos") or {}
    if not campos:
        raise HTTPException(status_code=400, detail="No se especificó ningún campo a aplicar.")

    prop_actual = await supabase_client.obtener_property(property_id)
    if not prop_actual:
        raise HTTPException(status_code=404, detail="Propiedad no encontrada.")

    prop_actualizada = fuente_externa.aplicar_cambios(prop_actual, campos)

    try:
        await property_service.guardar_y_reindexar_property(prop_actualizada)
    except Exception as e:
        logger.error(f"Error guardando cambios aplicados de la guía en {property_id}: {e}")
        raise HTTPException(status_code=500, detail=f"No se pudo guardar: {e}")

    await _disparar_redeploy_sitio()
    return {"ok": True, "propiedad": prop_actualizada}
