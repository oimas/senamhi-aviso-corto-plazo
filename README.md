# Índice SENAMHI de avisos de lluvias intensas → Earth Engine + API estática

Convierte el aviso diario de lluvias intensas del SENAMHI en **dos capas
permanentes de Earth Engine** (una ráster de todo el Perú y una vectorial
histórica) y en **endpoints estáticos JSON/GeoJSON** servidos por el propio
repositorio. Corre en GitHub Actions; no necesita tu PC encendida.

No genera reportes: genera capas y datos consumibles.

## Lo que produce

### 1. Capa ráster — el índice

`<ASSET_ROOT>/avisos_pp_img` · **ImageCollection**, una imagen por día.

| | |
|---|---|
| Banda | `nivel` (uint8) |
| Valores | `0` sin aviso · `1` verde · `2` amarillo · `3` naranja · `4` rojo |
| Cobertura | todo el Perú (recortado al límite nacional, océano enmascarado) |
| Resolución | 1000 m (configurable con `RASTER_SCALE`) |
| Proyección | EPSG:4326 |
| Propiedades | `system:time_start`, `fecha`, `aviso_num`, `nivel_max`, `fuente` |

Como es una `ImageCollection` con fecha, se comporta como cualquier otro
producto de GEE: `filterDate`, `reduceRegion`, series de tiempo, `mosaic`.
Donde no hay aviso el valor es `0`, no es un hueco — eso es lo que la vuelve
usable como índice.

### 2. Capa vectorial — los polígonos originales

`<ASSET_ROOT>/avisos_pp_fc` · **FeatureCollection** acumulada, con todos los
avisos apilados y los atributos del shapefile (`NIVEL`, `FECHA`, `RESPONS`,
`nivel_nombre`, `fecha`, `aviso_num`). Útil cuando necesitas el borde exacto
del polígono o los atributos de texto.

### 3. API estática (carpeta `api/`)

Archivos planos commiteados por el workflow. Sin servidor, sin credenciales.

| Endpoint | Contenido |
|---|---|
| `api/index.json` | catálogo: fechas disponibles, ids de los assets GEE, leyenda |
| `api/latest.json` | metadatos del aviso más reciente (nivel máximo, conteos, bbox) |
| `api/latest.geojson` | polígonos del aviso más reciente |
| `api/avisos/<fecha>.geojson` | histórico, un archivo por fecha |

URL base una vez subido el repo:

```
https://raw.githubusercontent.com/<usuario>/<repo>/main/api/index.json
```

Si activas **Settings → Pages** (rama `main`, carpeta `/`) también quedan en
`https://<usuario>.github.io/<repo>/api/index.json`, con CORS y CDN.

Los GeoJSON se simplifican a 0.005° (~550 m) para que pesen ~70 KB en lugar
de 3.5 MB; a escala de 1 km la diferencia de área es 0.013%. Ajustable con
`API_SIMPLIFY` (`0` desactiva la simplificación). En Earth Engine se sube la
geometría completa, sin simplificar.

## Visor web

`index.html` en la raíz del repositorio es un visor Leaflet que lee `api/`
directamente: selector de fecha, polígonos coloreados por nivel, leyenda con
el conteo y ficha al hacer clic. No necesita Earth Engine ni credenciales.

Se publica activando **Settings → Pages** (rama `main`, carpeta `/`):

```
https://oimas.github.io/senamhi-aviso-corto-plazo/
```

Se actualiza solo: cada corrida reescribe `api/`, y el visor lee de ahí.

## Cómo se usa la capa

### En el Code Editor

```js
var col = ee.ImageCollection('projects/ee-TUUSUARIO/assets/senamhi/avisos_pp_img');

var vis = {min: 0, max: 4,
           palette: ['f7f7f7', '2ecc71', 'f1c40f', 'e67e22', 'e74c3c']};

// Último aviso disponible
Map.addLayer(col.sort('system:time_start', false).first(), vis, 'Aviso de hoy');

// Nivel máximo alcanzado en el último mes
Map.addLayer(col.filterDate('2026-08-01', '2026-09-01').max(), vis, 'Máx. agosto');

// Días con aviso naranja o rojo en el periodo
var diasCriticos = col.map(function (img) { return img.gte(3); }).sum();
Map.addLayer(diasCriticos, {min: 0, max: 10}, 'Días naranja/rojo');
```

### Combinado con otra capa (que es de lo que se trata)

```js
var aviso = col.sort('system:time_start', false).first();

// Nivel de aviso sobre tus parcelas / distritos
var stats = miCapaDePoligonos.map(function (f) {
  return f.set(aviso.reduceRegion({
    reducer: ee.Reducer.max(),
    geometry: f.geometry(),
    scale: 1000
  }));
});
```

### Desde Python

```python
import ee
ee.Initialize(project='ee-TUUSUARIO')

col = ee.ImageCollection('projects/ee-TUUSUARIO/assets/senamhi/avisos_pp_img')
img = col.filterDate('2026-08-22', '2026-08-23').first()

print(img.reduceRegion(
    reducer=ee.Reducer.max(),
    geometry=ee.Geometry.Point([-77.03, -12.05]),   # Lima
    scale=1000).getInfo())
```

### Desde tu servidor externo

Dos caminos, no excluyentes:

1. **Consumir `api/`** por HTTP. Cero credenciales; lee `index.json` para
   saber qué fechas hay y baja el GeoJSON que necesites.
2. **Consultar GEE directamente** con la misma cuenta de servicio (o una
   propia con permiso de lectura sobre la carpeta de assets). Ahí tienes
   `reduceRegion` para consultas puntuales y `getMapId` para tiles XYZ.

## Configuración inicial (una sola vez)

### 1. Repositorio

Sube **todo el contenido de esta carpeta** a un repo nuevo, de modo que
`.github/`, `senamhi_avisos.py`, `gee_publish.py`, `build_api.py`,
`requirements.txt` y `api/` queden en la raíz.

### 2. Proyecto Cloud vinculado a Earth Engine

- https://code.earthengine.google.com → icono de perfil → ahí ves el proyecto
  activo (suele llamarse `ee-tuusuario`). Si no tienes:
  https://code.earthengine.google.com/register
- Habilita la API si el registro no lo hizo:
  https://console.cloud.google.com/apis/library/earthengine.googleapis.com

### 3. Cuenta de servicio

1. https://console.cloud.google.com/iam-admin/serviceaccounts → tu proyecto
2. *Create service account* → `senamhi-bot` → Create
3. Rol: **Earth Engine Resource Writer**
4. *Keys* → *Add key* → *Create new key* → **JSON**

> Ese JSON es la llave de acceso. Nunca lo subas al repo: va solo como secret.

### 4. Permisos sobre la carpeta de assets

En code.earthengine.google.com → pestaña **Assets** → clic derecho sobre la
carpeta donde vivirán las capas → **Share** → agrega
`senamhi-bot@<proyecto>.iam.gserviceaccount.com` como **Writer**.

Si la carpeta aún no existe, el script la crea sola en la primera corrida
(siempre que la cuenta de servicio pueda escribir en el proyecto).

### 5. Secrets del repo

Settings → Secrets and variables → Actions → *New repository secret*:

| Secret | Valor |
|---|---|
| `GEE_SA_JSON` | contenido completo del JSON de la clave |
| `GEE_PROJECT` | id del proyecto Cloud, ej: `ee-tuusuario` |
| `ASSET_ROOT` | **carpeta** destino, ej: `projects/ee-tuusuario/assets/senamhi` |

`ASSET_ROOT` es una carpeta, no un prefijo de nombre. Dentro se crean
`avisos_pp_img` y `avisos_pp_fc`.

### 6. Probar

Actions → *SENAMHI diario* → *Run workflow*. La primera corrida crea la
carpeta, la ImageCollection y sube los 5 últimos avisos.

## Variables de entorno

| Variable | Por defecto | Para qué |
|---|---|---|
| `GEE_SA_JSON` | — | credencial de la cuenta de servicio |
| `GEE_PROJECT` | — | proyecto Cloud de Earth Engine |
| `ASSET_ROOT` | — | carpeta destino de las capas |
| `N_AVISOS` | `5` | cuántos avisos recientes procesar por corrida |
| `RELAY_URL` | allorigins | plantilla del relay HTTP; vacío lo desactiva |
| `TIMEOUT_DIRECTO` | `20` | segundos antes de dar por bloqueado el acceso directo |
| `TIMEOUT_RELAY` | `180` | segundos de espera para el relay (es lento) |
| `FORCE_ALL` | — | `1` para reprocesar fechas que ya están en `api/` |
| `RASTER_SCALE` | `1000` | resolución del ráster, en metros |
| `API_SIMPLIFY` | `0.005` | tolerancia de simplificación del GeoJSON, en grados |
| `PERU_ASSET` | — | límite nacional propio (por defecto usa `USDOS/LSIB_SIMPLE/2017`) |
| `SKIP_GEE` | — | `1` para correr solo scraping + `api/`, sin tocar GEE |

## Uso local

```bash
pip install -r requirements.txt

# Solo scraping y generación de api/ (no requiere credenciales)
set SKIP_GEE=1
python senamhi_avisos.py

# Con publicación en Earth Engine
set GEE_SA_JSON=<contenido del json>
set GEE_PROJECT=ee-tuusuario
set ASSET_ROOT=projects/ee-tuusuario/assets/senamhi
python senamhi_avisos.py
```

## SENAMHI bloquea las IPs de GitHub

El servidor de SENAMHI **descarta el tráfico proveniente de los datacenters de
GitHub**: la conexión no se rechaza, se pierde, y `requests` muere con
`ConnectTimeout` a los 30 segundos. Desde una IP peruana el mismo código
funciona sin problema.

Por eso `fetch()` intenta primero el acceso directo y, si falla, sale por un
relay HTTP público. En cuanto una petición falla se marca el directo como
bloqueado para el resto de la corrida, para no quemar el timeout en cada una.

El relay funciona pero es lento: ~83 s por ZIP frente a 0.4 s directo. De ahí
que solo se descarguen las fechas que aún no están en `api/`; en régimen normal
eso es un único aviso por día.

Alternativas más robustas, si el relay se vuelve un problema:

- **Runner propio**: cambiar `runs-on: ubuntu-latest` por `runs-on: self-hosted`
  y registrar un runner en un servidor con IP no bloqueada. GitHub sigue
  orquestando; solo cambia dónde se ejecuta.
- **Relay propio**: un Cloudflare Worker o cualquier VPS que haga de
  intermediario, apuntando `RELAY_URL` a él. Elimina la dependencia de un
  servicio gratuito de terceros.

## Notas de operación

- **Idempotente**: si la fecha ya tiene imagen en la colección, no se
  reexporta. Puedes correr el workflow varias veces al día sin duplicar.
- **La FeatureCollection histórica se reescribe** en cada corrida (Earth
  Engine no permite anexar). Se exporta a un asset temporal y solo entonces
  se reemplaza el original, conservando un `__bak` hasta confirmar.
- **Una capa por fecha**: si dos avisos comparten fecha, gana el de número
  más alto.
- El commit diario de `api/` mantiene vivo el cron; GitHub desactiva los
  schedules tras ~60 días sin actividad en el repo.
- Si SENAMHI cambia su HTML, ajusta `get_current_aviso` y
  `get_shapefile_links` en [senamhi_avisos.py](senamhi_avisos.py).
