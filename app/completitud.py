"""
Chequeo de completitud de una propiedad — señala en el panel Admin qué
datos importantes faltan (wifi, método de acceso, zona, cómo llegar,
capacidad), sin depender de que cada campo tenga un nombre fijo (wifi
y el código de acceso suelen vivir dentro de `extra`, con etiquetas
que varían de una unidad a otra).
"""


def _util(valor) -> bool:
    if valor is None:
        return False
    return str(valor).strip().lower() not in ("", "no", "n/a", "na", "-", "none")


def evaluar_propiedad(prop: dict) -> list[str]:
    avisos = []

    if not _util(prop.get("zone")):
        avisos.append("Falta la zona/dirección de la propiedad.")

    tiene_como_llegar = (
        _util((prop.get("guiaDigital") or {}).get("comoLlegar"))
        or _util((prop.get("requisitosCheckIn") or {}).get("detalle"))
    )
    if not tiene_como_llegar:
        avisos.append("Falta información de cómo llegar.")

    quick_info = prop.get("quickInfo") or []
    tiene_checkin_hora = any(
        isinstance(p, list) and len(p) >= 2 and "check" in str(p[0]).lower() and "in" in str(p[0]).lower() and _util(p[1])
        for p in quick_info
    )
    tiene_checkout_hora = any(
        isinstance(p, list) and len(p) >= 2 and "check" in str(p[0]).lower() and "out" in str(p[0]).lower() and _util(p[1])
        for p in quick_info
    )
    if not tiene_checkin_hora:
        avisos.append("Falta el horario de check-in.")
    if not tiene_checkout_hora:
        avisos.append("Falta el horario de check-out.")

    tiene_reglas = _util("; ".join(prop.get("rules") or [])) or _util("; ".join((prop.get("publicInfo") or {}).get("rules") or []))
    if not tiene_reglas:
        avisos.append("Faltan las reglas de la casa.")

    tiene_bienvenida = any(
        "welcome" in str(m.get("title", "")).lower() or "bienvenid" in str(m.get("title", "")).lower()
        for m in prop.get("messages") or []
    )
    if not tiene_bienvenida:
        avisos.append("Falta un mensaje de bienvenida.")

    unidades = prop.get("units") or []
    if not unidades:
        avisos.append("No tiene ninguna unidad cargada.")

    palabras_acceso = ("codigo", "código", "llave", "keypad", "touch pad", "acceso", "lock")

    for u in unidades:
        nombre_u = u.get("name") or u.get("id") or "unidad sin nombre"
        extra = u.get("extra") or []

        tiene_wifi = any(
            isinstance(p, list) and len(p) >= 2 and "wifi" in str(p[0]).lower() and _util(p[1])
            for p in extra
        )
        if not tiene_wifi:
            avisos.append(f"{nombre_u}: falta wifi.")

        tiene_acceso = _util(u.get("accessCode")) or any(
            isinstance(p, list) and len(p) >= 2 and any(w in str(p[0]).lower() for w in palabras_acceso) and _util(p[1])
            for p in extra
        )
        if not tiene_acceso:
            avisos.append(f"{nombre_u}: falta método de acceso/código.")

        if not _util(u.get("pax")):
            avisos.append(f"{nombre_u}: falta capacidad (pax).")

        listing = u.get("listing") or {}
        if not (_util(listing.get("title")) or _util(listing.get("airbnbTitle"))):
            avisos.append(f"{nombre_u}: falta el título real del anuncio de Airbnb.")

        if not (_util(listing.get("airbnbUrl")) or _util(listing.get("url"))):
            avisos.append(f"{nombre_u}: falta el link del anuncio de Airbnb.")

        if not (_util(listing.get("beds")) or _util(listing.get("bedrooms"))):
            avisos.append(f"{nombre_u}: falta la cantidad de camas/habitaciones.")

        if not _util(listing.get("bathrooms")):
            avisos.append(f"{nombre_u}: falta la cantidad de baños.")

    return avisos
