# PROMPT G — Auditoría Sección Estados (Validar valor de la dimensión tienda)

> **Pre-requisito**: PROMPT 0 (MASTER) + Prompts A-F entregados y validados.
> **Frente**: G
> **Dependencias**: depende crítica de Prompt A (taxonomía maestra).

---

## 1. Contexto del problema

### 1.1. Sección actual: "Estados" (cap_todos_estados)

La sección clasifica SKU × tienda en estados (DORMIDO / SIN VENTA / MUERTO / CRÍTICO / etc.) y permite drill-down por tienda.

### 1.2. Cuestionamiento operativo

**Las decisiones de pricing/markdown se toman a nivel SKU para toda la cadena, no por tienda individual.**

Si un SKU está dormido en 9 tiendas pero vivo en 3 → no puedes ponerle markdown solo en las 9 tiendas dormidas. **El descuento aplica al SKU completo.**

Entonces: la vista por tienda crea data interesante pero **no accionable para markdown**.

### 1.3. Reto contrario

La data por tienda **sí podría servir para otras decisiones distintas a markdown**:
- Detección de oportunidades de transferencia (mover stock de tiendas dormidas a vivas).
- Detección de problemas de exhibición tienda-específicos.
- Identificación de tiendas "cementerio" sistémicas.

### 1.4. Problema a resolver

¿La data por tienda en la sección Estados **realmente aporta valor** para esos casos, o **es ruido** y conviene migrar a vista por SKU agregada?

---

## 2. Objetivo del prompt

**Esto es una auditoría con datos reales que llevará a una decisión de rediseño.**

Determinar empíricamente con la data actual:
1. ¿Qué % de SKUs están en mismo estado en todas las tiendas?
2. ¿Qué % tienen patrón mixto (varios estados según tienda)?
3. Decidir el rediseño según los resultados.

---

## 3. Especificación funcional

### 3.1. Auditoría a ejecutar

#### 3.1.1. Análisis de homogeneidad

Para cada SKU del catálogo:
1. Identificar el estado de cada combo SKU × tienda (usando taxonomía Prompt A).
2. Calcular distribución de estados dentro del SKU.
3. Clasificar el SKU según homogeneidad:

| Categoría | Criterio |
|---|---|
| **HOMOGÉNEO** | >80% de tiendas en el mismo estado |
| **MAYORÍA** | 50-80% en el mismo estado |
| **DISPERSO** | Ningún estado supera el 50% |

#### 3.1.2. Análisis por estado dominante

Para los SKUs HOMOGÉNEOS:
- ¿Cuántos están en cada estado de la taxonomía?
- ¿En qué estados específicos la homogeneidad es alta vs baja?

Hipótesis a validar:
- Los SKUs en estados extremos (MUERTO, ZOMBIE, ÓPTIMO) tienden a ser homogéneos.
- Los SKUs en estados intermedios (VIGILAR, SOBRESTOCK) tienden a ser dispersos.

#### 3.1.3. Análisis de oportunidades de transferencia

Para los SKUs DISPERSOS y MAYORÍA:
- ¿Cuántos tienen tiendas en SOBRESTOCK + tiendas en QUIEBRE/ALERTA simultáneamente? Estos son oportunidades de transferencia claras.
- ¿Cuántos tienen tiendas en MUERTO/DORMIDO + tiendas en ÓPTIMO? Idem.

### 3.2. Decisiones a tomar según resultados

Basado en los hallazgos, decidir entre 3 escenarios:

#### Escenario A — Mayoría HOMOGÉNEOS (>70%)
- La data por tienda no aporta valor extra para pricing — la mayoría de SKUs están en mismo estado en todas.
- **Acción**: vista principal pasa a ser **por SKU agregado** (un estado por SKU usando regla de mayoría >70%).
- Drill-down por tienda se mantiene como **vista secundaria opcional** para los pocos casos mixtos.

#### Escenario B — Distribución mixta (40-70% homogéneos)
- La data por tienda aporta valor pero no como vista principal.
- **Acción**: dos vistas con propósitos separados.
  - **Vista 1 (default)**: por SKU agregado → para decisiones de pricing.
  - **Vista 2 (drill-down)**: por SKU × tienda → para decisiones de allocation/transferencia. **Solo se muestra cuando el SKU es DISPERSO o MAYORÍA**.

#### Escenario C — Mayoría DISPERSOS (>50%)
- La data por tienda es el formato natural — los SKUs realmente se comportan distinto entre tiendas.
- **Acción**: mantener vista actual por tienda como principal, pero agregar:
  - Resumen ejecutivo por SKU (qué % de tiendas en cada estado).
  - Filtros para identificar patrones específicos (ej. "SKUs con sobrestock en algunas + quiebre en otras").

### 3.3. Independiente del escenario

Tres mejoras aplicables siempre:

1. **Migrar la clasificación a la taxonomía maestra del Prompt A.** No mantener los 9 estados antiguos.
2. **Agregar columna de "homogeneidad"** al listado para identificar de un vistazo si el SKU es homogéneo, mayoría o disperso.
3. **Eliminar SKUs en RAMPA** del listado por defecto (lanzamientos en rampa no son problema accionable).

### 3.4. Lógica de clasificación SKU agregado

Para escenarios A y B, definir reglas claras:

```
Estado SKU agregado:
- Si >70% tiendas en estado X → estado SKU = X
- Si entre 50-70% en estado X → estado SKU = "X DOMINANTE"
- Si ningún estado >50% → estado SKU = "MIXTO"
```

(Esto ya está propuesto en Prompt A — confirmar consistencia).

---

## 4. Notas a validar antes de ejecutar

1. **Threshold de homogeneidad**: la propuesta usa 80% / 50%. ¿Ajustar?
2. **Si la auditoría concluye Escenario A**, ¿implementar el rediseño en este mismo prompt o en uno posterior?
   - **Recomendación**: implementar en este prompt (es coherente con el alcance).
3. **¿Qué pasa con los SKUs en MIXTO?**: para decisiones de pricing, no se les puede asignar acción única. ¿Listarlos aparte como "Requieren análisis manual"?
4. **Vista por tienda**: si pasa a secundaria, ¿se mantiene en la misma sección o se mueve a la sección de Transferencias (cuando exista)?

---

## 5. Arquitectura técnica esperada

### 5.1. Módulos involucrados

- Consume `taxonomia.py` (Prompt A) — clasificación de estados.
- Modifica módulo existente de Estados.
- Posiblemente crea nuevo módulo `agregacion_sku.py` para reglas de SKU agregado.

### 5.2. Cálculos a pre-computar

- Estado SKU × tienda (heredado de taxonomía).
- Estado SKU agregado (nuevo).
- Categoría de homogeneidad (HOMOGÉNEO / MAYORÍA / DISPERSO).
- Distribución de estados dentro de cada SKU (para tooltip / drill-down).

### 5.3. Streamlit UI

- Tabla principal: una fila por SKU (en escenarios A y B).
- Drill-down: tabla expandida con SKU × tienda (solo cuando SKU es DISPERSO/MAYORÍA o usuario lo solicita).
- Resumen ejecutivo: distribución agregada de SKUs por estado.

---

## 6. Criterios de aceptación

### 6.1. Funcionales

- [ ] Auditoría de homogeneidad ejecutada y documentada.
- [ ] Análisis de oportunidades de transferencia documentado.
- [ ] Decisión de escenario (A / B / C) tomada con evidencia.
- [ ] Rediseño implementado según escenario decidido.
- [ ] Sección usa taxonomía maestra del Prompt A (no estados antiguos).
- [ ] SKUs en RAMPA no aparecen por defecto.
- [ ] Columna de homogeneidad agregada al listado.
- [ ] Si aplica, vista SKU × tienda disponible como drill-down/secundaria.

### 6.2. Métricos

- [ ] Distribución de SKUs por categoría de homogeneidad reportada (% homogéneos / mayoría / dispersos).
- [ ] Conteo total de SKUs migrados al nuevo formato coincide con conteo previo.
- [ ] Validar muestra de 10 SKUs aleatorios: estado agregado calculado coincide con regla de mayoría aplicada manualmente.

### 6.3. Auditoría

- [ ] Documento de auditoría con resultados cuantitativos.
- [ ] Ejecutar skill `code-audit` y entregar resultados.
- [ ] Validación de muestra.

---

## 7. Entregables

1. **Documento de auditoría** con resultados de homogeneidad y oportunidades de transferencia.
2. **Decisión documentada** sobre escenario A / B / C.
3. **Sección Estados rediseñada** según escenario decidido.
4. **Migración a taxonomía maestra** completa.
5. **Resultados de auditoría** (skill `code-audit`).
6. **Validación de muestra**.
7. **Backlog de hallazgos**.

---

## 8. Lo que NO entra en este prompt

- Crear sección dedicada de Transferencias (es proyecto separado).
- Implementar recomendación automática de markdown por SKU.
- Forecasting de migración entre estados.
- Cualquier feature del backlog global del MASTER.

---

**Fin del Prompt G.**
