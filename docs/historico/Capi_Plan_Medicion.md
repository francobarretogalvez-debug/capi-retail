# Capi — Plan de medicion de resultados

> Como probar que Capi funciona — con numeros, no con opiniones.
> Metodologia: piloto controlado con baseline historico + grupo control natural por categoria.

---

## Las 4 fases de medicion

### Fase 1: Baseline — fotografiar el estado actual
**Cuando:** Semana 0 (antes de activar Capi con Rodrigo)

Sin baseline no hay comparacion. Tomas una foto de todas las metricas ANTES de que cualquier buyer use Capi. Esto es tu "control historico".

- Correr motor_v2.py con data actual y registrar: % SKUs por estado, cobertura promedio, capital en criticos/precriticos/obsoletos
- Documentar horas actuales por actividad (time study de 1 semana con 2-3 buyers)
- Exportar sell-through, margen efectivo y % obsoletos de las ultimas 8 semanas como referencia historica
- Registrar # de quiebres activos, # transferencias ejecutadas, # markdowns aplicados en la semana

### Fase 2: Piloto controlado — Franco como caso cero
**Cuando:** Semanas 1-4 (Franco usa Capi, otros buyers no)

Tu categoria es el grupo tratamiento. Las categorias de otros compradores son el grupo control. Mismas tiendas, mismo periodo, diferente herramienta. Este es el A/B test natural mas limpio que puedes conseguir.

- Registrar cada accion tomada con Capi: repo preventiva, transferencia, markdown progresivo, empuje
- Log semanal de horas invertidas (formato: actividad + minutos + resultado)
- Medir las mismas metricas del baseline cada viernes — automatizado desde el motor
- Al cierre de semana 4: comparar delta en tus categorias vs categorias control

### Fase 3: Expansion — 2-3 buyers adicionales
**Cuando:** Semanas 5-8 (validacion con mas categorias)

Si los numeros del piloto son positivos, expandes a 2-3 buyers mas. Esto valida que no es "Franco es bueno" sino "la herramienta funciona". Majo selecciona los buyers.

- Onboarding de 30 min por buyer (la herramienta debe ser autoexplicativa)
- Misma metodologia de tracking: horas + acciones + metricas semanales
- Comparar curva de aprendizaje: cuanto tarda un buyer nuevo en ser productivo con Capi
- Encuesta rapida al cierre de cada semana (1 pregunta: "me sirvio hoy? si/no")

### Fase 4: Reporte ejecutivo — el caso para Rodrigo
**Cuando:** Semana 8 (presentacion con datos reales)

Con 8 semanas de data, tienes suficiente para un caso solido. Presentas a Rodrigo con numeros reales, no estimados. El formato: 1 pagina, 3 KPIs, delta vs baseline, testimonio de Majo + 2 buyers.

- Dashboard de 1 pagina: sell-through, margen y obsoletos — antes vs despues
- Horas ahorradas documentadas con log real (no estimacion)
- Testimonios de 3 buyers (incluida Majo como sponsor)
- Propuesta de rollout a todo el buying team con timeline y recursos necesarios

---

## Las 12 metricas que se miden cada semana

### Sell-through (4 metricas)

| Metrica | Formula | Fuente |
|---------|---------|--------|
| Sell-through rate por marca | Uds vendidas / uds recibidas (ultimas 4 sem) | Base Profundidad |
| % SKUs en quiebre | Stock = 0 en tienda con demanda historica | motor_v2.py |
| Cobertura promedio (sem) | Stock actual / prom venta semanal | motor_v2.py |
| Repos preventivas vs reactivas | Activadas antes vs despues de quiebre | Log manual + alertas Capi |

### Margen (4 metricas)

| Metrica | Formula | Fuente |
|---------|---------|--------|
| Margen efectivo por marca | Contribucion / VtasMF (terceras) | Base Profundidad (agregar a pipeline) |
| % ventas a precio lleno | Venta sin descuento / venta total | Base Profundidad |
| Descuento promedio aplicado | % ponderado por valor de venta | Base Profundidad |
| Descuentos evitados | # sobrestock aparente detectado y redirigido a empuje | Log manual + Capi |

### Obsoletos (4 metricas)

| Metrica | Formula | Fuente |
|---------|---------|--------|
| % inventario critico/obsoleto | SKUs en estado critico + muerto / total | motor_v2.py |
| Capital atrapado (S/) | Stock valor costo en estados criticos | motor_v2.py |
| Edad promedio inventario | Promedio ponderado de edad_semanas | motor_v2.py |
| SKUs rescatados | # que pasaron de precritico a saludable por accion temprana | motor_v2.py (comparar semana a semana) |

---

## El grupo control — como aislar el efecto de Capi

**Grupo A (con Capi):** Categorias de Franco (y luego 2-3 buyers mas). Usan Capi para revision semanal, alertas, y acciones. Se registran horas y acciones tomadas.

**Grupo B (sin Capi):** Categorias de otros buyers que siguen con el proceso actual (Power BI + Excel). Mismas tiendas, mismo periodo. No se les dice que estan siendo comparados.

**Por que funciona:** Las categorias de Franco y las de otros buyers comparten las mismas tiendas, la misma estacionalidad, y el mismo contexto macro. La unica variable diferente es la herramienta. Si las metricas de Franco mejoran y las del grupo control no, el efecto es atribuible a Capi.

**Limitacion honesta:** No es un RCT perfecto — las categorias son diferentes, Franco tiene mas contexto sobre su propia data. Pero para una decision interna de rollout, es suficientemente riguroso. Rodrigo no necesita un paper academico, necesita evidencia razonable.

---

## Cadencia de reporte

| Frecuencia | Que se mide | Quien | Formato |
|------------|-------------|-------|---------|
| Diario | Log de acciones tomadas con Capi | Franco registra | 2 min al cierre del dia |
| Semanal | 12 metricas + horas invertidas | Automatizado por motor | Export viernes 5pm |
| Quincenal | Check con Majo — delta vs baseline | Franco presenta | 15 min, 3 KPIs |
| Semana 8 | Reporte ejecutivo a Rodrigo | Franco + Majo | 1 pagina, caso completo |

---

## Riesgos de medicion y mitigaciones

| Riesgo | Descripcion | Mitigacion |
|--------|-------------|------------|
| Efecto Hawthorne | Franco mejora porque sabe que lo estan midiendo, no por la herramienta | Expandir a buyers que no saben que son parte del piloto en fase 3 |
| Estacionalidad | Las metricas mejoran porque es temporada alta, no por Capi | Comparar vs grupo control en el mismo periodo. Ambos ven la misma temporada |
| Datos insuficientes | 4 semanas puede no ser suficiente para ver tendencia significativa | 8 semanas de piloto. Si en semana 4 no hay señal, extender antes de presentar |
| Atribucion multiple | Otros factores (promos, clima, competencia) explican la mejora | El grupo control filtra factores externos. Lo que queda es efecto herramienta |

---

## Lo que Rodrigo ve en semana 8

- **Formato:** 1 pagina
- **Tiempo de atencion:** 3 minutos (lo que dura su atencion)
- **Contenido:** 3 KPIs con delta vs baseline + horas ahorradas + testimonio Majo + 2 buyers
- **Decision esperada:** Go / No-go para rollout a todo el buying team

---

## Registro de horas — con vs sin Capi (por semana, por comprador)

| Actividad del comprador | Sin Capi | Con Capi | Ahorro |
|-------------------------|----------|----------|--------|
| Revision CAPI semanal (lunes) | 3.0 h | 0.5 h | 2.5 h |
| Cruce de Power BIs (obsoletos, exhibicion, repo, stock) | 2.0 h | 0.0 h | 2.0 h |
| Identificar SKUs criticos para reposicion | 1.5 h | 0.2 h | 1.3 h |
| Analisis de sobrestock por tienda | 1.5 h | 0.2 h | 1.3 h |
| Revision margen y performance marcas terceras | 1.0 h | 0.2 h | 0.8 h |
| Armado de ordenes de reposicion | 1.5 h | 0.5 h | 1.0 h |
| Seguimiento transferencias entre tiendas | 1.0 h | 0.2 h | 0.8 h |
| Deteccion de mercaderia sin salir a piso | 1.0 h | 0.1 h | 0.9 h |
| Analisis de antiguedad para markdown | 0.5 h | 0.1 h | 0.4 h |
| Preparacion de reporte semanal a gerencia | 1.0 h | 0.1 h | 0.9 h |
| **Total semanal por comprador** | **14.0 h** | **2.1 h** | **11.9 h** |

**Ahorro semanal:** 11.9 h (85% de reduccion)
**Ahorro mensual:** 47.6 h (~6 dias laborales/mes liberados)
**Ahorro anual (x20 buyers):** 19,040 h (~S/ 1.14M en productividad)

---

*Capi — Plan de medicion v1 · Abril 2026 · Franco Barreto*
