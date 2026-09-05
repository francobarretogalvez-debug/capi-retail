# PROMPT F — Auditoría Sección Reposición

> **Pre-requisito**: PROMPT 0 (MASTER) + Prompts A-E entregados y validados.
> **Frente**: F
> **Dependencias**: ninguna técnica (es auditoría de lógica existente).

---

## 1. Contexto del problema

### 1.1. Sección actual: "Reposición"

La sección calcula automáticamente reposición por SKU × tienda para:
- Marcas propias (PL): se ejecuta internamente con logística + Mariana.
- Marcas terceras (3ras): el detalle se comunica a la marca para que ellas despachen.

### 1.2. Hipótesis a validar

Bajo la premisa de que **la reposición automática ya cubre quiebres**, la sección hace de "alerta de quiebre" implícita: si el sistema calcula repo, es porque detectó riesgo.

**Pero esta premisa tiene huecos potenciales** que no están auditados. Si alguno se rompe, el sistema cree que está cubriendo el quiebre cuando en realidad no.

### 1.3. Importancia de la auditoría

Antes de descartar definitivamente la idea de tener una "alerta de quiebre" separada, hay que validar que la repo automática efectivamente cubre todos los escenarios. Si hay casos borde no cubiertos, **se necesita una mini-vista de "Repo en Riesgo"** dentro de Salud del Stock o de Reposición misma.

---

## 2. Objetivo del prompt

**Esto es una auditoría, no una implementación de feature nueva.**

Validar la lógica actual de la sección Reposición frente a 5 escenarios críticos. Para cada uno:
1. Determinar si la lógica actual lo cubre.
2. Si no, proponer ajuste o nueva mini-vista.

Al cierre del prompt, decidiremos juntos si:
- (a) La lógica actual es robusta → no se necesita "Alerta de Quiebre".
- (b) Hay gaps puntuales → se agrega mini-vista de "Repo en Riesgo".
- (c) La lógica tiene problemas serios → se rediseña la sección Reposición.

---

## 3. Escenarios a auditar

### 3.1. Escenario 1 — Quiebre por talla

**Pregunta**: ¿el cálculo de repo automática opera a nivel SKU agregado o por talla?

**Riesgo si opera agregado**:
- Un SKU puede tener stock total OK pero estar quebrado en tallas core (M, L).
- El sistema "cree" que está sano y no calcula repo.
- El piso queda con stock inservible (solo XS y XXL, por ejemplo).

**Tarea**:
- Revisar el código actual de la repo.
- Confirmar nivel de granularidad del cálculo.
- Si es agregado, proponer ajuste: cálculo de repo a nivel SKU × tienda × talla.
- Si es por talla, validar con 5 muestras que efectivamente detecta quiebres específicos.

**Entregable**: documento de auditoría con código revisado, hallazgo, y propuesta.

### 3.2. Escenario 2 — Lead time del proveedor 3ro

**Pregunta**: ¿el cálculo de repo considera el lead time del proveedor 3ro al momento de calcular cuándo se va a quebrar el SKU?

**Riesgo si no considera lead time**:
- Lacoste demora 3 semanas en despachar.
- Cobertura proyectada = 2 semanas.
- Sistema calcula repo → pero el quiebre va a ocurrir igual antes de que llegue.
- Repo "calculada" no es lo mismo que "quiebre evitado".

**Tarea**:
- Revisar si existe campo de lead time por marca / proveedor en la data.
- Si existe, validar que está siendo usado en el cálculo.
- Si no existe, levantar gap: campo necesario en data fuente.
- Proponer alerta cuando `cobertura_proyectada < lead_time` → quiebre inminente, escalar.

**Entregable**: documento de auditoría + propuesta de alerta de "quiebre inminente".

### 3.3. Escenario 3 — Cumplimiento de marcas 3ras

**Pregunta**: ¿hay forma de trackear si una marca 3ra efectivamente despachó la repo solicitada o no?

**Riesgo si no hay tracking**:
- Tú pasas el detalle de repo a Lacoste.
- Lacoste no responde / despacha parcial / despacha tarde.
- El sistema asume que la repo está "en camino" → no levanta alerta.
- El quiebre ocurre silenciosamente.

**Tarea**:
- Revisar si el sistema actual tiene campo o estado para "repo solicitada" vs "repo recibida".
- Si no existe, proponer:
  - Mini-vista: "**Repo en Riesgo de Incumplimiento**" → SKUs con repo solicitada hace >X días sin confirmar despacho Y cobertura <2 semanas.
  - Esto sí es alerta de quiebre real, distinta del cálculo de repo automática.

**Entregable**: documento de auditoría + propuesta de mini-vista de seguimiento de repo a 3ras.

### 3.4. Escenario 4 — SKUs sin stock central disponible

**Pregunta**: ¿qué pasa cuando la repo automática se calcula pero no hay stock disponible en bodega central para reponer?

**Riesgo**:
- Sistema calcula repo de 50 uds.
- Bodega central tiene 0 uds disponibles.
- ¿Qué hace el sistema? ¿Levanta alerta? ¿Sugiere transferencia? ¿O simplemente registra "repo no ejecutable" sin avisar?

**Tarea**:
- Revisar comportamiento actual del sistema en este escenario.
- Si no hay alerta, proponer flag: "Repo no ejecutable — sin stock central. Considerar transferencia inter-tienda."

**Entregable**: documento de auditoría + propuesta de flag.

### 3.5. Escenario 5 — SKUs descontinuados

**Pregunta**: ¿el cálculo de repo distingue entre SKUs activos (reponibles) y SKUs descontinuados (no reponibles)?

**Riesgo si no distingue**:
- Un SKU descontinuado pero con velocidad de venta alta podría aparecer en el listado de repo, generando ruido.
- Peor: el sistema puede "ignorar" un SKU descontinuado en quiebre que aún tiene demanda → la única solución sería transferencia, pero el sistema no lo flageó.

**Tarea**:
- Revisar si existe campo de estado del SKU (activo / descontinuado).
- Validar que el sistema usa el campo correctamente.
- Proponer comportamiento: SKUs descontinuados con cobertura baja → ir a vista de "Transferencias necesarias", no a Reposición.

**Entregable**: documento de auditoría.

---

## 4. Notas a validar antes de ejecutar

1. **Acceso al código de Reposición**: confirmar que tienes acceso completo al código actual de la sección.
2. **Disponibilidad de campos en data fuente**: muchos de los escenarios dependen de campos como `lead_time_proveedor`, `estado_sku`, `stock_central_disponible`, etc. Si no existen, queda como gap a cerrar con el área de data.
3. **Nivel de cambio permitido**: ¿esta auditoría puede llevar a refactorizar el código de Reposición o solo identifica gaps para resolver después?
   - **Recomendación**: identificar gaps en este prompt; refactorizar en un prompt posterior si la auditoría lo amerita.

---

## 5. Arquitectura del entregable

Para cada escenario, el entregable debe incluir:

1. **Hallazgo**: ¿la lógica actual cubre el escenario? Sí / No / Parcial.
2. **Evidencia**: extracto del código relevante o test ejecutado.
3. **Riesgo cuantificado** (si aplica): ¿cuántos SKUs estarían afectados por este gap?
4. **Propuesta**: ajuste de código, nueva mini-vista, o gap de data a cerrar con área fuente.
5. **Prioridad**: alta / media / baja según impacto.

---

## 6. Criterios de aceptación

### 6.1. Funcionales

- [ ] Los 5 escenarios fueron auditados con evidencia documentada.
- [ ] Cada hallazgo tiene una propuesta concreta (no solo descripción del problema).
- [ ] Se priorizan los gaps por impacto.
- [ ] Si algún gap requiere refactor del código, se propone alcance del refactor (no se ejecuta en este prompt).
- [ ] Decisión final clara sobre necesidad de mini-vista "Repo en Riesgo" (sí / no / parcial).

### 6.2. Métricos

- [ ] Para escenarios donde es posible cuantificar riesgo, validar con muestra de 10 SKU × tienda.
- [ ] Si se detectan SKUs en quiebre por talla (escenario 1), reportar conteo total.
- [ ] Si se detectan SKUs sin stock central (escenario 4), reportar conteo total.

### 6.3. Auditoría

- [ ] Documento de auditoría completo con los 5 escenarios.
- [ ] Tabla resumen con hallazgo + propuesta + prioridad.
- [ ] Validación de muestra para escenarios cuantificables.

---

## 7. Entregables

1. **Documento maestro de auditoría** (markdown estructurado por escenario).
2. **Tabla resumen ejecutiva** (1 página) con hallazgos y propuestas.
3. **Lista priorizada** de gaps de data a cerrar con área fuente.
4. **Propuestas de cambios** (sin implementar) priorizadas.
5. **Decisión final** sobre necesidad de "Repo en Riesgo".

---

## 8. Lo que NO entra en este prompt

- Implementar las propuestas detectadas. Eso será un prompt posterior si decidimos avanzar.
- Refactorizar el código de Reposición (solo identificar y proponer).
- Crear la mini-vista de "Repo en Riesgo" si se decide que se necesita (será prompt posterior).
- Cualquier feature del backlog global del MASTER.

---

**Fin del Prompt F.**
