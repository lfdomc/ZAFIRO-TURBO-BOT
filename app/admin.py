import asyncio
import logging
import httpx
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import Response

from app.config import settings
from app import supabase_client, property_service, state, fuente_externa, completitud, email_client, graficos_informe

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
    filas = await supabase_client.listar_propiedades_con_datos()
    return [
        {
            "id": f["id"], "nombre": f["nombre"], "zona": f.get("zona"),
            "actualizado_en": f.get("actualizado_en"),
            "avisos": completitud.evaluar_propiedad(f.get("datos") or {}),
        }
        for f in filas
    ]


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


# ------------------------------------------------------------
# Información general (FAQs, mensajes frecuentes, contactos) — lo que no
# pertenece a una propiedad puntual. Antes solo se podía cambiar
# reimportando el JSON completo; esto permite editarla directo desde el
# panel, sin tocar el archivo a mano.
# ------------------------------------------------------------

@router.get("/admin/general")
async def obtener_general(x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    general = await supabase_client.obtener_configuracion_general("general", {})
    return general or {}


@router.post("/admin/general")
async def guardar_general(payload: dict, x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    check_in = await supabase_client.obtener_configuracion_general("checkInGeneral")
    check_out = await supabase_client.obtener_configuracion_general("checkOutGeneral")
    master_table = await supabase_client.obtener_configuracion_general("masterTable", [])
    # Reindexar_general guarda Y regenera los fragmentos de búsqueda del
    # bot (knowledge_chunks) — así un cambio acá se refleja también en lo
    # que Sofía puede encontrar, no solo en lo que se ve en el sitio.
    await property_service.reindexar_general({
        "checkInGeneral": check_in, "checkOutGeneral": check_out,
        "general": payload, "masterTable": master_table,
    })
    await _disparar_redeploy_sitio()
    return {"ok": True}


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


@router.get("/admin/informe-mensual")
async def informe_mensual(anio: int, mes: int, x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    if not (1 <= mes <= 12):
        raise HTTPException(status_code=400, detail="mes debe estar entre 1 y 12")
    return await supabase_client.generar_informe_mensual(anio, mes)


NOMBRES_MES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _seccion_indice_servicio(datos: dict | None, etiqueta_celda: str, estilo_celda: str, estilo_header: str, estilo_tabla: str) -> str:
    if not datos or datos.get("pct") is None:
        return "<p style='color:#94a3b8;font-size:13px;'>Sin reportes este mes.</p>"
    filas_unidad = "".join(
        f"<tr><td style='{estilo_celda}'>{u['unidad']}</td>"
        f"<td style='{estilo_celda};background:{ {'verde': '#dcfce7', 'amarillo': '#fef9c3', 'rojo': '#fee2e2'}.get(u['color'], '#f8fafc') };"
        f"color:{ {'verde': '#166534', 'amarillo': '#854d0e', 'rojo': '#991b1b'}.get(u['color'], '#1e293b') };font-weight:600;'>{u['pct']}% ({u['resueltos_primera_vez']} de {u['total_reportes']})</td></tr>"
        for u in datos["por_unidad"]
    )
    return f"""
    <p style="font-size:14px;">
      <strong>{datos['pct']}%</strong> de los reportes ({datos['resueltos_primera_vez']} de {datos['total_reportes']})
      no necesitaron un reporte de seguimiento del mismo tipo, en la misma unidad, dentro de los 7 días
      siguientes.
    </p>
    <table style="{estilo_tabla}">
      <tr><th style="{estilo_header}">Unidad</th><th style="{estilo_header}">{etiqueta_celda}</th></tr>
      {filas_unidad or f"<tr><td style='{estilo_celda}' colspan='2'>—</td></tr>"}
    </table>
    <p style="color:#94a3b8;font-size:12px;">
      Nota: es una aproximación (basada en el estándar internacional "First Contact Resolution") — todavía no
      hay un botón para marcar un reporte como resuelto, así que no es una medición exacta.
    </p>
    """


def _formatear_informe_html(informe: dict) -> str:
    total = informe["total_consultas"]
    nombre_mes = NOMBRES_MES[informe["mes"] - 1]

    # Resumen ejecutivo — los números que más le importan al encargado,
    # arriba de todo, antes de entrar al detalle.
    pct_confianza_alta = round(informe["por_confianza"].get("alta", 0) / total * 100) if total else 0
    pct_sentimiento_negativo = round(informe["por_sentimiento"].get("negativo", 0) / total * 100) if total else 0
    indices_servicio = informe.get("indices_servicio") or {}
    pct_limpieza = (indices_servicio.get("limpieza") or {}).get("pct")
    pct_mantenimiento = (indices_servicio.get("mantenimiento") or {}).get("pct")
    texto_limpieza = f"{pct_limpieza}%" if pct_limpieza is not None else "—"
    texto_mantenimiento = f"{pct_mantenimiento}%" if pct_mantenimiento is not None else "—"

    def _filas(d: dict) -> str:
        if not d:
            return f"<tr><td style='{estilo_celda}' colspan='3'>—</td></tr>"
        filas_ordenadas = sorted(d.items(), key=lambda kv: kv[1], reverse=True)
        return "".join(
            f"<tr><td style='{estilo_celda}'>{k}</td><td style='{estilo_celda}'>{v}</td>"
            f"<td style='{estilo_celda}'>{round(v / total * 100) if total else 0}%</td></tr>"
            for k, v in filas_ordenadas
        )

    # Preferir los campos por unidad (más detallados); si viniera de una
    # versión vieja del backend sin estos campos, usar los de propiedad.
    por_unidad = informe.get("por_unidad") or informe.get("por_propiedad") or {}
    confianza_baja_por_unidad = informe.get("confianza_baja_por_unidad") or informe.get("confianza_baja_por_propiedad") or {}
    sentimiento_negativo_por_unidad = informe.get("sentimiento_negativo_por_unidad") or informe.get("sentimiento_negativo_por_propiedad") or {}

    def _etiqueta_corta(etiqueta: str) -> str:
        """Para el eje del gráfico de barras — la etiqueta completa es
        'Propiedad — Número — Nombre de Airbnb'; para el gráfico alcanza
        con 'Propiedad — Número', sin el nombre de Airbnb que puede ser
        largo y amontonar el eje."""
        partes = etiqueta.split(" — ")
        return " — ".join(partes[:2]) if len(partes) > 2 else etiqueta

    def _filas_unidad() -> str:
        filas = ""
        for u, tipos in por_unidad.items():
            resumen = ", ".join(f"{t}: {n}" for t, n in sorted(tipos.items(), key=lambda kv: kv[1], reverse=True))
            filas += f"<tr><td style='{estilo_celda}'>{u}</td><td style='{estilo_celda}'>{resumen}</td></tr>"
        return filas or f"<tr><td style='{estilo_celda}' colspan='2'>—</td></tr>"

    def _filas_conteo_unidad(d: dict) -> str:
        if not d:
            return f"<tr><td style='{estilo_celda}' colspan='2'>Ninguna 🎉</td></tr>"
        filas_ordenadas = sorted(d.items(), key=lambda kv: kv[1], reverse=True)
        return "".join(
            f"<tr><td style='{estilo_celda}'>{u}</td><td style='{estilo_celda}'>{n}</td></tr>"
            for u, n in filas_ordenadas
        )

    def _lista_ejemplos(ejemplos: list) -> str:
        if not ejemplos:
            return "<p style='color:#94a3b8;font-size:13px;'>Ninguno este mes.</p>"
        items = "".join(
            f"<li style='margin-bottom:8px;'><strong>{e['propiedad']}</strong>: {e['mensaje']}</li>"
            for e in ejemplos
        )
        return f"<ul style='font-size:13px;padding-left:18px;'>{items}</ul>"

    def _celda_color(color: str, texto: str) -> str:
        fondos = {"verde": "#dcfce7", "amarillo": "#fef9c3", "rojo": "#fee2e2"}
        letras = {"verde": "#166534", "amarillo": "#854d0e", "rojo": "#991b1b"}
        return (
            f"<td style='{estilo_celda}background:{fondos.get(color, '#f8fafc')};"
            f"color:{letras.get(color, '#1e293b')};font-weight:600;'>{texto}</td>"
        )

    def _filas_rendimiento_unidad() -> str:
        filas_r = informe.get("rendimiento_por_unidad") or []
        if not filas_r:
            return "<tr><td colspan='3'>—</td></tr>"
        partes = []
        for r in filas_r:
            fila = f"<tr><td style='{estilo_celda}'>{r['unidad']} ({r['total_consultas']} consultas)</td>"
            fila += _celda_color(r["confianza_color"], f"{r['confianza_pct']}% confianza alta")
            fila += _celda_color(r["sentimiento_color"], f"{r['sentimiento_pct']}% sin sentimiento negativo")
            fila += "</tr>"
            partes.append(fila)
        return "".join(partes)

    def _tarjeta_kpi(valor: str, etiqueta: str, color: str) -> str:
        return f"""
        <td style="width:50%;padding:6px;">
          <div style="border:1px solid #e2e8f0;border-radius:8px;padding:14px 16px;background:#f8fafc;">
            <p style="font-size:26px;font-weight:800;color:{color};margin:0;">{valor}</p>
            <p style="font-size:11px;color:#64748b;margin:4px 0 0 0;text-transform:uppercase;letter-spacing:0.5px;">{etiqueta}</p>
          </div>
        </td>
        """

    estilo_marca_bg = "#1e3a8a"

    estilo_tabla = "border-collapse:collapse;width:100%;margin-bottom:24px;"
    estilo_celda = "border:1px solid #e2e8f0;padding:8px 12px;text-align:left;font-size:14px;"
    estilo_header = estilo_celda + "background:#f8fafc;font-weight:600;"

    def _img(b64: str) -> str:
        if not b64:
            return ""
        return f'<img src="data:image/png;base64,{b64}" width="380" style="display:block;margin:8px 0 16px 0;" />'

    img_tipo = _img(graficos_informe.grafico_circular(informe["por_tipo"], "Consultas por tipo"))
    img_sentimiento = _img(graficos_informe.grafico_circular(informe["por_sentimiento"], "Consultas por sentimiento"))
    img_confianza = _img(graficos_informe.grafico_circular(informe["por_confianza"], "Confianza de las respuestas"))
    por_unidad_corto = {_etiqueta_corta(u): tipos for u, tipos in por_unidad.items()}
    img_volumen = _img(graficos_informe.grafico_barras_volumen(por_unidad_corto, "Consultas por unidad"))
    rendimiento_corto = [{**r, "unidad": _etiqueta_corta(r["unidad"])} for r in (informe.get("rendimiento_por_unidad") or [])]
    img_confianza_unidad = _img(graficos_informe.grafico_barras_semaforo(
        rendimiento_corto, "confianza_pct", "confianza_color",
        "% de respuestas de confianza alta, por unidad", "% confianza alta",
    ))
    img_sentimiento_unidad = _img(graficos_informe.grafico_barras_semaforo(
        rendimiento_corto, "sentimiento_pct", "sentimiento_color",
        "% sin sentimiento negativo, por unidad", "% sin sentimiento negativo",
    ))

    def _seccion(html_interno: str) -> str:
        return f'<div style="page-break-inside:avoid;">{html_interno}</div>'

    return f"""
    <style>
      @page {{ size: letter; margin: 1.5cm; }}
      table {{ page-break-inside: avoid; }}
      h3 {{ page-break-after: avoid; }}
    </style>
    <div style="font-family:sans-serif;color:#1e293b;max-width:520px;">

      <div style="background:{estilo_marca_bg};padding:22px 24px;border-radius:8px;margin-bottom:24px;">
        <span style="color:#93c5fd;font-size:11px;letter-spacing:1.5px;">S.O.F.I.A. — Sistema Operativo de Fidelización e Información Avanzada</span><br/>
        <span style="color:white;font-size:22px;font-weight:700;line-height:2;">Informe mensual de atención al huésped</span><br/>
        <span style="color:#dbeafe;font-size:13px;">{nombre_mes.capitalize()} {informe['anio']} · Cliente: Zafiro Property Management</span>
      </div>

      <p style="font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#64748b;margin-bottom:10px;">Resumen ejecutivo</p>
      <table style="width:100%;border-collapse:collapse;margin-bottom:28px;">
        <tr>
          {_tarjeta_kpi(str(total), "Consultas totales", "#1e3a8a")}
          {_tarjeta_kpi(f"{pct_confianza_alta}%", "Confianza alta", "#16a34a" if pct_confianza_alta >= 70 else "#d97706" if pct_confianza_alta >= 40 else "#dc2626")}
        </tr>
        <tr>
          {_tarjeta_kpi(f"{pct_sentimiento_negativo}%", "Sentimiento negativo", "#dc2626" if pct_sentimiento_negativo >= 20 else "#d97706" if pct_sentimiento_negativo >= 10 else "#16a34a")}
          {_tarjeta_kpi(texto_limpieza, "Índice de limpieza", "#16a34a" if (pct_limpieza or 0) >= 70 else "#d97706" if (pct_limpieza or 0) >= 40 else "#dc2626")}
        </tr>
        <tr>
          {_tarjeta_kpi(texto_mantenimiento, "Índice de mantenimiento", "#16a34a" if (pct_mantenimiento or 0) >= 70 else "#d97706" if (pct_mantenimiento or 0) >= 40 else "#dc2626")}
          <td style="width:50%;"></td>
        </tr>
      </table>

      {_seccion(f'''
      <h3>Por tipo de consulta</h3>
      {img_tipo}
      <table style="{estilo_tabla}">
        <tr><th style="{estilo_header}">Tipo</th><th style="{estilo_header}">Cantidad</th><th style="{estilo_header}">%</th></tr>
        {_filas(informe['por_tipo'])}
      </table>
      ''')}

      {_seccion(f'''
      <h3>Por sentimiento</h3>
      {img_sentimiento}
      <table style="{estilo_tabla}">
        <tr><th style="{estilo_header}">Sentimiento</th><th style="{estilo_header}">Cantidad</th><th style="{estilo_header}">%</th></tr>
        {_filas(informe['por_sentimiento'])}
      </table>
      ''')}

      {_seccion(f'''
      <h3>Confianza de las respuestas del bot</h3>
      {img_confianza}
      <table style="{estilo_tabla}">
        <tr><th style="{estilo_header}">Confianza</th><th style="{estilo_header}">Cantidad</th><th style="{estilo_header}">%</th></tr>
        {_filas(informe['por_confianza'])}
      </table>
      ''')}

      {_seccion(f'''
      <h3>Índice de limpieza</h3>
      {_seccion_indice_servicio(indices_servicio.get("limpieza"), "Índice", estilo_celda, estilo_header, estilo_tabla)}
      ''')}

      {_seccion(f'''
      <h3>Índice de mantenimiento</h3>
      {_seccion_indice_servicio(indices_servicio.get("mantenimiento"), "Índice", estilo_celda, estilo_header, estilo_tabla)}
      ''')}

      {_seccion(f'''
      <h3>Por unidad</h3>
      {img_volumen}
      <table style="{estilo_tabla}">
        <tr><th style="{estilo_header}">Unidad</th><th style="{estilo_header}">Desglose</th></tr>
        {_filas_unidad()}
      </table>
      ''')}

      <h3>Rendimiento del bot por unidad</h3>
      <p style="color:#94a3b8;font-size:12px;">
        El detalle más fino — por casa/apartamento puntual, no solo por condominio. Verde = va bien,
        amarillo = revisar, rojo = necesita atención.
      </p>
      {img_confianza_unidad}
      {img_sentimiento_unidad}
      <table style="{estilo_tabla}">
        <tr>
          <th style="{estilo_header}">Unidad</th>
          <th style="{estilo_header}">Confianza</th>
          <th style="{estilo_header}">Sentimiento</th>
        </tr>
        {_filas_rendimiento_unidad()}
      </table>

      {_seccion(f'''
      <h3>⚠️ Unidades con más respuestas de baja confianza</h3>
      <p style="color:#94a3b8;font-size:12px;">Señal directa de dónde completar más datos — cruzalo con los avisos de la lista de propiedades en Admin.</p>
      <table style="{estilo_tabla}">
        <tr><th style="{estilo_header}">Unidad</th><th style="{estilo_header}">Respuestas de baja confianza</th></tr>
        {_filas_conteo_unidad(confianza_baja_por_unidad)}
      </table>
      ''')}

      {_seccion(f'''
      <h3>😟 Unidades con más sentimiento negativo</h3>
      <table style="{estilo_tabla}">
        <tr><th style="{estilo_header}">Unidad</th><th style="{estilo_header}">Consultas con sentimiento negativo</th></tr>
        {_filas_conteo_unidad(sentimiento_negativo_por_unidad)}
      </table>
      ''')}

      {_seccion(f'''
      <h3>Ejemplos de baja confianza este mes</h3>
      {_lista_ejemplos(informe['ejemplos_baja_confianza'])}
      ''')}

      <div style="border-top:1px solid #e2e8f0;margin-top:16px;padding-top:12px;">
        <p style="color:#94a3b8;font-size:11px;margin:0;">
          Generado automáticamente por S.O.F.I.A. el {datetime.now(timezone.utc).strftime('%d/%m/%Y')}. Documento
          confidencial — uso interno de Zafiro Property Management.
        </p>
      </div>
    </div>
    """


@router.get("/admin/informe-mensual/pdf")
async def informe_mensual_pdf(anio: int, mes: int, x_admin_key: str | None = Header(default=None)):
    _verificar_admin_key(x_admin_key)
    if not (1 <= mes <= 12):
        raise HTTPException(status_code=400, detail="mes debe estar entre 1 y 12")

    from io import BytesIO
    from xhtml2pdf import pisa

    informe = await supabase_client.generar_informe_mensual(anio, mes)
    html = _formatear_informe_html(informe)

    buffer = BytesIO()
    resultado = pisa.CreatePDF(html, dest=buffer)
    if resultado.err:
        raise HTTPException(status_code=500, detail="No se pudo generar el PDF del informe.")

    nombre_archivo = f"informe_{anio}-{mes:02d}.pdf"
    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo}"'},
    )


@router.post("/admin/informe-mensual/enviar")
async def enviar_informe_mensual(
    anio: int | None = None, mes: int | None = None, x_admin_key: str | None = Header(default=None)
):
    _verificar_admin_key(x_admin_key)

    if anio is None or mes is None:
        # Sin especificar, se manda el MES ANTERIOR (el que ya cerró) —
        # tiene sentido como default para un envío mensual de cierre.
        hoy = datetime.now(timezone.utc)
        primer_dia_mes_actual = hoy.replace(day=1)
        mes_pasado = primer_dia_mes_actual - timedelta(days=1)
        anio, mes = mes_pasado.year, mes_pasado.month

    destinatarios_raw = await supabase_client.obtener_config("informe_mensual_destinatarios")
    destinatarios = [d.strip() for d in (destinatarios_raw or "").split(",") if d.strip()]
    if not destinatarios:
        raise HTTPException(
            status_code=400,
            detail="No hay destinatarios configurados — agregalos en Admin → Configuración general.",
        )

    informe = await supabase_client.generar_informe_mensual(anio, mes)
    html = _formatear_informe_html(informe)
    asunto = f"Informe mensual Zafiro — {NOMBRES_MES[mes - 1].capitalize()} {anio}"

    ok = await email_client.enviar_email(destinatarios, asunto, html)
    if not ok:
        raise HTTPException(
            status_code=500,
            detail="No se pudo enviar el correo — revisá RESEND_API_KEY en Railway.",
        )
    return {"ok": True, "enviado_a": destinatarios, "anio": anio, "mes": mes}


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

CLAVES_CONFIG_EDITABLE = ["whatsapp_mantenimiento_default", "whatsapp_limpieza_default", "informe_mensual_destinatarios"]


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
