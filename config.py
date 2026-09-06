"""
config.py — Configuración centralizada de Capi
================================================
Umbrales, colores y mapeos que controlan la clasificación de estados.

NOTA: estos defaults pueden ser sobrescritos por params del usuario
(sliders en app_streamlit.py). El shim de backward-compat en taxonomia.py
traduce los nombres antiguos de DEFAULT_PARAMS al schema nuevo.

Cambios respecto a v1 (motor_v2.DEFAULT_PARAMS):
- ÓPTIMO ahora va 8-16 sem (antes 8-12). Banda alrededor de target 12.
- Se introduce ALTO 16-26 (antes ALTO era 12-16).
- SOBRESTOCK ahora es banda acotada 26-52 (antes >16 sin techo).
- ESTANCADO (nuevo) cubre cob >52 + edad ≤26 sem.
- LIQUIDAR ahora exige cob >52 + edad >26 (antes solo cob >16 + edad >26).
- LANZAMIENTO (nuevo, reemplaza NUEVO SIN VENTA) para sin venta + edad <8.
"""

# ─────────────────────────────────────────────────────────────
#  UMBRALES DE COBERTURA Y EDAD
# ─────────────────────────────────────────────────────────────

UMBRALES_DEFAULT = {
    # Cobertura (semanas) — límite SUPERIOR de cada banda
    'critico':    4,    # <4         → QUIEBRE
    'baja':       8,    # 4–8        → BAJA
    'optimo':    16,    # 8–16       → ÓPTIMO   (incluye target 12)
    'alto':      26,    # 16–26      → ALTO
    'sobrestock':52,    # 26–52      → SOBRESTOCK
    # >52 + edad ≤26     → ESTANCADO
    # >52 + edad 26–39   → PRE-OBSOLETO · >52 + edad >39 → OBSOLETO (Franco 2026-09-06)

    # Edad (semanas) — para casos sin venta y para subdivisión >52 sem
    'edad_lanzamiento': 8,    # <8 sem sin venta → LANZAMIENTO (en rampa, esperar)
    'edad_dormido':    26,    # 8-26 sem sin venta → DORMIDO
    # 26–39 sem sin venta → PRE-OBSOLETO · >39 → OBSOLETO

    # LIQUIDAR exige cob >52 Y edad > este umbral
    'edad_maduro': 26,
    # Frontera pre-obsoleto / obsoleto (9 meses)
    'edad_obsoleto': 39,
}


# ─────────────────────────────────────────────────────────────
#  MAPEO DE rango_antiguedad (del maestro) → ESTADO SIN VENTA
# ─────────────────────────────────────────────────────────────
# Fallback para asignar sub-estado SIN VENTA cuando NO hay edad_semanas.
# La edad en semanas manda y revalida cuando se conoce (ver taxonomia.py);
# este mapa solo aplica a filas sin edad. OJO: "RANGO 0_3" = 0-3 meses
# (hasta ~13 sem), por eso ya no se usa como criterio de NUEVO si hay edad.

RANGO_SIN_VENTA_MAP = {
    "RANGO 0":     "NUEVO SIN VENTA",  # recién llegado (solo si no hay edad)
    "RANGO 0_3":   "NUEVO SIN VENTA",  # 0-3 meses (solo si no hay edad)
    "RANGO 3_6":   "DORMIDO",          # 3-6 meses, ya debería vender
    "RANGO 6_9":   "PRE-OBSOLETO",     # 6-9 meses
    "RANGO 9_12":  "OBSOLETO",         # 9-12 meses
    "RANGO 12_99": "OBSOLETO",         # >12 meses
}


# ─────────────────────────────────────────────────────────────
#  COLORES POR ESTADO (hex, consumido por UI y formato condicional)
# ─────────────────────────────────────────────────────────────

COLOR_MAP = {
    "QUIEBRE":      "#B71C1C",  # rojo oscuro — urgente reponer
    "PRE-QUIEBRE":  "#E65100",  # naranja oscuro — reponer pronto, riesgo de quiebre
    "ÓPTIMO":       "#1B5E20",  # verde oscuro — sano
    "PRE-SOBRESTOCK":         "#F57F17",  # ámbar — vigilar
    "SOBRESTOCK":   "#BF360C",  # naranja-rojo — empuje
    "ESTANCADO":    "#424242",  # gris oscuro — capital parado, markdown evaluado
    "PRE-OBSOLETO": "#880E4F",  # púrpura-rojo — markdown agresivo
    "NUEVO SIN VENTA":  "#90CAF9",  # azul claro — recién llegado, sin movimiento todavía
    "DORMIDO":      "#5D4037",  # marrón — revisar exhibición
    "OBSOLETO":     "#212121",  # gris muy oscuro — liquidar
}


# ─────────────────────────────────────────────────────────────
#  ORDEN POR CRITICIDAD (para sort_values)
# ─────────────────────────────────────────────────────────────
# Mantiene la lógica de v1: cobertura ascendente, sin venta al final.

ESTADO_ORDEN = {
    "QUIEBRE":      0,
    "PRE-QUIEBRE":  1,
    "ÓPTIMO":       2,
    "PRE-SOBRESTOCK":         3,
    "SOBRESTOCK":   4,
    "ESTANCADO":    5,
    "PRE-OBSOLETO": 6,
    "NUEVO SIN VENTA":  7,
    "DORMIDO":      8,
    "OBSOLETO":     9,
}
