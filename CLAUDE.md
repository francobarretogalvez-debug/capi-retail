# Capi — Herramienta de Gestión Retail

## Qué es Capi

SaaS de gestión de inventario para retail construido con Streamlit + Python. El usuario es **Franco Barreto**, buyer de moda en **Ripley Peru** (tienda departamental). Franco tiene expertise profundo en retail pero NO escribe código — Claude escribe todo.

**Audiencia clave:** Majo (jefa directa de Franco), Zina (gerenta comercial), Rodrigo Guajardo (gerente transformación digital).

**Objetivo estratégico:** Franco quiere proponer una gerencia de IA dentro de transformación digital en Ripley. Capi es la evidencia de que es posible.

## Deploy

- **Streamlit Cloud:** https://appapppy-f85ojwkntafrwyrvqscxac.streamlit.app/
- **GitHub repo:** `francobarretogalvez-debug/capi-retail` (branch `main`, público)
- Auto-deploy: cada push a `main` dispara redeploy en Streamlit Cloud
- Cold start tarda ~3 min por dependencias pesadas (pandas, plotly, pyarrow)
- Filesystem es efímero — `snapshots/` no persiste entre reboots. Bases históricas en `data2/bases antiguas/` sí persisten (están en git)

## Arquitectura de archivos

```
capi-deploy/
├── app_streamlit.py          # UI principal (7383 líneas, 17 vistas)
├── motor_v2.py               # Motor de cálculo (4380 líneas)
├── afinidad_engine.py        # Clustering producto×plaza (873 líneas)
├── chat_engine.py            # Chat IA con Claude API (787 líneas)
├── motor_calculo.py          # Motor legacy (723 líneas, poco usado)
├── transformar_profundidad.py # Pipeline ETL Base Ripley → Plantilla Capi (548 líneas)
├── clima_engine.py           # Fenómeno del Niño, temperatura×ventas (324 líneas)
├── taxonomia.py              # 10 estados de inventario (322 líneas)
├── transformador_base_ripley.py # Transformador alternativo (279 líneas)
├── acciones_stock.py         # Acciones de stock complementarias (277 líneas)
├── renderers_alertas_tienda.py # Renderizado de alertas por tienda (228 líneas)
├── transicion_engine.py      # Motor de transiciones de estado (868 líneas)
├── audit_transicion.py       # Auditoría de transiciones (165 líneas)
├── config.py                 # COLOR_MAP, ESTADO_ORDEN, UMBRALES (93 líneas)
├── snapshots_engine/         # Módulo de snapshots semanales
│   ├── __init__.py
│   ├── config.py             # COLUMN_MAP, SNAPSHOT_SCHEMA, paths
│   ├── loader.py             # Carga bases antiguas + process_micro_profundidad
│   ├── storage.py            # save/load snapshots como Parquet
│   ├── api.py                # 6 funciones de análisis comparativo
│   └── validators.py         # Validación de schema
├── config_afinidad.json      # Params clustering + filtro estacional
├── config_calorico.json      # Mapeo sublínea → GRUESO/MEDIO/LIGERO
├── config_lead_times.json    # Lead times por marca (días)
├── config_marca_tiendas.json # Distribución marca×tienda
├── config_matriz_tiendas.json # Matriz categoría×tienda para predistribución
├── data2/bases antiguas/     # Bases históricas Excel (persisten en git)
├── requirements.txt          # pandas, numpy, openpyxl, streamlit, plotly, altair, anthropic, pyarrow...
└── .streamlit/               # Config Streamlit
```

## Pipeline de datos (flujo crítico)

```
Base Profundidad (Excel Ripley)
  ↓ transformar_profundidad.py  (columnas Ripley → columnas Capi)
  ↓ motor_v2.py                (run_analysis: cobertura, reposición, transferencias, precio, alertas)
  ↓ app_streamlit.py           (renderiza 17 vistas)
```

**IMPORTANTE:** El `snapshots_engine` necesita la base CON columnas Ripley originales (antes de transformar). El `COLUMN_MAP` en `snapshots_engine/config.py` mapea FROM nombres Ripley. Si le pasas la base ya transformada, falla silenciosamente.

## Sistema de 10 estados (taxonomia.py)

| Estado | Cobertura (sem) | Criterio |
|--------|----------------|----------|
| QUIEBRE | <4 | Urgente reponer |
| PRE-QUIEBRE | 4–8 | Reponer pronto |
| ÓPTIMO | 8–16 | Sano (target ~12) |
| ALTO | 16–26 | Vigilar |
| SOBRESTOCK | 26–52 | Empuje/markdown |
| ESTANCADO | >52, edad ≤26 | Capital parado |
| LIQUIDAR | >52, edad >26 | Markdown agresivo |
| NUEVO SIN VENTA | sin venta, <8 sem | Recién llegado |
| DORMIDO | sin venta, 8-26 sem | Revisar exhibición |
| MUERTO | sin venta, >26 sem | Liquidar |

Los colores y orden están centralizados en `config.py` → `COLOR_MAP` y `ESTADO_ORDEN`.

## Navegación (17 vistas en 4 categorías)

**VISIÓN GENERAL:** Dashboard, Salud del Stock, Briefing Semanal, Diario de Gestión
**GESTIÓN DE STOCK:** Reposición, Cobertura, Transferencias, Predistribución, Sobrestock, Acciones de Stock
**GESTIÓN COMERCIAL:** Gestión por Antigüedad, Acciones Precio, Marcas Terceras
**ANÁLISIS PREDICTIVO:** Ventana de Compra, Evolución Semanal, Fenómeno del Niño, Afinidad Producto×Plaza, Alertas IA, Simulador Predictivo

Cada vista es un bloque `elif` dentro de `app_streamlit.py`. Cada `elif` es una isla de scope — variables definidas en un bloque NO existen en otro.

## Reglas de negocio críticas

### Marcas propias vs terceras
Marcas propias: MARQUIS, NAVIGATA, CACHAREL, OSCAR DE LA RENTA, NAUTICA, US POLO, SPAVALDI.
Todo lo demás = tercera. El motor usa flag `_es_propia` / `es_tercera`.

### Filtro descuento 40%
Excluir reposición para SKUs con dscto ≥40% **SOLO aplica a marcas terceras**. Propias se reponen siempre, sin importar descuento. Razón: propias se reponen desde CD propio.

### Pricing omnicanal
Descuento aplica parejo a todas las tiendas. Margen por SKU a nivel global es representativo.

### Filtro estacional (Afinidad)
Filtrar por temporada, NO por volumen. Invierno (desde May-1): excluir M/C + Shorts + TdB. Verano (desde Nov-1): excluir abrigador.

### Naming de estados
CRÍTICO → QUIEBRE, PRE-CRÍTICO → PRE-QUIEBRE. Migrado el 24-May-2026.

### Mapeo de tiendas
SB=San Borja, CHII=Chiclayo 2, CBT=Chimbote, LO=Mega Plaza (solo LO para repo, no MP).

## Convenciones de trabajo

### Output-first (regla #1)
SIEMPRE definir outputs concretos + decisión accionable ANTES de construir. Si un output no lleva a una acción, no debería existir.

### Auditoría post-cambio
Después de cualquier cambio >50 líneas o reestructuración, correr auditoría de 6 fases:
1. `py_compile` (necesario pero insuficiente)
2. AST scoping (detecta NameErrors en bloques elif)
3. Grep de referencias huérfanas
4. Consistencia de navegación (entries vs elifs)
5. Constantes y colores (py_compile NO detecta NameError en f-strings)
6. Reporte final

### Debugging
Trazar dato de inicio a fin. Poner debug al FINAL del flujo, no al inicio. No asumir que la detección es el problema — leer funciones COMPLETAS.

### Idioma
Todo en español (peruano). Código, comentarios, UI, documentación.

### Estilo de colaboración con Franco
Franco quiere un partner que cuestione sus ideas, explore opciones, y dé respuestas con contexto detallado. NO respuestas superficiales. Siempre explicar el "por qué" detrás de decisiones técnicas.

## Roadmap (status al 9-Jun-2026)

### Prompts completados
- ✅ **Prompt A** — Taxonomía Maestra: 10 estados, classify() centralizado
- ✅ **Prompt B** — Snapshots Semanales: snapshots_engine/, 5+ bases, 6 funciones análisis
- ✅ **Prompt C** — Salud del Stock: health score por marca, scatter impacto, delta temporal
- ✅ **Prompt F** — Auditoría Reposición: 5 escenarios, flags quiebre_inminente/requiere_proveedor

### Prompts pendientes
- ⬜ **Prompt D** — Vista Cobertura mejorada (~3h)
- ⬜ **Prompt E** — Acciones de Precio mejoradas (~2h)
- ⬜ **Prompt G** — Chat IA mejorado con herramientas (~2h)

### Módulos adicionales implementados (post-prompts)
- Fenómeno del Niño (clima_engine.py): correlación venta×temperatura×calórico
- Afinidad Producto×Plaza (afinidad_engine.py): clustering, empujes, redistribución
- Predistribución: gaps de distribución, matriz categoría×tienda editable
- Modo día/noche
- Diario de gestión
- YoY comparativo con data 2025

### Tasks pendientes
- #173: Auto-captura de KPIs al ejecutar análisis
- #174: Rediseñar sección tracking en Dashboard
- #175: Eliminar sección snapshot de scope global
- #262: Franco integrará data 2023 + categorización compradores

### Evolución V1.5 → V3
- V1.5: SKU hijo (talla/color), curvas de distribución, lost sales, cobertura target configurable
- V2: Agentes autónomos (Capi Agente Terceras: detecta oportunidades, envía correos a proveedores)
- V3: Multi-tenant SaaS

## Gotchas técnicos

1. **py_compile es insuficiente** — No detecta NameError en runtime. Los bugs reales en Streamlit son de scoping entre bloques elif.
2. **Cada elif es una isla** — Variables de un bloque elif NO existen en otro. Tratar cada sección como archivo independiente.
3. **Constantes en f-strings** — py_compile no detecta `{SLATE_900}` si SLATE_900 no está definida. Auditar constantes usadas vs definidas.
4. **Streamlit Cloud efímero** — snapshots/ se pierde en cada reboot. Solo data2/bases antiguas/ persiste.
5. **use_container_width deprecation** — Streamlit 1.58 warning masivo. Migrar a `width="stretch"` eventualmente.
6. **app_streamlit.py es monolítico** — 7383 líneas. Refactorizar a módulos es deuda técnica conocida pero no prioritaria ahora.
7. **importlib.reload()** — Se usa para forzar recarga de módulos durante desarrollo. Funciona en Streamlit Cloud.

## Contexto adicional

Archivos de memoria detallados disponibles en `docs/context/`. Incluyen reglas de negocio, decisiones históricas, y referencias que no caben en este CLAUDE.md.

## KPIs que importan a Ripley

- **EBITDA** y **Contribución/m²** son lo que mira el dueño para área comercial
- Margen efectivo = Contribución / Venta a precio vigente
- Capital parado = stock × costo unitario para SKUs en SOBRESTOCK/ESTANCADO/LIQUIDAR
