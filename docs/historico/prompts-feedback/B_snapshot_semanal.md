# PROMPT B — Snapshot Semanal + Carga de Bases Antiguas

> **Pre-requisito**: PROMPT 0 (MASTER) leído + Prompt A entregado y validado.
> **Frente**: B
> **Dependencias**: ninguna técnica con A; pero conceptualmente la taxonomía consume velocidad de venta de 4 semanas que solo es posible con este snapshot.

---

## 1. Contexto del problema

### 1.1. Limitación de la fuente de datos

El reporte semanal **micro/profundidad** que alimenta la herramienta entrega:
- ✅ Stock actual por SKU × tienda × talla
- ✅ Venta de la **última semana cerrada** (semana completa, no parcial)
- ❌ **No entrega histórico de venta más allá de 1 semana**

Esto hace imposible calcular:
- Velocidad de venta promedio de 4 semanas
- Tendencia de venta (semanas 1-2 vs 3-4)
- Sell-through acumulado
- Patrón de "venta cero N semanas"
- Sensibilidad histórica a markdowns

### 1.2. Solución: snapshot semanal acumulativo

Construir nuestro propio histórico almacenando, semana a semana, los datos clave del micro/profundidad. Después de N semanas tenemos ventana móvil de N semanas.

### 1.3. Punto cero

**Tengo bases antiguas listas para cargar como punto cero.** Esto significa que la herramienta puede arrancar **madura desde día 1** con histórico precargado, sin esperar 4 semanas para acumular snapshots vivos.

---

## 2. Objetivo del prompt

Implementar un sistema de snapshots semanales que:

1. Cargue las bases antiguas como histórico inicial (punto cero).
2. Procese automáticamente el micro/profundidad semanal nuevo y lo guarde como snapshot.
3. Permita a las demás secciones consultar venta y stock de cualquier semana pasada con facilidad.
4. Sea robusto frente a fallas (semanas saltadas, archivos corruptos, duplicados).

---

## 3. Especificación funcional

### 3.1. Datos a guardar por snapshot

Por cada SKU × tienda × semana:

| Campo | Tipo | Notas |
|---|---|---|
| `sku` | int/str | ID del SKU |
| `tienda` | str | Nombre tienda |
| `semana_cierre` | date | Fecha de cierre de la semana (lunes o domingo, definir convención) |
| `unidades_vendidas` | int | Venta de la semana |
| `stock_total` | int | Stock al cierre de la semana |
| `stock_valor_costo` | float | Valor del stock al cierre |
| `marca` | str | Marca del SKU |
| `categoria` | str | Categoría |
| `temporada` | str | OI / PV / TT / etc. |
| `precio_actual` | float | PVP al cierre de semana |
| `pct_descuento` | float | Descuento aplicado al cierre |

> **Nota a validar**: confirmar que todos estos campos están disponibles en el micro/profundidad. Si alguno no existe, levantar el flag.

### 3.2. Convención de semana

Definir y documentar una sola convención (sugerencia: **semana ISO** con cierre domingo).

Formato de identificador: `YYYY-WW` (ej. `2026-19` para semana 19 de 2026).

### 3.3. Almacenamiento

**Opción recomendada**: archivos Parquet por semana en carpeta estructurada.

```
/snapshots/
  2026-15/snapshot.parquet
  2026-16/snapshot.parquet
  2026-17/snapshot.parquet
  ...
```

**Justificación**:
- Parquet es eficiente en lectura columnar.
- Una semana por archivo facilita validación y depuración.
- pandas lee Parquet nativamente.

> **Nota a validar**: si tienes preferencia distinta (ej. SQLite, CSV, base de datos), proponer alternativa.

### 3.4. Carga inicial (punto cero)

Las bases antiguas que tengo precargadas deben:

1. Procesarse y normalizarse al mismo schema definido en 3.1.
2. Guardarse como snapshots con su fecha de cierre correspondiente.
3. Quedar disponibles inmediatamente para todas las secciones de la herramienta.

> **Nota a validar**: necesito definir contigo el formato exacto de las bases antiguas (cuántas semanas de histórico tienen, qué columnas exactas, en qué archivo viven). Esto es lo primero que vamos a discutir antes de codificar.

### 3.5. Proceso de actualización semanal

Cada vez que llegue un micro/profundidad nuevo:

1. La herramienta detecta que es un archivo nuevo (no procesado antes).
2. Valida que la semana del archivo no esté ya guardada (anti-duplicados).
3. Normaliza los datos al schema.
4. Guarda como snapshot de la semana correspondiente.
5. Actualiza un índice maestro de snapshots disponibles.

### 3.6. API de consulta

Crear funciones de utilidad que las demás secciones puedan llamar fácilmente:

```python
# Obtener snapshot de una semana específica
get_snapshot(semana='2026-19')

# Obtener venta acumulada de las últimas N semanas por SKU × tienda
get_venta_ultimas_n_semanas(n=4, hasta_semana='2026-19')

# Obtener evolución de stock de un SKU × tienda
get_evolucion_stock(sku=12345, tienda='Jockey Plaza', desde='2026-15', hasta='2026-19')

# Calcular velocidad de venta semanal promedio
get_velocidad_venta(sku=12345, tienda='Jockey Plaza', n_semanas=4)

# Detectar reposiciones (incrementos de stock entre semanas)
detect_reposiciones(sku=12345, tienda='Jockey Plaza', n_semanas=4)
```

### 3.7. Manejo de casos borde

- **Semana faltante en histórico**: log de advertencia + opción para interpolar o excluir SKU.
- **SKU nuevo (aparece por primera vez)**: marca `edad_en_data = 1` y va creciendo.
- **SKU descontinuado (deja de aparecer)**: mantiene última fecha registrada.
- **Snapshot corrupto**: validación al cargar; si falla, se omite y se loguea.
- **Archivo micro/profundidad parcial o malformado**: rechazar y alertar, no escribir snapshot.

---

## 4. Notas a validar antes de ejecutar

1. **Formato de las bases antiguas**: ¿cuántas semanas, qué columnas, cómo están guardadas? Discutir antes de codificar el cargador.
2. **Convención de semana**: ¿domingo/lunes? ¿semana ISO o semana de retail?
3. **Path de almacenamiento**: ¿guardamos en disco local de la PC personal, en carpeta del proyecto, en cloud (Google Drive)? Definir.
4. **Versionado del schema**: si en el futuro cambia el micro/profundidad y agrega/quita columnas, ¿cómo manejamos compatibilidad hacia atrás?
5. **Confirmar campos disponibles**: validar que precio, descuento, temporada, fecha de ingreso del SKU están todos en el micro/profundidad. Si alguno no, lo pones como gap.
6. **Tamaño esperado del histórico**: estimar volumen de datos para validar que Parquet/disco aguanta. Cálculo: 30 tiendas × ~10K SKUs activos × 52 semanas = ~15M filas/año. Parquet lo aguanta sin problema, pero confirma.
7. **Backup**: ¿implementamos backup automático de snapshots o lo gestiono yo manualmente?

---

## 5. Arquitectura técnica esperada

### 5.1. Estructura de módulos

Sugerencia (validar):

```
/snapshots_engine/
  __init__.py
  loader.py           # Carga del micro/profundidad y bases antiguas
  storage.py          # Lectura/escritura de snapshots Parquet
  api.py              # Funciones de consulta (get_snapshot, get_venta, etc.)
  validators.py       # Validación de schema y datos
  config.py           # Paths, schema, convenciones
```

### 5.2. Schema canónico

Definir un schema único (Pydantic o pandas dtype dict) que todos los snapshots respeten. Si un archivo no cumple → no se guarda.

### 5.3. Índice maestro

Un archivo (`snapshots_index.parquet` o similar) que liste todas las semanas disponibles + metadata (fecha de creación del snapshot, número de filas, hash de validación).

Permite a otras secciones saber rápidamente qué semanas hay disponibles sin escanear carpetas.

### 5.4. Idempotencia

Si re-procesas el mismo micro/profundidad dos veces, el resultado debe ser el mismo. No duplicación, no corrupción.

---

## 6. Criterios de aceptación

### 6.1. Funcionales

- [ ] Las bases antiguas se cargan exitosamente como snapshots históricos.
- [ ] Un nuevo micro/profundidad se procesa y guarda como snapshot semanal sin intervención manual más allá de subir el archivo.
- [ ] El sistema detecta y rechaza duplicados (mismo archivo cargado 2 veces).
- [ ] Existe API de consulta funcional con al menos las 5 funciones listadas en 3.6.
- [ ] Schema de snapshots es validado antes de guardar; archivos malformados son rechazados.
- [ ] Existe índice maestro de snapshots disponibles.

### 6.2. Métricos

- [ ] Velocidad de venta de 4 semanas calculada para 10 SKU × tienda aleatorios coincide con suma manual del reporte fuente.
- [ ] Total de SKU × tienda en snapshot de la semana N coincide con el total del reporte fuente de esa semana.
- [ ] El cálculo de evolución de stock (semanas 1 a 4) detecta correctamente las reposiciones manuales en una muestra de 5 SKUs.

### 6.3. Auditoría

- [ ] Ejecutar skill `code-audit` y entregar resultados.
- [ ] Test manual: cargar bases antiguas, validar conteo de semanas y filas.
- [ ] Test manual: simular carga de micro/profundidad nuevo y verificar que se guarda correctamente.
- [ ] Test manual: cargar el mismo archivo dos veces y validar que no se duplica.

---

## 7. Entregables

1. Módulos del engine de snapshots (loader, storage, api, validators, config).
2. Bases antiguas cargadas como snapshots iniciales.
3. Índice maestro de snapshots con metadata.
4. Documentación de la API de consulta (con ejemplos de uso).
5. Resultados de auditoría.
6. Validación de muestra de 10 SKU × tienda.
7. Backlog de hallazgos.

---

## 8. Lo que NO entra en este prompt

- Implementar funciones de forecasting (eso es backlog global).
- Detectar markdowns automáticamente (basta con guardar el campo, el análisis viene en otros prompts).
- Visualización de la evolución temporal en la UI (eso vendrá cuando se rediseñen las secciones específicas).
- Cualquier feature del backlog global del MASTER.

---

**Fin del Prompt B.**
