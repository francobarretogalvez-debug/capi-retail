# PROMPT F — Resultado de Auditoría: Sección Reposición

> **Fecha**: 24-May-2026  
> **Auditor**: Capi (Claude)  
> **Data de referencia**: Snapshot semana 2026-19 (5,333 SKUs)  
> **Código auditado**: `motor_v2.py` → `build_reposiciones()` (L530-674)

---

## Tabla Resumen Ejecutiva

| # | Escenario | Cubre? | Riesgo | Prioridad | Gap principal |
|---|-----------|--------|--------|-----------|---------------|
| 1 | Quiebre por talla | **NO** | Alto | 🔴 Alta | Data source no tiene granularidad talla; repo opera a nivel SKU agregado |
| 2 | Lead time proveedor 3ro | **NO** | Alto | 🔴 Alta | No existe campo lead_time; repo no anticipa demora de despacho |
| 3 | Cumplimiento marcas 3ras | **NO** | Alto | 🔴 Alta | No hay tracking solicitud→despacho; repo calculada ≠ repo recibida |
| 4 | SKUs sin stock central | **PARCIAL** | Medio | 🟠 Media | Propias sin CD se excluyen correctamente; terceras sin CD aparecen sin flag |
| 5 | SKUs descontinuados | **PARCIAL** | Medio | 🟠 Media | No hay campo "descontinuado"; taxonomía MUERTO/DORMIDO es proxy parcial |

**Decisión recomendada**: **(b) Hay gaps puntuales → se necesita mini-vista "Repo en Riesgo".**

---

## Escenario 1 — Quiebre por Talla

### Hallazgo: NO CUBRE

La lógica de reposición opera a nivel **SKU × Tienda**. No existe dimensión de talla en ningún punto del pipeline.

**Evidencia en código** (`motor_v2.py`, L376-485):
```python
def build_cobertura(df_maestro, df_ventas, df_stock, params):
    """Produce tabla de cobertura por SKU × Tienda."""  # ← granularidad máxima
```

La función `build_reposiciones()` itera sobre `candidatos` (L582) donde cada fila es un SKU × Tienda. La cobertura se calcula como `stock_total / prom_vta_uds` a nivel agregado del SKU, sin desglose por talla.

**Evidencia en data**: El snapshot 2026-19 tiene 27 columnas; ninguna contiene `talla`, `size`, o `color`. La Base Profundidad de Ripley ya viene pre-agregada a nivel SKU — el desglose por talla existe en los sistemas transaccionales (POS/SAP) pero no llega al micro/profundidad.

**Riesgo cuantificado**:
- No es posible cuantificar con la data actual (no hay campo talla).
- Estimación cualitativa: en ropa masculina, las tallas core (M, L, XL) representan ~60-70% de la venta. Un SKU con 50 uds de stock pero todo en XS/XXL tendría cobertura calculada de, digamos, 5 semanas, cuando en realidad las tallas que venden están en quiebre.
- Impacto potencial: **perdida de venta silenciosa** en tallas core.

**Propuesta**:
1. **Gap de data a cerrar con área fuente**: Solicitar que la Base Profundidad incluya desglose por talla (o al menos stock por talla por tienda). Esto requiere coordinación con el equipo de data/BI de Ripley.
2. **Ajuste de corto plazo** (sin data de talla): Usar la curva de tallas estándar por categoría como proxy. Si `stock_total < curva × n_tallas_core`, flagear como "posible quiebre de talla".
3. **Ajuste de largo plazo**: Cuando la data de talla esté disponible, refactorizar `build_cobertura` y `build_reposiciones` para operar a nivel SKU × Tienda × Talla.

**Prioridad**: 🔴 **ALTA** — Este es el gap más estructural. Sin data de talla, la repo puede estar dando falsa seguridad.

---

## Escenario 2 — Lead Time del Proveedor 3ro

### Hallazgo: NO CUBRE

No existe campo `lead_time`, `plazo_entrega`, ni similar en la data fuente ni en el motor. La única referencia a lead time es `alertas_tienda_edad_min = 2` (L50), que es un concepto distinto (edad mínima de un SKU para generar alertas).

**Evidencia en código** (`motor_v2.py`, L530-674):
La función `build_reposiciones()` calcula:
```python
stk_ideal = cob_target * avg        # stock ideal = 6 semanas × venta promedio
a_reponer_base = ceil(max(0, stk_ideal - stk))  # déficit
```

Esto calcula **cuánto reponer**, pero no **cuándo reponer**. No hay comparación entre cobertura restante y tiempo de entrega del proveedor.

**Ejemplo de riesgo real**:
- SKU Lacoste: cobertura actual = 2.5 semanas, cob_target = 6 → se calcula repo de 28 uds.
- Lead time Lacoste = 3-4 semanas (despacho desde bodega marca).
- Para cuando llegue la repo, el SKU ya estuvo 0.5-1.5 semanas en quiebre.
- El sistema lo marcó como "repo calculada" pero el quiebre fue inevitable.

**Data disponible**: No existe campo de lead time en la Base Profundidad. Esta información es conocimiento tribal del buyer (Franco sabe que Lacoste demora ~3 semanas, Dockers ~2, etc.).

**Propuesta**:
1. **Tabla de referencia de lead times**: Crear `config_lead_times.json` con lead time por marca (o por proveedor si hay mapeo). Franco provee los valores iniciales.
   ```json
   {
     "LACOSTE": 21,
     "DOCKERS": 14,
     "SELECTED": 14,
     "PIERRE CARDIN": 10,
     "JOHN HOLDEN": 7,
     "SILBON": 28,
     "PSYCHO BUNNY": 21,
     "MARQUIS": 3,
     "NAVIGATA": 3
   }
   ```
   (Valores en días; marcas propias = 3 días porque es logística interna)

2. **Alerta de quiebre inminente**: Nueva lógica en `build_reposiciones()`:
   ```python
   dias_cobertura_restante = cobertura_actual * 7  # semanas → días
   if dias_cobertura_restante < lead_time_marca:
       flag = "⚠️ QUIEBRE INMINENTE — repo no llega a tiempo"
   ```

3. **Mini-vista**: SKUs donde `cobertura_restante_dias < lead_time` → "Repo en Riesgo" (quiebre va a ocurrir aunque se haya calculado repo).

**Prioridad**: 🔴 **ALTA** — Cierra la brecha entre "repo calculada" y "quiebre evitado". Es el gap más accionable (no requiere data nueva, solo conocimiento del buyer).

---

## Escenario 3 — Cumplimiento de Marcas 3ras

### Hallazgo: NO CUBRE

No existe tracking de estado de despacho. La lógica actual calcula qué reponer y genera el listado, pero no tiene feedback loop para saber si la marca despachó.

**Evidencia en código**: `build_reposiciones()` retorna un DataFrame con la columna `a_reponer` (L639) pero no tiene campos como `estado_despacho`, `fecha_solicitud`, `fecha_recepcion`, ni similar. La sección UI ("📦 Reposición", L2034) muestra el plan de reposición como tabla descargable — Franco lo descarga como Excel y lo envía manualmente a las marcas.

**Flujo actual**:
1. Capi calcula repo → genera Excel con pivot SKU × Tienda.
2. Franco descarga y envía a la marca por email/WhatsApp.
3. **No hay paso 3.** No hay registro en Capi de que se envió, ni tracking de respuesta.

**Riesgo real**: Si Lacoste no despacha (o despacha parcial), Franco no tiene alerta automática. Tendría que recordar revisar manualmente, comparando el Excel que envió con la siguiente semana de data.

**Propuesta**:
1. **Registro de repo solicitada**: Cuando Franco descarga el Excel de repo, registrar en un log persistente:
   ```json
   {
     "fecha_solicitud": "2026-05-24",
     "semana_iso": "2026-21",
     "marca": "LACOSTE",
     "skus_solicitados": 12,
     "uds_solicitadas": 284,
     "estado": "PENDIENTE"
   }
   ```

2. **Detección automática de cumplimiento**: En la siguiente corrida semanal, comparar:
   - Stock CD de los SKUs solicitados: ¿subió? → repo recibida (total o parcial).
   - ¿No subió y cobertura empeoró? → repo no cumplida → escalar.

3. **Mini-vista "Repo en Riesgo de Incumplimiento"**: SKUs con repo solicitada hace >X días sin confirmar despacho Y cobertura actual < 2 semanas.

**Nota importante**: Este escenario tiene una dependencia con Prompt B (snapshots). Los snapshots semanales permiten detectar si el stock CD subió entre semanas, lo cual es proxy de "repo recibida". La infraestructura ya existe.

**Prioridad**: 🔴 **ALTA** — Es la "alerta de quiebre real" que el Prompt F busca validar. Sin este tracking, la repo para terceras es un acto de fe.

---

## Escenario 4 — SKUs sin Stock Central Disponible

### Hallazgo: CUBRE PARCIALMENTE

**Caso propias — BIEN MANEJADO** (`motor_v2.py`, L663-672):
```python
_MARCAS_PROPIAS = {'MARQUIS', 'NAVIGATA', 'CACHAREL', 'SPAVALDI',
                   'OSCAR DE LA RENTA', 'US POLO', 'NAUTICA'}
_es_propia = df_rep['marca'].str.upper().str.strip().isin(_MARCAS_PROPIAS)
_sin_cd = df_rep['stock_cd'] <= 0
_excluir = _es_propia & _sin_cd
df_rep = df_rep[~_excluir].reset_index(drop=True)
```
Las marcas propias sin stock CD se excluyen del listado de repo. Correcto — no se puede reponer desde CD si no hay stock.

**Caso terceras — GAP**: Las terceras con `stock_cd == 0` se mantienen en el listado (L664: "repo va al proveedor"). Esto es lógicamente correcto — la repo de terceras se envía a la marca, no depende del CD. Pero **no se levanta ningún flag** indicando que esta repo depende 100% del proveedor y no hay plan B.

**Cuantificación** (snapshot 2026-19):
- SKUs con cobertura < 6 semanas: **575**
- De esos, con stock_cd == 0: **429** (75%)
  - Propias sin CD: 216 SKUs → **correctamente excluidos** por el motor
  - Terceras sin CD: 213 SKUs → en listado de repo sin flag
- Valor en riesgo (terceras sin CD, cob < 6): **S/ 165,973**
- Marcas afectadas: Pierre Cardin (64), John Holden (53), Lacoste (28), Selected (28), Dockers (22)

**Propuesta**:
1. **Flag en repo existente**: Agregar columna `requiere_proveedor = True` para terceras sin stock CD. No cambiar la lógica — solo informar.
2. **Cruce con Escenario 2**: Si `requiere_proveedor AND cobertura_dias < lead_time`, marcar como "🔴 QUIEBRE INMINENTE — depende 100% del proveedor".
3. **Propias sin CD → redirigir a transferencias**: Cuando una propia se excluye de repo por falta de CD, evaluar si hay exceso del mismo SKU en otra tienda. Actualmente `build_transferencias()` ya cubre esto (L681-787), pero el usuario no sabe que la repo fue excluida Y que existe una alternativa de transferencia.

**Prioridad**: 🟠 **MEDIA** — La lógica base es correcta. El gap es de información/visibilidad, no de cálculo.

---

## Escenario 5 — SKUs Descontinuados

### Hallazgo: CUBRE PARCIALMENTE

No existe campo formal `descontinuado` o `estado_sku` en la data fuente. La taxonomía de 10 estados actúa como proxy parcial:

**Proxy existente** (`taxonomia.py`):
- `MUERTO`: edad > 26 semanas, 0 ventas en 4 semanas → no se repone (excluido en L561)
- `DORMIDO`: edad > 26 semanas, venta < 20% del promedio → no se repone (excluido en L561)
- `LIQUIDAR`: cobertura > umbral_alto Y edad > umbral_edad → no se repone (excluido en L559)
- `LANZAMIENTO`: edad < 8 semanas, sin venta → no se repone (excluido en L561)

Estados excluidos de repo:
```python
_estados_excluir = {
    Estado.SOBRESTOCK, Estado.ESTANCADO, Estado.LIQUIDAR,
    Estado.LANZAMIENTO, Estado.DORMIDO, Estado.MUERTO,
}
```

**Gap 1 — Temporada PV en pleno OI**: Un SKU de temporada Primavera-Verano (PV) con edad > 16 semanas en mayo (otoño-invierno) es funcionalmente descontinuado — no se va a re-comprar. Si aún tiene cobertura baja (vende pero se acaba), el sistema lo incluye en repo porque su estado es ÓPTIMO o PRE-QUIEBRE.

Cuantificación:
- SKUs PV con edad > 16 semanas: **939** (18% del portafolio)
- De esos, con cobertura < 6: **229** → potencialmente en listado de repo
- Stock total: 18,938 uds → valor S/ 1,230,542

Estos 229 SKUs aparecerían en el listado de repo para marcas terceras, generando solicitudes de reposición que la marca no va a cumplir (ya no producen esa temporada).

**Gap 2 — No hay señal de "última compra"**: Sin campo de fecha de última orden de compra o flag de descontinuación, Capi no puede distinguir entre un SKU activo con baja cobertura (reponer) y un SKU en fase de salida (liquidar stock remanente).

**Propuesta**:
1. **Regla de temporada**: SKUs de temporada PV con edad > 16 semanas en OI (y viceversa) se marcan como `descontinuado_temporal = True`. Estos no deben ir a repo sino a acciones de precio o transferencia.
   ```python
   es_pv_en_oi = (temporada == 'PV') and (mes_actual in [4,5,6,7,8,9])
   es_oi_en_pv = (temporada == 'OI') and (mes_actual in [10,11,12,1,2,3])
   if (es_pv_en_oi or es_oi_en_pv) and edad > 16:
       descontinuado_temporal = True
   ```
2. **Redirigir a transferencias**: SKUs descontinuados con cobertura baja en una tienda pero stock en otra → candidato a transferencia inter-tienda (liquidar concentrando).
3. **Campo futuro**: Solicitar a área de data un flag `estado_compra` (activo/descontinuado/last buy) por SKU. Esto eliminaría la necesidad de la heurística temporal.

**Prioridad**: 🟠 **MEDIA** — El proxy de taxonomía funciona para los casos extremos (MUERTO, DORMIDO). El gap está en la zona gris: SKUs de temporada opuesta que aún venden pero no deberían reponerse.

---

## Lista Priorizada de Gaps de Data

| # | Campo necesario | Fuente | Impacto | Escenario |
|---|----------------|--------|---------|-----------|
| 1 | Stock por talla por SKU por tienda | SAP / POS | 🔴 Estructural | Esc. 1 |
| 2 | Lead time por marca/proveedor | Conocimiento buyer (Franco) | 🔴 Inmediato | Esc. 2 |
| 3 | Log de repo solicitada (fecha, marca, uds) | Capi interno | 🔴 Inmediato | Esc. 3 |
| 4 | Flag `estado_compra` (activo/descontinuado) | Planeamiento comercial | 🟠 Medio plazo | Esc. 5 |

Gaps 2 y 3 no requieren data externa — se pueden resolver con configuración (lead times) y logging interno (repo solicitada). Son los más rápidos de implementar.

---

## Propuestas de Cambios (sin implementar)

### Propuesta 1: Mini-vista "Repo en Riesgo" (RECOMENDADA)

Crear una sub-sección dentro de la vista Reposición que muestre SKUs en riesgo de quiebre a pesar de tener repo calculada. Criterios de inclusión:

- `cobertura_dias_restante < lead_time_marca` → quiebre inminente
- `repo_solicitada_hace > 7 dias AND sin_incremento_stock_cd` → incumplimiento proveedor
- `temporada_opuesta AND edad > 16 sem` → repo no ejecutable (descontinuado temporal)
- `stock_cd == 0 AND marca_tercera` → depende 100% de proveedor

**Alcance estimado**: ~2 horas de implementación (config lead times + lógica de flags + UI).

### Propuesta 2: Tabla `config_lead_times.json`

Archivo de configuración con lead times por marca. Franco lo llena una vez y se actualiza periódicamente.

**Alcance**: 30 min.

### Propuesta 3: Log de repo solicitada

Guardar registro cada vez que Franco descarga el Excel de repo. Usar snapshots semanales para detectar cumplimiento automáticamente.

**Alcance**: ~1.5 horas (logging + detección + UI de seguimiento).

### Propuesta 4: Regla de temporada opuesta

Excluir SKUs de temporada opuesta con edad > 16 semanas del listado de repo. Redirigirlos a acciones de precio o transferencias.

**Alcance**: 30 min (ajuste en `build_reposiciones()`).

---

## Decisión Final

**Recomendación: (b) — Se necesita mini-vista "Repo en Riesgo".**

La lógica actual de reposición es sólida para el caso base (calcular cuánto reponer y dónde), pero tiene 3 gaps críticos que hacen que la premisa "repo calculada = quiebre cubierto" sea falsa:

1. **No anticipa** que la repo no llegará a tiempo (lead time).
2. **No trackea** si la repo fue efectivamente despachada (cumplimiento).
3. **No distingue** SKUs que ya no se pueden reponer (descontinuados temporales).

Estos 3 gaps se resuelven con una mini-vista de ~3-4 horas de implementación total, sin necesidad de refactorizar la lógica core de reposición.

El gap de talla (Escenario 1) es estructural y depende de data que hoy no existe en la Base Profundidad. Se documenta como mejora de largo plazo.

---

**Fin de Auditoría Prompt F.**
