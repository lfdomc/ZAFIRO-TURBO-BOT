"""
Estado en memoria del proceso. El servidor de FastAPI es un proceso
continuo — un diccionario en memoria persiste mientras el proceso siga
corriendo, sin necesitar un servicio externo.

Limitación honesta: si Railway reinicia el proceso (nuevo despliegue,
caída), este estado se pierde — para un solo proceso, es suficiente.
"""
import time

TTL_IDEMPOTENCIA_SEG = 600
TTL_CACHE_FAQ_SEG = 10800  # 3 horas — respuestas idénticas repetidas no vuelven a gastar embedding + Gemini

_updates_procesados: dict[int, float] = {}
_cache_faq: dict[str, tuple[str, float]] = {}

_estado_importacion: dict = {
    "corriendo": False, "completadas": 0, "total": 0, "error": None, "propiedad_actual": None,
}


def _limpiar_vencidos(store: dict, ttl: int, ahora: float):
    vencidos = [k for k, v in store.items() if (ahora - (v[1] if isinstance(v, tuple) else v)) > ttl]
    for k in vencidos:
        del store[k]


def ya_procesado(update_id: int) -> bool:
    ahora = time.time()
    _limpiar_vencidos(_updates_procesados, TTL_IDEMPOTENCIA_SEG, ahora)
    if update_id in _updates_procesados:
        return True
    _updates_procesados[update_id] = ahora
    return False


def cache_faq_get(clave: str) -> str | None:
    entrada = _cache_faq.get(clave)
    if not entrada:
        return None
    valor, ts = entrada
    if (time.time() - ts) > TTL_CACHE_FAQ_SEG:
        del _cache_faq[clave]
        return None
    return valor


def cache_faq_set(clave: str, valor: str):
    _cache_faq[clave] = (valor, time.time())


def iniciar_importacion(total: int):
    _estado_importacion.update({"corriendo": True, "completadas": 0, "total": total, "error": None, "propiedad_actual": None})


def avanzar_importacion(nombre_propiedad: str):
    _estado_importacion["completadas"] += 1
    _estado_importacion["propiedad_actual"] = nombre_propiedad


def finalizar_importacion(error: str | None = None):
    _estado_importacion["corriendo"] = False
    _estado_importacion["error"] = error
    _estado_importacion["propiedad_actual"] = None


def obtener_estado_importacion() -> dict:
    return dict(_estado_importacion)
