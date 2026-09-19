import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException

from app import supabase_client, guest_filter

logger = logging.getLogger("public")
router = APIRouter()


@router.get("/consulta/{token}")
async def consultar_propiedad(token: str):
    acceso = await supabase_client.obtener_acceso_temporal(token)
    if not acceso:
        raise HTTPException(status_code=404, detail="Este link no existe o ya no es válido.")

    expira_en = datetime.fromisoformat(acceso["expira_en"].replace("Z", "+00:00"))
    if expira_en < datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Este link ya venció. Pedile uno nuevo a Zafiro.")

    datos = await supabase_client.obtener_property(acceso["property_id"])
    if not datos:
        raise HTTPException(status_code=404, detail="La propiedad de este link ya no existe.")

    await supabase_client.registrar_uso_acceso_temporal(token, acceso["usos"])

    return {
        "propiedad": guest_filter.filtrar_property_para_huesped(datos),
        "expira_en": acceso["expira_en"],
    }
