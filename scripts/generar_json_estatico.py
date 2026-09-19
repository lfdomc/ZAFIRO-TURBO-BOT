"""
Exporta desde Supabase el mismo propiedades.json que ya consume
App.jsx — el sitio en React NUNCA consulta Supabase en tiempo de
ejecución del visitante. Corré esto antes de cada build/deploy del
sitio (mismo lugar donde antes corrías generar-propiedades.mjs, solo
que ahora la fuente es Supabase en vez del propio App.jsx).

Uso:
    python -m scripts.generar_json_estatico ruta/al/repo/react/public/propiedades.json
"""
import asyncio
import json
import sys

from app import supabase_client


async def main(ruta_salida: str):
    datos = await supabase_client.obtener_todo_para_export()
    if not datos:
        print("✘ No se pudo exportar (revisá SUPABASE_URL/SUPABASE_KEY).")
        sys.exit(1)

    with open(ruta_salida, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)

    print(f"✔ Exportadas {len(datos.get('properties', []))} propiedades a {ruta_salida}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python -m scripts.generar_json_estatico ruta/de/salida/propiedades.json")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
