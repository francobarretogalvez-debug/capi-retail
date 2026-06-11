# Memorias Consolidadas del Proyecto Capi
# Generado: 10-Jun-2026
# Contiene: 17 archivos de contexto acumulados en 287+ tareas

---

## USUARIO

### user_franco.md
- **Expertise**: Retail — reposición, transferencias, gestión de precio, cobertura de inventario. Conocimiento profundo del negocio.
- **Perfil técnico**: No-code / low-code. Claude escribe todo el código.
- **Idioma**: Español (peruano). Todas las interacciones en español.
- **Estilo de colaboración**: Quiere un partner que cuestione sus ideas, explore múltiples opciones, y dé respuestas con trasfondo detallado.
- **Meta actual**: Crear herramienta SaaS de gestión de inventario AI para retail.

---

## PROYECTO

### project_retail_tool.md
Franco Barreto, buyer retail/moda en Ripley (Lima). Construye SaaS de inventario como proyecto principal.
**Audiencia:** Majo (jefa directa), Zina (gerenta comercial), Rodrigo Guajardo (gerente transformación digital).
**Objetivo:** Proponer gerencia de IA dentro de transformación digital. Capi es la evidencia.

### project_sku_hijo_design.md
Decisiones V2 — repo a nivel talla/color:
- Curva (S2-M3-L2-XL1) es para COMPRA INICIAL, no reposición
- Reposición sigue velocidad de venta real por talla
- cob_target_hijo = configurable por cliente (6-20 sem)
- Lost sales: si stock_cierre=0, semana cuenta como 0.5
- Trigger repo = cobertura < X semanas (preventivo)
- Acciones de precio se mantienen a nivel SKU padre
- Transferencias SÍ bajan a talla/color en V2

### project_agente_terceras.md
Primer agente autónomo de Capi. Detecta SKUs terceras con capital parado, genera correos a proveedores.
**Inducción supervisada:** Fase 1 (Franco aprueba cada correo) → Fase 2 (ajusta criterios) → Fase 3 (autonomía parcial con veto)
**Dependencias:** Ventana de Mercadería, contactos proveedores, templates correo, Gmail MCP

### project_fenomeno_nino.md
Perú vive el Fenómeno del Niño (mayo 2026): no hay invierno. Mercadería abrigadora no se vende.
- 2023 fue último Niño pero economía mala → venta desastre
- 2026 tiene Niño + economía buena → oportunidad real
- API temperatura: Open-Meteo (gratis)
- Categorización: GRUESO / MEDIO / LIGERO

---

## REGLAS DE NEGOCIO (FEEDBACK)

### feedback_dscto_propias.md
Excluir repos con dscto>=40% **SOLO aplica a terceras**. Propias se reponen siempre. Ya corregido en build_reposiciones() L597-608.

### feedback_naming_estados.md
CRÍTICO→QUIEBRE, PRE-CRÍTICO→PRE-QUIEBRE. Decisión 24-May-2026. taxonomia.py ya usa nuevos nombres.

### feedback_output_first.md
**REGLA #1**: Definir outputs concretos + decisión accionable ANTES de construir. Si un output no lleva a una acción, no debería existir. Aplica a TODO.

### feedback_debugging.md
Trazar dato de inicio a fin. Debug al FINAL del flujo, no al inicio. No asumir que la detección es el problema — leer funciones COMPLETAS. No culpar caché/encoding sin evidencia.

### feedback_color_constants.md
py_compile no detecta NameError en constantes (SLATE_XXX, TEAL_XXX). Auditar constantes usadas vs definidas después de cada cambio UI.

### feedback_rotacion_volumen.md
Vta/Stk infla categorías agotadas (TdB, shorts con stock ~0). Categorías accionables actuales: Polos M/C (104K stk), Camisas M/L (68K stk). Filtrar por stock mínimo o ponderar por volumen.

### feedback_pricing_omnicanal.md
Precios Ripley son omnicanal: descuento en una tienda aplica a todas. Margen por SKU global ES representativo por tienda.

### feedback_filtro_estacional_afinidad.md
Filtrar por temporada, NO por volumen:
- INVIERNO (desde May-1): excluir M/C + Shorts + TdB
- VERANO (desde Nov-1): excluir Casacas + Polerones + Chompas + Polos M/L
- TODO EL AÑO: Polos M/C, Camisas M/L, Pantalones, Jeans, Blazers, Accesorios
- Siempre excluir: Otros, Rebates
- Config MANUAL (Franco quiere poder forzar temporada)

---

## REFERENCIAS

### reference_marcas_propias.md
Propias: MARQUIS, NAVIGATA, CACHAREL, OSCAR DE LA RENTA, NAUTICA, US POLO, SPAVALDI.
Todo lo demás = tercera.

### reference_kpis_ripley.md
KPIs del dueño de Ripley: **EBITDA** y **Contribución/m²**. Todo feature de Capi debe trazar impacto a estos dos.

### reference_tiendas_mapeo.md
SB=San Borja, STA ANITA=Santa Anita, CHII=Chiclayo 2, ICA=Ica, CBT=Chimbote, LO=Mega Plaza (solo usar LO, no MP).

### reference_marca_tiendas.md
13 marcas con cobertura: MARQUIS/NAVIGATA=30 tiendas, CACHAREL=29, JOHN HOLDEN=27, US POLO=18, DOCKERS=16, OSCAR DE LA RENTA=15, PIERRE CARDIN=13, SELECTED=11, SPAVALDI=6, NAUTICA=5, NORTON=2, SILBON=2. Fuente: config_marca_tiendas.json
