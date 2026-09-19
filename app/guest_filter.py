"""
Reduce el objeto completo de una propiedad (properties.datos) a la
versión que puede ver un huésped en el link temporal — sin notas
internas, sin instrucciones operativas del equipo, y sin campos que
solo dicen "NO" o quedaron vacíos (ruido sin valor para el huésped).

Esto NO usa knowledge_chunks/audiencia — es una página informativa
plana, no un chat con IA, así que directamente se sirve un JSON
filtrado del mismo `datos` que ya existe.
"""
import unicodedata

# Campos que NUNCA se muestran al huésped, sin importar dónde aparezcan.
# requisitosCheckIn es el procedimiento INTERNO del equipo para autorizar
# el ingreso (llenar formulario, avisar por WhatsApp) — nunca es para el
# huésped, aunque a veces incluya un link que en realidad SÍ debería
# llegarle (eso se resuelve cargándolo aparte, en quickInfo o mensajes).
CAMPOS_INTERNOS_PROPIEDAD = {"note", "owner", "correoTemplate", "requisitosCheckIn"}
CAMPOS_INTERNOS_UNIDAD = {"note", "forms", "correo"}

# Etiquetas de quickInfo/extra que son operativas del equipo, sin
# importar el valor que tengan (a diferencia del filtro por "NO" de
# abajo, que depende del valor).
ETIQUETAS_INTERNAS = {"correo recepcion", "correo de recepcion", "whatsapp caseta"}

VALORES_SIN_UTILIDAD = {"", "no", "n/a", "na", "-", "none", "ninguno"}


def _normalizar(texto) -> str:
    texto = unicodedata.normalize("NFD", str(texto).lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn").strip()


def _es_valor_util(valor) -> bool:
    if valor is None:
        return False
    return _normalizar(valor) not in VALORES_SIN_UTILIDAD


def _filtrar_pares(pares):
    """Filtra pares [etiqueta, valor] (quickInfo, extra) — saca los de
    etiqueta operativa conocida, y cualquiera cuyo valor sea 'NO'/vacío/
    N-A, sin importar la etiqueta (un valor negativo/vacío no le sirve
    de nada al huésped, sea cual sea el campo)."""
    resultado = []
    for par in pares or []:
        if not isinstance(par, list) or len(par) < 2:
            continue
        etiqueta, valor = par[0], par[1]
        if _normalizar(etiqueta) in ETIQUETAS_INTERNAS:
            continue
        if not _es_valor_util(valor):
            continue
        resultado.append([etiqueta, valor])
    return resultado


def filtrar_property_para_huesped(prop: dict, unit_id: str | None = None) -> dict:
    """Si unit_id viene dado, la propiedad tiene varias unidades (un
    complejo/condominio) y este link es solo para UNA de ellas — se
    conserva toda la info compartida del complejo (reglas, amenidades
    comunes) pero `units` se recorta a solo esa unidad, para no
    exponerle a un huésped el wifi/código de acceso de las casas de
    sus vecinos."""
    limpio = {k: v for k, v in prop.items() if k not in CAMPOS_INTERNOS_PROPIEDAD}

    if "quickInfo" in limpio:
        limpio["quickInfo"] = _filtrar_pares(limpio["quickInfo"])

    if "units" in limpio:
        unidades_filtradas = []
        for u in limpio["units"]:
            u_limpia = {k: v for k, v in u.items() if k not in CAMPOS_INTERNOS_UNIDAD}
            if "extra" in u_limpia:
                u_limpia["extra"] = _filtrar_pares(u_limpia["extra"])
            for campo_escalar in ("whatsapp", "app", "parqueo", "accessCode"):
                if campo_escalar in u_limpia and not _es_valor_util(u_limpia[campo_escalar]):
                    u_limpia.pop(campo_escalar, None)
            unidades_filtradas.append(u_limpia)
        if unit_id:
            unidades_filtradas = [u for u in unidades_filtradas if u.get("id") == unit_id]
        limpio["units"] = unidades_filtradas

    return limpio
