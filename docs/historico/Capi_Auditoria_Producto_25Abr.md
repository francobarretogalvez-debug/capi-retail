# Capi — Auditoría de Producto y Visión Predictiva

> Auditoría técnica + mapa estratégico de cada pieza del producto.
> Fecha: 25 Abril 2026 · Franco Barreto

---

## 1. Lo que Capi tiene hoy (piezas funcionales)

### Motor de cálculo (motor_v2.py — 26+ funciones)
- Cobertura por SKU×Tienda (stock / promedio venta semanal)
- 9 estados de clasificación (Crítico → Liquidar, incluyendo Sin Venta subdividido)
- Reposiciones sugeridas con cob_target configurable (default 12 sem)
- Transferencias entre tiendas (sin matriz logística)
- Acciones de precio con markdown progresivo
- Aging analysis con 4 acciones inteligentes: Empuje, Markdown, Negociar, Liquidar
- Margen efectivo: Contribución / VtasMF a nivel SKU y marca
- Sobrestock aparente: proxy "no salió a piso" via ratio CD/total
- Anomalías por tienda (desviaciones de venta significativas)
- Análisis por ventana de compra (embarques A-F)
- Alertas consolidadas por marca

### Pipeline de datos (transformar_profundidad.py)
- Detección automática de formato v1 vs v2
- Extracción de tiendas dinámicamente desde columnas
- Cálculo de venta S/ y contribución S/ (4 semanas)
- Detección de embarque/ventana de compra
- Output: Excel formateado con headers multi-nivel

### UI (app_streamlit.py — ~4100 líneas, 11 vistas)
- Dashboard: Ventana de Mercadería (4 capas) + Margen Efectivo + Ventana de Compra + Acciones del Día
- Vistas CAPI: Reposición, Sobrestock, Marcas Terceras
- Alertas IA agrupadas por marca con expanders
- Chat IA con memoria (5 turnos) + validación de columnas

### Documentación y medición
- Plan de medición de resultados (4 fases, 12 métricas)
- Registro de horas estimado (14h sin Capi → 2.1h con Capi)
- Roadmap V1.5 → V3

---

## 2. Hallazgos de la auditoría técnica (25 Abril)

### Bugs corregidos hoy

**BUG-1 y BUG-2: Marca propia >26 sem caía a OK**
- 527 rows de productos propios viejos (>26 sem) se clasificaban como OK porque:
  - LIQUIDAR exige ST<2% (si ST era mayor, no entraba)
  - NEGOCIAR exige marca tercera (propia no entraba)
  - MARKDOWN exige edad ≤26 (>26 no entraba)
- Fix: catchall para propia >26 sem antes del bloque EMPUJE
- Resultado: 0 rows OK con >26 sem post-fix

**BUG-3 previo: Columnas de margen no llegaban a cobertura**
- `maestro_final_cols` descartaba `vta_soles_4sem`, `contrib_soles_4sem`, `margen_efectivo`
- Fix: agregar a la lista `cols` en build_cobertura
- Mismo patrón que el bug de `stock_cd` — lección de debugging confirmada

### Warnings pendientes (mejoras recomendadas)

**WARN-1: NEGOCIAR inflado a nivel SKU×Tienda**
- El clasificador marca TODO tercera >16 sem como NEGOCIAR (2,518 rows)
- El filtro de S/50K por marca solo se aplica en los top ejemplos
- El headline KPI `n_negociar` cuenta rows individuales, no marcas filtradas
- **Mejora:** Usar `n_marcas_negociar` (=7) en vez de `n_negociar` (=2,518) en la UI

**WARN-2: EMPUJE con margen negativo (24 rows)**
- Estamos empujando a piso productos que venden a pérdida
- El aging ignora margen completamente — prioriza rotación sobre rentabilidad
- **Mejora:** Cruzar acción aging × margen → si margen < 0, cambiar sugerencia de "empuje a piso" a "revisar precio antes de exhibir"

**WARN-3: 1,985 rows OK con edad >16**
- Son terceras individuales que no alcanzan S/50K acumulado por marca
- Correctos por diseño, pero representan S/~X de capital sin acción sugerida
- **Mejora:** Crear una 5ta categoría "MONITOREAR" para terceras >16 sem con capital bajo

---

## 3. Mapa de piezas del producto — qué hay vs qué falta

### Pieza 1: DIAGNÓSTICO (lo que tenemos)
| Componente | Estado | Madurez |
|------------|--------|---------|
| Cobertura por SKU×Tienda | ✅ Funcional | Alta |
| Clasificación por estados | ✅ 9 estados | Alta |
| Aging con acciones | ✅ 4 tipos + ejemplos | Alta |
| Margen efectivo | ✅ Global + por marca | Media |
| Sobrestock aparente | ✅ Proxy CD | Media |
| Anomalías tienda | ✅ Detección estadística | Media |

### Pieza 2: ACCIÓN (lo que tenemos parcialmente)
| Componente | Estado | Gap |
|------------|--------|-----|
| Reposiciones sugeridas | ✅ | Falta validación vs forecast |
| Transferencias | ✅ | Falta matriz logística real |
| Markdown progresivo | ✅ | Falta elasticidad precio |
| Alertas por marca | ✅ | Falta priorización por impacto $ |
| Alertas por tienda | ✅ | Falta geolocalización |

### Pieza 3: MEDICIÓN (lo que estamos construyendo)
| Componente | Estado | Gap |
|------------|--------|-----|
| Plan de medición | ✅ Doc completo | Falta ejecución |
| Baseline semana 0 | 🔲 Pendiente | Necesita correr con data 16-Abr |
| Comparativo semanal | 🔲 Pendiente | Función en motor no existe |
| Log de acciones | 🔲 Pendiente | No hay UI para registrar |
| KPIs para Rodrigo | 🔲 Pendiente | Depende de 4+ semanas de data |

### Pieza 4: PREDICCIÓN (lo que no tenemos)
| Componente | Estado | Qué necesita |
|------------|--------|--------------|
| Forecast de demanda | ❌ | Histórico 52+ sem, datos clima, calendario |
| Elasticidad de precio | ❌ | Histórico de precios × ventas |
| Clustering de tiendas | ❌ | Datos demográficos, tráfico, ventas cruzadas |
| Predicción de obsolescencia | ❌ | Patrones de lifecycle por categoría |
| Optimización de surtido | ❌ | Curvas talla×color, canibalización |
| Simulador what-if | ❌ | Todos los modelos anteriores |

---

## 4. Ruta hacia Capi Predictivo — 3 horizontes

### Horizonte 1: Capi Reactivo Inteligente (Ahora → Junio 2026)
**Objetivo:** Diagnosticar mejor con los datos que ya tenemos.

Mejoras sin datos nuevos:
- Cruzar aging × margen → no empujar productos a pérdida
- Calcular "velocidad de envejecimiento" (delta edad semana a semana)
- Índice de urgencia = capital × días hasta obsolescencia
- Score compuesto por SKU: priorizar acciones por impacto en S/
- Categoría "MONITOREAR" para terceras en zona gris

Mejoras con datos que Franco ya tiene:
- Sell-through real: uds vendidas / uds recibidas (si el reporte lo trae)
- Histórico de precios: si se puede extraer precio_vigente de semanas anteriores
- Estacionalidad simple: comparar misma semana del año pasado (si hay data LY)

### Horizonte 2: Capi Analítico (Julio → Septiembre 2026)
**Objetivo:** Entender patrones y anticipar comportamiento.

Requiere acumulación de datos semanales (lo que estamos armando con el baseline):
- **Curva de vida del SKU:** Con 8-12 semanas de data, modelar la trayectoria típica de un producto (lanzamiento → pico → declive). Detectar cuándo un SKU se desvía de la curva esperada para su categoría.
- **Clustering de tiendas:** Agrupar tiendas por comportamiento de venta (no por geografía). Tiendas que venden categorías similares deberían tener surtido similar.
- **SKU hijo (talla×color):** Repo a nivel granular. Una camiseta puede tener cobertura "óptima" a nivel SKU pero estar agotada en talla M.
- **Patrones de markdown:** ¿Cuánto descuento se necesita realmente para mover un producto? Histórico de acciones tomadas vs resultado.

### Horizonte 3: Capi Predictivo (Octubre 2026+)
**Objetivo:** Predecir qué va a pasar y recomendar antes de que pase.

Requiere modelos estadísticos/ML:
- **Forecast de demanda por SKU×Tienda:** Modelo de series de tiempo (Prophet, LightGBM) con features: temporada, día de pago, feriados, clima, tendencia.
- **Predicción de obsolescencia:** ¿Cuándo va a morir este SKU? Basado en velocidad de venta, edad, categoría, temporada.
- **Elasticidad de precio:** Si bajo el precio 10%, ¿cuántas unidades más vendo? Esto convierte el markdown de "arte" a "ciencia".
- **Optimización de compra:** Dado el forecast, ¿cuánto comprar de cada SKU para la próxima temporada?
- **Simulador what-if:** "Si hago markdown de 20% en estos 50 SKUs, ¿cuánto capital libero y cuánto margen pierdo?"

---

## 5. Datos críticos que Franco debe empezar a acumular

| Dato | Por qué es crítico | Cómo conseguirlo |
|------|-------------------|-----------------|
| Base Profundidad semanal | Sin histórico no hay curva de vida ni forecast | Guardar cada semana como snapshot (ya planeado) |
| Acciones tomadas + resultado | Entrenar el modelo de "qué funciona" | Log de gestión en Capi (Task #87) |
| Precios históricos por semana | Elasticidad de precio | Extraer precio_vigente de cada base semanal |
| Unidades recibidas por SKU | Sell-through real (no proxy) | Reporte de ingresos de mercadería |
| Datos de tráfico por tienda | Conversión = ventas / visitas | Puede venir de footfall counters (si Ripley tiene) |
| Calendario comercial | Correlacionar promos con picos de venta | Franco lo tiene en la cabeza — documentarlo |

---

## 6. Ideas de mejora inmediata (sprint actual)

1. **Cruzar aging × margen en el clasificador** — Si margen_efectivo < 0 y acción es EMPUJE, cambiar a "Revisar precio antes de empujar". Empujar venta a pérdida es peor que no vender.

2. **Score de prioridad por SKU** — Combinar: capital × urgencia × margen → un número que diga "atender este primero". Hoy las alertas están ordenadas por capital, pero no ponderan urgencia temporal.

3. **Velocidad de envejecimiento** — Con el comparativo semanal (Task #86), calcular delta_edad = cobertura_semN - cobertura_semN-1. Un SKU que envejece rápido necesita acción antes que uno estable.

4. **Categoría MONITOREAR** — Para terceras >16 sem con capital bajo (<S/50K por marca). No justifican reunión con proveedor, pero tampoco deberían ser ignoradas. Sugerencia: markdown individual.

5. **Filtro de margen negativo en alertas** — Agregar badge rojo "⚠️ MARGEN NEGATIVO" en SKUs con contribución negativa. El buyer debe saberlo antes de tomar cualquier acción.

6. **Sell-through por ventana de compra** — Comparar ST de ventana A vs B vs C. Las ventanas tempranas deberían tener mejor ST; si no, la planificación de compra falló.

---

## 7. Lo que le falta a Capi para ser un producto SaaS vendible

| Dimensión | Estado actual | Para ser vendible |
|-----------|--------------|-------------------|
| Onboarding | Manual (Franco sube Excel) | Auto-conexión a ERP/WMS |
| Multi-usuario | Single user (Franco) | Roles: buyer, gerente, proveedor |
| Datos | Snapshot semanal | Real-time o daily refresh |
| Alertas | Dentro de la app | Push notifications, email, WhatsApp |
| Acciones | Sugeridas en pantalla | Ejecutables (crear OC, enviar email, cambiar precio) |
| Histórico | No existe | Time series con 52+ semanas |
| IA | Chat Q&A + reglas | Predicción + optimización |
| Seguridad | Local | Cloud, SSO, permisos por tienda/marca |
| Pricing | Propuesto S/400-800/mes | Por # SKUs o # tiendas |

---

*Capi — Auditoría de Producto v1 · 25 Abril 2026 · Franco Barreto + Claude*
