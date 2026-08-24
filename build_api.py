# ============================================================
# Endpoints estaticos (JSON/GeoJSON) publicados en el propio repo
#
# Se escriben en api/ y el workflow los commitea. Quedan servidos por
# raw.githubusercontent.com y, si activas Pages, tambien por HTTPS propio.
#
#   api/index.json               catalogo: fechas disponibles + ids de GEE
#   api/latest.json              metadatos del aviso mas reciente
#   api/latest.geojson           poligonos del aviso mas reciente
#   api/avisos/<fecha>.geojson   historico, un archivo por fecha
#
# Son archivos planos: cualquier servidor externo puede replicarlos o
# consumirlos por HTTP sin credenciales de Earth Engine.
# ============================================================

import os
import json
import glob
import logging
from datetime import datetime, timezone

log = logging.getLogger("senamhi.api")

API_DIR = "api"
FUENTE = "https://www.senamhi.gob.pe/?p=aviso-24H"

NIVELES_DOC = {
    "0": "SIN AVISO",
    "1": "VERDE",
    "2": "AMARILLO",
    "3": "NARANJA",
    "4": "ROJO",
}

# Atributos del shapefile que se exponen en el GeoJSON.
CAMPOS = ("NIVEL", "FECHA", "DESCRIPCIO", "RECOMENDAC", "RESPONS")

# Los poligonos de SENAMHI traen ~86k vertices por aviso (3.5 MB de GeoJSON).
# Para un indice de 1 km eso es ruido: con 0.005 grados (~550 m) el archivo
# baja a ~140 KB y el area cambia 0.013%. En Earth Engine se sube completo.
SIMPLIFICAR = float(os.environ.get("API_SIMPLIFY", "0.005"))
DECIMALES = 5


def _escribir(path, data, compacto=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        if compacto:  # los GeoJSON pesan el triple con sangria
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(data, f, ensure_ascii=False, indent=2)
    log.info("Escrito %s (%.0f KB)", path, os.path.getsize(path) / 1024)


def _valor(v):
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, (int, float, str)):
        return v
    return str(v)


def _redondear(obj):
    """Recorta decimales de las coordenadas sin tocar el resto del mapping."""
    if isinstance(obj, float):
        return round(obj, DECIMALES)
    if isinstance(obj, (list, tuple)):
        return [_redondear(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _redondear(v) for k, v in obj.items()}
    return obj


def registro_a_geojson(registro):
    from shapely.geometry import mapping

    gdf = registro["gdf"]
    features = []
    for _, fila in gdf.iterrows():
        geom = fila.geometry
        if geom is None or geom.is_empty:
            continue
        if SIMPLIFICAR > 0:
            geom = geom.simplify(SIMPLIFICAR, preserve_topology=True)
            if geom.is_empty:
                continue
        props = {c: _valor(fila.get(c)) for c in CAMPOS if c in gdf.columns}
        props["nivel"] = int(fila.get("_nivel_num", 0))
        props["nivel_nombre"] = str(fila.get("_nivel", "DESCONOCIDO"))
        props["fecha"] = registro["fecha_iso"]
        props["aviso_num"] = str(registro["aviso"])
        features.append({
            "type": "Feature",
            "properties": props,
            "geometry": _redondear(mapping(geom)),
        })

    minx, miny, maxx, maxy = gdf.total_bounds
    return {
        "type": "FeatureCollection",
        "bbox": [round(float(v), 6) for v in (minx, miny, maxx, maxy)],
        "properties": {
            "fecha": registro["fecha_iso"],
            "aviso_num": str(registro["aviso"]),
            "nivel_max": int(registro["nivel_max"]),
            "fuente": FUENTE,
        },
        "features": features,
    }


def resumen(registro, geojson):
    conteo = {}
    for f in geojson["features"]:
        nombre = f["properties"]["nivel_nombre"]
        conteo[nombre] = conteo.get(nombre, 0) + 1
    return {
        "fecha": registro["fecha_iso"],
        "aviso_num": str(registro["aviso"]),
        "nivel_max": int(registro["nivel_max"]),
        "nivel_max_nombre": NIVELES_DOC.get(str(registro["nivel_max"]), "SIN AVISO"),
        "n_poligonos": len(geojson["features"]),
        "poligonos_por_nivel": conteo,
        "bbox": geojson["bbox"],
        "geojson": f"avisos/{registro['fecha_iso']}.geojson",
    }


def catalogo(base_dir, assets, ultimo):
    """Reconstruye index.json escaneando el historico ya presente en el repo."""
    fechas = sorted(
        os.path.basename(p)[:-len(".geojson")]
        for p in glob.glob(os.path.join(base_dir, "avisos", "*.geojson"))
    )
    return {
        "nombre": "SENAMHI - Avisos de lluvias intensas (Peru)",
        "descripcion": (
            "Indice diario de nivel de aviso por lluvias intensas. "
            "0 = sin aviso, 4 = rojo."
        ),
        "fuente": FUENTE,
        "generado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "niveles": NIVELES_DOC,
        "gee": assets or {},
        "ultimo": ultimo,
        "n_fechas": len(fechas),
        "fechas": fechas,
        "endpoints": {
            "catalogo": "index.json",
            "ultimo_meta": "latest.json",
            "ultimo_geojson": "latest.geojson",
            "historico": "avisos/{fecha}.geojson",
        },
    }


def generar(registros, assets=None, base_dir=API_DIR):
    """Escribe todos los endpoints. Devuelve las rutas creadas."""
    if not registros:
        log.warning("Sin registros: no se genera API")
        return []

    ordenados = sorted(registros, key=lambda r: r["fecha_iso"])
    rutas = []
    ultimo_resumen = None

    for r in ordenados:
        gj = registro_a_geojson(r)
        path = os.path.join(base_dir, "avisos", f"{r['fecha_iso']}.geojson")
        _escribir(path, gj, compacto=True)
        rutas.append(path)
        ultimo_resumen = resumen(r, gj)
        ultimo_gj = gj

    _escribir(os.path.join(base_dir, "latest.geojson"), ultimo_gj, compacto=True)
    _escribir(os.path.join(base_dir, "latest.json"), ultimo_resumen)
    _escribir(os.path.join(base_dir, "index.json"),
              catalogo(base_dir, assets, ultimo_resumen))

    rutas += [
        os.path.join(base_dir, "latest.geojson"),
        os.path.join(base_dir, "latest.json"),
        os.path.join(base_dir, "index.json"),
    ]
    return rutas
