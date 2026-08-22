# ============================================================
# SENAMHI - Avisos de Lluvias Intensas: scraping + Earth Engine
# Corre desatendido (GitHub Actions) o local: python senamhi_avisos.py
# ============================================================

import io
import os
import re
import sys
import time
import tempfile
import logging
import zipfile
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.senamhi.gob.pe"
AVISO_URL = f"{BASE_URL}/?p=aviso-24H"
OUTPUT_DIR = "salidas"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

NIVEL_POR_VALOR = {
    "4": "ROJO", "ROJO": "ROJO", "ROJA": "ROJO",
    "3": "NARANJA", "NARANJA": "NARANJA",
    "2": "AMARILLO", "AMARILLO": "AMARILLO",
    "1": "VERDE", "VERDE": "VERDE", "NORMAL": "VERDE", "0": "VERDE",
}
NIVELES = ["ROJO", "NARANJA", "AMARILLO", "VERDE"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("senamhi")


def mapear_nivel(valor):
    v = str(valor).upper().strip()
    return NIVEL_POR_VALOR.get(v, "DESCONOCIDO")


def detectar_columna_alerta(gdf):
    for col in gdf.columns:
        if col.upper() in ("NIVEL", "ALERTA", "PELIGRO"):
            return col
    for col in gdf.columns:
        if col.startswith("_") or col == "geometry":
            continue
        valores = {str(v).strip() for v in gdf[col].dropna().unique()}
        if valores & set(NIVEL_POR_VALOR):
            return col
    return None


# ── Scraping ────────────────────────────────────────────────

def get_current_aviso():
    log.info("Conectando a %s ...", AVISO_URL)
    resp = requests.get(AVISO_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    aviso = {}

    h2 = soup.find("h2", class_="desaparecerHR")
    if h2:
        texto = h2.get_text(" ", strip=True)
        m = re.search(r"N°(\d+)\s*-\s*(\d+)", texto)
        if m:
            aviso["numero"] = m.group(1)
            aviso["anio"] = m.group(2)
        span = h2.find("span")
        aviso["nivel_alerta"] = span.get_text(strip=True) if span else "DESCONOCIDO"

    for row in soup.select(".row"):
        label = row.find("strong")
        if label and "Fecha de inicio" in label.get_text():
            s = row.find("span", class_="text-info")
            aviso["fecha_inicio"] = s.get_text(strip=True) if s else ""
            break

    regiones = ["SIERRA", "SELVA", "COSTA"]
    descripciones = {}
    for p in soup.select("article .alerta p"):
        b = p.find("b")
        if b and b.get_text(strip=True).upper() in regiones:
            descripciones[b.get_text(strip=True).upper()] = p.get_text(" ", strip=True)
    aviso["descripciones"] = descripciones

    tabla_rows = []
    table = soup.find("table", class_="table-descripcion")
    if table:
        for tr in table.find_all("tr")[1:]:
            cols = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cols) == 5:
                tabla_rows.append({
                    "region": cols[0], "tipo_pp": cols[1], "max_mm": cols[2],
                    "probabilidad": cols[3], "fenomenos": cols[4],
                })
    aviso["tabla_pp"] = tabla_rows
    return aviso, soup


def get_shapefile_links(soup):
    shapefiles = []
    table = soup.find("table", {"id": "table_shapes"})
    if not table:
        log.warning("No se encontro la tabla de shapefiles")
        return shapefiles
    for tr in table.find_all("tr")[1:]:
        cols = tr.find_all("td")
        if len(cols) >= 3:
            fecha = cols[0].get_text(strip=True)
            nro = cols[1].get_text(strip=True).zfill(3)
            a_tag = cols[2].find("a")
            href = a_tag["href"] if a_tag else None
            if href:
                shapefiles.append({
                    "fecha": fecha,
                    "numero_aviso": nro,
                    "url_zip": urljoin(BASE_URL, href),
                })
    return shapefiles


def extraer_zip_seguro(zip_bytes, destino):
    base = os.path.realpath(destino)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        if zf.testzip() is not None:
            raise ValueError("ZIP corrupto")
        for nombre in zf.namelist():
            ruta = os.path.realpath(os.path.join(destino, nombre))
            if not ruta.startswith(base + os.sep):
                raise ValueError(f"Ruta peligrosa en el zip: {nombre}")
        zf.extractall(destino)


def download_shapefiles(shapefiles, n, force=False):
    resultados = []
    for entry in shapefiles[:n]:
        dest = os.path.join(OUTPUT_DIR, "shapefiles", f"aviso_{entry['numero_aviso']}")
        marcador = os.path.join(dest, ".ok")

        if os.path.exists(marcador) and not force:
            log.info("Aviso %s ya descargado, saltando", entry["numero_aviso"])
            entry["local_folder"] = dest
            resultados.append(entry)
            continue

        try:
            log.info("Descargando aviso %s (%s)", entry["numero_aviso"], entry["fecha"])
            r = requests.get(entry["url_zip"], headers=HEADERS, timeout=60)
            r.raise_for_status()
            os.makedirs(dest, exist_ok=True)
            extraer_zip_seguro(r.content, dest)
            open(marcador, "w").close()
            entry["local_folder"] = dest
            resultados.append(entry)
            time.sleep(1)
        except Exception as e:
            log.error("Error descargando aviso %s: %s", entry["numero_aviso"], e)
    return resultados


def find_shp(folder):
    resultado = []
    for root, _, files in os.walk(folder):
        for f in files:
            if f.lower().endswith(".shp"):
                resultado.append(os.path.join(root, f))
    return resultado


# ── Carga de shapefiles ─────────────────────────────────────

def load_shapefiles(descargados):
    import geopandas as gpd
    import pandas as pd

    registros = []
    for entry in descargados:
        folder = entry.get("local_folder", "")
        for shp_path in find_shp(folder):
            try:
                gdf = gpd.read_file(shp_path)
                if gdf.empty:
                    continue
                if gdf.crs and gdf.crs.to_epsg() != 4326:
                    gdf = gdf.to_crs(epsg=4326)

                columna = detectar_columna_alerta(gdf)
                if columna is not None:
                    gdf["_nivel"] = gdf[columna].apply(mapear_nivel)
                else:
                    gdf["_nivel"] = "DESCONOCIDO"
                    log.warning("%s sin columna de alerta reconocible", shp_path)

                gdf["_aviso_numero"] = entry["numero_aviso"]
                gdf["_aviso_fecha"] = entry["fecha"]
                registros.append({
                    "gdf": gdf,
                    "aviso": entry["numero_aviso"],
                    "fecha": entry["fecha"],
                    "archivo": os.path.basename(shp_path),
                })
                conteo = gdf["_nivel"].value_counts().to_dict()
                log.info("Aviso %s | %s -> %s (%d poligonos)",
                         entry["numero_aviso"], os.path.basename(shp_path), conteo, len(gdf))
            except Exception as e:
                log.error("Error leyendo %s: %s", shp_path, e)
    return registros


def combinar_gdfs(registros):
    import geopandas as gpd
    import pandas as pd

    lista = [r["gdf"] for r in registros]
    if not lista:
        return gpd.GeoDataFrame()
    return gpd.GeoDataFrame(pd.concat(lista, ignore_index=True), crs="EPSG:4326")


# ── Salidas locales (Excel + mapa HTML) ─────────────────────

def generar_salidas_locales(aviso, registros):
    import pandas as pd

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    archivos = []

    filas = []
    for r in registros:
        niveles = r["gdf"]["_nivel"].value_counts().to_dict()
        for nivel, cantidad in niveles.items():
            filas.append({
                "aviso": r["aviso"], "fecha": r["fecha"],
                "shapefile": r["archivo"], "nivel": nivel,
                "n_poligonos": int(cantidad),
            })

    path_excel = os.path.join(OUTPUT_DIR, f"senamhi_aviso_{aviso.get('numero', 'na')}.xlsx")
    try:
        with pd.ExcelWriter(path_excel, engine="openpyxl") as writer:
            pd.DataFrame([
                {"Campo": "Numero Aviso", "Valor": aviso.get("numero", "")},
                {"Campo": "Anio", "Valor": aviso.get("anio", "")},
                {"Campo": "Nivel de Alerta", "Valor": aviso.get("nivel_alerta", "")},
                {"Campo": "Fecha Inicio", "Valor": aviso.get("fecha_inicio", "")},
                {"Campo": "Extraccion", "Valor": datetime.now().strftime("%Y-%m-%d %H:%M")},
            ]).to_excel(writer, sheet_name="Resumen Aviso", index=False)
            if aviso.get("tabla_pp"):
                pd.DataFrame(aviso["tabla_pp"]).to_excel(writer, sheet_name="Tabla PP", index=False)
            if filas:
                pd.DataFrame(filas).to_excel(writer, sheet_name="Shapefiles", index=False)
        archivos.append(path_excel)
        log.info("Excel generado: %s", path_excel)
    except Exception as e:
        log.error("No se pudo generar el Excel: %s", e)

    path_geojson = os.path.join(OUTPUT_DIR, "avisos.geojson")
    try:
        combinar_gdfs(registros).drop(columns=["_nivel"], errors="ignore").to_file(
            path_geojson, driver="GeoJSON")
        archivos.append(path_geojson)
        log.info("GeoJSON generado: %s", path_geojson)
    except Exception as e:
        log.error("No se pudo generar el GeoJSON: %s", e)

    return archivos


# ── Google Earth Engine ─────────────────────────────────────

def inicializar_gee():
    import ee

    key_json = os.environ.get("GEE_SA_JSON", "").strip()
    project = os.environ.get("GEE_PROJECT", "").strip()
    if not key_json or not project:
        raise RuntimeError(
            "Faltan las variables GEE_SA_JSON y/o GEE_PROJECT. "
            "Configura los secrets en GitHub o exporta las variables de entorno.")

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    tmp.write(key_json)
    tmp.close()

    credenciales = ee.ServiceAccountCredentials(None, key_file=tmp.name)
    ee.Initialize(project=project, credentials=credenciales)
    log.info("Earth Engine inicializado (proyecto: %s)", project)
    return ee


def sanitizar_props(props):
    limpio = {}
    for k, v in props.items():
        if k.startswith("_"):
            continue
        if v is None or (isinstance(v, float) and v != v):
            continue
        if isinstance(v, (int, float, str)):
            limpio[k] = v
        elif isinstance(v, bool):
            limpio[k] = int(v)
        else:
            limpio[k] = str(v)
    return limpio


def gdf_a_featurecollection(ee, gdf, nivel_por_indice=None):
    features = []
    for idx, (_, fila) in enumerate(gdf.iterrows()):
        if fila.geometry is None or fila.geometry.is_empty:
            continue
        props = sanitizar_props({k: v for k, v in fila.items() if k != "geometry"})
        if nivel_por_indice is not None:
            props["NIVEL_NOMBRE"] = nivel_por_indice[idx]
        features.append(ee.Feature(ee.Geometry(fila.geometry.__geo_interface__), props))
    return ee.FeatureCollection(features)


def exportar_a_gee(registros):
    import geopandas as gpd
    import pandas as pd
    import ee

    prefijo = os.environ.get("ASSET_PREFIX", "").rstrip("/")
    if not prefijo:
        raise RuntimeError("Falta ASSET_PREFIX (ej: users/tu_usuario/senamhi/aviso)")

    ee = inicializar_gee()
    tareas = []
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    objetivos = []
    for r in registros:
        gdf = r["gdf"]
        fc = gdf_a_featurecollection(ee, gdf, nivel_por_indice=list(gdf["_nivel"]))
        num = re.sub(r"[^A-Za-z0-9_-]", "", str(r["aviso"]))
        fech = re.sub(r"[^A-Za-z0-9_-]", "", str(r["fecha"]))
        objetivos.append((fc, f"{prefijo}_{num}_{fech}", f"aviso_{num}"))
        objetivos.append((fc, f"{prefijo}_latest", f"aviso_latest_{num}"))

    for fc, asset_id, desc in objetivos:
        tarea = ee.batch.Export.table.toAsset(
            collection=fc,
            description=f"SENAMHI_{desc}_{stamp}",
            assetId=asset_id,
            fileFormat="GeoJSON",
        )
        tarea.start()
        tareas.append((tarea, asset_id))
        log.info("Tarea iniciada -> %s", asset_id)

    fallidas = []
    for tarea, asset_id in tareas:
        inicio = time.time()
        while time.time() - inicio < 600:
            estado = tarea.status()
            state = estado.get("state")
            if state == "COMPLETED":
                log.info("OK: %s", asset_id)
                break
            if state in ("FAILED", "CANCELLED"):
                log.error("Fallo %s: %s", asset_id, estado.get("error_message"))
                fallidas.append(asset_id)
                break
            time.sleep(10)
        else:
            log.error("Timeout esperando %s", asset_id)
            fallidas.append(asset_id)

    if fallidas:
        raise RuntimeError(f"Tareas GEE fallidas: {fallidas}")


# ── Pipeline principal ──────────────────────────────────────

def main():
    n_avisos = int(os.environ.get("N_AVISOS", "5"))

    aviso, soup = get_current_aviso()
    log.info("Aviso N°%s-%s | %s",
             aviso.get("numero", "?"), aviso.get("anio", "?"),
             aviso.get("nivel_alerta", "?"))

    links = get_shapefile_links(soup)
    log.info("%d shapefiles disponibles", len(links))

    descargados = download_shapefiles(links, n=n_avisos)
    if not descargados:
        raise RuntimeError("Ningun shapefile pudo descargarse")

    registros = load_shapefiles(descargados)
    if not registros:
        raise RuntimeError("Ningun shapefile pudo leerse")

    generar_salidas_locales(aviso, registros)

    if os.environ.get("SKIP_GEE", "").lower() in ("1", "true", "yes"):
        log.info("SKIP_GEE activo: no se sube nada a Earth Engine")
        return

    exportar_a_gee(registros)
    log.info("Pipeline completado")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception("Pipeline abortado: %s", e)
        sys.exit(1)
