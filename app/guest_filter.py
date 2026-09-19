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


def filtrar_property_para_huesped(prop: dict) -> dict:
    limpio = {k: v for k, v in prop.items() if k not in CAMPOS_INTERNOS_PROPIEDAD}

    if "units" in limpio:
        limpio["units"] = [
            {k: v for k, v in u.items() if k not in CAMPOS_INTERNOS_UNIDAD}
            for u in limpio["units"]
        ]

    return limpio
