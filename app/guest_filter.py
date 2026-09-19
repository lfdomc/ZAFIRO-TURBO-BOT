"""
Reduce el objeto completo de una propiedad (properties.datos) a la
versión que puede ver un huésped en el link temporal — sin notas
internas ni canales operativos internos del equipo.

Esto NO usa knowledge_chunks/audiencia — es una página informativa
plana, no un chat con IA, así que directamente se sirve un JSON
filtrado del mismo `datos` que ya existe.
"""

# Campos que NUNCA se muestran al huésped, sin importar dónde aparezcan.
CAMPOS_INTERNOS_PROPIEDAD = {"note", "owner", "correoTemplate"}
CAMPOS_INTERNOS_UNIDAD = {"note", "forms", "correo"}


def filtrar_property_para_huesped(prop: dict, unit_id: str | None = None) -> dict:
    """Si unit_id viene dado, la propiedad tiene varias unidades (un
    complejo/condominio) y este link es solo para UNA de ellas — se
    conserva toda la info compartida del complejo (dirección, reglas,
    check-in general, amenidades comunes) pero `units` se recorta a
    solo esa unidad, para no exponerle a un huésped el wifi/código de
    acceso de las casas de sus vecinos."""
    limpio = {k: v for k, v in prop.items() if k not in CAMPOS_INTERNOS_PROPIEDAD}

    if "units" in limpio:
        unidades_filtradas = [
            {k: v for k, v in u.items() if k not in CAMPOS_INTERNOS_UNIDAD}
            for u in limpio["units"]
        ]
        if unit_id:
            unidades_filtradas = [u for u in unidades_filtradas if u.get("id") == unit_id]
        limpio["units"] = unidades_filtradas

    return limpio
