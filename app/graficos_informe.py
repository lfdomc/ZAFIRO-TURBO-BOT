"""
Genera los gráficos del informe mensual como imágenes PNG en base64,
para incrustar en el HTML del correo/PDF (xhtml2pdf renderiza <img
src="data:image/png;base64,..."> sin problema). matplotlib con backend
'Agg' — no necesita pantalla, funciona bien en un servidor.
"""
import base64
from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Estilo visual consistente en todos los gráficos — look limpio,
# corporativo, sin bordes de más ni grises por defecto de matplotlib.
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.size": 10,
    "axes.edgecolor": "#cbd5e1",
    "axes.labelcolor": "#334155",
    "axes.titlecolor": "#1e293b",
    "text.color": "#334155",
    "xtick.color": "#64748b",
    "ytick.color": "#64748b",
    "axes.grid": True,
    "grid.color": "#e2e8f0",
    "grid.linewidth": 0.6,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
})

COLOR_MARCA = "#1e3a8a"
COLOR_SEMAFORO = {"verde": "#16a34a", "amarillo": "#d97706", "rojo": "#dc2626"}
PALETA = ["#1e3a8a", "#2563eb", "#3b82f6", "#60a5fa", "#93c5fd", "#bfdbfe", "#dbeafe"]


def _quitar_bordes(ax):
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    ax.spines["left"].set_color("#cbd5e1")
    ax.spines["bottom"].set_color("#cbd5e1")


def _a_base64(fig) -> str:
    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=110)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def grafico_circular(datos: dict, titulo: str) -> str:
    """Gráfico de dona (más prolijo que un pastel completo) — para
    distribuciones (tipo, sentimiento, confianza). Devuelve '' si no
    hay datos, para no mostrar un gráfico vacío."""
    datos_validos = {k: v for k, v in datos.items() if v > 0}
    if not datos_validos:
        return ""
    etiquetas = list(datos_validos.keys())
    valores = list(datos_validos.values())
    fig, ax = plt.subplots(figsize=(4.4, 4.4))
    wedges, _, autotextos = ax.pie(
        valores, autopct="%1.0f%%", startangle=90, pctdistance=0.8,
        colors=PALETA[: len(etiquetas)],
        wedgeprops={"width": 0.42, "edgecolor": "white", "linewidth": 2},
        textprops={"fontsize": 10, "color": "white", "fontweight": "bold"},
    )
    ax.legend(
        wedges, etiquetas, loc="center left", bbox_to_anchor=(1.0, 0.5),
        frameon=False, fontsize=9.5,
    )
    ax.set_title(titulo, fontsize=13.5, fontweight="bold", pad=14)
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
    ax.set_title(titulo, fontsize=13.5, fontweight="bold", pad=12)
    ax.invert_yaxis()  # la primera unidad de la lista arriba del todo
    ax.grid(axis="y", visible=False)
    _quitar_bordes(ax)
    for barra, valor in zip(barras, valores):
        ax.text(min(valor + 2, 96), barra.get_y() + barra.get_height() / 2, f"{valor}%",
                 va="center", fontsize=9.5, fontweight="bold", color="#1e293b")
    fig.tight_layout()
    return _a_base64(fig)


def grafico_barras_volumen(por_propiedad: dict, titulo: str) -> str:
    """Barras verticales con el total de consultas por propiedad — para
    ver de un vistazo cuáles propiedades generan más tráfico."""
    if not por_propiedad:
        return ""
    totales = {prop: sum(tipos.values()) for prop, tipos in por_propiedad.items()}
    totales = dict(sorted(totales.items(), key=lambda kv: kv[1], reverse=True))
    etiquetas = list(totales.keys())
    valores = list(totales.values())

    fig, ax = plt.subplots(figsize=(6.5, 4))
    barras = ax.bar(etiquetas, valores, color=COLOR_MARCA, width=0.55, zorder=3)
    ax.set_ylabel("Consultas", fontsize=10)
    ax.set_title(titulo, fontsize=13.5, fontweight="bold", pad=12)
    ax.grid(axis="x", visible=False)
    _quitar_bordes(ax)
    for barra, valor in zip(barras, valores):
        ax.text(barra.get_x() + barra.get_width() / 2, barra.get_height() + max(valores) * 0.02,
                 str(valor), ha="center", fontsize=9.5, fontweight="bold", color="#1e293b")
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right", fontsize=9.5)
    fig.tight_layout()
    return _a_base64(fig)
