"""
Carga (o re-carga) tu propiedades.json real a Supabase — usa la misma
lógica de indexación que el panel Admin (app/property_service.py), así
que ambos caminos generan exactamente los mismos fragmentos.

Uso:
    python -m scripts.ingest_propiedades ruta/a/propiedades.json

Reingestar el mismo archivo es seguro: se actualiza por id (upsert) y
se limpian los fragmentos viejos de cada propiedad antes de recrearlos
— no se acumulan duplicados. Para agregar UNA propiedad nueva sueltas,
generalmente es más rápido usar la pestaña Admin del sitio en vez de
este script.
"""
import asyncio
import json
import sys

from app import property_service


async def main(ruta_json: str):
    with open(ruta_json, "r", encoding="utf-8") as f:
        datos = json.load(f)

    print("\n== Información general de la compañía ==")
    await property_service.reindexar_general(datos)

    for prop in datos.get("properties", []):
        print(f"\n== {prop['name']} ({prop['id']}) ==")
        await property_service.guardar_y_reindexar_property(prop)

    print("\nIngesta completa.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python -m scripts.ingest_propiedades ruta/a/propiedades.json")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
