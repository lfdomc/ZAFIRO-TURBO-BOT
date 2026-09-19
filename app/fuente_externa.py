"""
Adaptador de fuente externa: hoy lee HTML de guia.zafiropm.com, mañana
se puede reemplazar por una llamada a una API real sin tocar
`app/admin.py` ni el resto del sistema — el contrato es siempre
"le doy una URL/id, me devuelve un dict con estos mismos campos".

Guardamos el resultado en `properties.datos.guiaExterna`, SEPARADO de
los campos curados a mano (quickInfo, rules, etc.) — es una foto de
referencia, no la fuente de verdad. Si el diseño de esa página cambia
algún día, esto puede dejar de encontrar cosas (devolverá campos
vacíos) pero nunca debería tirar el resto del sistema abajo.
"""
import re
import copy
import httpx
import logging
from bs4 import BeautifulSoup

logger = logging.getLogger("fuente_externa")

# Identifica el pedido honestamente — no se hace pasar por navegador.
# Es tráfico bajo (un fetch por propiedad, disparado a mano desde el
# panel Admin), nunca un rastreo masivo.
USER_AGENT = "ZafiroBot-Sync/1.0 (uso interno, sincroniza guia.zafiropm.com con Supabase)"


async def obtener_html(url: str) -> str:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        return resp.text


def _texto(el) -> str | None:
    return el.get_text(" ", strip=True) if el else None


def _url_de_background(style: str) -> str | None:
    if not style:
        return None
    m = re.search(r'url\(\s*["\']?([^"\')]+)["\']?\s*\)', style)
    return m.group(1) if m else None


def parsear_guia_zafiropm(html: str) -> dict:
    """Devuelve un dict con lo que se pudo extraer — nunca lanza
    excepción por un campo faltante, cada sección es independiente."""
    soup = BeautifulSoup(html, "html.parser")
    datos: dict = {}

    header = soup.select_one("header.ghero")
    if header:
        h1 = header.select_one("h1")
        if h1:
            datos["titulo"] = h1.get_text(strip=True)
        zona = header.select_one(".ghero-zone")
        if zona:
            datos["zona"] = zona.get_text(strip=True)

    bienvenida = soup.select_one("p.gwelcome")
    if bienvenida:
        datos["bienvenida"] = bienvenida.get_text(strip=True)

    fotos = []
    for a in soup.select("a.ggal-ph"):
        url_foto = _url_de_background(a.get("style", ""))
        if url_foto:
            fotos.append(url_foto)
    if fotos:
        datos["fotos"] = fotos

    for sec in soup.select("section.gsec"):
        h2 = sec.select_one("h2.gsec-h")
        titulo_sec = h2.get_text(strip=True) if h2 else ""

        if "Cómo llegar" in titulo_sec:
            direccion = sec.select_one("p.grow")
            if direccion:
                datos["direccion"] = direccion.get_text(strip=True)
            pin = sec.select_one(".gmap-pin b")
            if pin:
                datos["coordenadas"] = pin.get_text(strip=True)
            parrafos = sec.select("p.gtext")
            for p in parrafos:
                texto = p.get_text(" ", strip=True)
                if texto.lower().startswith("parqueo"):
                    datos["parqueo"] = texto.split(":", 1)[-1].strip()
                elif "como_llegar" not in datos:
                    datos["como_llegar"] = p.get_text("\n", strip=True)

        elif "Check-in y check-out" in titulo_sec:
            for bloque in sec.select(".gblock b"):
                texto = bloque.get_text(" ", strip=True)
                if "check-in" in texto.lower():
                    datos["checkin_hora"] = texto.split("·")[-1].strip()
                elif "check-out" in texto.lower():
                    datos["checkout_hora"] = texto.split("·")[-1].strip()

        elif "Antes de salir" in titulo_sec:
            items = [li.get_text(strip=True) for li in sec.select("ul.gchecklist li")]
            if items:
                datos["checklist_salida"] = items

        elif titulo_sec == "WiFi":
            wifi = {}
            for fila in sec.select(".gkv > div"):
                partes = fila.find_all(["span", "b"])
                if len(partes) >= 2:
                    wifi[partes[0].get_text(strip=True)] = partes[1].get_text(strip=True)
            if wifi:
                datos["wifi"] = wifi

        elif "Reglas de la casa" in titulo_sec:
            bloque_reglas = sec.select_one("p.gtext")
            if bloque_reglas:
                lineas = [r.strip() for r in bloque_reglas.get_text("\n").split("\n") if r.strip()]
                if lineas:
                    datos["reglas"] = lineas

        elif "Recomendaciones locales" in titulo_sec:
            lugares = []
            for a in sec.select("a.gplace"):
                lugares.append({
                    "nombre": _texto(a.select_one(".gplace-t")),
                    "categoria": _texto(a.select_one(".gplace-c")),
                    "rating": _texto(a.select_one(".gplace-r")),
                    "resenas": _texto(a.select_one(".gplace-n")),
                    "url": a.get("href"),
                })
            if lugares:
                datos["lugares_cercanos"] = lugares

        elif "Reservá experiencias" in titulo_sec:
            link_localbird = sec.select_one("a.gbtn.gold")
            if link_localbird and link_localbird.get("href"):
                datos["link_experiencias"] = link_localbird["href"]

    # WhatsApp de servicio al cliente (botón flotante, es de la propiedad puntual)
    fab = soup.select_one("a.gfab")
    if fab and fab.get("href", "").startswith("https://wa.me/"):
        m = re.search(r"wa\.me/(\d+)", fab["href"])
        if m:
            datos["whatsapp_servicio_cliente"] = f"+{m.group(1)}"

    # Link para dejar reseña en Airbnb
    for a in soup.select("a.gbtn.gold"):
        if "airbnb.com" in (a.get("href") or ""):
            datos["link_resena_airbnb"] = a["href"]
            break

    return datos


# ------------------------------------------------------------
# Comparación contra lo ya curado — nunca decide por vos, solo señala
# dónde hay una diferencia clara (campos cortos y estructurados) o
# dónde conviene mirar con tus propios ojos (contenido libre, donde
# una comparación de texto exacto daría falsos positivos/negativos).
# ------------------------------------------------------------

def _buscar_quick_info(quick_info: list, patron: str):
    for par in (quick_info or []):
        if len(par) >= 2 and re.search(patron, str(par[0]), re.I):
            return par[1]
    return None


def comparar_con_actual(guia: dict, prop_actual: dict) -> list[dict]:
    filas = []
    quick = prop_actual.get("quickInfo") or []
    unidades = prop_actual.get("units") or []
    unidad_unica = unidades[0] if len(unidades) == 1 else None

    def _fila_estructurada(campo, etiqueta, valor_actual, valor_guia, aplicable=True, nota=None):
        filas.append({
            "campo": campo, "etiqueta": etiqueta, "tipo": "estructurado",
            "valor_actual": valor_actual, "valor_guia": valor_guia,
            "hay_diferencia": (valor_actual or "").strip() != (valor_guia or "").strip(),
            "aplicable": aplicable, "nota": nota,
        })

    if guia.get("checkin_hora"):
        _fila_estructurada("checkin_hora", "Check-in", _buscar_quick_info(quick, r"check.?in"), guia["checkin_hora"])
    if guia.get("checkout_hora"):
        _fila_estructurada("checkout_hora", "Check-out", _buscar_quick_info(quick, r"check.?out"), guia["checkout_hora"])
    if guia.get("parqueo"):
        _fila_estructurada("parqueo", "Parqueo", _buscar_quick_info(quick, r"parqueo|parking"), guia["parqueo"])

    if guia.get("wifi"):
        red = guia["wifi"].get("Red", "")
        clave = guia["wifi"].get("Contraseña", "")
        valor_guia_wifi = f"Red: {red} · Contraseña: {clave}".strip(" ·")
        actual_wifi = None
        if unidad_unica:
            for par in (unidad_unica.get("extra") or []):
                if len(par) >= 2 and "wifi" in str(par[0]).lower():
                    actual_wifi = par[1]
                    break
        _fila_estructurada(
            "wifi", "WiFi", actual_wifi, valor_guia_wifi,
            aplicable=unidad_unica is not None,
            nota=None if unidad_unica else "Esta propiedad tiene varias unidades — actualizá el wifi a mano en la unidad correspondiente.",
        )

    # --- Contenido libre: se muestra para que lo mires, nunca se marca igual/diferente ---
    if guia.get("como_llegar"):
        filas.append({
            "campo": "como_llegar", "etiqueta": "Cómo llegar", "tipo": "libre",
            "valor_actual": (prop_actual.get("requisitosCheckIn") or {}).get("detalle"),
            "valor_guia": guia["como_llegar"], "hay_diferencia": None, "aplicable": True, "nota": None,
        })
    if guia.get("reglas"):
        actuales = prop_actual.get("rules") or (prop_actual.get("publicInfo") or {}).get("rules") or []
        filas.append({
            "campo": "reglas", "etiqueta": "Reglas de la casa", "tipo": "libre",
            "valor_actual": "\n".join(actuales) if actuales else None,
            "valor_guia": "\n".join(guia["reglas"]), "hay_diferencia": None, "aplicable": True, "nota": None,
        })

    # --- Informativo: no tiene campo equivalente hoy, solo para que lo veas ---
    if guia.get("lugares_cercanos"):
        nombres = ", ".join(l["nombre"] for l in guia["lugares_cercanos"] if l.get("nombre"))
        filas.append({
            "campo": "lugares_cercanos", "etiqueta": "Lugares cercanos", "tipo": "informativo",
            "valor_actual": None, "valor_guia": nombres, "hay_diferencia": None, "aplicable": False, "nota": None,
        })
    if guia.get("whatsapp_servicio_cliente"):
        filas.append({
            "campo": "whatsapp_servicio_cliente", "etiqueta": "WhatsApp servicio al cliente", "tipo": "informativo",
            "valor_actual": None, "valor_guia": guia["whatsapp_servicio_cliente"],
            "hay_diferencia": None, "aplicable": False, "nota": None,
        })

    return filas


# ------------------------------------------------------------
# Aplicar cambios elegidos a mano — nunca se llama automáticamente,
# solo cuando el admin tildó explícitamente qué campos aceptar.
# ------------------------------------------------------------

def _upsert_quick_info(quick_info: list, patron: str, etiqueta_nueva: str, valor: str) -> list:
    pares = [list(p) for p in (quick_info or [])]
    for p in pares:
        if len(p) >= 2 and re.search(patron, str(p[0]), re.I):
            p[1] = valor
            return pares
    pares.append([etiqueta_nueva, valor])
    return pares


def aplicar_cambios(prop_actual: dict, campos_a_aplicar: dict) -> dict:
    """campos_a_aplicar puede traer: checkin_hora, checkout_hora,
    parqueo (strings), wifi ({"Red":.., "Contraseña":..}), como_llegar
    (string), reglas (list[str]). Devuelve una COPIA de prop_actual con
    esos campos actualizados — nunca muta el original."""
    prop = copy.deepcopy(prop_actual)

    if "checkin_hora" in campos_a_aplicar:
        prop["quickInfo"] = _upsert_quick_info(prop.get("quickInfo"), r"check.?in", "Check in", campos_a_aplicar["checkin_hora"])
    if "checkout_hora" in campos_a_aplicar:
        prop["quickInfo"] = _upsert_quick_info(prop.get("quickInfo"), r"check.?out", "Check out", campos_a_aplicar["checkout_hora"])
    if "parqueo" in campos_a_aplicar:
        prop["quickInfo"] = _upsert_quick_info(prop.get("quickInfo"), r"parqueo|parking", "Parqueo", campos_a_aplicar["parqueo"])

    if "wifi" in campos_a_aplicar and len(prop.get("units") or []) == 1:
        red = campos_a_aplicar["wifi"].get("Red", "")
        clave = campos_a_aplicar["wifi"].get("Contraseña", "")
        valor = f"Red: {red} · Contraseña: {clave}".strip(" ·")
        unidad = prop["units"][0]
        extra = [list(p) for p in (unidad.get("extra") or [])]
        actualizado = False
        for p in extra:
            if len(p) >= 2 and "wifi" in str(p[0]).lower():
                p[1] = valor
                actualizado = True
                break
        if not actualizado:
            extra.append(["WiFi", valor])
        unidad["extra"] = extra

    if "como_llegar" in campos_a_aplicar:
        req = dict(prop.get("requisitosCheckIn") or {})
        req["detalle"] = campos_a_aplicar["como_llegar"]
        prop["requisitosCheckIn"] = req

    if "reglas" in campos_a_aplicar:
        prop["rules"] = campos_a_aplicar["reglas"]

    return prop


# ------------------------------------------------------------
# Pre-llenado para una propiedad NUEVA — arma un objeto con la misma
# forma que ya usa el resto del sistema, para que el admin lo revise
# y edite en el formulario antes de guardar (nunca se crea solo).
# ------------------------------------------------------------

def mapear_a_property_nueva(guia: dict, property_id: str, url_guia: str) -> dict:
    unidad = {"id": f"{property_id}-main", "name": guia.get("titulo", ""), "num": "", "pax": ""}
    if guia.get("wifi"):
        red = guia["wifi"].get("Red", "")
        clave = guia["wifi"].get("Contraseña", "")
        unidad["extra"] = [["WiFi", f"Red: {red} · Contraseña: {clave}".strip(" ·")]]

    quick_info = []
    if guia.get("checkin_hora"):
        quick_info.append(["Check in", guia["checkin_hora"]])
    if guia.get("checkout_hora"):
        quick_info.append(["Check out", guia["checkout_hora"]])
    if guia.get("parqueo"):
        quick_info.append(["Parqueo", guia["parqueo"]])

    return {
        "id": property_id,
        "name": guia.get("titulo", ""),
        "group": "sanjose",
        "zone": guia.get("zona", ""),
        "owner": "",
        "quickInfo": quick_info,
        "rules": guia.get("reglas", []),
        "requisitosCheckIn": {"resumen": "", "detalle": guia.get("como_llegar", ""), "link": "", "linkLabel": ""},
        "units": [unidad],
        "camposPersonalizados": {"link_guia_publica": url_guia},
    }
