# 🚢 PV en Tránsito — guía de uso

Cada semana, cuando llegue el reporte de comex, antes de escribir el correo de comentarios a Majo. Tarda 2 minutos: subir el archivo, mirar el resumen, descargar el Excel. Lo puede operar cualquier buyer o asistente del equipo; no hace falta tocar código.

## Qué es

Responde una pregunta que hoy se saca a mano: **¿qué porcentaje de la compra Primavera-Verano ya está en el CD, cuánto falta, cuándo llega y qué está atrasado?** Lee el reporte de comex, cruza con la Base Profundidad cargada en Capi para poner costo en soles y stock en tienda, guarda el **historial de cada fecha de llegada (ETA)** y exporta un Excel con un esquema fijo que el formato semanal consume en automático.

Alcance de esta versión: las **4 marcas propias importadas** (Marquis, Navigata, Cacharel, Spavaldi). Las terceras (Lacoste, Dockers, John Holden…) no pasan por comex porque son compra nacional o consignación; no están en el reporte y el módulo lo avisa.

## De dónde sale cada dato

| Dato | Fuente | Detalle |
|---|---|---|
| Órdenes de compra, unidades, estado, FOB, ETA | **DETALLE de comex** (Luis Huerta, archivo `DETALLE (NN).xlsx`, hoja `Export`) | Una fila por **OC × SKU**. Columnas que usa el módulo: `OC, SKU, COD PADRE, TEMP, VENTANA, DIVISION, DEPARTAMENTO, LINEA, MARCA, MODELO, ESTADO_EMBARQUE_FINAL, UND, MONTO_FOB, ETA, ETD, PROYECTADO INGRESO CD3, INGRESOCD, MOTIVO_RETRASO`. Se buscan **por nombre de encabezado**, no por posición: si comex cambia el orden, no pasa nada. Si cambia un nombre, se agrega un alias en `COLUMN_MAP` de `pv_transito.py`. |
| Fallback: solo lo pendiente | Hojas **"LLEGADAS RETRASOS COMEX"** (las 4 pestañas que arma comex para Franco, `.xlsb`) | Una fila por OC × modelo. Sirve para pendiente, ETA y atrasos. **No trae costo ni OC recibidas**, así que el % recibido no se puede calcular con él. El módulo lo detecta y lo dice. |
| Costo en soles | `Costo S/.` de la **Base Profundidad** cargada en Capi, cruzado por `COD PADRE` ↔ `Cód. Prod.` | Cuando el modelo todavía no tiene costo en la Base (modelos nuevos sin stock), se usa **FOB USD × 4,04**. Ese factor se midió el 04-oct-2026 sobre 165 modelos (mediana 4,04; el 80 % de los casos cae entre 3,91 y 4,39) y vive en `config_pv_transito.json`. Las columnas de costo quedan rotuladas "estimado" y los **% a costo no dependen del factor** (se cancela arriba y abajo). |
| Stock en tienda | `Total Tiendas Unidades` de la Base, por modelo | Se muestra como **"% de la compra hoy en tienda"** = stock en tiendas de los modelos de esa compra ÷ unidades compradas. Incluye lo ya vendido como "no en tienda" y no distingue de qué OC vino cada unidad. Es una aproximación y así se rotula. |
| Semana de llegada | `calendario_ripley.py` | Siempre **semana comercial Ripley** (W2026xx), nunca ISO. |

## Cómo se calcula cada métrica

Todo parte de la tabla estándar `oc` (una fila por OC × SKU) que arma `pv_transito.leer_reporte`. Nada se calcula sobre el Excel crudo.

| Métrica | Regla |
|---|---|
| **Recibida** | Una fila está recibida si su estado es `1.Almacenado` o `2.En CD`, **o** si trae fecha en `INGRESOCD`. Medido el 04-oct-2026: las dos señales coinciden en el 100 % de las filas. |
| **Compra PV** (`und_compra_pv`, `costo_compra_pv`) | Suma de unidades (y costo) de todas las filas con `TEMP` = PV, recibidas o no. |
| **Recibido en CD** | Suma de las filas recibidas. `pct_recibido_und` = recibido ÷ compra, en fracción 0–1 (0,47 = 47 %). `pct_recibido_costo` igual pero en soles. |
| **Pendiente** | Compra − recibido. Se agrupa por **semana Ripley de la ETA al CD** (`PROYECTADO INGRESO CD3`, o la ETA corregida a mano si existe). |
| **Atrasado** (`und_atrasadas`) | Pendiente cuya ETA es **anterior a hoy** o que **no tiene ETA**. Se calcula contra la fecha de hoy, no contra la fecha del reporte; por eso la pantalla muestra cuántos días tiene el reporte. |
| **Próxima ETA** | La menor ETA futura del pendiente de la marca. |
| **Semáforo por marca** | 🟢 sin atrasos · 🟡 atrasado < 10 % del pendiente · 🔴 ≥ 10 %. El 10 % es un umbral **elegido**, no medido; recalibrar con 4 cortes. |
| **Líneas partidas** | Si la misma OC y SKU aparece en dos filas (pasa cuando comex parte una línea), se **suman**; no es un error. |
| **Temporada** | De `TEMP` (`PV 26/27` → PV, `OI 27` → OI). En el fallback, de la pestaña: `VENTANA E/F` → PV, `VENTANA OI27 A` → OI. |

## Los 6 pasos

1. **Pedir el archivo.** A comex (Luis Huerta): el DETALLE **tal cual lo exporta, en xlsx, sin pivotes**. Si solo tienes las 4 hojas de llegadas, también sirven (modo pendiente).
2. **Subirlo.** Menú lateral → **🚢 PV en Tránsito** → "Reporte de comex". El archivo queda en la carpeta `inputs/` (fuera del repo) y aparece en el selector "…o uno ya cargado" para la próxima vez.
3. **Revisar la fecha del reporte.** Se deduce del nombre del archivo (`… 24.09.xlsb` → 24/09). Si comex lo exportó otro día, corrígela: los atrasos se miden contra hoy y el encabezado dice cuántos días tiene el reporte.
4. **Leer de arriba hacia abajo.** 3 KPIs (recibido, atrasadas, semana pico) → tabla por marca con semáforo → llegadas por semana. Lo rojo es lo que hay que preguntar a comex hoy.
5. **Corregir una ETA si hace falta.** En "Por OC", edita la columna **ETA vigente ✏️**, escribe un comentario y pulsa **💾 Guardar ETA corregidas**. El cambio se **agrega** al historial; nunca pisa la fecha anterior. En "Historial de una OC" ves todas las fechas que tuvo, quién la cambió y cuántos días corrió. Si el proveedor mandó un Excel con fechas, usa **Carga masiva** (columnas `OC`, `ETA`, `Comentario`).
6. **Exportar.** **⬇️ pv_transito_AAAA-MM-DD.xlsx** → hoja `pv_transito` con las 13 columnas del contrato (abajo), hoja `notas` con la fuente y advertencias, hoja `detalle_oc` con la tabla por OC. Opcional: **📌 Registrar corte** guarda el resumen de la semana para comparar.

## El export (contrato fijo, no cambiar)

Hoja `pv_transito`, una fila por marca, exactamente estas columnas en este orden:

| columna | qué es |
|---|---|
| `marca` | nombre normalizado (Marquis, Navigata, Cacharel, Spavaldi) |
| `und_compra_pv` | unidades PV compradas (recibidas + pendientes) |
| `und_recibida_cd` | unidades ya en el CD |
| `und_en_tienda` | stock hoy en tiendas de los modelos de la compra (vacío si no hay Base cargada) |
| `costo_compra_pv` | S/ costo total de la compra (Base, o FOB × factor) |
| `costo_recibido_cd` | S/ costo de lo recibido |
| `pct_recibido_und` | fracción 0–1 |
| `pct_recibido_costo` | fracción 0–1 |
| `und_pendiente` | unidades por llegar |
| `costo_pendiente` | S/ por llegar |
| `proxima_eta` | fecha de la siguiente llegada |
| `und_atrasadas` | unidades con ETA vencida o sin ETA |
| `detalle_llegadas` | texto "Polos M/C 24,662 u 08/10; Shorts 6,645 u 08/10; …; atrasado 4,130 u" |

Con las hojas de llegadas (sin costo) las 4 columnas de costo y los `pct_` salen vacíos; la hoja `notas` lo dice.

## Cómo se guarda el historial de ETA

- **Dónde:** `snapshots/pv_transito/eta_historial.parquet` (caché local, fuera del repo) y una página por cambio en la base de Notion **🚢 ETA Capi** (`NOTION_DB_ETA`). Notion es la fuente de verdad: en Streamlit Cloud el disco se borra en cada publicación y la vista reconstruye el historial desde Notion al abrir.
- **Qué guarda cada fila:** OC, ETA, fuente (`comex` = vino en el reporte · `manual` = corregida en pantalla · `proveedor` = carga masiva), comentario, fecha de registro, semana Ripley, usuario, marca, modelo.
- **Cuándo se escribe:** al cargar un corte, por cada OC cuya ETA cambió respecto de la vigente (o que aparece por primera vez) → fila `comex`. Al guardar en pantalla → fila `manual`. En carga masiva → fila `proveedor`, solo las que cambian.
- **ETA vigente** = la última fila de cada OC. "Veces movida" y "Días corridos" salen de contar las filas y restar la primera ETA de la última: así se ve qué proveedor incumple.
- **Sin token de Notion** todo sigue funcionando en local; el sidebar lo avisa.

## Si algo falla

- **"Al reporte le faltan columnas obligatorias"**: el archivo no trae `OC`, `MARCA`, `UND`/`UNIDADES` o `PROYECTADO INGRESO CD3` con esos nombres. Revisa la hoja; si comex renombró una columna, agrégala como alias en `COLUMN_MAP` (`pv_transito.py`) y listo.
- **"Este archivo solo trae lo pendiente"**: subiste las hojas de llegadas. Pide el DETALLE para tener % recibido y costo.
- **Costo en blanco o "estimado"**: no hay Base Profundidad cargada en Capi (súbela en el uploader principal) o el modelo es nuevo y no tiene costo en la Base (se usa FOB × 4,04).
- **Atrasadas muy altas**: mira la fecha del reporte. Un reporte de hace 10 días "atrasa" todo lo que debía llegar esos días. Pide el corte fresco.
- **No aparece Notion**: falta `NOTION_TOKEN` o `NOTION_DB_ETA`, o la integración no está conectada a la base 🚢 ETA Capi. El historial sigue en el parquet local.

## Para quien mantiene el código

- Motor: `pv_transito.py` (pandas puro, 23 tests en `tests/test_pv_transito.py` con fixtures **ficticios** `tests/fixtures/detalle_comex_mini.xlsx` y `llegadas_franco_mini.xlsx`, generados por `tests/fixtures/make_detalle_mini.py`).
- Persistencia: `eta_store.py` (8 tests, aislados con `CAPI_SNAPSHOTS_DIR`). Notion: `notion_store.registrar_eta` / `listar_eta`.
- Vista: `vista_pv_transito.py` (AppTest en `tests/test_apptest_pv_transito.py`, corre en CI con `base_mini`).
- Config: `config_pv_transito.json` (marcas foco, estados recibido, factor FOB→S/, umbral semáforo, carpeta de inputs) y `config_marcas_alias.json` (alias de marcas).
- La tabla `oc` (OC × SKU, cantidad + fecha + estado) está pensada como entrada de un futuro motor de planificación tipo MRP. No se construyó el MRP; solo no se le cerró la puerta.
- Decisión y auditoría con data real: `Vault de Franco/04-Negocios-e-Ideas/Herramienta Retail AI/Decisiones-Desarrollo/2026-10-04-PV-Transito-Fuente-Comex.md`. Re-audit: 2026-10-11 con el DETALLE xlsx directo de comex.
