"""
Genera los gráficos del informe (PDF) como imágenes PNG en base64, para
incrustar en el HTML (xhtml2pdf renderiza <img
src="data:image/png;base64,..."> sin problema). matplotlib con backend
'Agg' — no necesita pantalla, funciona bien en un servidor.

Paleta y tema oscuro IDÉNTICOS a los del Dashboard en vivo (App.jsx:
PALETA_DASH, COLOR_SENTIMIENTO, COLOR_CONFIANZA, COLOR_SEMAFORO_DASH) —
a propósito, para que el PDF se sienta como una foto del Dashboard, no
como un informe corporativo aparte con otra paleta.
"""
import base64
from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

FONDO_TARJETA = "#131c2e"
BORDE_TARJETA = "#334155"
TEXTO_CLARO = "#e5eaf3"
TEXTO_TENUE = "#94a3b8"

# Estilo visual consistente en todos los gráficos — mismo tema oscuro
# navy del Dashboard, no el look corporativo claro de antes.
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 10,
    "axes.edgecolor": BORDE_TARJETA,
    "axes.labelcolor": TEXTO_TENUE,
    "axes.titlecolor": TEXTO_CLARO,
    "text.color": TEXTO_CLARO,
    "xtick.color": TEXTO_TENUE,
    "ytick.color": TEXTO_TENUE,
    "axes.grid": True,
    "grid.color": "#1e293b",
    "grid.linewidth": 0.6,
    "figure.facecolor": FONDO_TARJETA,
    "axes.facecolor": FONDO_TARJETA,
    "savefig.facecolor": FONDO_TARJETA,
})

COLOR_ACENTO = "#e1543c"  # naranja de marca — barras de volumen, igual que BarraViva en el Dashboard
COLOR_SEMAFORO = {"verde": "#16a34a", "amarillo": "#d97706", "rojo": "#dc2626"}
PALETA = ["#1e3a8a", "#2563eb", "#3b82f6", "#60a5fa", "#93c5fd", "#bfdbfe"]
COLOR_SENTIMIENTO = {"positivo": "#16a34a", "neutral": "#2563eb", "negativo": "#dc2626"}
COLOR_CONFIANZA = {"alta": "#16a34a", "media": "#d97706", "baja": "#dc2626"}


def _quitar_bordes(ax):
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    ax.spines["left"].set_color(BORDE_TARJETA)
    ax.spines["bottom"].set_color(BORDE_TARJETA)


def _a_base64(fig) -> str:
    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=110, facecolor=fig.get_facecolor())
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def imagen_solida(color_hex: str, ancho: int = 850, alto: int = 1100) -> str:
    """PNG sólido de un color, en base64 — se usa como fondo de página
    completo del PDF (xhtml2pdf no pinta el fondo de página con
    "@page { background-color }" ni con "html, body { background }" —
    probado, no tiene efecto — pero SÍ soporta "@page { background-image }",
    así que le damos una imagen del tamaño de la hoja en vez de un color)."""
    from PIL import Image

    color_hex = color_hex.lstrip("#")
    rgb = tuple(int(color_hex[i:i + 2], 16) for i in (0, 2, 4))
    img = Image.new("RGB", (ancho, alto), rgb)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def grafico_circular(datos: dict, titulo: str, colores_por_etiqueta: dict | None = None) -> str:
    """Gráfico de dona (más prolijo que un pastel completo) — para
    distribuciones (tipo, sentimiento, confianza). Si se pasa
    `colores_por_etiqueta` (ej. COLOR_SENTIMIENTO, COLOR_CONFIANZA), se
    usa ESE color por etiqueta — igual que el Dashboard en vivo, donde
    "sentimiento" y "confianza" tienen colores con significado (verde/
    ámbar/rojo) en vez de la paleta azul genérica de "por tipo".
    Devuelve '' si no hay datos, para no mostrar un gráfico vacío."""
    datos_validos = {k: v for k, v in datos.items() if v > 0}
    if not datos_validos:
        return ""
    etiquetas = list(datos_validos.keys())
    valores = list(datos_validos.values())
    if colores_por_etiqueta:
        colores = [colores_por_etiqueta.get(e, "#94a3b8") for e in etiquetas]
    else:
        colores = [PALETA[i % len(PALETA)] for i in range(len(etiquetas))]

    fig, ax = plt.subplots(figsize=(4.4, 4.4))
    wedges, _, autotextos = ax.pie(
        valores, autopct="%1.0f%%", startangle=90, pctdistance=0.8,
        colors=colores,
        wedgeprops={"width": 0.42, "edgecolor": FONDO_TARJETA, "linewidth": 2},
        textprops={"fontsize": 10, "color": "white", "fontweight": "bold"},
    )
    leyenda = ax.legend(
        wedges, etiquetas, loc="center left", bbox_to_anchor=(1.0, 0.5),
        frameon=False, fontsize=9.5, labelcolor=TEXTO_CLARO,
    )
    ax.set_title(titulo, fontsize=13.5, fontweight="bold", pad=14, color=TEXTO_CLARO)
    return _a_base64(fig)


def grafico_barras_semaforo(rendimiento: list, campo_pct: str, campo_color: str, titulo: str, etiqueta_x: str) -> str:
    """Barras horizontales, una por unidad, coloreadas según su nivel
    (verde/amarillo/rojo) — para el rendimiento del bot por unidad."""
    if not rendimiento:
        return ""
    etiquetas = [r["unidad"] for r in rendimiento]
    valores = [r[campo_pct] for r in rendimiento]
    colores = [COLOR_SEMAFORO.get(r[campo_color], "#94a3b8") for r in rendimiento]

    alto = max(2.2, 0.5 * len(etiquetas))
    fig, ax = plt.subplots(figsize=(6.5, alto))
    barras = ax.barh(etiquetas, valores, color=colores, height=0.6, zorder=3)
    ax.set_xlim(0, 100)
    ax.set_xlabel(etiqueta_x, fontsize=10)
    ax.set_title(titulo, fontsize=13.5, fontweight="bold", pad=12, color=TEXTO_CLARO)
    ax.invert_yaxis()  # la primera unidad de la lista arriba del todo
    ax.grid(axis="y", visible=False)
    _quitar_bordes(ax)
    for barra, valor in zip(barras, valores):
        ax.text(min(valor + 2, 96), barra.get_y() + barra.get_height() / 2, f"{valor}%",
                 va="center", fontsize=9.5, fontweight="bold", color=TEXTO_CLARO)
    fig.tight_layout()
    return _a_base64(fig)


def grafico_barras_volumen(por_propiedad: dict, titulo: str) -> str:
    """Barras verticales con el total de consultas por propiedad — para
    ver de un vistazo cuáles propiedades generan más tráfico. Naranja
    de marca, igual que las barras de "Consultas por unidad" del
    Dashboard en vivo."""
    if not por_propiedad:
        return ""
    totales = {prop: sum(tipos.values()) for prop, tipos in por_propiedad.items()}
    totales = dict(sorted(totales.items(), key=lambda kv: kv[1], reverse=True))
    etiquetas = list(totales.keys())
    valores = list(totales.values())

    fig, ax = plt.subplots(figsize=(6.5, 4))
    barras = ax.bar(etiquetas, valores, color=COLOR_ACENTO, width=0.55, zorder=3)
    ax.set_ylabel("Consultas", fontsize=10)
    ax.set_title(titulo, fontsize=13.5, fontweight="bold", pad=12, color=TEXTO_CLARO)
    ax.grid(axis="x", visible=False)
    _quitar_bordes(ax)
    for barra, valor in zip(barras, valores):
        ax.text(barra.get_x() + barra.get_width() / 2, barra.get_height() + max(valores) * 0.02,
                 str(valor), ha="center", fontsize=9.5, fontweight="bold", color=TEXTO_CLARO)
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right", fontsize=9.5)
    fig.tight_layout()
    return _a_base64(fig)
