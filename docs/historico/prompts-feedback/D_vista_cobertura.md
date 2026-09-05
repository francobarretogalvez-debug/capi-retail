# PROMPT D — Rediseño Vista Cobertura (Heatmap + Drill-down + Alertas)

> **Pre-requisito**: PROMPT 0 (MASTER) + Prompts A, B y C entregados y validados.
> **Frente**: D
> **Dependencias**: requiere taxonomía maestra (A) y snapshot semanal (B).

---

## 1. Contexto del problema

### 1.1. Sección actual: "Cobertura por Marca"

La vista actual muestra una tabla simple con cobertura promedio por marca (Stock Uds, Capital S/, Cobertura sem, SKUs, Tiendas).

### 1.2. Problemas detectados

1. **Cobertura agregada por marca es métrica engañosa**: una marca con cobertura promedio 19 sem puede tener Jockey en 8 sem (quiebre) y Cajamarca en 60 sem (zombie). El promedio oculta los extremos opuestos.
2. **No accionable**: "Pierre Cardin: 29 sem" no dice qué hacer ni dónde.
3. **Granularidad incorrecta**: el grano de decisión real es **marca × categoría × tienda**, no marca sola.
4. **No detecta gaps de cobertura por categoría**: la repo automática opera SKU-a-SKU. Si una categoría completa pierde stock porque su PV en liquidación se agotó, la repo OI puede no cubrir el gap.

### 1.3. Insight central a resolver

La repo automática a nivel SKU tiene un **blind spot estructural**:
- Solo repone SKUs OI activos con stock central disponible.
- No detecta el gap de demanda de la categoría completa que dejaba la liquidación PV cuando se agota.
- La demanda categórica continúa pero la repo no la cubre porque opera SKU-a-SKU.

**Ejemplo real**: Marquis × Camisas ML × Jockey.
- Repo SKU automática = 100 uds (solo OI).
- Pero la categoría completa demanda 200 uds (incluyendo lo que cubría PV).
- Gap = 100 uds → oportunidad de reposición OI no detectada.

La nueva vista debe **detectar este gap macro** para que el comprador (yo) decida manualmente qué SKUs OI enviar para cubrirlo.

---

## 2. Objetivo del prompt

Reemplazar la tabla actual de Cobertura por una vista de 3 componentes que permita:

1. Ver de un vistazo la salud del stock por marca × tienda (heatmap macro).
2. Hacer drill-down a categoría dentro de un cruce marca × tienda.
3. Recibir alertas accionables sobre gaps de cobertura por marca × categoría × tienda.

Toda la vista debe ser **diagnóstica/visual**. No reemplaza la sección de Reposición — la complementa.

---

## 3. Especificación funcional

### 3.1. Componente 1 — Heatmap Marca × Tienda

**Estructura**:
- Filas: Marcas (todas las del catálogo de moda masculina)
- Columnas: Tiendas (30)
- Celdas: cobertura de la marca completa en esa tienda + color de semáforo

**Semáforo de 6 niveles** (alineado con taxonomía Prompt A):

| Color | Cobertura | Lectura |
|---|---|---|
| Rojo oscuro | <4 sem | Quiebre crítico |
| Naranja | 4-8 sem | Faltante / alerta |
| Verde | 8-16 sem | Saludable (target zone) |
| Amarillo | 16-26 sem | Vigilar |
| Marrón | 26-52 sem | Sobrestock |
| Negro | >52 sem | Zombie |

**Interacción**:
- Hover sobre celda → tooltip con stock uds, capital S/, velocidad sem.
- Click sobre celda → abre Componente 2 (drill-down a categoría).

### 3.2. Componente 2 — Drill-down Marca × Categoría × Tienda

Al hacer click en una celda del heatmap (ej. MARQUIS × Jockey), abrir tabla:

| Categoría | Stock Uds | Capital S/ | Cobertura sem | Velocidad sem | Estado |
|---|---|---|---|---|---|
| Camisas M/L | 320 | 28K | 8.0 | 40 | Alerta (naranja) |
| Polos M/C | 1,200 | 45K | 14.0 | 86 | Saludable (verde) |
| Pantalones | 180 | 22K | 22.0 | 8 | Vigilar (amarillo) |
| Casacas | 95 | 18K | 6.5 | 15 | Alerta (naranja) |
| ... | | | | | |

**Mismos colores de semáforo** que el heatmap.

### 3.3. Componente 3 — Listado de Alertas

Pantalla accionable separada que liste **TODOS** los cruces marca × categoría × tienda en zona crítica/alerta (cobertura <8 sem), ordenados por relevancia.

**Columnas**:

| Columna | Notas |
|---|---|
| Marca | |
| Categoría | |
| Tienda | |
| Cobertura sem | Coloreada con semáforo |
| Stock unidades | |
| Velocidad sem | |
| Capital en riesgo | Stock × precio promedio = lo que se va a quebrar |
| Acción sugerida | Texto corto |

**Filtros**:
- Marca (multi-select)
- Tienda (multi-select)
- Categoría (multi-select)
- **Temporada (todo / OI / PV / etc.)** — crítico
- Toggle "Solo alertas" — muestra solo cruces con cobertura <8 sem
- Toggle "Excluir descontinuados"
- Slider de umbral de cobertura para alertas (default 8 sem, ajustable)

**Orden por defecto**: por capital en riesgo descendente.

### 3.4. Lógica de cobertura

**Cobertura categoría-tienda (default — incluye todo el stock en piso, sin distinción de temporada):**

```
cobertura = stock_total_categoria / velocidad_venta_categoria_semanal
```

**Filtro de temporada disponible**:

```
cobertura_filtrada = stock_temporada_X / velocidad_venta_total_categoria
```

> Esto permite alternar entre "visión real del piso hoy" (default, todo el stock) y "visión sostenible OI" (solo OI), entre otras combinaciones.

### 3.5. Lógica de alerta

Una alerta dispara cuando se cumplen **las 3 condiciones**:

```
cobertura < 8 sem
Y velocidad_categoria > 0
Y stock_total > umbral_minimo (default: 10 uds)
```

**Razón de cada filtro**:
- `cobertura <8 sem`: criterio de severidad.
- `velocidad >0`: la categoría sí está vendiendo (no es producto inerte).
- `stock >10 uds`: evita alertas marginales de SKUs casi vacíos.

### 3.6. Caso borde — Tiendas sin venta histórica

Si un cruce marca × categoría × tienda tiene **velocidad = 0** (nunca vendió):
- No calcular cobertura como infinita.
- Flagear como **"Sin venta histórica — decisión manual"**.
- Esto evita falsos positivos en heatmap (toda la categoría aparecería negra) y falsos negativos en alertas (no se detectaría como oportunidad de prueba).

### 3.7. Lo que la vista NO hace (alcance acotado)

- ❌ No sugiere SKUs específicos para reponer (decisión del comprador).
- ❌ No aplica factor de elasticidad de precio (backlog).
- ❌ No reemplaza la sección de Reposición.
- ❌ No decide cuánto reponer (solo señala el gap).

---

## 4. Notas a validar antes de ejecutar

1. **Nombre final de la sección**: hoy es "Cobertura". ¿Renombrar a "Mapa de Cobertura" / "Salud del Surtido" / mantener? **Decisión a tomar contigo.**
2. **Componentes en una sola pestaña vs separados**: ¿los 3 componentes (heatmap, drill-down, alertas) viven en la misma pestaña/sección o en pestañas distintas dentro de la sección Cobertura?
3. **Heatmap performance**: con 30 tiendas × ~14 marcas = 420 celdas. Streamlit puede renderizar esto, pero validar UX. Si es lento, considerar paginación o agrupar marcas.
4. **¿Qué pasa con marcas que solo están en algunas tiendas?**: ej. una marca premium solo en 8 tiendas. ¿Mostrar celdas vacías o filtrar la fila? **Recomendación**: celdas grises con tooltip "marca no asignada a tienda".
5. **Umbral mínimo de stock para alertas**: default propuesto = 10 uds. ¿Ajustar?
6. **Drill-down: ¿modal, expandible inline, o pestaña aparte?**
7. **Export del listado de alertas**: ¿permitir exportar a Excel/CSV? Probablemente sí.

---

## 5. Arquitectura técnica esperada

### 5.1. Módulos involucrados

- Consume `taxonomia.py` (Prompt A) — clasificación de estados.
- Consume `snapshots_engine` (Prompt B) — velocidad y stock.
- Nuevo módulo: `cobertura.py` — lógica de heatmap, drill-down y alertas.

### 5.2. Cálculos pre-computados

Para performance, considerar pre-computar:
- Matriz cobertura marca × tienda (al cargar la sección).
- Tabla cobertura marca × categoría × tienda (idem).

Refresh on-demand cuando cambian filtros de temporada.

### 5.3. Streamlit UI

- Heatmap con `st.dataframe` con formato condicional, o componente personalizado (plotly heatmap).
- Drill-down: `st.expander` o `st.modal` (validar disponibilidad en versión Streamlit usada).
- Alertas: tabla principal con `st.dataframe` + filtros sidebar.

---

## 6. Criterios de aceptación

### 6.1. Funcionales

- [ ] Heatmap Marca × Tienda renderiza correctamente con semáforo de 6 niveles.
- [ ] Click en celda del heatmap abre drill-down a categoría.
- [ ] Drill-down muestra cobertura por categoría con mismos colores.
- [ ] Listado de Alertas muestra solo cruces con cobertura <8 sem (default).
- [ ] Filtros (marca, tienda, categoría, temporada) funcionan y son combinables.
- [ ] Toggle "Solo alertas" filtra correctamente.
- [ ] Slider de umbral de cobertura ajusta la alerta dinámicamente.
- [ ] Caso borde "sin venta histórica" se maneja correctamente (no infinito, sí flag).
- [ ] Export del listado filtrado funciona.

### 6.2. Métricos

- [ ] Cobertura calculada por marca × tienda coincide con suma manual del reporte fuente en muestra de 10 cruces.
- [ ] Cobertura por marca × categoría × tienda coincide con cálculo manual en muestra de 10 cruces.
- [ ] Cuando se aplica filtro de temporada (solo OI), el stock filtrado coincide con suma manual.
- [ ] Lista de alertas no contiene falsos positivos (todos los items cumplen las 3 condiciones).

### 6.3. Auditoría

- [ ] Ejecutar skill `code-audit` y entregar resultados.
- [ ] Validar consistencia de colores y umbrales con la taxonomía maestra (Prompt A).
- [ ] Validar muestra de 10 cruces marca × categoría × tienda manualmente.

---

## 7. Entregables

1. Módulo `cobertura.py`.
2. Sección Streamlit rediseñada con los 3 componentes.
3. Función de export del listado de alertas.
4. Resultados de auditoría.
5. Validación de muestra.
6. Backlog de hallazgos.

---

## 8. Lo que NO entra en este prompt

- **Cobertura sostenible proyectada (forward-looking)**: backlog. Este prompt solo cubre cobertura presente con filtros de temporada.
- **Factor de elasticidad de precio**: backlog.
- **Integración del gap detectado como input directo a Reposición**: backlog. Por ahora la vista es diagnóstica paralela.
- **Recomendación automática de SKUs específicos para empuje**: fuera de alcance — decisión del comprador.
- **Cualquier feature del backlog global del MASTER.**

---

**Fin del Prompt D.**
