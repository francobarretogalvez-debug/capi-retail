"""
acciones_stock.py — Sección "Acciones de Stock"
==================================================
Convierte la clasificación de estados (taxonomia.py) en una lista
priorizada y accionable de SKUs que requieren intervención.

Reemplaza al antiguo "Briefing Semanal" — pasa de listado plano a
**tiers + acciones diferenciadas + Pareto por tienda**.

INCLUYE estos estados (los que generan trabajo):
    DORMIDO, MUERTO              (sin venta — DORMIDO 8-26 sem, MUERTO >26 sem)
    ALTO                          (cob 16-26 — vigilar/empuje)
    SOBRESTOCK, ESTANCADO         (cob 26-52 y >52, edad ≤26)
    LIQUIDAR                      (cob >52 + edad >26)

EXCLUYE:
    LANZAMIENTO    — esperar, está en rampa (no problema accionable)
    QUIEBRE, BAJA  — problemas de FALTANTE, no de capital parado (van a Reposición)
    ÓPTIMO         — sano

USO TÍPICO:
    from acciones_stock import build_acciones_stock, build_resumen_acciones

    df_acciones = build_acciones_stock(df_cobertura, df_maestro, params)
    resumen = build_resumen_acciones(df_acciones)
"""

from typing import Optional
import pandas as pd

from taxonomia import Estado
from config import UMBRALES_DEFAULT


# ─────────────────────────────────────────────────────────────
#  TIERS — Mapeo Estado (+ edad) → Tier + Acción sugerida
# ─────────────────────────────────────────────────────────────
# Filosofía: markdown NO es palanca primaria. Antes de descuento, agotar:
#   1. Exhibición — ¿está en piso? ¿zona caliente?
#   2. Comunicación de precio — ¿bien etiquetado?
#   3. Visual merchandising — ¿outfit armado?
#   4. Capacitación de vendedor — ¿saben que existe?
# Solo SKUs maduros (edad >26 sem) sugieren markdown directo.

_TIER_DEFINITIONS = {
    'A1': {'accion': 'Markdown agresivo + liquidación',                              'criticidad': 'alta'},
    'A2': {'accion': 'Revisar exhibición primero, markdown solo si confirmado',     'criticidad': 'alta'},
    'A3': {'accion': 'Markdown agresivo + transferir lo movible',                   'criticidad': 'alta'},
    'A4': {'accion': 'Markdown profundizado + liquidación',                         'criticidad': 'alta'},
    'B1': {'accion': 'Revisar exhibición → markdown si no responde',                'criticidad': 'media'},
    'B2': {'accion': 'Exhibición + transferencia, NO descuento',                    'criticidad': 'media'},
    'B3': {'accion': 'Empuje + markdown moderado evaluado',                         'criticidad': 'media'},
    'C':  {'accion': 'Empuje en piso, exhibición',                                  'criticidad': 'baja'},
}

PARETO_THRESHOLD = 0.80  # 80% del capital de cada tienda


def asignar_tier(estado: str, edad: Optional[float]) -> Optional[dict]:
    """
    Asigna tier según estado + edad.
    Retorna dict {tier, accion, criticidad} o None si el estado no genera acción.
    """
    edad_maduro = UMBRALES_DEFAULT['edad_maduro']
    edad_es_maduro = edad is not None and edad >= edad_maduro

    if estado == Estado.MUERTO:
        return {'tier': 'A1', **_TIER_DEFINITIONS['A1']}
    if estado == Estado.LIQUIDAR:
        return {'tier': 'A4', **_TIER_DEFINITIONS['A4']}
    if estado == Estado.ESTANCADO:
        return ({'tier': 'A3', **_TIER_DEFINITIONS['A3']} if edad_es_maduro
                else {'tier': 'A2', **_TIER_DEFINITIONS['A2']})
    if estado == Estado.DORMIDO:
        return {'tier': 'B1', **_TIER_DEFINITIONS['B1']}
    if estado == Estado.SOBRESTOCK:
        return ({'tier': 'B3', **_TIER_DEFINITIONS['B3']} if edad_es_maduro
                else {'tier': 'B2', **_TIER_DEFINITIONS['B2']})
    if estado == Estado.ALTO:
        return {'tier': 'C', **_TIER_DEFINITIONS['C']}

    # QUIEBRE, BAJA, ÓPTIMO, LANZAMIENTO → no entran a Acciones de Stock
    return None


# ─────────────────────────────────────────────────────────────
#  BUILDER PRINCIPAL
# ─────────────────────────────────────────────────────────────

def build_acciones_stock(
    df_cobertura: pd.DataFrame,
    df_maestro: Optional[pd.DataFrame] = None,
    params: Optional[dict] = None,
) -> pd.DataFrame:
    """
    Genera tabla unificada de SKUs que requieren acción.

    Args:
        df_cobertura: salida de motor_v2.build_cobertura (debe tener columna 'estado').
        df_maestro:   no se usa hoy. Reservado para enriquecer con más campos.
        params:       no se usa hoy. Reservado para sobrescribir umbrales.

    Returns:
        DataFrame con columnas:
            sku, nombre, marca, categoria, tienda, stock_total, stock_valor_costo,
            cobertura_sem, edad_semanas, prom_vta_uds, estado, pct_descuento,
            tier, accion_sugerida, criticidad,
            flag_sin_markdown, flag_markdown_insuf, flag_pareto

        Ordenada por: criticidad descendente, stock_valor_costo descendente.
    """
    # ── 1. Filtrar a estados accionables ──
    estados_accion = [
        Estado.DORMIDO, Estado.MUERTO, Estado.ALTO,
        Estado.SOBRESTOCK, Estado.ESTANCADO, Estado.LIQUIDAR,
    ]
    df = df_cobertura[df_cobertura['estado'].isin(estados_accion)].copy()

    if df.empty:
        return pd.DataFrame()

    # ── 2. Asignar tier + acción + criticidad ──
    edad_col = df['edad_semanas'] if 'edad_semanas' in df.columns else None
    tier_data = df.apply(
        lambda r: asignar_tier(r['estado'], r.get('edad_semanas')),
        axis=1
    )
    df['tier']            = tier_data.apply(lambda x: x['tier'] if x else None)
    df['accion_sugerida'] = tier_data.apply(lambda x: x['accion'] if x else None)
    df['criticidad']      = tier_data.apply(lambda x: x['criticidad'] if x else None)

    # ── 3. Flags de markdown sobre estados de capital parado severo ──
    # Solo aplican a ESTANCADO y LIQUIDAR (los muy excedidos, cob >52).
    if 'pct_descuento' in df.columns:
        _es_estancado_o_liq = df['estado'].isin([Estado.ESTANCADO, Estado.LIQUIDAR])
        _dscto = df['pct_descuento'].fillna(0)
        df['flag_sin_markdown']     = _es_estancado_o_liq & (_dscto < 0.20)
        df['flag_markdown_insuf']   = _es_estancado_o_liq & (_dscto >= 0.20) & (_dscto < 0.35)
    else:
        df['flag_sin_markdown']   = False
        df['flag_markdown_insuf'] = False

    # ── 4. Pareto 80/20 por tienda ──
    # Marca como flag_pareto=True a los SKUs que acumulan hasta el 80%
    # del capital parado de su tienda. Útil para filtro "solo críticos".
    if 'tienda' in df.columns and 'stock_valor_costo' in df.columns:
        df = df.sort_values(['tienda', 'stock_valor_costo'], ascending=[True, False])
        _cap_acum   = df.groupby('tienda')['stock_valor_costo'].cumsum()
        _cap_total  = df.groupby('tienda')['stock_valor_costo'].transform('sum')
        df['flag_pareto'] = (_cap_acum / _cap_total.where(_cap_total > 0, 1)) <= PARETO_THRESHOLD
    else:
        df['flag_pareto'] = False

    # ── 5. Ordenar por criticidad + capital descendente ──
    crit_orden = {'alta': 0, 'media': 1, 'baja': 2}
    df['_crit_orden'] = df['criticidad'].map(crit_orden).fillna(99)
    df = df.sort_values(
        ['_crit_orden', 'stock_valor_costo'],
        ascending=[True, False]
    ).drop(columns='_crit_orden').reset_index(drop=True)

    return df


# ─────────────────────────────────────────────────────────────
#  RESUMEN EJECUTIVO (KPIs para header de UI)
# ─────────────────────────────────────────────────────────────

def build_resumen_acciones(df_acciones: pd.DataFrame) -> dict:
    """
    KPIs ejecutivos para el header de la sección "Acciones de Stock".

    Returns:
        dict con:
            capital_total      : S/ total inmovilizado en problemas accionables
            n_combos           : # de combos SKU×tienda en la tabla
            n_skus             : # de SKUs únicos
            n_tiendas_afectadas: # de tiendas con al menos 1 SKU en problema
            top_marcas         : dict marca → capital (top 5)
            top_tiendas        : dict tienda → capital (top 5)
            dist_tiers         : dict tier → count
            dist_criticidad    : dict criticidad → count
            capital_pareto     : capital concentrado en el 80% Pareto
            n_sin_markdown     : SKUs ESTANCADO/LIQUIDAR sin descuento intentado
            n_markdown_insuf   : SKUs ESTANCADO/LIQUIDAR con descuento insuficiente
    """
    if df_acciones.empty:
        return {
            'capital_total': 0.0,
            'n_combos': 0,
            'n_skus': 0,
            'n_tiendas_afectadas': 0,
            'top_marcas': {},
            'top_tiendas': {},
            'dist_tiers': {},
            'dist_criticidad': {},
            'capital_pareto': 0.0,
            'n_sin_markdown': 0,
            'n_markdown_insuf': 0,
        }

    out = {
        'capital_total':       float(df_acciones['stock_valor_costo'].sum()),
        'n_combos':            int(len(df_acciones)),
        'n_skus':              int(df_acciones['sku'].nunique()),
        'n_tiendas_afectadas': int(df_acciones['tienda'].nunique()) if 'tienda' in df_acciones.columns else 0,
        'dist_tiers':          df_acciones['tier'].value_counts().to_dict(),
        'dist_criticidad':     df_acciones['criticidad'].value_counts().to_dict(),
        'capital_pareto':      float(df_acciones.loc[df_acciones['flag_pareto'], 'stock_valor_costo'].sum()),
        'n_sin_markdown':      int(df_acciones['flag_sin_markdown'].sum()),
        'n_markdown_insuf':    int(df_acciones['flag_markdown_insuf'].sum()),
    }

    if 'marca' in df_acciones.columns:
        out['top_marcas'] = (df_acciones.groupby('marca')['stock_valor_costo']
                              .sum().sort_values(ascending=False).head(5)
                              .round(0).astype(int).to_dict())
    else:
        out['top_marcas'] = {}

    if 'tienda' in df_acciones.columns:
        out['top_tiendas'] = (df_acciones.groupby('tienda')['stock_valor_costo']
                               .sum().sort_values(ascending=False).head(5)
                               .round(0).astype(int).to_dict())
    else:
        out['top_tiendas'] = {}

    return out


# ─────────────────────────────────────────────────────────────
#  FILTROS HELPER (para UI)
# ─────────────────────────────────────────────────────────────

def filtrar_acciones(
    df_acciones: pd.DataFrame,
    marcas: Optional[list] = None,
    tiendas: Optional[list] = None,
    categorias: Optional[list] = None,
    tiers: Optional[list] = None,
    estados: Optional[list] = None,
    solo_sin_markdown: bool = False,
    solo_pareto: bool = False,
) -> pd.DataFrame:
    """
    Aplica filtros combinados a la tabla de acciones.

    Args:
        marcas:            si se pasa, filtra a esa lista de marcas.
        tiendas:           idem.
        categorias:        idem.
        tiers:             si se pasa, filtra a esa lista de tiers (A1, A2, B1, ...).
        estados:           si se pasa, filtra a esa lista de estados de la taxonomía.
        solo_sin_markdown: si True, solo SKUs con flag_sin_markdown=True.
        solo_pareto:       si True, solo SKUs del 80% Pareto de su tienda.

    Returns:
        DataFrame filtrado.
    """
    df = df_acciones

    if marcas and 'marca' in df.columns:
        df = df[df['marca'].isin(marcas)]
    if tiendas and 'tienda' in df.columns:
        df = df[df['tienda'].isin(tiendas)]
    if categorias and 'categoria' in df.columns:
        df = df[df['categoria'].isin(categorias)]
    if tiers:
        df = df[df['tier'].isin(tiers)]
    if estados:
        df = df[df['estado'].isin(estados)]
    if solo_sin_markdown:
        df = df[df['flag_sin_markdown']]
    if solo_pareto:
        df = df[df['flag_pareto']]

    return df.reset_index(drop=True)
