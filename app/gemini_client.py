"""
Cliente de Gemini con rotación de claves y failover de modelos.
Rescatado tal cual del proyecto JARVIS — ya está calibrado con casos
reales (límite gratis de 1,000 embeddings/día por cuenta; rotar entre
varias cuentas multiplica la cuota efectiva).
"""
import httpx
import logging
from tenacity import retry, stop_after_attempt, wait_chain, wait_fixed, retry_if_exception_type
from app.config import settings
from app import logging_utils

logger = logging.getLogger("gemini_client")

MODELOS_GENERACION = [
    "gemini-3.1-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.7-flash",
]


class TodasLasClavesSinCupo(Exception):
    """Las claves respondieron 429 (sin cuota) en el mismo ciclo — el
    límite por minuto se libera solo en segundos, así que reintentar con
    espera recupera solicitudes que antes se perdían en silencio."""
    pass


async def _intentar_embedding_con_todas_las_claves(texto: str, claves: list[str]) -> list[float] | None:
    todas_sin_cupo = True
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, clave in enumerate(claves):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent?key={clave}"
            payload = {
                "model": "models/gemini-embedding-001",
                "content": {"parts": [{"text": texto}]},
                "outputDimensionality": 768,
            }
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    valores = data.get("embedding", {}).get("values")
                    if valores:
                        return valores
                    todas_sin_cupo = False
                elif resp.status_code == 429:
                    logger.warning(f"[EMBEDDING] Clave #{i+1}/{len(claves)} sin cuota (429), probando la siguiente...")
                    continue
                else:
                    logger.warning(f"[EMBEDDING] Clave #{i+1} HTTP {resp.status_code}: {resp.text}")
                    todas_sin_cupo = False
            except Exception as e:
                logger.warning(f"[EMBEDDING] Clave #{i+1} excepción: {e}")
                todas_sin_cupo = False

    if todas_sin_cupo:
        raise TodasLasClavesSinCupo()
    return None


@retry(
    retry=retry_if_exception_type(TodasLasClavesSinCupo),
    wait=wait_chain(wait_fixed(5), wait_fixed(15), wait_fixed(30)),
    stop=stop_after_attempt(4),
    reraise=False,
)
async def _generar_embedding_con_reintento(texto: str, claves: list[str]) -> list[float] | None:
    return await _intentar_embedding_con_todas_las_claves(texto, claves)


async def generar_embedding(texto: str) -> list[float] | None:
    claves = settings.obtener_pool_claves_gemini()
    if not claves:
        logger.error("No hay ninguna clave de Gemini configurada (GEMINI_KEY_*).")
        return None

    try:
        resultado = await _generar_embedding_con_reintento(texto, claves)
        if resultado:
            return resultado
    except TodasLasClavesSinCupo:
        pass

    logger.error("Todas las claves de Gemini fallaron o están sin cuota para embeddings.")
    await logging_utils.registrar_error(
        "AIRBNB_BOT_EMBEDDING",
        "Todas las claves de Gemini fallaron o están sin cuota",
        "Ver logs de Railway para el detalle por clave",
        "Revisar cuotas en ai.dev/rate-limit o agregar más claves GEMINI_KEY_*"
    )
    return None


async def generar_respuesta(payload_contents: list[dict], system_instruction: str | None = None, temperatura: float = 0.2) -> str:
    claves = settings.obtener_pool_claves_gemini()
    if not claves:
        raise RuntimeError("No hay ninguna clave de Gemini configurada.")

    payload = {
        "contents": payload_contents,
        "generationConfig": {"temperature": temperatura},
    }
    if system_instruction:
        payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    ultimo_error = ""

    async with httpx.AsyncClient(timeout=60.0) as client:
        for k, clave in enumerate(claves):
            for modelo in MODELOS_GENERACION:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
                try:
                    resp = await client.post(url, json=payload, headers={"x-goog-api-key": clave})
                    if resp.status_code == 200:
                        data = resp.json()
                        candidatos = data.get("candidates", [])
                        if candidatos:
                            partes = candidatos[0].get("content", {}).get("parts", [])
                            if partes:
                                return partes[0].get("text", "")
                    elif resp.status_code == 429:
                        ultimo_error = f"[clave #{k+1}][{modelo}] sin cuota (429)"
                        logger.warning(ultimo_error)
                    else:
                        ultimo_error = f"[clave #{k+1}][{modelo}] HTTP {resp.status_code}: {resp.text}"
                except Exception as e:
                    ultimo_error = f"[clave #{k+1}][{modelo}] excepción: {e}"

    await logging_utils.registrar_error(
        "AIRBNB_BOT_GENERACION",
        "Todos los modelos y todas las claves fallaron",
        ultimo_error,
        "Revisar cuotas en ai.dev/rate-limit o agregar más claves GEMINI_KEY_*"
    )
    raise RuntimeError(f"Todos los modelos y todas las claves fallaron. Último error: {ultimo_error}")


async def clasificar_incidencia(texto: str) -> str:
    """Devuelve 'mantenimiento', 'limpieza' o 'ninguno'. Se usa para
    decidir si hay que ofrecer el botón de reporte por WhatsApp."""
    system = (
        "Clasificás mensajes de un chat interno de administración de propiedades "
        "de alquiler vacacional. Respondé con EXACTAMENTE una palabra, sin "
        "explicación ni puntuación: 'mantenimiento' si el mensaje reporta un "
        "problema físico o técnico a reparar (aire acondicionado, fuga de agua, "
        "electrodoméstico roto, cerradura, plomería, electricidad, etc.), "
        "'limpieza' si reporta un problema de limpieza o aseo (suciedad, ropa de "
        "cama, basura, olores, etc.), o 'ninguno' si el mensaje es una pregunta "
        "informativa, un saludo, o no reporta ningún problema."
    )
    try:
        respuesta = await generar_respuesta(
            [{"role": "user", "parts": [{"text": texto}]}],
            system_instruction=system, temperatura=0.0,
        )
    except Exception as e:
        logger.warning(f"No se pudo clasificar la incidencia (se asume 'ninguno'): {e}")
        return "ninguno"

    r = respuesta.strip().lower()
    if "mantenimiento" in r:
        return "mantenimiento"
    if "limpieza" in r:
        return "limpieza"
    return "ninguno"
