"""
Panel de Correcciones — Fases 3 y 4 del sistema de autoaprendizaje por
retrieval. A propósito vive en su propio router (prefijo /correcciones),
separado de /admin/* (edición de propiedades) y del informe mensual del
Dashboard: es una cola de revisión humana propia, con su propio "informe"
de correcciones (aparte del informe mensual de atención al huésped).

Ninguna corrección afecta una respuesta en vivo hasta que pasa por
POST /correcciones/{id}/aprobar — ver buscar_correcciones_estilo_aprobadas
en supabase_client.py y su uso en main.py.
"""
import logging
from fastapi import APIRouter, Header, HTTPException

from app import supabase_client, gemini_client
from app.admin import _verificar_admin_key

logger = logging.getLogger("correcciones")
router = APIRouter()


@router.get("/correcciones/pendientes")
async def listar_pendientes(x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    return await supabase_client.listar_correcciones_estilo("pendientes")


@router.post("/correcciones/{correccion_id}/aprobar")
async def aprobar_correccion(correccion_id: int, x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    fila = await supabase_client.obtener_correccion_estilo(correccion_id)
    if not fila:
        raise HTTPException(status_code=404, detail="Corrección no encontrada.")
    if fila.get("descartada"):
        raise HTTPException(status_code=400, detail="Esta corrección ya fue descartada, no se puede aprobar.")
    if fila.get("aprobada"):
        return {"ok": True}  # ya estaba aprobada — idempotente

    embedding = await gemini_client.generar_embedding(fila["texto_editado"])
    if not embedding:
        raise HTTPException(status_code=502, detail="No se pudo generar el embedding. Probá de nuevo en un momento.")

    await supabase_client.aprobar_correccion_estilo(correccion_id, embedding)
    return {"ok": True}


@router.post("/correcciones/{correccion_id}/descartar")
async def descartar_correccion(correccion_id: int, x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    fila = await supabase_client.obtener_correccion_estilo(correccion_id)
    if not fila:
        raise HTTPException(status_code=404, detail="Corrección no encontrada.")
    await supabase_client.descartar_correccion_estilo(correccion_id)
    return {"ok": True}


@router.get("/correcciones/informe")
async def informe_correcciones(x_admin_key: str | None = Header(default=None)):
    """Fase 4: informe PROPIO de correcciones — separado del informe
    mensual de atención al huésped. Muestra el estado general de la cola
    y el historial de lo ya aprobado (lo que hoy influye en el tono de
    Sofía) para que el equipo pueda revisar o revertir criterio."""
    _verificar_admin_key(x_admin_key)
    pendientes = await supabase_client.listar_correcciones_estilo("pendientes")
    aprobadas = await supabase_client.listar_correcciones_estilo("aprobadas")
    descartadas = await supabase_client.listar_correcciones_estilo("descartadas")
    return {
        "total_pendientes": len(pendientes),
        "total_aprobadas": len(aprobadas),
        "total_descartadas": len(descartadas),
        "aprobadas": aprobadas,
    }
