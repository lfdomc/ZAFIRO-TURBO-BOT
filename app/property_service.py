"""
Lógica de indexación de propiedades — compartida entre
scripts/ingest_propiedades.py (carga masiva desde un JSON) y
app/admin.py (guardado individual desde el panel Admin del sitio),
para que ambos caminos generen exactamente los mismos fragmentos.
"""
import logging

from app import gemini_client, supabase_client

logger = logging.getLogger("property_service")


def _texto_par(par) -> str | None:
    if not isinstance(par, list) or len(par) < 2:
        return None
    label, valor = par[0], par[1]
    if not label or valor in (None, ""):
        return None
    return f"{label}: {valor}"


def _texto_nearby(item) -> str:
    if isinstance(item, dict):
        return item.get("text", "")
    return str(item)


async def _chunk(contenido: str, categoria: str, property_id: str | None,
                  unit_id: str | None = None, idioma: str = "es"):
    contenido = (contenido or "").strip()
    if not contenido:
        return
    embedding = await gemini_client.generar_embedding(contenido)
    if not embedding:
        logger.warning(f"No se pudo generar embedding para: {contenido[:60]}...")
        return
    ok = await supabase_client.insertar_chunk(
        contenido=contenido, embedding=embedding, categoria=categoria,
        property_id=property_id, unit_id=unit_id, idioma=idioma, audiencia="admin",
    )
    if not ok:
        logger.warning(f"Falló insertando chunk [{categoria}]: {contenido[:60]}...")


async def guardar_y_reindexar_property(prop: dict) -> None:
    """Upsert de la propiedad completa + regeneración de todos sus
    fragmentos indexados. Se usa tanto desde el panel Admin (una
    propiedad a la vez) como desde el script de carga masiva."""
    pid = prop["id"]
    nombre = prop["name"]

    ok = await supabase_client.upsert_property(
        pid, nombre, prop.get("group"), prop.get("zone"), prop.get("owner"), prop
    )
    if not ok:
        raise RuntimeError(f"No se pudo guardar la propiedad {pid} en Supabase.")

    await supabase_client.borrar_chunks_de_property(pid)

    partes_info = [f"Propiedad: {nombre}"]
    if prop.get("zone"):
        partes_info.append(f"Zona: {prop['zone']}")
    if prop.get("owner"):
        partes_info.append(f"Responsable/Owner: {prop['owner']}")
    await _chunk(" | ".join(partes_info), "info", pid)

    req = prop.get("requisitosCheckIn") or {}
    if req.get("resumen") or req.get("detalle"):
        texto = f"{nombre} — Requisitos de check-in (proceso interno del equipo): {req.get('resumen', '')} {req.get('detalle', '')}".strip()
        if req.get("link"):
            texto += f" Formulario: {req['link']}"
        await _chunk(texto, "proceso_interno", pid)

    for par in prop.get("quickInfo", []):
        texto = _texto_par(par)
        if texto:
            await _chunk(f"{nombre} — {texto}", "info", pid)

    correo_tpl = prop.get("correoTemplate")
    if correo_tpl and correo_tpl.get("body"):
        await _chunk(f"{nombre} — Plantilla de correo a recepción (uso interno del equipo): {correo_tpl['body']}", "proceso_interno", pid)

    guia = prop.get("guiaDigital") or {}
    if guia.get("comoLlegar"):
        await _chunk(f"{nombre} — Cómo llegar: {guia['comoLlegar']}", "info", pid)

    for regla in prop.get("rules", []):
        await _chunk(f"{nombre} — Regla de la casa: {regla}", "reglas", pid)

    public_info = prop.get("publicInfo") or {}
    if public_info.get("amenities"):
        await _chunk(f"{nombre} — Amenidades: {', '.join(public_info['amenities'])}", "amenidades", pid)
    if public_info.get("nearby"):
        cercanos = "; ".join(_texto_nearby(n) for n in public_info["nearby"])
        await _chunk(f"{nombre} — Lugares cercanos: {cercanos}", "info", pid)
    for regla in public_info.get("rules", []):
        await _chunk(f"{nombre} — Regla (ficha pública): {regla}", "reglas", pid)

    local_exp = prop.get("localExperiences") or {}
    if local_exp.get("resumen"):
        texto = f"{nombre} — Experiencias locales: {local_exp['resumen']}"
        if local_exp.get("url"):
            texto += f" ({local_exp['url']})"
        await _chunk(texto, "info", pid)
    if local_exp.get("destacados"):
        destacados = "; ".join(f"{d.get('nombre')} ({d.get('precio')})" for d in local_exp["destacados"] if d.get("nombre"))
        if destacados:
            await _chunk(f"{nombre} — Experiencias destacadas: {destacados}", "info", pid)

    for msg in prop.get("messages", []):
        idioma = "en" if "español" not in msg.get("title", "").lower() and "(es" not in msg.get("title", "").lower() else "es"
        await _chunk(f"{nombre} — {msg.get('title', '')}: {msg.get('body', '')}", "mensajes", pid, idioma=idioma)

    if prop.get("note"):
        await _chunk(f"{nombre} — Nota interna: {prop['note']}", "nota_interna", pid)

    unidades = prop.get("units", [])
    if len(unidades) > 1:
        lista = "; ".join(f"{u.get('name', '')} ({u.get('num', '')})".strip() for u in unidades if u.get("name"))
        await _chunk(f"{nombre} — Este complejo tiene {len(unidades)} unidades: {lista}", "info", pid)

    campos_personalizados = prop.get("camposPersonalizados") or {}
    todos_los_campos = await supabase_client.listar_campos_personalizados()
    etiquetas_campos = {d["id"]: d["etiqueta"] for d in todos_los_campos}
    if campos_personalizados:
        for clave, valor in campos_personalizados.items():
            if not valor:
                continue
            etiqueta = etiquetas_campos.get(clave, clave)
            await _chunk(f"{nombre} — {etiqueta}: {valor}", "info", pid)

    for unidad in prop.get("units", []):
        uid = unidad.get("id")
        nombre_unidad = f"{unidad.get('name', '')} ({unidad.get('num', '')})".strip()

        datos_operativos = [f"pax {unidad.get('pax', '?')}"]
        if unidad.get("parqueo"):
            datos_operativos.append(f"parqueo {unidad['parqueo']}")
        for campo, etiqueta in (("forms", "formulario"), ("correo", "correo recepción"),
                                 ("whatsapp", "WhatsApp caseta"), ("app", "ingreso por app")):
            if unidad.get(campo):
                datos_operativos.append(f"{etiqueta}: {unidad[campo]}")
        await _chunk(
            f"{nombre} — {nombre_unidad} — Datos operativos: {', '.join(datos_operativos)}",
            "info", pid, uid,
        )

        if unidad.get("accessCode"):
            await _chunk(f"{nombre} — {nombre_unidad} — Código de acceso: {unidad['accessCode']}", "check_in", pid, uid)

        listing = unidad.get("listing") or {}
        if listing.get("description"):
            await _chunk(f"{nombre} — {nombre_unidad}: {listing['description']}", "info", pid, uid)
        detalles_listing = []
        for campo, etiqueta in (("guests", "huéspedes"), ("bedrooms", "habitaciones"),
                                 ("beds", "camas"), ("bathrooms", "baños")):
            if listing.get(campo) is not None:
                detalles_listing.append(f"{etiqueta}: {listing[campo]}")
        if detalles_listing:
            await _chunk(f"{nombre} — {nombre_unidad} — {', '.join(detalles_listing)}", "info", pid, uid)

        if unidad.get("rooms"):
            await _chunk(f"{nombre} — {nombre_unidad} — Distribución: {'; '.join(unidad['rooms'])}", "info", pid, uid)

        for par in unidad.get("extra", []):
            texto = _texto_par(par)
            if not texto:
                continue
            categoria = "wifi" if "wifi" in str(par[0]).lower() else "info"
            await _chunk(f"{nombre} — {nombre_unidad} — {texto}", categoria, pid, uid)

        for msg in unidad.get("checkin", []):
            idioma = "en" if "español" not in msg.get("title", "").lower() else "es"
            await _chunk(f"{nombre} — {nombre_unidad} — {msg.get('title', '')}: {msg.get('body', '')}", "check_in", pid, uid, idioma=idioma)

        if unidad.get("note"):
            await _chunk(f"{nombre} — {nombre_unidad} — Nota interna: {unidad['note']}", "nota_interna", pid, uid)

        campos_unidad = unidad.get("camposPersonalizados") or {}
        for clave, valor in campos_unidad.items():
            if not valor:
                continue
            etiqueta = etiquetas_campos.get(clave, clave)
            await _chunk(f"{nombre} — {nombre_unidad} — {etiqueta}: {valor}", "info", pid, uid)


async def reindexar_general(datos: dict) -> None:
    """Igual que guardar_y_reindexar_property, pero para la info de
    compañía que no pertenece a una propiedad puntual (checkInGeneral,
    general.faqs, contactos, etc.) — property_id queda en null."""
    await supabase_client.upsert_configuracion_general("checkInGeneral", datos.get("checkInGeneral"))
    await supabase_client.upsert_configuracion_general("checkOutGeneral", datos.get("checkOutGeneral"))
    await supabase_client.upsert_configuracion_general("general", datos.get("general", {}))
    await supabase_client.upsert_configuracion_general("masterTable", datos.get("masterTable", []))

    await supabase_client.borrar_chunks_de_property(None)

    general = datos.get("general") or {}

    formulario = general.get("formulario") or {}
    if formulario.get("texto"):
        await _chunk(f"General — Formulario de ingreso: {formulario['texto']}", "check_in", None)

    comunicacion = general.get("comunicacion") or {}
    if comunicacion.get("bullets"):
        await _chunk("General — Cómo contestar mensajes: " + " | ".join(comunicacion["bullets"]), "info", None)

    reserva = general.get("reservaDirecta") or {}
    if reserva.get("nota"):
        await _chunk(f"General — Reserva directa: {reserva['nota']} ({reserva.get('url', '')})", "info", None)

    for msg in general.get("mensajesFrecuentes", []):
        await _chunk(f"General — Mensaje frecuente '{msg.get('title', '')}': {msg.get('body', '')}", "mensajes", None)

    for c in general.get("contactos", []):
        texto = f"General — Contacto {c.get('label', '')}: {c.get('value', '')}"
        if c.get("note"):
            texto += f" — {c['note']}"
        await _chunk(texto, "contacto", None)

    for faq in general.get("faqs", []):
        await _chunk(f"General — FAQ: {faq.get('q', '')} => {faq.get('a', '')}", "faq", None)
