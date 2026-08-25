# ============================================================
# Publicacion en Google Earth Engine
#
# Genera DOS capas permanentes (no reportes):
#
#   <ASSET_ROOT>/avisos_pp_img   ImageCollection - una imagen por dia,
#                                banda "nivel" (0=sin aviso, 1..4), todo el
#                                Peru, lista para filterDate / reduceRegion.
#
#   <ASSET_ROOT>/avisos_pp_fc    FeatureCollection acumulada - los poligonos
#                                originales con sus atributos y "fecha".
#
# Ambas son idempotentes: si la fecha ya existe, no se vuelve a exportar.
# ============================================================

import os
import time
import logging
import tempfile
from datetime import datetime, timezone

log = logging.getLogger("senamhi.gee")

# Limite nacional usado para recortar el raster.
PERU_FC = "USDOS/LSIB_SIMPLE/2017"
PERU_FILTRO = ("country_na", "Peru")

NOMBRE_IC = "avisos_pp_img"
NOMBRE_FC = "avisos_pp_fc"

# Propiedades de texto largo que no aportan al indice y engordan el asset.
PROPS_DESCARTADAS = {"RECOMENDAC", "DESCRIPCIO"}


# ── Conexion ────────────────────────────────────────────────

def inicializar_gee():
    import ee

    project = os.environ.get("GEE_PROJECT", "").strip()
    if not project:
        raise RuntimeError("Falta la variable GEE_PROJECT (ej: ee-tuusuario)")

    # En local es mas comodo apuntar al archivo de la clave; en un CI se pasa
    # el contenido por variable de entorno porque no hay disco persistente.
    keyfile = os.environ.get("GEE_SA_KEYFILE", "").strip()
    if keyfile:
        if not os.path.isfile(keyfile):
            raise RuntimeError(f"GEE_SA_KEYFILE apunta a un archivo inexistente: {keyfile}")
        ruta = keyfile
    else:
        key_json = os.environ.get("GEE_SA_JSON", "").strip()
        if not key_json:
            raise RuntimeError(
                "Falta la credencial: define GEE_SA_KEYFILE (ruta al JSON) "
                "o GEE_SA_JSON (contenido del JSON).")
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        tmp.write(key_json)
        tmp.close()
        ruta = tmp.name

    credenciales = ee.ServiceAccountCredentials(None, key_file=ruta)
    ee.Initialize(project=project, credentials=credenciales)
    log.info("Earth Engine inicializado (proyecto: %s)", project)
    return ee


def asset_root():
    """Carpeta contenedora de las capas, ej: projects/ee-user/assets/senamhi."""
    root = os.environ.get("ASSET_ROOT", "").strip().rstrip("/")
    if root:
        return root
    # Compatibilidad con la configuracion anterior (ASSET_PREFIX terminaba
    # en el nombre del asset, no en la carpeta).
    prefijo = os.environ.get("ASSET_PREFIX", "").strip().rstrip("/")
    if prefijo:
        return prefijo.rsplit("/", 1)[0]
    raise RuntimeError(
        "Falta ASSET_ROOT (ej: projects/ee-tuusuario/assets/senamhi)")


def existe_asset(ee, asset_id):
    try:
        ee.data.getAsset(asset_id)
        return True
    except Exception:
        return False


def crear_carpetas(ee, root):
    """Crea root y cualquier carpeta intermedia que falte."""
    partes = root.split("/")
    # projects/<proyecto>/assets es el contenedor raiz: no se crea.
    inicio = 3 if partes[0] == "projects" else 2
    for i in range(inicio, len(partes) + 1):
        ruta = "/".join(partes[:i])
        if not existe_asset(ee, ruta):
            ee.data.createAsset({"type": "FOLDER"}, ruta)
            log.info("Carpeta creada: %s", ruta)


def asegurar_contenedores(ee, root):
    """Crea la carpeta y la ImageCollection si aun no existen."""
    crear_carpetas(ee, root)

    ic_id = f"{root}/{NOMBRE_IC}"
    if not existe_asset(ee, ic_id):
        ee.data.createAsset({"type": "IMAGE_COLLECTION"}, ic_id)
        log.info("ImageCollection creada: %s", ic_id)
    return ic_id, f"{root}/{NOMBRE_FC}"


def fechas_publicadas(ee, ic_id):
    """Fechas (YYYY-MM-DD) que ya tienen imagen en la coleccion."""
    try:
        listado = ee.data.listAssets({"parent": ic_id})
    except Exception as e:
        log.warning("No se pudo listar %s: %s", ic_id, e)
        return set()
    fechas = set()
    for asset in listado.get("assets", []):
        nombre = asset["name"].rsplit("/", 1)[-1]
        if nombre.startswith("aviso_"):
            fechas.add(nombre[len("aviso_"):])
    return fechas


# ── Conversion GeoDataFrame -> Earth Engine ─────────────────

def sanitizar_props(props):
    limpio = {}
    for k, v in props.items():
        if k.startswith("_") or k in PROPS_DESCARTADAS:
            continue
        if v is None or (isinstance(v, float) and v != v):
            continue
        if isinstance(v, bool):
            limpio[k] = int(v)
        elif isinstance(v, (int, float, str)):
            limpio[k] = v
        else:
            limpio[k] = str(v)
    return limpio


def gdf_a_featurecollection(ee, gdf, fecha, aviso_num):
    """FeatureCollection con NIVEL numerico garantizado en cada feature."""
    features = []
    for _, fila in gdf.iterrows():
        geom = fila.geometry
        if geom is None or geom.is_empty:
            continue
        props = sanitizar_props({k: v for k, v in fila.items() if k != "geometry"})
        props["NIVEL"] = int(fila.get("_nivel_num", 0))
        props["nivel_nombre"] = str(fila.get("_nivel", "DESCONOCIDO"))
        props["fecha"] = fecha
        props["aviso_num"] = str(aviso_num)
        features.append(ee.Feature(ee.Geometry(geom.__geo_interface__), props))
    return ee.FeatureCollection(features)


def peru_geometry(ee):
    """Limite del Peru. Se puede sustituir por un asset propio via PERU_ASSET."""
    propio = os.environ.get("PERU_ASSET", "").strip()
    if propio:
        log.info("Usando limite nacional propio: %s", propio)
        return ee.FeatureCollection(propio)

    fc = ee.FeatureCollection(PERU_FC).filter(
        ee.Filter.eq(PERU_FILTRO[0], PERU_FILTRO[1]))
    if fc.size().getInfo() == 0:
        # Si el dataset cambia de esquema, mejor fallar con un mensaje claro
        # que exportar un raster vacio en silencio.
        raise RuntimeError(
            f"No se encontro el Peru en {PERU_FC} ({PERU_FILTRO[0]}="
            f"{PERU_FILTRO[1]}). Define PERU_ASSET con tu propio limite.")
    return fc


def fc_a_imagen_peru(ee, fc, peru, fecha, aviso_num, nivel_max):
    """Raster de todo el Peru: 0 donde no hay aviso, 1..4 donde si lo hay.

    Se usa el reducer max para que, en solapes, gane el nivel mas alto.
    """
    alerta = fc.reduceToImage(properties=["NIVEL"], reducer=ee.Reducer.max())
    nivel = alerta.unmask(0).toByte().rename("nivel")

    ts = int(datetime.strptime(fecha, "%Y-%m-%d")
             .replace(tzinfo=timezone.utc).timestamp() * 1000)

    return nivel.clipToCollection(peru).set({
        "system:time_start": ts,
        "system:time_end": ts + 86_400_000,
        "fecha": fecha,
        "aviso_num": str(aviso_num),
        "nivel_max": int(nivel_max),
        "fuente": "SENAMHI - Aviso de lluvias intensas",
        "actualizado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })


# ── Exportaciones ───────────────────────────────────────────

def esperar(tareas, timeout=1800):
    """Bloquea hasta que todas las tareas terminen. Devuelve las fallidas."""
    fallidas = []
    for tarea, asset_id in tareas:
        inicio = time.time()
        while time.time() - inicio < timeout:
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
    return fallidas


def exportar_rasters(ee, registros, ic_id, escala, ya_publicadas):
    peru = peru_geometry(ee)
    region = peru.geometry().bounds()
    tareas = []

    for r in registros:
        fecha = r["fecha_iso"]
        if fecha in ya_publicadas:
            log.info("Raster %s ya existe, se omite", fecha)
            continue

        fc = gdf_a_featurecollection(ee, r["gdf"], fecha, r["aviso"])
        img = fc_a_imagen_peru(ee, fc, peru, fecha, r["aviso"], r["nivel_max"])
        asset_id = f"{ic_id}/aviso_{fecha}"

        tarea = ee.batch.Export.image.toAsset(
            image=img,
            description=f"SENAMHI_img_{fecha.replace('-', '')}",
            assetId=asset_id,
            region=region,
            scale=escala,
            crs="EPSG:4326",
            maxPixels=1e10,
            pyramidingPolicy={"nivel": "mode"},
        )
        tarea.start()
        tareas.append((tarea, asset_id))
        log.info("Raster en cola -> %s (escala %d m)", asset_id, escala)

    return tareas


def actualizar_fc_historica(ee, registros, fc_id):
    """Reescribe la FeatureCollection acumulada agregando las fechas nuevas.

    Earth Engine no permite anexar a un asset existente, asi que se exporta
    la union a un asset temporal y recien entonces se reemplaza el original.
    """
    nuevas = None
    fechas_nuevas = []
    for r in registros:
        fc = gdf_a_featurecollection(ee, r["gdf"], r["fecha_iso"], r["aviso"])
        nuevas = fc if nuevas is None else nuevas.merge(fc)
        fechas_nuevas.append(r["fecha_iso"])

    if nuevas is None:
        log.warning("Sin features para la FeatureCollection historica")
        return []

    tiene_previo = existe_asset(ee, fc_id)
    if tiene_previo:
        previo = ee.FeatureCollection(fc_id).filter(
            ee.Filter.inList("fecha", fechas_nuevas).Not())
        union = previo.merge(nuevas)
        destino = f"{fc_id}__tmp"
        if existe_asset(ee, destino):
            ee.data.deleteAsset(destino)
    else:
        union = nuevas
        destino = fc_id

    tarea = ee.batch.Export.table.toAsset(
        collection=union,
        description=f"SENAMHI_fc_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        assetId=destino,
    )
    tarea.start()
    log.info("FeatureCollection en cola -> %s", destino)
    return [(tarea, destino, fc_id if tiene_previo else None)]


def confirmar_fc(ee, pendientes):
    """Sustituye el asset historico por el temporal ya exportado."""
    fallidas = []
    for tarea, destino, final in pendientes:
        if esperar([(tarea, destino)]):
            fallidas.append(destino)
            continue
        if not final:
            continue
        respaldo = f"{final}__bak"
        try:
            if existe_asset(ee, respaldo):
                ee.data.deleteAsset(respaldo)
            ee.data.renameAsset(final, respaldo)
            ee.data.renameAsset(destino, final)
            ee.data.deleteAsset(respaldo)
            log.info("FeatureCollection historica actualizada: %s", final)
        except Exception as e:
            log.error("No se pudo reemplazar %s: %s", final, e)
            fallidas.append(final)
    return fallidas


# ── Entrada principal del modulo ────────────────────────────

def publicar(registros):
    """Sube rasters + vector. Devuelve los ids de los assets involucrados."""
    ee = inicializar_gee()
    root = asset_root()
    escala = int(os.environ.get("RASTER_SCALE", "1000"))

    ic_id, fc_id = asegurar_contenedores(ee, root)
    ya = fechas_publicadas(ee, ic_id)

    tareas_img = exportar_rasters(ee, registros, ic_id, escala, ya)
    pendientes_fc = actualizar_fc_historica(ee, registros, fc_id)

    fallidas = esperar(tareas_img)
    fallidas += confirmar_fc(ee, pendientes_fc)

    if fallidas:
        raise RuntimeError(f"Tareas GEE fallidas: {fallidas}")

    return {"image_collection": ic_id, "feature_collection": fc_id, "escala_m": escala}
