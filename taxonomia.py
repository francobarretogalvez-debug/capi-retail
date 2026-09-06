"""
taxonomia.py — Clasificación unificada de estados SKU × tienda
================================================================
Reemplaza la lógica inline de motor_v2.classify_coverage() con matriz
Cobertura × Edad explícita.

Punto único de verdad para clasificar un combo (SKU × tienda) en uno
de los 10 estados. Todas las secciones de Capi deben consumir de aquí
(no aplicar clasificación propia paralela).

USO TÍPICO:
    from taxonomia import classify, Estado, COLOR_MAP

    estado = classify(cobertura_sem=3.2, edad_semanas=18, rango_antiguedad="RANGO 0_3")
    # → "QUIEBRE"

    color = COLOR_MAP[estado]
    # → "#B71C1C"

BACKWARD-COMPAT:
    El módulo expone classify_coverage(cob, edad, params, rango) con la
    misma firma que motor_v2 v1, para no romper código existente. Internamente
    traduce los keys viejos (umbral_critico, umbral_precritico, ...) al
    schema nuevo de UMBRALES_DEFAULT.
"""

from typing import Optional, Tuple
import pandas as pd

from config import (
    UMBRALES_DEFAULT,
    COLOR_MAP,
    ESTADO_ORDEN,
    RANGO_SIN_VENTA_MAP,
)


# ─────────────────────────────────────────────────────────────
#  CONSTANTES DE ESTADO
# ─────────────────────────────────────────────────────────────
# Usar Estado.QUIEBRE en vez de "QUIEBRE" cuando se hace filtrado:
#   df_critico = df[df['estado'] == Estado.QUIEBRE]
#   df_problemas = df[df['estado'].isin([Estado.QUIEBRE, Estado.PRE_QUIEBRE])]
# Esto permite renombrar un estado en el futuro tocando solo este archivo.

class Estado:
    """Nombres canónicos de los 10 estados. Usar en filtros y comparaciones."""
    QUIEBRE      = "QUIEBRE"
    PRE_QUIEBRE  = "PRE-QUIEBRE"
    OPTIMO       = "ÓPTIMO"
    ALTO         = "PRE-SOBRESTOCK"   # renombrado de "ALTO" (Franco 2026-09-06): cobertura 16–26 sem = antesala del sobrestock
    PRE_SOBRESTOCK = "PRE-SOBRESTOCK"
    SOBRESTOCK   = "SOBRESTOCK"
    ESTANCADO    = "ESTANCADO"
    LIQUIDAR     = "LIQUIDAR"
    LANZAMIENTO  = "NUEVO SIN VENTA"
    DORMIDO      = "DORMIDO"
    MUERTO       = "MUERTO"

    @classmethod
    def todos(cls) -> list:
        return [
            cls.QUIEBRE, cls.PRE_QUIEBRE, cls.OPTIMO, cls.ALTO,
            cls.SOBRESTOCK, cls.ESTANCADO, cls.LIQUIDAR,
            cls.LANZAMIENTO, cls.DORMIDO, cls.MUERTO,
        ]

    @classmethod
    def problemas_repo(cls) -> list:
        """Estados que generan acción de reposición."""
        return [cls.QUIEBRE, cls.PRE_QUIEBRE]

    @classmethod
    def problemas_markdown(cls) -> list:
        """Estados que potencialmente requieren markdown."""
        return [cls.ESTANCADO, cls.LIQUIDAR, cls.MUERTO]

    @classmethod
    def sin_venta(cls) -> list:
        """Estados que indican que el SKU no vendió en últimas 4 sem."""
        return [cls.LANZAMIENTO, cls.DORMIDO, cls.MUERTO]


# ─────────────────────────────────────────────────────────────
#  CLASIFICADOR PRINCIPAL
# ─────────────────────────────────────────────────────────────

def classify(
    cobertura_sem: Optional[float],
    edad_semanas: Optional[float] = None,
    rango_antiguedad: Optional[str] = None,
    umbrales: Optional[dict] = None,
) -> str:
    """
    Clasifica un combo SKU × tienda en uno de los 10 estados.

    Args:
        cobertura_sem: cobertura en semanas (None o NaN = sin venta).
        edad_semanas: antigüedad del producto en semanas. Requerido
                      para subdividir sin venta y para diferenciar
                      ESTANCADO vs LIQUIDAR.
        rango_antiguedad: string del rango (RANGO 0_3, RANGO 3_6, ...)
                          del maestro. Solo es FALLBACK para clasificar sin
                          venta cuando no hay edad_semanas; la edad manda.
        umbrales: dict opcional para sobrescribir UMBRALES_DEFAULT.

    Returns:
        str: uno de los estados de la clase Estado.

    Reglas:
        - Sin venta (cobertura_sem None o NaN) — edad manda, rango es fallback:
            * <8 sem                   → LANZAMIENTO (NUEVO SIN VENTA)
            * 8–26 sem                 → DORMIDO
            * >26 sem                  → MUERTO
            * sin edad → rango (RANGO 0/0_3→NUEVO, 3_6→DORMIDO, 6+→MUERTO)
        - Con venta:
            * cob <4                   → QUIEBRE
            * 4–8                      → BAJA
            * 8–16                     → ÓPTIMO
            * 16–26                    → PRE-SOBRESTOCK (antes ALTO, Franco 2026-09-06)
            * 26–52                    → SOBRESTOCK
            * >52, edad ≤26            → ESTANCADO
            * >52, edad >26            → LIQUIDAR
    """
    u = umbrales if umbrales is not None else UMBRALES_DEFAULT

    # ── Caso sin venta ──
    if cobertura_sem is None or (
        isinstance(cobertura_sem, float) and pd.isna(cobertura_sem)
    ):
        return _classify_sin_venta(edad_semanas, rango_antiguedad, u)

    # ── Caso con venta: matriz de cobertura ──
    if cobertura_sem < u['critico']:               # <4
        return Estado.QUIEBRE
    if cobertura_sem < u['baja']:                  # 4–8
        return Estado.PRE_QUIEBRE
    if cobertura_sem < u['optimo']:                # 8–16
        return Estado.OPTIMO
    if cobertura_sem < u['alto']:                  # 16–26
        return Estado.ALTO
    if cobertura_sem < u['sobrestock']:            # 26–52
        return Estado.SOBRESTOCK
    # >52 → subdividir por edad
    if edad_semanas is not None and edad_semanas > u['edad_maduro']:
        return Estado.LIQUIDAR
    return Estado.ESTANCADO


def _classify_sin_venta(
    edad_semanas: Optional[float],
    rango_antiguedad: Optional[str],
    umbrales: dict,
) -> str:
    """Asigna LANZAMIENTO / DORMIDO / MUERTO.

    La EDAD EN SEMANAS manda y revalida cuando se conoce; el rango_antiguedad
    del maestro solo es fallback cuando no hay edad. (Antes el rango tenía
    prioridad y metía en NUEVO productos de hasta ~13 sem, porque "RANGO 0_3"
    cubre 0-3 meses; ahora el límite de NUEVO es estrictamente < edad_lanzamiento.)"""
    # Prioridad 1: edad en semanas (fuente de verdad)
    _edad_ok = edad_semanas is not None and not (
        isinstance(edad_semanas, float) and pd.isna(edad_semanas))
    if _edad_ok:
        if edad_semanas < umbrales['edad_lanzamiento']:    # <8
            return Estado.LANZAMIENTO
        if edad_semanas < umbrales['edad_dormido']:        # 8–26
            return Estado.DORMIDO
        return Estado.MUERTO

    # Prioridad 2 (fallback, sin edad): rango_antiguedad del maestro
    sub = RANGO_SIN_VENTA_MAP.get(str(rango_antiguedad or '').strip(), None)
    if sub is not None:
        return sub

    # Sin edad ni rango: conservador (no asumimos que sea reciente)
    return Estado.DORMIDO


# ─────────────────────────────────────────────────────────────
#  CLASIFICADOR VECTORIZADO (Series/DataFrame)
# ─────────────────────────────────────────────────────────────

def classify_series(
    cobertura: pd.Series,
    edad: pd.Series = None,
    rango: pd.Series = None,
    umbrales: dict = None,
) -> pd.Series:
    """
    Versión vectorizada de classify() — ~50x más rápido que iterrows.

    Aplica exactamente la misma lógica que classify() pero opera sobre
    Series completas usando np.select en vez de bucles row-by-row.

    Args:
        cobertura: Series de cobertura_sem (puede tener NaN).
        edad: Series de edad_semanas (opcional, puede ser None).
        rango: Series de rango_antiguedad (opcional, puede ser None).
        umbrales: dict de umbrales (default UMBRALES_DEFAULT).

    Returns:
        Series de str con los 10 estados posibles.
    """
    import numpy as np

    u = umbrales if umbrales is not None else UMBRALES_DEFAULT
    n = len(cobertura)

    # Preparar series auxiliares
    # NaN = sin venta. Cobertura negativa = con venta (→ QUIEBRE).
    # Usamos un sentinel que no colisiona con coberturas reales negativas.
    _SIN_VENTA_SENTINEL = -99999.0
    has_venta = cobertura.notna().values
    cob = cobertura.fillna(_SIN_VENTA_SENTINEL).values

    if edad is not None:
        # NaN edad: en classify() original, NaN < X es siempre False,
        # así que la cascada if/elif/else cae al return MUERTO.
        # Para replicar: tratamos NaN como edad=9999 → cae a MUERTO.
        ed = edad.fillna(9999).values
    else:
        ed = np.full(n, -1.0)

    # Flag: edad es realmente conocida (no NaN ni None)
    edad_conocida = ed < 9999

    # ── Sin venta: la EDAD EN SEMANAS manda (revalida) cuando se conoce;
    #    el rango_antiguedad solo es fallback para filas SIN edad.
    #    (Antes el rango tenía prioridad y metía en NUEVO productos de hasta
    #    ~13 sem, porque "RANGO 0_3" cubre 0-3 meses.) ──
    sin_venta_estado = np.full(n, Estado.DORMIDO, dtype=object)  # fallback sin info

    # Prioridad 2 (fallback): rango del maestro, SOLO donde no hay edad conocida
    if rango is not None:
        rng = rango.fillna('').astype(str).str.strip().values
        for rango_key, estado_val in RANGO_SIN_VENTA_MAP.items():
            sin_venta_estado = np.where(
                ~edad_conocida & (rng == rango_key), estado_val, sin_venta_estado
            )

    # Prioridad 1: edad en semanas (fuente de verdad; cubre todo el rango de edad)
    sin_venta_estado = np.where(
        edad_conocida & (ed < u['edad_lanzamiento']),
        Estado.LANZAMIENTO, sin_venta_estado
    )
    sin_venta_estado = np.where(
        edad_conocida & (ed >= u['edad_lanzamiento']) & (ed < u['edad_dormido']),
        Estado.DORMIDO, sin_venta_estado
    )
    sin_venta_estado = np.where(
        edad_conocida & (ed >= u['edad_dormido']),
        Estado.MUERTO, sin_venta_estado
    )

    # ── Con venta: cascada de cobertura ──
    # Para LIQUIDAR: requiere edad real conocida (no NaN→9999) y > edad_maduro
    con_venta_estado = np.where(
        cob < u['critico'], Estado.QUIEBRE,
        np.where(cob < u['baja'], Estado.PRE_QUIEBRE,
        np.where(cob < u['optimo'], Estado.OPTIMO,
        np.where(cob < u['alto'], Estado.ALTO,
        np.where(cob < u['sobrestock'], Estado.SOBRESTOCK,
        np.where(edad_conocida & (ed > u['edad_maduro']), Estado.LIQUIDAR,
                 Estado.ESTANCADO))))))

    # ── Combinar: sin venta vs con venta ──
    result = np.where(has_venta, con_venta_estado, sin_venta_estado)

    return pd.Series(result, index=cobertura.index)


# ─────────────────────────────────────────────────────────────
#  BACKWARD-COMPAT SHIM
# ─────────────────────────────────────────────────────────────

def classify_coverage(cob, edad_semanas, params, rango_antiguedad=None):
    """
    Wrapper backward-compat con motor_v2.classify_coverage() v1.

    Mantiene firma idéntica (cob, edad, params, rango) y mismo return type
    ((estado, color)) para no romper código que llama a la versión vieja.

    Traduce DEFAULT_PARAMS de motor_v2 al schema nuevo de UMBRALES_DEFAULT:
      umbral_critico    →  critico     (sin cambio semántico)
      umbral_precritico →  baja        (sin cambio semántico)
      umbral_alto       →  optimo      (era el "techo" de ÓPTIMO en v1, ahora también)
      umbral_edad       →  edad_maduro (criterio de LIQUIDAR)

    Los umbrales nuevos (alto=26, sobrestock=52, edad_lanzamiento=8,
    edad_dormido=26) NO están en DEFAULT_PARAMS de v1, así que se toman
    de UMBRALES_DEFAULT.

    NOTA: el slider UI "ÓPTIMO — hasta (sem)" (default 12 en v1) deja de
    usarse. ÓPTIMO ahora termina en umbral_alto (16 default). El slider
    queda visible pero inactivo en Sprint 1. Cleanup en Sprint 3.
    """
    umbrales = {
        'critico':          params.get('umbral_critico', 4),
        'baja':             params.get('umbral_precritico', 8),
        'optimo':           params.get('umbral_alto', 16),
        'alto':             UMBRALES_DEFAULT['alto'],
        'sobrestock':       UMBRALES_DEFAULT['sobrestock'],
        'edad_lanzamiento': UMBRALES_DEFAULT['edad_lanzamiento'],
        'edad_dormido':     UMBRALES_DEFAULT['edad_dormido'],
        'edad_maduro':      params.get('umbral_edad', 26),
    }
    estado = classify(cob, edad_semanas, rango_antiguedad, umbrales)
    color = COLOR_MAP[estado]
    return estado, color
