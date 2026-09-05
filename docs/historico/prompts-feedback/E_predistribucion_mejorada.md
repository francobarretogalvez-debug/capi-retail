# PROMPT E — Mejora Predistribución (Cobertura en Tiendas Faltantes)

> **Pre-requisito**: PROMPT 0 (MASTER) + Prompts A, B, C y D entregados y validados.
> **Frente**: E
> **Dependencias**: requiere taxonomía (A), snapshot (B) y especialmente vista Cobertura (D) operativa.

---

## 1. Contexto del problema

### 1.1. Sección actual: "Predistribución"

Hoy lista, por SKU, los gaps de distribución entre tiendas. Estructura típica de columnas:

| sku | nombre | marca | categoria | edad_s | stock_c | costo | precio | n_tiendas | n_tiendas_con_stock | n_tiendas_sin_stock | pct_cobertura | tiendas_faltantes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|

**Ejemplo**: PK BAS RGT FW26 (Navigata, Casacas) está en 30 tiendas total, 29 con stock, 1 sin stock (Juliaca). Cobertura 97%.

### 1.2. Problema detectado

**"Tienda faltante" no es lo mismo que "oportunidad de empuje"**.

Si Juliaca no tiene la PK BAS RGT pero **ya tiene 30 semanas de cobertura en Casacas Navigata** (categoría saturada), mandarle más stock es contraproducente — la categoría ya está sobrestockeada con otros SKUs.

La sección actual lista gaps técnicos pero no valida si vale la pena cubrirlos. La decisión queda 100% en cabeza del operador, que tiene que ir a otra sección a chequear cobertura.

### 1.3. Insight a resolver

Cruzar cada tienda faltante con la **cobertura de su marca × categoría** para clasificar la oportunidad:

- 🟢 Cobertura baja → **empujar** (oportunidad real)
- 🟡 Cobertura media → **evaluar**
- 🔴 Cobertura alta → **no empujar** (saturada)
- ⚪ Sin venta histórica → **decisión manual**

Esto convierte la sección de "inventario de gaps técnicos" a "priorización de oportunidades reales".

---

## 2. Objetivo del prompt

Mejorar la sección Predistribución agregando columnas y filtros que crucen automáticamente cada tienda faltante con la cobertura de su marca × categoría, permitiendo identificar a primera vista qué empujes valen la pena.

---

## 3. Especificación funcional

### 3.1. Columnas nuevas a agregar

Junto a la columna actual `tiendas_faltantes`, agregar:

| Columna nueva | Descripción | Cálculo |
|---|---|---|
| `cob_marca_cat_promedio_faltantes` | Cobertura promedio Marca × Categoría en las tiendas faltantes | Promedio simple de la cobertura por tienda |
| `tiendas_oportunidad` | # tiendas faltantes con cobertura <16 sem | Conteo |
| `tiendas_saturadas` | # tiendas faltantes con cobertura >26 sem | Conteo |
| `tiendas_sin_data` | # tiendas faltantes sin venta histórica de la categoría | Conteo |
| `recomendacion` | Veredicto pre-calculado | Lógica abajo |

### 3.2. Lógica de la columna `recomendacion`

```
Si todas las tiendas faltantes son "tiendas_oportunidad" → "Empujar"
Si todas son "tiendas_saturadas" → "No empujar"
Si todas son "sin_data" → "Decisión manual"
Si hay mezcla → "Mixto — revisar drill-down"
```

### 3.3. Drill-down por SKU

Click en una fila de Predistribución → expansión que muestra detalle por cada tienda faltante:

| Tienda Faltante | Cobertura Marca × Categoría | Velocidad sem | Stock unidades | Color | Recomendación |
|---|---|---|---|---|---|
| Juliaca | 8 sem | 12 | 95 | 🟢 | Empujar |
| Cajamarca | 22 sem | 7 | 155 | 🟡 | Evaluar |
| Chiclayo | 45 sem | 3 | 135 | 🔴 | No empujar — saturada |

### 3.4. Filtros nuevos

- Toggle **"Solo SKUs con oportunidad real"** → filtra el listado para mostrar solo SKUs donde **al menos una tienda faltante** tiene cobertura <16 sem.
- Filtro de marca, categoría, temporada (consistente con otras secciones).

### 3.5. Niveles de cobertura

Usar la misma taxonomía que la vista Cobertura (Prompt D):

| Color | Cobertura | Recomendación de empuje |
|---|---|---|
| 🟢 Verde | <16 sem | **Empujar** |
| 🟡 Amarillo | 16-26 sem | **Evaluar** |
| 🔴 Rojo | >26 sem | **No empujar — saturada** |
| ⚪ Gris | Sin venta histórica | **Decisión manual** |

> **Nota importante**: aquí los umbrales son de "**cobertura para decidir empuje**", no de "salud del stock". Por eso el verde llega hasta 16 sem (no hasta 8 como en alertas), porque incluso con cobertura 12 sem (target) hay espacio para más.

### 3.6. Caso borde — Tiendas sin venta histórica

Si una tienda faltante nunca ha vendido la marca × categoría:
- No calcular cobertura (no hay velocidad).
- Marcar como "Sin venta histórica — decisión manual".
- **No descartar automáticamente**: podría ser oportunidad de prueba (push de marca a tienda nueva).

---

## 4. Notas a validar antes de ejecutar

1. **¿La sección actual lista todas las tiendas faltantes o solo las que estaban en plan de distribución original?**
   - Si lista todas indistintamente, hay ruido (tiendas que no debían recibir el SKU aparecen como faltantes).
   - Si solo lista las del plan, está bien.
   - **Validar lógica actual antes de cambiar.**

2. **Promedio simple vs ponderado**: la columna `cob_marca_cat_promedio_faltantes` propone promedio simple. ¿Considerar ponderar por capital o stock de cada tienda faltante? **Recomendación**: simple por ahora; ponderado al backlog si surge necesidad.

3. **Umbrales de empuje (verde/amarillo/rojo)**: la propuesta usa 16 / 26 sem. ¿Ajustar?

4. **Drill-down**: ¿expansión inline (`st.expander`) o modal? Consistente con Prompt D.

5. **Cobertura de la marca completa vs marca × categoría**: la propuesta usa marca × categoría. ¿Agregar también marca × tienda completa como columna adicional? Probablemente sí, para tener vista macro y micro juntas. **Recomendación**: agregar ambas, con la marca×categoría como decisión principal y marca×tienda como contexto.

6. **¿Mostrar la cobertura de la categoría completa (todas las marcas)?**: lectura adicional de "salud de la línea". Útil pero no crítico.

7. **Confirmar campo de plan de distribución**: si el SKU tiene un plan que define a qué tiendas debía ir, ese campo debe estar disponible para filtrar correctamente. Si no existe, levantar gap.

---

## 5. Arquitectura técnica esperada

### 5.1. Módulos involucrados

- Consume `cobertura.py` (Prompt D) — para obtener cobertura por marca × categoría × tienda.
- Consume `taxonomia.py` (Prompt A) — para clasificar estados de cobertura.
- Modifica módulo existente de Predistribución (no crear uno nuevo).

### 5.2. Cálculos

Para cada SKU del listado actual, por cada tienda en `tiendas_faltantes`:
1. Consultar cobertura marca × categoría × tienda al módulo de cobertura.
2. Clasificar la oportunidad según umbrales 3.5.
3. Agregar al SKU las columnas resumen.
4. Pre-calcular el detalle expandible.

### 5.3. Performance

Si el listado de Predistribución tiene cientos de SKUs y cada uno tiene varias tiendas faltantes, el cálculo cruzado puede ser pesado. Considerar:
- Pre-cálculo al cargar la sección (cache).
- Computar on-demand solo cuando se expande el drill-down.

---

## 6. Criterios de aceptación

### 6.1. Funcionales

- [ ] Las nuevas columnas aparecen en la tabla principal de Predistribución.
- [ ] La columna `recomendacion` se calcula correctamente según las 4 reglas (Empujar / No empujar / Decisión manual / Mixto).
- [ ] Drill-down por SKU muestra detalle por tienda faltante con cobertura específica y color.
- [ ] Toggle "Solo SKUs con oportunidad real" filtra correctamente.
- [ ] Caso borde "sin venta histórica" se maneja sin generar errores ni cobertura infinita.
- [ ] Filtros de marca, categoría, temporada funcionan.

### 6.2. Métricos

- [ ] Para 5 SKUs aleatorios, validar manualmente:
  - Conteo de tiendas oportunidad / saturadas / sin data.
  - Recomendación final.
  - Cobertura mostrada en drill-down.
- [ ] Validar que la cobertura cruzada coincide con la mostrada en la sección Cobertura (Prompt D) para los mismos cruces.
- [ ] Validar que ningún SKU "cae" del listado por la mejora (mismo conteo total que antes, solo agregamos columnas).

### 6.3. Auditoría

- [ ] Ejecutar skill `code-audit` y entregar resultados.
- [ ] Validación de consistencia con Prompt D (mismos números de cobertura).
- [ ] Validación de muestra.

---

## 7. Entregables

1. Módulo de Predistribución modificado.
2. Nuevas columnas implementadas.
3. Drill-down funcional.
4. Filtros nuevos.
5. Resultados de auditoría.
6. Validación de muestra de 5 SKUs.
7. Backlog de hallazgos.

---

## 8. Lo que NO entra en este prompt

- Recomendación automática de qué SKU específico enviar para cubrir un gap (decisión del comprador).
- Generar la orden de transferencia automáticamente.
- Integración con sistema de logística para ejecutar el empuje.
- Cualquier feature del backlog global del MASTER.

---

**Fin del Prompt E.**
