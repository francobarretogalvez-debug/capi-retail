# PROMPT 0 — MASTER: Reglas del juego para la evolución de la herramienta de retail analytics

> **Lee este documento primero. Es el contrato de trabajo. Todos los prompts siguientes (A, B, C, D, E, F, G) se ejecutan bajo estas reglas.**

---

## 1. Contexto del proyecto

Estoy evolucionando mi herramienta personal de retail analytics, construida en **Python + Streamlit**, que uso semanalmente para gestionar el inventario de moda masculina de Ripley (32 tiendas + ecommerce, USD 40M anuales, marcas Lacoste, Nautica, Psycho Bunny, Dockers, U.S. Polo Assn., Jack & Jones, Oscar de la Renta, Cacharel, Spavaldi, Marquis, Navigata, John Holden, entre otras).

La herramienta hoy se alimenta de un reporte semanal llamado **"micro/profundidad"** que entrega data de stock y venta a nivel SKU × tienda × talla. La actualización es **semanal** y la única persona que opera la herramienta soy yo (Franco).

Después de revisar a fondo varias secciones de la herramienta, identifiqué problemas estructurales y oportunidades de mejora que vamos a ejecutar en 7 frentes secuenciales (A → G). Cada frente tiene su propio prompt detallado.

---

## 2. Reglas del juego (cómo trabajamos)

### 2.1. Modalidad secuencial con validación

- Te paso **un prompt a la vez**, en el orden A → B → C → D → E → F → G.
- Al terminar cada prompt: **entrega + auditoría + esperas mi validación** antes de pasar al siguiente.
- No avances al siguiente prompt sin mi aprobación explícita.

### 2.2. Antes de ejecutar cualquier prompt

Cada prompt incluye una sección **"Notas a validar antes de ejecutar"**. Léelas, respóndelas o aclara dudas conmigo **antes** de tocar código. Si alguna duda bloquea la implementación, espera mi respuesta.

### 2.3. Arquitectura técnica

Para cada cambio:
1. **Propón arquitectura técnica antes de codificar** (estructura de archivos, módulos, funciones, flujo de datos).
2. Espera mi validación de la arquitectura.
3. Recién entonces implementas.

### 2.4. Auditoría al finalizar cada tarea

Al cerrar cada prompt, ejecuta el skill `code-audit` que tengo desarrollado para auditar el funcionamiento de la herramienta. La auditoría es parte del entregable, no opcional.

### 2.5. Criterios de aceptación

Cada prompt incluye criterios de aceptación funcionales + métricos. La tarea no se considera cerrada hasta que todos los criterios pasen.

### 2.6. Tono y comunicación

- Conmigo: **español peruano**, directo, sin relleno.
- En specs de código y comentarios técnicos: **inglés técnico cuando lo prefieras**, está bien.
- Si detectas algo que no cuadra con la lógica de negocio, **rétame antes de implementar**. Prefiero discutir 5 minutos que rehacer 2 horas.

---

## 3. Anti-instrucciones (qué NO hacer)

1. **No proponer features fuera del alcance del prompt actual.** Si tienes una buena idea adicional, la mandas al backlog del MASTER, no la implementas.
2. **No cambiar nombres de secciones, columnas, o variables sin mi aprobación explícita.** El naming es decisión de negocio, no técnica.
3. **No asumir campos de data que no estén en el reporte fuente (micro/profundidad).** Si necesitas un campo que no existe, levanta el flag y discutimos.
4. **No mezclar dos prompts en un solo entregable.** Cada prompt es autocontenido.
5. **No avanzar a "lo siguiente lógico" sin esperar mi validación.** La secuencia es estricta.
6. **No reescribir secciones que no estén en el alcance del prompt actual.** Si encuentras un bug en otra sección, lo reportas en el backlog.
7. **No simplificar la lógica de negocio para hacerla técnicamente más limpia** sin discutirlo conmigo. La lógica viene de cómo opera el retail real, no de elegancia de código.
8. **No proponer migrar a otro stack** (ej. de Streamlit a otro framework). Trabajamos con lo que hay.
9. **No asumir que un cálculo está bien si no lo validaste contra el reporte fuente.** Cada cálculo nuevo se valida con muestra de 10 SKUs reales.
10. **No usar emojis ni decoración excesiva en la UI**. Semáforos de colores sí; emojis decorativos no.

---

## 4. Stack técnico

- **Lenguaje**: Python
- **Framework UI**: Streamlit
- **Procesamiento de datos**: pandas (asumido — confirmar si usas otro)
- **Fuente de datos**: reporte semanal "micro/profundidad" (Excel/CSV)
- **Almacenamiento de snapshots**: a definir en Prompt B (recomendación inicial: Parquet/CSV en estructura de carpetas por semana)

---

## 5. Glosario de términos del negocio

Para que estemos alineados:

| Término | Significado |
|---|---|
| **OTB** | Open To Buy — presupuesto de compra disponible |
| **ST / Sell-Through** | Unidades vendidas / unidades recibidas (acumulado) |
| **Cobertura** | Stock actual / velocidad de venta semanal. Target: **12 semanas (3 meses)** |
| **Velocidad de venta** | Unidades vendidas promedio por semana |
| **OI / PV** | Otoño-Invierno / Primavera-Verano (temporadas) |
| **Liquidación** | Temporada saliente con precio reducido |
| **Markdown** | Aplicación de descuento al PVP |
| **PL** | Private Label (marca propia) |
| **3ras** | Marcas terceras (no propias) |
| **Repo / Reposición** | Envío de stock desde central a tienda |
| **Predistribución** | Distribución inicial de un SKU a tiendas antes de su lanzamiento |
| **Empuje / Girar** | Acciones operativas en piso para mover stock (exhibición, VM, comunicación de precio) |
| **Dormido / Zombie / Muerto** | SKUs sin venta (ver Prompt A para taxonomía oficial) |

---

## 6. Audiencia y casos de uso

Toda la herramienta tiene **un solo usuario**: el comprador (yo). En el futuro, otros buyers de la categoría podrían usarla con la misma estructura. **No es herramienta para tiendas** — yo proceso la data y comunico a tiendas/marcas/áreas según corresponda.

Cada sección sirve a un caso de uso distinto:

| Sección | Caso de uso principal |
|---|---|
| **Salud del Stock** (rediseño C) | Identificar SKUs zombies / sobrestock para acción |
| **Cobertura** (rediseño D) | Detectar gaps de cobertura por marca × categoría × tienda |
| **Predistribución** (mejora E) | Validar oportunidad real de empuje a tiendas faltantes |
| **Reposición** (auditoría F) | Validar lógica del cálculo automático |
| **Estados** (auditoría G) | Validar si la dimensión tienda aporta valor o solo agrega ruido |

---

## 7. Secuencia de prompts (orden estricto)

| # | Prompt | Frente | Razón del orden |
|---|---|---|---|
| **A** | Taxonomía maestra | Definir matriz cobertura × edad como criterio único | Sin esto, todas las demás secciones se construyen sobre criterios inconsistentes |
| **B** | Snapshot semanal + bases antiguas | Habilitar histórico de venta y stock | Sin histórico, los cálculos de velocidad y antigüedad son aproximaciones |
| **C** | Salud del Stock | Rediseño venta cero + sobrestock con tiers | Depende de A (taxonomía) y B (histórico) |
| **D** | Vista Cobertura | Heatmap + drill-down + alertas | Depende de A y B; complementa repo automática |
| **E** | Predistribución mejorada | Cobertura en tiendas faltantes | Depende de D (vista cobertura) |
| **F** | Auditoría Reposición | Validar lead time, quiebre de talla, cumplimiento 3ras | Auditoría cruzada — no toca código sino lógica |
| **G** | Auditoría Estados | Validar si data por tienda aporta o no | Auditoría que define si se rediseña o elimina la sección |

---

## 8. Forma de los entregables

Para cada prompt, el entregable final debe incluir:

1. **Arquitectura técnica propuesta** (validada por mí antes de codificar)
2. **Código implementado** (Python + Streamlit)
3. **Resultados de auditoría** (skill `code-audit`)
4. **Validación de cálculos** contra muestra de 10 SKUs del reporte fuente
5. **Documentación** mínima: qué se cambió, por qué, qué archivos toca
6. **Backlog de hallazgos** detectados durante la implementación que no entran en el alcance

---

## 9. Backlog global (a no implementar en esta serie de prompts)

Lo siguiente queda **fuera del alcance** de los prompts A-G. Se tocará en una segunda fase:

1. **Factor de elasticidad de precio** para ajustar repo automática OI por mix de descuento PV histórico
2. **Validación automática por margen** (no reponer si margen bajo/negativo)
3. **Cobertura sostenible proyectada** (forward-looking de quiebre)
4. **Integración del gap de cobertura como input directo a Reposición** (hoy es diagnóstico paralelo)
5. **Loop de cumplimiento con marcas 3ras** (tracking automático de repo solicitada vs despachada)
6. **Recomendador automático de transferencias entre tiendas**
7. **Forecasting de venta por SKU × tienda**
8. **Detección automática de quiebre de talla** (si la sección de Reposición no lo cubre — ver Prompt F)

---

## 10. Confirmación antes de empezar

Cuando termines de leer este MASTER, respóndeme con:

1. ¿Tienes el contexto técnico completo de la herramienta (archivos, secciones, flujo actual)?
2. ¿Hay alguna regla del juego que no te cuadra o que necesitas ajustar?
3. ¿Listo para recibir el Prompt A (Taxonomía Maestra)?

No avances a ningún prompt sin mi confirmación explícita después de tu respuesta a estos 3 puntos.

---

**Fin del MASTER.**
