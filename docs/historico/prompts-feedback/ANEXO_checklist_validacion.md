# ANEXO — Checklist de Notas a Validar (Decisiones Pendientes)

> Este checklist consolida **todas las notas a validar** identificadas durante la conversación. Cada decisión está agrupada por prompt y debe resolverse antes o durante la ejecución del prompt correspondiente.
>
> Marca con ✅ a medida que vayas decidiendo con Cowork.

---

## PROMPT 0 (MASTER)

- [ ] Confirmar que Cowork tiene contexto completo del stack y archivos de la herramienta.
- [ ] Confirmar que el orden secuencial A→G es definitivo o si quieres mover algo.
- [ ] Confirmar que las anti-instrucciones son completas (¿agregar alguna específica?).

---

## PROMPT A — Taxonomía Maestra

- [ ] **Campo de edad del SKU**: confirmar nombre técnico exacto en el reporte fuente (micro/profundidad).
- [ ] **Definición operativa de "sin venta"**: ventana de medición — recomendación 4 semanas.
- [ ] **Granularidad**: SKU × tienda como base + agregación por SKU. Confirmar regla de agregación (>70% mismo estado).
- [ ] **Subdivisión SOBRESTOCK**: mantener un único SOBRESTOCK o subdividir en CLARO (26-52) y ZOMBIE (>52).
- [ ] **Nombres de estados**: ¿mantener nomenclatura técnica (ÓPTIMO-N, QUIEBRE-M) o simplificar?
- [ ] **Migración**: compatibilidad temporal con clasificación antigua o migración directa.

---

## PROMPT B — Snapshot Semanal + Bases Antiguas

- [ ] **Formato exacto de bases antiguas**: cuántas semanas, qué columnas, en qué archivo viven (discutir antes de codificar el cargador).
- [ ] **Convención de semana**: ISO o retail; cierre domingo o lunes.
- [ ] **Path de almacenamiento**: disco local, carpeta proyecto, cloud (Google Drive).
- [ ] **Versionado del schema**: estrategia ante cambios futuros del micro/profundidad.
- [ ] **Confirmar campos disponibles**: precio, descuento, temporada, fecha de ingreso del SKU. Levantar gap si falta alguno.
- [ ] **Tamaño esperado del histórico**: validar viabilidad técnica (~15M filas/año estimadas).
- [ ] **Backup**: automático o manual.

---

## PROMPT C — Salud del Stock

- [ ] **Nombre de la sección**: "Salud del Stock" vs "Acciones de Stock".
- [ ] **Tracking de ejecución en piso**: ¿incluir en este prompt (marcar revisado/exhibición corregida/markdown aplicado) o backlog?
- [ ] **Threshold de Pareto**: 80/20 estándar o ajustar.
- [ ] **Subdivisión SOBRESTOCK por edad**: mantener B2/B3 según edad o simplificar.
- [ ] **Lanzamientos (RAMPA)**: confirmar exclusión total del listado.
- [ ] **Acciones sugeridas**: texto fijo por tier o personalizable por marca/categoría.
- [ ] **Output exportable**: formato esperado (Excel/CSV) y qué columnas incluir para enviar a tiendas/marcas.

---

## PROMPT D — Vista Cobertura

- [ ] **Nombre final de la sección**: "Cobertura" / "Mapa de Cobertura" / "Salud del Surtido".
- [ ] **Componentes**: una sola pestaña vs pestañas separadas.
- [ ] **Performance del heatmap**: 420 celdas (30 tiendas × 14 marcas) — validar UX en Streamlit.
- [ ] **Marcas en algunas tiendas**: cómo mostrar celdas vacías (gris vs ocultar).
- [ ] **Umbral mínimo de stock para alertas**: default 10 uds.
- [ ] **Drill-down**: modal vs expander vs pestaña aparte.
- [ ] **Export**: necesidad de exportar listado de alertas.

---

## PROMPT E — Predistribución Mejorada

- [ ] **Lógica actual de "tiendas faltantes"**: ¿lista todas las tiendas o solo las del plan original? Validar antes de modificar.
- [ ] **Promedio simple vs ponderado** en columna `cob_marca_cat_promedio_faltantes`.
- [ ] **Umbrales de empuje** (verde/amarillo/rojo): validar 16/26 sem.
- [ ] **Drill-down**: expander vs modal.
- [ ] **Cobertura adicional a mostrar**: ¿también marca × tienda completa? ¿categoría completa todas las marcas?
- [ ] **Campo de plan de distribución**: confirmar disponibilidad o levantar gap.

---

## PROMPT F — Auditoría Reposición

- [ ] **Acceso al código actual** de Reposición.
- [ ] **Disponibilidad de campos en data fuente**: lead_time_proveedor, estado_sku, stock_central_disponible.
- [ ] **Nivel de cambio permitido**: solo identificar gaps o también refactorizar.
- [ ] **Decisión final** sobre necesidad de mini-vista "Repo en Riesgo" según resultados de los 5 escenarios:
  - Escenario 1 — Quiebre por talla
  - Escenario 2 — Lead time del proveedor 3ro
  - Escenario 3 — Cumplimiento de marcas 3ras
  - Escenario 4 — SKUs sin stock central disponible
  - Escenario 5 — SKUs descontinuados

---

## PROMPT G — Auditoría Estados

- [ ] **Threshold de homogeneidad**: 80% / 50%.
- [ ] **Si auditoría concluye Escenario A**: implementar rediseño en este prompt o posterior.
- [ ] **SKUs en MIXTO**: cómo presentarlos (sección aparte "Requieren análisis manual").
- [ ] **Vista por tienda secundaria**: ubicación final (Estados o futura sección Transferencias).

---

## DECISIONES TRANSVERSALES (validar al inicio)

Las siguientes aplican a múltiples prompts y conviene resolverlas temprano:

- [ ] **Granularidad temporal de la herramienta**: actualización semanal confirmada.
- [ ] **Único usuario**: tú (Franco). Confirmar que no hay otros usuarios planeados a corto plazo.
- [ ] **Naming conventions**: ¿mantener nombres en español o usar inglés técnico para variables?
- [ ] **Idioma de la UI**: español peruano confirmado.
- [ ] **Idioma de comentarios y docstrings en código**: ¿inglés técnico o español?
- [ ] **Estructura de directorios**: ¿tienes ya una estructura definida o Cowork la propone desde cero?
- [ ] **Versionado del proyecto**: ¿usas git? ¿branches por prompt? ¿commits por feature?
- [ ] **Testing**: ¿agregar tests unitarios (pytest) o validación manual basta?

---

## GAPS DE DATA CONFIRMADOS

Lista de campos que deben estar disponibles en el reporte fuente (micro/profundidad). **Tu nota indicó que estos están cubiertos** — confirmar con Cowork al inicio.

| Campo | Para qué prompt | Estado |
|---|---|---|
| Fecha de ingreso del SKU (edad) | A, B | ✅ Cubierto (confirmado por Franco) |
| Histórico de venta multi-semana | B | ✅ Cubierto vía bases antiguas como punto cero |
| Temporada del SKU (OI/PV/etc.) | B, C, D | ✅ Existe (Franco usa filtro de liquidación) |
| Estado de reposición central | F | ⚠️ Por validar en auditoría |
| Lead time por proveedor 3ro | F | ⚠️ Por validar en auditoría |
| Stock central disponible | F | ⚠️ Por validar en auditoría |
| Plan de distribución por SKU | E | ⚠️ Por validar |
| Stock por talla | F | ⚠️ Por validar (escenario quiebre por talla) |

---

## BACKLOG GLOBAL (NO IMPLEMENTAR EN ESTA SERIE)

Para referencia, lo que queda fuera del alcance de los prompts A-G:

1. Factor de elasticidad de precio para ajustar repo automática OI por mix de descuento PV histórico.
2. Validación automática por margen (no reponer si margen bajo/negativo).
3. Cobertura sostenible proyectada (forward-looking de quiebre).
4. Integración del gap de cobertura como input directo a Reposición (hoy es diagnóstico paralelo).
5. Loop de cumplimiento con marcas 3ras (tracking automático de repo solicitada vs despachada).
6. Recomendador automático de transferencias entre tiendas.
7. Forecasting de venta por SKU × tienda.
8. Detección automática de quiebre de talla (si la sección de Reposición no lo cubre).
9. Sección dedicada de Transferencias Sugeridas.
10. Loop de cierre con tiendas (foto/checklist de revisión de piso).
11. Recomendación automática de % de markdown óptimo por tier.
12. Integración con sistema de logística para ejecutar empujes.

---

**Fin del Anexo.**
