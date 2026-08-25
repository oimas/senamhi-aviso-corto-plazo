# ============================================================
# Descarga de capas de SIGRID (CENEPRED)
#
# Los servicios ArcGIS de SIGRID viven en sig.cenepred.gob.pe y exigen un
# token, pero el token no hay que pedirlo a nadie: el propio visor lo deja
# escrito en el HTML de su pagina, asi que se renueva solo en cada corrida.
#
#   python cenepred_sigrid.py --servicios
#   python cenepred_sigrid.py --capas Cartografia_Peligros
#   python cenepred_sigrid.py --descargar Cartografia_Peligros 5010300
#   python cenepred_sigrid.py --descargar Cartografia_Peligros --todas
# ============================================================

import os
import re
import sys
import json
import time
import logging
import argparse
import unicodedata

import requests

VISOR = ("https://sigrid.cenepred.gob.pe/sigridv3/mapa"
         "?xmin=-81&ymin=-18&xmax=-68&ymax=0")
BASE = "https://sig.cenepred.gob.pe/arcgis_server/rest/services"
CARPETA = "sigrid"
DESTINO = os.path.join("salidas", "cenepred")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Referer": "https://sigrid.cenepred.gob.pe/",
    "Origin": "https://sigrid.cenepred.gob.pe",
}

# Las capas raster no se pueden consultar como entidades; se listan pero
# se saltan al descargar.
TIPOS_VECTOR = ("esriGeometryPoint", "esriGeometryPolyline",
                "esriGeometryPolygon", "esriGeometryMultipoint")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("sigrid")

_token = None


def token(refrescar=False):
    """Token de sesion, leido del HTML del visor. Se cachea por corrida."""
    global _token
    if _token and not refrescar:
        return _token
    html = requests.get(VISOR, headers=HEADERS, timeout=60).text
    m = re.search(r'arcgis_token"\s*,\s*"([^"]+)"', html)
    if not m:
        raise RuntimeError(
            "No se encontro el token en el HTML del visor. "
            "Es probable que SIGRID haya cambiado su pagina.")
    _token = m.group(1)
    log.info("Token obtenido (%s...)", _token[:16])
    return _token


def api(url, **params):
    params.setdefault("f", "json")
    params["token"] = token()
    r = requests.get(url, params=params, headers=HEADERS, timeout=120)
    r.raise_for_status()
    d = r.json()
    if isinstance(d, dict) and d.get("error"):
        # Un token caducado se manifiesta como error 498/499.
        if d["error"].get("code") in (498, 499):
            log.warning("Token rechazado, renovando")
            params["token"] = token(refrescar=True)
            d = requests.get(url, params=params, headers=HEADERS,
                             timeout=120).json()
        if d.get("error"):
            raise RuntimeError(f"{url}: {d['error'].get('message')}")
    return d


def servicios():
    d = api(f"{BASE}/{CARPETA}")
    return [(s["name"].split("/")[-1], s["type"]) for s in d.get("services", [])]


def capas(servicio):
    """Lista (id, nombre, tipo, es_grupo) de un MapServer."""
    d = api(f"{BASE}/{CARPETA}/{servicio}/MapServer")
    salida = []
    for l in d.get("layers", []):
        grupo = bool(l.get("subLayerIds"))
        salida.append((l["id"], l["name"], l.get("geometryType") or "raster", grupo))
    return salida


def _nombre_archivo(servicio, lid, nombre):
    # Sin translitear, "Áreas de exposición" quedaria como "reas_de_exposici_n".
    limpio = unicodedata.normalize("NFKD", nombre)
    limpio = limpio.encode("ascii", "ignore").decode("ascii")
    limpio = re.sub(r"[^A-Za-z0-9]+", "_", limpio).strip("_").lower()
    return f"{servicio.lower()}_{lid}_{limpio}.geojson"


def descargar(servicio, lid, nombre="", destino=DESTINO, por_pagina=2000):
    """Baja una capa completa como GeoJSON, paginando."""
    url = f"{BASE}/{CARPETA}/{servicio}/MapServer/{lid}"
    meta = api(url)
    nombre = nombre or meta.get("name", str(lid))
    tipo = meta.get("geometryType")

    if tipo not in TIPOS_VECTOR:
        log.warning("Capa %s (%s) es raster: no se puede exportar como GeoJSON",
                    lid, nombre)
        return None

    total = api(f"{url}/query", where="1=1", returnCountOnly="true").get("count", 0)
    log.info("Capa %s | %s | %s entidades", lid, nombre, total)
    if not total:
        return None

    features = []
    offset = 0
    while offset < total:
        d = api(f"{url}/query", where="1=1", outFields="*", outSR="4326",
                resultOffset=offset, resultRecordCount=por_pagina, f="geojson")
        lote = d.get("features", [])
        if not lote:
            break
        features.extend(lote)
        offset += len(lote)
        log.info("   %d / %d", len(features), total)
        time.sleep(0.4)          # cortesia con el servidor

    os.makedirs(destino, exist_ok=True)
    ruta = os.path.join(destino, _nombre_archivo(servicio, lid, nombre))
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection",
                   "properties": {"servicio": servicio, "capa_id": lid,
                                  "nombre": nombre, "fuente": "SIGRID - CENEPRED"},
                   "features": features}, f, ensure_ascii=False)
    log.info("-> %s (%.0f KB)", ruta, os.path.getsize(ruta) / 1024)
    return ruta


def main():
    p = argparse.ArgumentParser(description="Descarga capas de SIGRID (CENEPRED)")
    p.add_argument("--servicios", action="store_true", help="lista los MapServer")
    p.add_argument("--capas", metavar="SERVICIO", help="lista las capas de un servicio")
    p.add_argument("--descargar", nargs="+", metavar=("SERVICIO", "ID"),
                   help="descarga una capa: SERVICIO ID")
    p.add_argument("--todas", action="store_true",
                   help="con --descargar SERVICIO, baja todas sus capas vectoriales")
    p.add_argument("--destino", default=DESTINO)
    a = p.parse_args()

    if a.servicios:
        for n, t in servicios():
            print(f"  {n:28s} {t}")
        return

    if a.capas:
        for lid, nombre, tipo, grupo in capas(a.capas):
            marca = "[grupo]" if grupo else ("[raster]" if tipo == "raster" else "")
            print(f"  {lid:>9}  {nombre[:48]:50s} {tipo[13:] or tipo:12s} {marca}")
        return

    if a.descargar:
        servicio = a.descargar[0]
        if a.todas:
            for lid, nombre, tipo, grupo in capas(servicio):
                if grupo or tipo not in TIPOS_VECTOR:
                    continue
                try:
                    descargar(servicio, lid, nombre, a.destino)
                except Exception as e:
                    log.error("Capa %s fallo: %s", lid, e)
        else:
            for lid in a.descargar[1:]:
                descargar(servicio, int(lid), destino=a.destino)
        return

    p.print_help()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error("%s", e)
        sys.exit(1)
