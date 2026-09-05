# PROMPT C — Rediseño Salud del Stock (Venta Cero + Sobrestock)

> **Pre-requisito**: PROMPT 0 (MASTER) + Prompts A y B entregados y validados.
> **Frente**: C
> **Dependencias**: requiere taxonomía maestra (A) y snapshot semanal (B) operativos.

---

## 1. Contexto del problema

### 1.1. Sección actual: "Briefing Semanal"

La sección actualmente llamada "Briefing Semanal" contiene:
- Detalle de venta cero
- Detalle de sobrestock

### 1.2. Problemas detectados

1. **Nombre genérico**: "Briefing Semanal" es palabra de información, no de acción. La sección entrega trabajo (cosas que hay que accionar), no informes.
2. **Listado plano sin priorización**: hoy todos los SKUs aparecen en una lista sin diferenciación por criticidad ni por tipo de acción esperada.
3. **Falta de tiers**: un SKU con cobertura 17 sem y uno con cobertura 80 sem requieren acciones radicalmente distintas, pero hoy se tratan igual.
4. **Sin loop de cierre**: paso el listado a tiendas/marcas pero no hay forma de verificar ejecución en piso.
5. **Markdown sesgado como primera palanca**: hoy el listado puede llevar a mandar descuento sin antes agotar palancas operativas (exhibición, comunicación de precio, VM).
6. **Falta variable crítica**: no se considera **edad del producto** para diferenciar zombie real de mal lanzamiento recuperable.

---

## 2. Objetivo del prompt

Rediseñar la sección como **"Salud del Stock"** (nombre tentativo a validar contigo), unificando venta cero + sobrestock bajo una lógica de tiers basada en la matriz cobertura × edad del Prompt A.

La sección debe entregar un listado **priorizado, accionable y con tipo de acción sugerida diferenciada**.

---

## 3. Especificación funcional

### 3.1. Alcance

Esta sección cubre:
- ✅ Detalle Venta Cero (SKUs sin movimiento)
- ✅ Detalle Sobrestock (SKUs con cobertura alta pero con venta)

Esta sección **NO cubre**:
- ❌ Transferencias sugeridas (es sección aparte)
- ❌ Alertas de quiebre (cubierto por Reposición — ver Prompt F)
- ❌ Markdown sugeridos (backlog global)

### 3.2. Lógica de tiers

Usar la taxonomía maestra del Prompt A. Los SKUs de esta sección caen en los siguientes estados:

**Para venta cero:**

| Estado (de Prompt A) | Tier interno | Acción primaria sugerida |
|---|---|---|
| RAMPA (sin venta, edad <8 sem) | **No incluir en listado** | Esperar — es lanzamiento |
| DORMIDO (sin venta, edad 8-26 sem) | **Tier B1** | Revisar exhibición → markdown si no responde |
| MUERTO (sin venta, edad >26 sem) | **Tier A1** | Markdown agresivo + liquidación |

**Para sobrestock (con venta):**

| Estado | Edad | Tier interno | Acción primaria sugerida |
|---|---|---|---|
| VIGILAR (16-26 sem) | cualquiera | **Tier C** | Empuje en piso, exhibición |
| SOBRESTOCK (26-52 sem) | <26 sem | **Tier B2** | Exhibición + transferencia, NO descuento |
| SOBRESTOCK (26-52 sem) | >26 sem | **Tier B3** | Empuje + markdown moderado evaluado |
| ZOMBIE (>52 sem) | <26 sem | **Tier A2** | Revisar exhibición primero, markdown solo si confirmado |
| ZOMBIE (>52 sem) | >26 sem | **Tier A3** | Markdown agresivo + transferir lo movible |
| LIQUIDAR | >26 sem | **Tier A4** | Markdown profundizado + liquidación |

### 3.3. Filosofía de palancas (importante)

**Markdown NO es palanca primaria.** Antes de aplicar descuento, agotar palancas operativas de costo cero:

1. **Exhibición** — ¿está en piso? ¿en zona caliente? ¿completo en talla?
2. **Comunicación de precio** — ¿tiene su PVP visible? ¿bien etiquetado?
3. **Visual merchandising** — ¿está mezclado con producto correcto? ¿outfit armado?
4. **Capacitación de vendedor** — ¿saben que existe? ¿lo ofrecen?

La acción sugerida del tier debe reflejar esta jerarquía. Solo los tiers con SKUs **maduros** (edad >26 sem) sugieren markdown directo.

### 3.4. Estructura de la vista

#### 3.4.1. Pantalla principal

Tabla unificada con todos los SKUs en problema, columnas mínimas:

| Columna | Notas |
|---|---|
| SKU | ID |
| Nombre | Descripción |
| Marca | |
| Categoría | |
| Tienda | |
| Stock unidades | |
| Capital S/ | |
| Cobertura sem | |
| Edad sem | |
| Velocidad sem | Promedio 4 sem |
| Estado | De taxonomía |
| Tier | A1/A2/A3/A4/B1/B2/B3/C |
| Acción sugerida | Texto corto |
| % Descuento actual | |

#### 3.4.2. Filtros

- Marca (multi-select)
- Tienda (multi-select)
- Categoría (multi-select)
- Tier (multi-select)
- Estado (multi-select de la taxonomía)
- Temporada (todo / OI / PV / etc.)
- Toggle: "Solo SKUs sin descuento aplicado" (útil para identificar zombies sin markdown previo)

#### 3.4.3. Resumen ejecutivo (panel arriba)

- Capital total parado en SKUs problema (S/)
- Distribución por tier (gráfico tipo barras o pie)
- Top 5 marcas por capital parado
- Top 5 tiendas por capital parado

### 3.5. Pareto por tienda

Implementar Pareto 80/20 **por tienda** (no global) para identificar el 20% de SKUs que concentra el 80% del capital parado en cada tienda.

Toggle adicional: "Mostrar solo Pareto crítico" → filtra a esos SKUs en cada tienda.

> **Razón**: si Pareto se hace global, las tiendas grandes (Jockey, San Miguel) acaparan el listado y las chicas quedan invisibles. Pareto por tienda da equidad.

### 3.6. Cruce con descuento histórico

Implementar lógica de detección de "zombies sin markdown intentado":

```
SKUs con cobertura >26 sem Y descuento <20% → flag "Sin markdown intentado"
SKUs con cobertura >26 sem Y descuento >35% → flag "Markdown insuficiente"
```

Estos flags ayudan a priorizar acciones de pricing.

### 3.7. Loop de cierre con tiendas (futuro, validar alcance)

Idealmente la sección debería permitir trackear ejecución:
- Marcar SKU como "revisado en piso" (con fecha)
- Marcar SKU como "exhibición corregida"
- Marcar SKU como "markdown aplicado"

> **Nota a validar**: ¿implementamos este componente de tracking en este prompt o queda para fase 2?

---

## 4. Notas a validar antes de ejecutar

1. **Nombre de la sección**: la propuesta es "Salud del Stock". Alternativa: "Acciones de Stock". **Decidir contigo antes de codificar.**
2. **Tracking de ejecución (3.7)**: ¿incluir en este prompt o backlog?
3. **Threshold de Pareto**: 80/20 estándar, ¿o ajustar?
4. **Subdivisión de SOBRESTOCK por edad**: la propuesta separa B2/B3 según edad. ¿Mantener distinción o simplificar?
5. **Lanzamientos (RAMPA)**: confirmar que se excluyen del listado, no se muestran ni siquiera con flag.
6. **Acciones sugeridas en tabla**: ¿texto fijo por tier o personalizable por marca/categoría?
7. **Output exportable**: ¿necesitas que la sección permita exportar el listado filtrado a Excel/CSV para mandar a tiendas/marcas? Definir formato esperado del export.

---

## 5. Arquitectura técnica esperada

### 5.1. Módulos involucrados

- Consume del módulo `taxonomia.py` (Prompt A) — clasificación de estados.
- Consume del módulo `snapshots_engine` (Prompt B) — velocidad y stock.
- Nuevo módulo: `salud_stock.py` — lógica de tiers y filtros específicos.

### 5.2. Configuración de tiers

Los mapeos estado → tier → acción sugerida deben vivir en archivo de configuración separado, no hardcodeados:

```python
TIERS = {
    'A1': {
        'estado': 'MUERTO',
        'condicion': 'edad >26 sem y sin venta',
        'accion': 'Markdown agresivo + liquidación',
        'criticidad': 'alta'
    },
    # ...
}
```

### 5.3. Streamlit UI

Estructura sugerida (validar):
1. Header con resumen ejecutivo (capital total, top tiendas/marcas).
2. Filtros laterales o expandibles.
3. Tabla principal con sorting y formato condicional (semáforo).
4. Botón de export del listado filtrado.

---

## 6. Criterios de aceptación

### 6.1. Funcionales

- [ ] Sección renombrada (nombre validado contigo).
- [ ] Tabla unificada muestra venta cero + sobrestock bajo lógica de tiers.
- [ ] Cada SKU está clasificado en exactamente un tier.
- [ ] Filtros funcionan correctamente y combinables.
- [ ] Pareto 80/20 por tienda funciona y es activable vía toggle.
- [ ] Flags de "Sin markdown intentado" / "Markdown insuficiente" se calculan correctamente.
- [ ] SKUs en RAMPA no aparecen en el listado.
- [ ] Resumen ejecutivo muestra métricas clave (capital total, top tiendas/marcas).
- [ ] Export del listado filtrado funciona.

### 6.2. Métricos

- [ ] Validar capital total parado contra suma manual del reporte fuente (tolerancia 1%).
- [ ] Validar distribución por tier en muestra de 20 SKUs aleatorios.
- [ ] Validar Pareto 80/20 manualmente en 3 tiendas elegidas al azar.
- [ ] Validar que ningún SKU aparece en más de un tier.

### 6.3. Auditoría

- [ ] Ejecutar skill `code-audit` y entregar resultados.
- [ ] Validación de cálculo de cobertura, edad y velocidad consistente con Prompts A y B.
- [ ] Validación de muestra de 10 SKU × tienda.

---

## 7. Entregables

1. Módulo `salud_stock.py` (o nombre equivalente).
2. Archivo de configuración de tiers.
3. Sección Streamlit rediseñada y operativa.
4. Función de export del listado filtrado.
5. Resultados de auditoría.
6. Validación de muestra.
7. Backlog de hallazgos.

---

## 8. Lo que NO entra en este prompt

- Loop de cumplimiento con marcas 3ras (backlog global).
- Recomendación automática de % de markdown óptimo (backlog global).
- Forecasting de cuándo un SKU pasará de tier B a tier A (backlog).
- Sección de Transferencias (sección independiente, no en este prompt).
- Cualquier feature del backlog global del MASTER.

---

**Fin del Prompt C.**
