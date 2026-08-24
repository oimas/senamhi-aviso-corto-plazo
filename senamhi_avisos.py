# ============================================================
# SENAMHI - Avisos de Lluvias Intensas
#
# Scrapea el aviso vigente, descarga los shapefiles y publica DOS capas
# permanentes en Earth Engine (raster de todo el Peru + vector historico),
# ademas de endpoints estaticos JSON/GeoJSON en api/.
#
# Uso: python senamhi_avisos.py
#   SKIP_GEE=1  -> solo scraping + api/ (util para probar sin credenciales)
# ============================================================

import io
import os
import re
import sys
import time
import logging
import zipfile
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import build_api
import gee_publish

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

# Codigo numerico -> nombre. El codigo es lo que se rasteriza en la banda.
NIVEL_POR_VALOR = {
    "4": 4, "ROJO": 4, "ROJA": 4,
    "3": 3, "NARANJA": 3,
    "2": 2, "AMARILLO": 2,
    "1": 1, "VERDE": 1,
    "0": 0, "NORMAL": 0, "SIN AVISO": 0,
}
NOMBRE_POR_NIVEL = {0: "SIN AVISO", 1: "VERDE", 2: "AMARILLO", 3: "NARANJA", 4: "ROJO"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("senamhi")


def mapear_nivel(valor):
    """Devuelve (codigo, nombre). Codigo 0 si no se reconoce el valor."""
    v = str(valor).upper().strip()
    if v.endswith(".0"):
        v = v[:-2]
    codigo = NIVEL_POR_VALOR.get(v)
    if codigo is None:
        return 0, "DESCONOCIDO"
    return codigo, NOMBRE_POR_NIVEL[codigo]


def detectar_columna_alerta(gdf):
    for col in gdf.columns:
        if col.upper() in ("NIVEL", "ALERTA", "PELIGRO"):
            return col
    for col in gdf.columns:
        if col.startswith("_") or col == "geometry":
            continue
        valores = {str(v).strip().upper() for v in gdf[col].dropna().unique()}
        if valores & set(NIVEL_POR_VALOR):
            return col
    return None


def parsear_fecha(*candidatos):
    """Primera fecha reconocible entre los candidatos, como YYYY-MM-DD."""
    formatos = ("%Y-%m-%d", "%d/%m/%Y", "%Y%m%d", "%d-%m-%Y", "%Y/%m/%d")
    for candidato in candidatos:
        if not candidato:
            continue
        texto = str(candidato).strip()
        for patron in (r"\d{4}-\d{2}-\d{2}", r"\d{2}/\d{2}/\d{4}",
                       r"\d{8}", r"\d{2}-\d{2}-\d{4}", r"\d{4}/\d{2}/\d{2}"):
            m = re.search(patron, texto)
            if not m:
                continue
            for fmt in formatos:
                try:
                    return datetime.strptime(m.group(0), fmt).strftime("%Y-%m-%d")
                except ValueError:
                    continue
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
    """Un registro por fecha, con nivel numerico y fecha normalizada."""
    import geopandas as gpd

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
                if columna is None:
                    log.warning("%s sin columna de alerta reconocible", shp_path)
                    niveles = [(0, "DESCONOCIDO")] * len(gdf)
                else:
                    niveles = [mapear_nivel(v) for v in gdf[columna]]
                gdf["_nivel_num"] = [n[0] for n in niveles]
                gdf["_nivel"] = [n[1] for n in niveles]

                fecha_iso = parsear_fecha(
                    gdf["FECHA"].iloc[0] if "FECHA" in gdf.columns else None,
                    entry.get("fecha"),
                    os.path.basename(shp_path),
                    os.path.basename(os.path.dirname(shp_path)),
                )
                if not fecha_iso:
                    log.error("No se pudo determinar la fecha de %s, se omite", shp_path)
                    continue

                registros.append({
                    "gdf": gdf,
                    "aviso": entry["numero_aviso"],
                    "fecha_iso": fecha_iso,
                    "nivel_max": int(max(gdf["_nivel_num"])),
                    "archivo": os.path.basename(shp_path),
                })
                conteo = {}
                for _, nombre in niveles:
                    conteo[nombre] = conteo.get(nombre, 0) + 1
                log.info("Aviso %s | %s -> %s (%d poligonos)",
                         entry["numero_aviso"], fecha_iso, conteo, len(gdf))
            except Exception as e:
                log.error("Error leyendo %s: %s", shp_path, e)

    # Una sola capa por fecha: si dos avisos comparten fecha, gana el mas nuevo.
    por_fecha = {}
    for r in sorted(registros, key=lambda x: str(x["aviso"])):
        por_fecha[r["fecha_iso"]] = r
    return sorted(por_fecha.values(), key=lambda r: r["fecha_iso"])


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

    assets = None
    if os.environ.get("SKIP_GEE", "").lower() in ("1", "true", "yes"):
        log.info("SKIP_GEE activo: no se sube nada a Earth Engine")
    else:
        assets = gee_publish.publicar(registros)

    build_api.generar(registros, assets=assets)
    log.info("Pipeline completado")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception("Pipeline abortado: %s", e)
        sys.exit(1)
