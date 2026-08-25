/**
 * Visor de avisos de lluvias intensas - Earth Engine App
 * ------------------------------------------------------
 * Pega este script en https://code.earthengine.google.com, dale a Run para
 * probarlo, y luego publicalo con el boton "Apps" -> "New App".
 *
 * Una vez publicado, cualquiera puede abrir la URL sin cuenta de Google:
 *   https://ee-oimas854.projects.earthengine.app/view/avisos-senamhi
 *
 * IMPORTANTE: al publicar, marca la coleccion como legible por la app
 * (el asistente te lo ofrece). Si no, los visitantes veran un mapa vacio.
 */

var COL_ID = 'projects/ee-oimas854/assets/senamhi/avisos_pp_img';

var NIVELES = [
  {v: 1, nombre: 'Verde',    color: '2ecc71'},
  {v: 2, nombre: 'Amarillo', color: 'f1c40f'},
  {v: 3, nombre: 'Naranja',  color: 'e67e22'},
  {v: 4, nombre: 'Rojo',     color: 'e74c3c'}
];

// El 0 (sin aviso) se enmascara para que se vea el mapa base debajo.
var VIS = {min: 0, max: 4, palette: ['f7f7f7', '2ecc71', 'f1c40f', 'e67e22', 'e74c3c']};

var col = ee.ImageCollection(COL_ID);

// ── Mapa ────────────────────────────────────────────────────

var mapa = ui.Map();
mapa.setCenter(-75.2, -9.8, 5);
mapa.setOptions('ROADMAP', {
  ROADMAP: [
    {featureType: 'poi',     stylers: [{visibility: 'off'}]},
    {featureType: 'transit', stylers: [{visibility: 'off'}]},
    {featureType: 'road',    stylers: [{visibility: 'simplified'}, {saturation: -70}]},
    {elementType: 'labels.text.fill', stylers: [{color: '#6b7c7f'}]}
  ]
});
mapa.setControlVisibility({layerList: false, fullscreenControl: false});

// ── Panel de control ────────────────────────────────────────

var titulo = ui.Label('Avisos de lluvias intensas', {
  fontSize: '18px', fontWeight: 'bold', margin: '0 0 2px 0', color: '#14211f'
});

var subtitulo = ui.Label('SENAMHI · Perú', {
  fontSize: '11px', color: '#7d8f8c', margin: '0 0 10px 0'
});

var etiquetaFecha = ui.Label('FECHA DEL AVISO', {
  fontSize: '10px', color: '#7d8f8c', margin: '4px 0 2px 0'
});

var selector = ui.Select({
  items: [],
  placeholder: 'Cargando fechas…',
  style: {width: '210px', margin: '0 0 8px 0'}
});

var info = ui.Label('', {fontSize: '12px', color: '#4a5c59', margin: '4px 0 0 0'});

var leyenda = ui.Panel({style: {margin: '10px 0 0 0'}});
leyenda.add(ui.Label('NIVEL', {fontSize: '10px', color: '#7d8f8c', margin: '0 0 4px 0'}));

NIVELES.forEach(function (n) {
  leyenda.add(ui.Panel({
    widgets: [
      ui.Label('', {
        backgroundColor: n.color, padding: '7px', margin: '0 7px 0 0',
        border: '1px solid rgba(0,0,0,0.18)'
      }),
      ui.Label(n.nombre, {fontSize: '12px', margin: '1px 0 0 0', color: '#4a5c59'})
    ],
    layout: ui.Panel.Layout.flow('horizontal'),
    style: {margin: '1px 0'}
  }));
});

var fuente = ui.Label('Fuente: SENAMHI. Visor de elaboración propia; no sustituye al aviso oficial.', {
  fontSize: '10px', color: '#9aa7a5', margin: '12px 0 0 0'
});

var panel = ui.Panel({
  widgets: [titulo, subtitulo, etiquetaFecha, selector, info, leyenda, fuente],
  style: {position: 'top-left', width: '250px', padding: '12px', backgroundColor: 'white'}
});

mapa.add(panel);

// ── Logica ──────────────────────────────────────────────────

function mostrar(fecha) {
  mapa.layers().reset();

  var img = ee.Image(col.filter(ee.Filter.eq('fecha', fecha)).first());
  // Sin aviso (0) queda transparente para no tapar el mapa base.
  mapa.addLayer(img.updateMask(img.gt(0)), VIS, 'Aviso ' + fecha);

  info.setValue('Cargando…');
  img.get('aviso_num').evaluate(function (num) {
    info.setValue(num ? 'Aviso N°' + num + ' · ' + fecha : fecha);
  });
}

col.aggregate_array('fecha').distinct().sort().evaluate(function (fechas, err) {
  if (err || !fechas || fechas.length === 0) {
    selector.setPlaceholder('Sin datos disponibles');
    info.setValue('La colección está vacía. Ejecuta el pipeline para poblarla.');
    return;
  }
  fechas.reverse();                      // la más reciente primero
  selector.items().reset(fechas);
  selector.setValue(fechas[0], false);
  selector.onChange(mostrar);
  mostrar(fechas[0]);
});

ui.root.clear();
ui.root.add(mapa);
