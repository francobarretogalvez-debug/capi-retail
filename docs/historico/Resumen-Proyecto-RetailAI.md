# RetailAI — Resumen del Proyecto y Estado Actual

**Fecha:** 13 abril 2026
**Owner:** Franco Barreto — buyer retail en Ripley, Lima.
**Objetivo:** SaaS de gestión de inventario para marcas de retail/moda en Perú y Latam.

---

## 1. Problema y solución

Las marcas de retail gestionan inventario en Excel manualmente. Un buyer invierte 3-4 horas semanales revisando cobertura, decidiendo reposiciones entre tiendas, y sugiriendo acciones de precio. RetailAI automatiza ese trabajo y lo convierte en minutos, con lógica de negocio explícita y trazabilidad.

---

## 2. Arquitectura actual

**Stack:** Python + Streamlit + openpyxl + pandas. Todo corre local en la Mac del buyer.

**Flujo:**
1. Buyer exporta 2 archivos de Ripley: `Base Profundidad` (stock + ventas actuales por tienda) y `Venta LY` (histórico año anterior).
2. Script `transformar_profundidad.py` convierte formato ancho (301 columnas) a plantilla estándar v2.2 (4 pestañas: Maestro, Historial LY, Ventas 4 sem, Stock).
3. `motor_v2.py` calcula cobertura, reposiciones, transferencias, acciones de precio, alertas y anomalías.
4. `app_streamlit.py` renderiza todo con filtros interactivos y permite exportar Excel.

**Archivos clave (en `/Herramienta Retail Generada con AI/`):**
- `motor_v2.py` — motor de cálculo (~1200 líneas)
- `app_streamlit.py` — UI Streamlit (~700 líneas)
- `transformar_profundidad.py` — ETL Base Profundidad → plantilla
- `Plantilla_RetailAI_Input.xlsx` — plantilla generada, 2,079 SKUs activos
- `INICIAR_APP.command` — lanzador macOS para el usuario final
- `Ficha-Proyecto-RetailAI.md` — ficha ejecutiva PM

---

## 3. V1 — Funciones core completadas

**Cobertura general por SKU × Tienda** con 6 estados: CRÍTICO (<4 sem), ÓPTIMO (4-8), ALTO (8-16), SOBRESTOCK (>16), LIQUIDAR (sobrestock + edad >26 sem), SIN VENTA. Filtros por marca, categoría, tienda, SKU y estado.

**Reposiciones automáticas** — calcula `a_reponer = ceil(cob_target × prom_vta_sem - stock_actual)` para SKUs en CRÍTICO. Formato pivotado con tiendas en columnas y TOTAL a la derecha. `cob_target` configurable vía slider (default 8, Franco usa 12 semanas).

**Transferencias entre tiendas** — matchea sobrestock del mismo SKU contra CRÍTICO en otra tienda. Cantidad = min(exceso fuente, déficit destino). Sin matriz logística todavía.

**Acciones de precio** — sobrestock antiguo sugiere descuento con piso de margen. IGV 18% ya corregido en IMU, margen y precio mínimo.

**Alertas inteligentes** (ya implementadas pero refinadas en esta sesión):
- FRENANDO — venta sem1 cayó >30% vs prom sem2-4
- ACELERANDO — venta sem1 subió >30% vs prom sem2-4
- SIN TRACCIÓN — edad 3-8 sem, vende <50% del promedio categoría
- RIESGO CRÍTICO — pasará a CRÍTICO en ≤3 semanas a ritmo actual

**Anomalías por tienda** — detecta SKUs que venden normal en la mayoría de tiendas pero frenan en una específica.

**Briefing ejecutivo** — resumen de prioridades del día con cards agregados.

---

## 4. Decisiones y cambios hechos en esta sesión (13 abril)

**Criterios de filtrado de SKUs activos** (decidido con Franco):
- Stock total = 0 → excluir
- Temporada OI con edad >52 semanas → excluir (invierno pasado)
- Temporada PV con edad >=40 semanas → excluir (PV terminó en feb, hasta 39 sem aún se gestiona)
- Resultado: 5,339 SKUs → 2,079 SKUs activos

**Plantilla v2.2** — agregamos columna `Marca` en Tab 1 (MARQUIS, NAVIGATA, CACHAREL, etc.). Motor auto-detecta versión (v2.0/v2.1/v2.2) por nombres de columnas.

**Cobertura mejorada:**
- Excluir Tienda Virtual (no hay visibilidad real del stock).
- Excluir stock ≤ 0.
- Marca agregada como columna y filtro.

**Decisión crítica sobre cálculo de venta semanal** (13 abril):
- La `Base Profundidad` solo tiene venta real por tienda de la última semana (Sem 1).
- Sem 2-4 vienen solo como total agregado, sin desglose por tienda.
- Antes: prorrateamos sem 2-4 entre tiendas usando el share de sem 1 → estimación inflada.
- Ahora: `prom_vta_uds` por tienda = **solo sem 1 (dato real)**.
- Alertas de tendencia (sem1 vs sem2-4) se calculan a nivel SKU total, donde sem2-4 sí son reales.

**Arreglos de UI:**
- Pandas Styler max_elements → 500K (soporta 21K+ filas).
- Briefing cards agregadas en vez de una por SKU (evita 883 cards).
- Texto de cards en color legible sobre fondo beige.
- Plantilla partida en archivo light (1.7MB sin Tab 2) para evitar crash de 12MB.

---

## 5. Roadmap planificado

### V1.5 — IA Básica (mayo 2026)

| Feature | Estado | Prioridad |
|---|---|---|
| Índice estacional con Tab 2 LY (52 sem) | Pendiente | Alta |
| Calendario de eventos híbrido (IA detecta + buyer ajusta) | Pendiente | Media |
| Alertas de aceleración/freno de SKUs | ✅ Hecho V1 | — |
| Anomalías por tienda (exhibición, quiebre parcial) | ✅ Hecho V1 | — |
| Excel formateado profesionalmente (export polished) | Pendiente | Media |
| Lost sales correction (semana sin stock cuenta 0.5 en denominador) | Pendiente | Alta |

### V2 — SKU Hijo (jun-jul 2026)

| Feature | Estado | Prioridad |
|---|---|---|
| Apertura talla × color en reposiciones | Pendiente | Alta |
| Apertura talla × color en transferencias | Pendiente | Alta |
| Curvas ideales (S-M-L-XL) por tendencia de venta | Pendiente | Alta |
| Push inicial = N curvas por tienda | Pendiente | Media |
| Reposición por velocidad real de cada talla (NO por curva) | Pendiente | Alta |
| Clustering de tiendas por perfil de venta | Pendiente | Alta (Franco) |
| Acciones de precio se mantienen a nivel SKU padre | Definido | — |
| Dashboard con gráficos | Pendiente | Media |
| Matriz logística para transferencias | Pendiente | Media |
| `cob_target_hijo` configurable por cliente (6-20 sem según lead time) | Definido | — |

### V3 — IA Avanzada (ago-sep 2026)

Predicción de demanda (series de tiempo), elasticidad de precio, simulador what-if, asistente conversacional sobre datos.

---

## 6. Primeros usuarios objetivo

1. **Ayrton D'Ambrossio @ Moments** — prioridad 1. Validar V1.5 con él antes de V2.
2. **Nuqa** — prioridad 2.
3. **Contacto Ripley** — prioridad 3.

**Modelo de precio:** S/400-800/mes SaaS.

---

## 7. Principios de diseño acordados

- Motor **agnóstico** al nivel de SKU — funciona con padre o padre+hijo según lo que tenga el cliente.
- Backward compatibility en la plantilla — v2.0, v2.1 y v2.2 todas deben funcionar.
- Buyer confirma/ajusta detección IA — híbrido, no totalmente automático (especialmente en eventos comerciales).
- Trigger de reposición SKU hijo = cobertura < umbral (preventivo), no stock = 0 (reactivo).
- IGV 18% siempre presente — precios incluyen IGV, costo sin IGV. Margen/IMU se calculan sobre `precio/1.18`.

---

## 8. Siguiente milestone definido — V1.5 refinada (abril 2026)

Decidido el 13 abril: antes de saltar a V2 (SKU hijo) se cierran dos capacidades que son diferenciales reales vs Excel y que abren la conversación con clientes piloto.

**FEATURE 1 — Detección de picos de demanda con ajuste de reposición**

El algoritmo actual usa Sem 1 como proxy de demanda. Si un producto se acelera (viral, promo, temporada adelantada) se subrrepone y quiebra. Si se desacelera, se sobrerepone.

Construir módulo `detect_demand_shifts()` que detecte picos positivos/negativos por SKU × Tienda y ajuste la cantidad sugerida de reposición con un factor explicable. Métodos a evaluar: Z-score sobre ventana móvil (simple, sin LY), comparación YoY (robusto, requiere Tab 2), quiebre de tendencia por regresión (sofisticado), o híbrido.

Output esperado en tabla de reposiciones: `factor_ajuste`, `razon_ajuste`, `a_reponer_ajustado`. Con umbrales acotados y lógica explicable al buyer.

**FEATURE 2 — Sistema de alertas amigable para tiendas**

El sistema actual genera ~14,000 alertas con jerga técnica. Personal de tienda necesita listas cortas, accionables y compartibles por WhatsApp/email.

Distinción crítica: "stock crítico" tiene dos sabores opuestos en acción:
- **Stock bajo por buena venta** → apurar reposición, reforzar exhibición.
- **Stock alto por lenta rotación** → bajar al piso, rotar, consultar descuento, evaluar transferencia.

Construir módulo `build_alertas_tienda()` que por cada tienda genere reporte con dos secciones: **🟢 Oportunidades de venta** (Top 15 CRÍTICO por velocidad) y **🔴 Mercadería lenta** (Top 15 SOBRESTOCK/LIQUIDAR por capital inmovilizado). Lenguaje coloquial, cero jerga, acción sugerida por línea.

Export: PDF por tienda, HTML print-friendly, texto WhatsApp. Integración en Streamlit con selector de tienda.

---

## 9. Otros milestones V1.5 pendientes (después del milestone priorizado)

- Índice estacional con Tab 2 LY (52 sem).
- Calendario de eventos comerciales híbrido (IA detecta + buyer ajusta).
- Lost sales correction (semana sin stock cuenta 0.5 en denominador).
- Excel formateado profesionalmente para export.

## 10. Preparar demo para Ayrton (Moments)

Paralelo al desarrollo: empaquetar V1 actual + V1.5 refinada con las dos features nuevas y agendar demo con Ayrton D'Ambrossio como primer cliente piloto.

---

## 11. Avances sesión 22 abril 2026

### Chat IA v2 — Cerebro reescrito (`chat_engine.py`)

Se reescribió completamente el motor del chat con 6 mejoras:

1. **Memoria conversacional** — ventana de 5 turnos. Permite follow-ups como "ahora dame eso para esas mismas tiendas". Cada turno guarda pregunta, título, resumen del resultado y respuesta conversacional.
2. **Validación de columnas pre-ejecución** — regex que verifica columnas referenciadas contra `df.columns` antes de ejecutar. Detecta columnas inventadas (ej: `ventas` en vez de `prom_vta_uds`) y auto-corrige con retry. Solo valida accesos al df original, ignora aliases de salida en `.agg()`.
3. **Few-shot examples** — 4 ejemplos reales del dominio retail en el system prompt (top tiendas, capital parado, SKUs críticos, follow-up contextual).
4. **Mapeo de sinónimos** — tabla de traducción lenguaje natural → columna real (ej: "venta" → `prom_vta_uds`, "stock" → `stock_total`).
5. **Lista negativa** — el schema incluye columnas que NO existen para prevenir alucinaciones.
6. **Retry inteligente** — incluye error completo + código que falló + lista de columnas válidas.

**Decisión técnica:** Se descartaron frameworks (LangChain, PandasAI) por ser overengineering para 18K filas. Llamada directa a Claude API (Sonnet) es más simple, debuggeable y controlable.

**Fase 2 pendiente:** Migrar a DuckDB SQL generation — los LLMs generan SQL 40-50% mejor que pandas.

### Chat UI

- Panel lateral ampliado a 40% del ancho (antes 30%), similar a Nansen.
- Fix bug follow-up: reemplazado `st.text_input` con patrón `on_change` callback que limpia el input después de enviar.

### Alertas IA

- Rediseño: cards planas reemplazadas por **expanders agrupados por marca**. Click en marca para ver detalle. Header muestra # alertas, capital en riesgo, y desglose por tipo. Marcas ordenadas por capital descendente.

### Dashboard

- Botón de descarga CSV para detalle completo de productos obsoletos (>6 meses).
- Checkbox "Excluir sin stock CD" eliminado — el motor (`build_reposiciones`) ya excluye automáticamente marcas propias sin stock CD.

### Marcas propias (reposición solo desde CD)

MARQUIS, NAVIGATA, CACHAREL, SPAVALDI, OSCAR DE LA RENTA, US POLO. El motor las excluye de reposición si `stock_cd = 0`.

### Próximos pasos

- Presentación Ripley: **viernes 25 abril** con datos reales.
- Fase 2 del chat: DuckDB SQL generation para mayor precisión y velocidad.
- Pendiente: deck de resultados en Canva (#20).
