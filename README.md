# Automatización SENAMHI → Google Earth Engine

Scrapea diariamente los avisos de lluvias intensas de SENAMHI, descarga los
shapefiles y los publica como FeatureCollections en tu cuenta de Earth Engine.
Corre en GitHub Actions, no necesita tu PC encendida.

## Qué hace cada día

1. Lee la página del aviso vigente (`?p=aviso-24H`)
2. Descarga los últimos 5 shapefiles de avisos
3. Detecta el nivel de alerta de cada polígono (columna `NIVEL`: 1=Verde,
   2=Amarillo, 3=Naranja, 4=Rojo)
4. Sube a GEE dos assets por aviso: uno con fecha
   (`..._056_2026-02-25`) y uno fijo que se sobrescribe (`..._latest`)
5. Genera Excel + GeoJSON descargables como artefactos del workflow

## Configuración inicial (una sola vez)

### 1. Crear el repositorio en GitHub
- Crea un repo nuevo (privado sirve) y sube **todo el contenido de esta carpeta**
  (que `.github/`, `senamhi_avisos.py` y `requirements.txt` queden en la raíz).
- Puedes arrastrar los archivos desde la web: repo → *Add file* → *Upload files*.

### 2. Vincular Earth Engine con un proyecto Cloud
Si ya usas code.earthengine.google.com, es muy probable que ya tengas un
proyecto (suele llamarse `ee-tuusuario`). Verifícalo:

- Entra a https://code.earthengine.google.com → icono de perfil (arriba derecha)
  → verás el proyecto activo.
- Si no tienes ninguno: https://code.earthengine.google.com/register te guía
  para registrar uso no comercial con un proyecto nuevo o existente.
- Anota el **ID del proyecto** (ej: `ee-tuusuario`).

Luego habilita la API (si el registro de EE no lo hizo ya):
https://console.cloud.google.com/apis/library/earthengine.googleapis.com
→ selecciona tu proyecto → Enable.

### 3. Crear la cuenta de servicio
1. https://console.cloud.google.com/iam-admin/serviceaccounts → tu proyecto
2. *Create service account* → nombre: `senamhi-bot` → Create
3. Roles: agrega **Earth Engine Resource Writer**
4. Pestaña *Keys* → *Add key* → *Create new key* → **JSON** → se descarga un archivo
5. Abre ese JSON: contiene `"project_id"`, `"client_email"`, etc.

> Guarda ese JSON con cuidado; es la llave de acceso. Nunca lo subas al repo:
> irá solo como *secret* de GitHub.

### 4. Compartir tus assets con la cuenta de servicio
En code.earthengine.google.com, pestaña **Assets**:
- Click derecho sobre tu carpeta personal (`users/tuusuario`) o sobre la carpeta
  donde quieras guardar los avisos → **Share**
- En *Earth Engine asset sharing*, agrega el email de la cuenta de servicio
  (`senamhi-bot@proyecto.iam.gserviceaccount.com`) con permiso **Writer**.

### 5. Crear los secrets en GitHub
Repo → Settings → Secrets and variables → Actions → *New repository secret*:

| Secret         | Valor                                                          |
|----------------|----------------------------------------------------------------|
| `GEE_SA_JSON`  | Todo el contenido del JSON de la clave (copiar/pegar completo) |
| `GEE_PROJECT`  | ID del proyecto Cloud (ej: `ee-tuusuario`)                     |
| `ASSET_PREFIX` | Ruta destino, ej: `users/tuusuario/senamhi/aviso`              |

Con esos valores los assets quedarán en:
- `users/tuusuario/senamhi/aviso_latest` (se sobrescribe a diario)
- `users/tuusuario/senamhi/aviso_056_2026-02-25` (histórico por fecha)

## Probar y programar

- **Prueba manual**: pestaña *Actions* → *SENAMHI diario* → *Run workflow*.
- **Horario**: edita `.github/workflows/diario.yml`, línea `cron:` (hora UTC;
  Lima = UTC−5). `'30 17 * * *'` = 12:30 m. en Lima.
- Los resultados locales (Excel, GeoJSON) quedan como artefactos del run,
  descargables durante 90 días.

## Uso local (opcional)

```bash
pip install -r requirements.txt
set GEE_SA_JSON=<contenido del json>   # o usa SKIP_GEE=1 para probar sin GEE
set GEE_PROJECT=ee-tuusuario
set ASSET_PREFIX=users/tuusuario/senamhi/aviso
python senamhi_avisos.py
```

## Notas

- GitHub desactiva workflows programados si el repo queda ~60 días sin pushes;
  re-habilítalo desde la pestaña Actions o haz un push ocasional.
- Si SENAMHI cambia la estructura HTML, los selectores de scraping pueden
  requerir ajuste (funciones `get_current_aviso` / `get_shapefile_links`).
