# Producción nacional vs solo-importado (categorías)

**Fuente:** Franco, 2026-06-12 (calibración de quiebres estructurales en Ventas Perdidas).

## Regla

Cuando un SKU IMPORTADO se agota en toda la cadena, el reorder importado demora ~2-3 meses (no llega para la venta en curso). La viabilidad del plan B depende de la categoría:

- **Producibles en Perú (reorder nacional viable):** POLOS M/C, CAMISAS M/L, CAMISAS M/C, PANTALONES, JEANS, POLERONES.
- **Solo importado** (costo nacional demasiado alto y el producto no queda bueno): CASACAS, CHOMPAS.
- Resto de categorías (shorts, blazers, accesorios, polos M/L, trajes de baño): sin definir — preguntar a Franco antes de asumir.

## Dónde se aplica

- `app_streamlit.py` → sección Ventas Perdidas → `_VP_NACIONALIZABLES` / `_VP_SOLO_IMPORTADO` en `_vp_accion()`:
  - IMP agotado + categoría nacionalizable → "⛔→🇵🇪 viable producir nacional"
  - IMP agotado + casacas/chompas → "⛔ estructural puro: sustituto o asumir pérdida"
- La procedencia (NAC/IMP) viene de la columna **'Proced.'** de la Base Profundidad (mapa SKU→procedencia cacheado, sin pasar por el ETL).

## Contexto relacionado

- Lead times de reposición logística por marca: `config_lead_times.json` (propias 3 días, terceras 7-28 días). Son lead de despacho/reposición, NO de reorder de producción.
- La base también trae 'Tipo de Evento Vigente' (MD1/PTR/MTR) con fechas inicio/fin — materia prima para el modelo de elasticidad post-demo.
