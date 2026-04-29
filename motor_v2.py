"""
motor_v2.py — Capi Inventory Engine v2
=======================================
Lee la plantilla de 4 pestañas del cliente y produce:
  - Cobertura general por SKU × Tienda
  - Reposiciones sugeridas
  - Transferencias entre tiendas (sin matriz logística)
  - Acciones de precio con fix de sobrestock residual

Thresholds default (configurables):
  CRÍTICO       < 4 sem
  PRE-CRÍTICO   4–8 sem
  ÓPTIMO        8–12 sem
  ALTO          12–16 sem
  SOBRESTOCK    > 16 sem
  LIQUIDAR      sobrestock + edad > 26 sem

  SIN VENTA se subdivide por antigüedad:
  NUEVO SIN VENTA   rango 0-3 meses (recién llegó)
  DORMIDO           rango 3-6 meses (debería estar vendiendo)
  MUERTO            rango 6+ meses (obsoleto sin venta)
"""

import pandas as pd
import numpy as np
from datetime import datetime
from math import ceil, floor
from collections import defaultdict


# ─────────────────────────────────────────────────────────────
#  PARÁMETROS DEFAULT
# ─────────────────────────────────────────────────────────────

DEFAULT_PARAMS = {
    "umbral_critico":    4,    # < 4 sem → CRÍTICO
    "umbral_precritico": 8,    # 4–8 sem → PRE-CRÍTICO
    "umbral_optimo":    12,    # 8–12 sem → ÓPTIMO
    "umbral_alto":      16,    # 12–16 sem → ALTO  (>16 → SOBRESTOCK)
    "umbral_edad":      26,    # semanas de antigüedad para LIQUIDAR
    "margen_min":     0.15,    # margen mínimo al calcular precio sugerido
    "uds_min_trans":     3,    # mínimo de unidades para generar transferencia
    "cob_target":       12,    # semanas objetivo post-reposición (centro del rango ÓPTIMO)
    # — Alertas para tiendas (reporte accionable por personal de piso) —
    "alertas_tienda_cob_min":  16,   # cobertura mín (sem) para disparar alerta (≥ sobrestock)
    "alertas_tienda_edad_min":  2,   # edad mín (sem) — leadtime de llegada/exhibición
    "alertas_tienda_top_n":    30,   # tope de ítems por tienda (ordenado por capital parado desc)
    # — Regla 40% (Majo): no reponer SKUs con descuento alto —
    "excluir_descuento_alto": True,
    "umbral_descuento_repo":  0.40, # descuento ≥40% → no reponer
}


# ─────────────────────────────────────────────────────────────
#  CARGA DE DATOS
# ─────────────────────────────────────────────────────────────

def _normalize_cols(df):
    """Quita saltos de línea y espacios extra de los nombres de columna."""
    df.columns = [str(c).replace('\n', ' ').strip() for c in df.columns]
    return df


def load_from_plantilla(path, params=None):
    """
    Lee el Excel de 4 pestañas del cliente.

    Parámetros
    ----------
    path   : ruta al archivo .xlsx
    params : dict con overrides de DEFAULT_PARAMS (opcional)

    Retorna
    -------
    (df_maestro, df_ventas, df_stock, params_usados)
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    IGV = 1.18  # Factor IGV Perú

    # ── Tab 1: Maestro Productos ──────────────────────────────
    df_m = pd.read_excel(path, sheet_name='1. Maestro Productos', header=1)
    df_m = _normalize_cols(df_m)
    df_m = df_m.dropna(subset=[df_m.columns[0]])
    df_m = df_m[~df_m.iloc[:, 0].astype(str).str.startswith('=')]
    # Detectar formato por nombres de columnas (auto-detect)
    # v2.2: SKU(0), Nombre(1), Marca(2), Categoría(3), Dpto(4), Edad(5), PB(6), PV(7), Costo(8)
    # v2.1: SKU(0), Nombre(1), Categoría(2), Dpto(3), Edad(4), PB(5), PV(6), Costo(7)
    # v2.0: SKU(0), Nombre(1), Categoría(2), Edad(3), PB(4), PV(5), Costo(6)
    col2_name = str(df_m.columns[2]).lower().replace('\n', ' ')
    col3_name = str(df_m.columns[3]).lower().replace('\n', ' ')

    if 'marca' in col2_name:
        # Formato v2.2 con Marca + Departamento
        rename_map = {
            df_m.columns[0]: 'sku',
            df_m.columns[1]: 'nombre',
            df_m.columns[2]: 'marca',
            df_m.columns[3]: 'categoria',
            df_m.columns[4]: 'departamento',
            df_m.columns[5]: 'edad_semanas',
            df_m.columns[6]: 'precio_blanco',
            df_m.columns[7]: 'precio_vigente',
            df_m.columns[8]: 'costo',
        }
        # Columna 9 = Temporada, Columna 10 = Rango Antigüedad (v2.2+)
        if len(df_m.columns) > 9:
            rename_map[df_m.columns[9]] = 'temporada'
        if len(df_m.columns) > 10:
            col10_name = str(df_m.columns[10]).lower().replace('\n', ' ')
            if 'rango' in col10_name or 'antigüedad' in col10_name or 'antiguedad' in col10_name:
                rename_map[df_m.columns[10]] = 'rango_antiguedad'
        # Buscar stock_cd y vta semanal por NOMBRE en cualquier posición
        for _ci, _cname in enumerate(df_m.columns):
            _cn = str(_cname).lower().replace('\n', ' ')
            if _cname not in rename_map:
                if 'stock cd' in _cn or ('cd' in _cn and 'bodega' in _cn):
                    rename_map[_cname] = 'stock_cd'
                elif 'vta sem1' in _cn or 'vendidas sem. 1' in _cn:
                    rename_map[_cname] = 'vta_sem1_total'
                elif 'vta sem2' in _cn or 'vendidas sem. 2' in _cn:
                    rename_map[_cname] = 'vta_sem2_total'
                elif 'vta sem3' in _cn or 'vendidas sem. 3' in _cn:
                    rename_map[_cname] = 'vta_sem3_total'
                elif 'vta sem4' in _cn or 'vendidas sem. 4' in _cn:
                    rename_map[_cname] = 'vta_sem4_total'
                elif 'ventana' in _cn and 'compra' in _cn:
                    rename_map[_cname] = 'ventana_compra'
                elif 'vta s/' in _cn and '4 sem' in _cn:
                    rename_map[_cname] = 'vta_soles_4sem'
                elif 'contrib s/' in _cn and '4 sem' in _cn:
                    rename_map[_cname] = 'contrib_soles_4sem'
                elif 'margen' in _cn and 'efectivo' in _cn:
                    rename_map[_cname] = 'margen_efectivo'
        df_m = df_m.rename(columns=rename_map)
        # Fallback posicional: si stock_cd NO se detectó por nombre, intentar col 11 (v2.2)
        if 'stock_cd' not in df_m.columns and len(df_m.columns) > 11:
            _c11 = df_m.columns[11]
            if _c11 not in ('temporada', 'rango_antiguedad', 'sku', 'nombre', 'marca',
                            'categoria', 'departamento', 'edad_semanas', 'precio_blanco',
                            'precio_vigente', 'costo'):
                df_m = df_m.rename(columns={_c11: 'stock_cd'})
        # Fallback posicional para vta semanal (cols 12-15 en v2.2)
        _vta_targets = [('vta_sem1_total', 12), ('vta_sem2_total', 13),
                        ('vta_sem3_total', 14), ('vta_sem4_total', 15)]
        for _vt_name, _vt_idx in _vta_targets:
            if _vt_name not in df_m.columns and len(df_m.columns) > _vt_idx:
                _cvt = df_m.columns[_vt_idx]
                if _cvt not in ('temporada', 'rango_antiguedad', 'stock_cd', 'sku', 'nombre',
                                'marca', 'categoria', 'departamento', 'edad_semanas',
                                'precio_blanco', 'precio_vigente', 'costo'):
                    df_m = df_m.rename(columns={_cvt: _vt_name})
    elif 'depart' in col3_name or 'dpto' in col3_name:
        # Formato v2.1 con Departamento
        df_m = df_m.rename(columns={
            df_m.columns[0]: 'sku',
            df_m.columns[1]: 'nombre',
            df_m.columns[2]: 'categoria',
            df_m.columns[3]: 'departamento',
            df_m.columns[4]: 'edad_semanas',
            df_m.columns[5]: 'precio_blanco',
            df_m.columns[6]: 'precio_vigente',
            df_m.columns[7]: 'costo',
        })
        df_m['marca'] = ''
    else:
        # Formato v2.0 sin Departamento ni Marca
        df_m = df_m.rename(columns={
            df_m.columns[0]: 'sku',
            df_m.columns[1]: 'nombre',
            df_m.columns[2]: 'categoria',
            df_m.columns[3]: 'edad_semanas',
            df_m.columns[4]: 'precio_blanco',
            df_m.columns[5]: 'precio_vigente',
            df_m.columns[6]: 'costo',
        })
        df_m['departamento'] = ''
        df_m['marca'] = ''

    # FIX 2: Edad ya viene directa en semanas (no calcular desde fecha)
    df_m['edad_semanas'] = pd.to_numeric(df_m['edad_semanas'], errors='coerce').fillna(0).astype(int)

    # Asegurar que stock_cd y vta semanal sean numéricos
    if 'stock_cd' in df_m.columns:
        df_m['stock_cd'] = pd.to_numeric(df_m['stock_cd'], errors='coerce').fillna(0).astype(int)
    for _vs in ['vta_sem1_total', 'vta_sem2_total', 'vta_sem3_total', 'vta_sem4_total']:
        if _vs in df_m.columns:
            df_m[_vs] = pd.to_numeric(df_m[_vs], errors='coerce').fillna(0).astype(int)

    # FIX 1: IMU y Margen Vigente sin IGV (precio / 1.18 = precio ex-IGV)
    # El costo ya viene sin IGV, los precios de venta incluyen IGV
    pb_ex = df_m['precio_blanco']  / IGV
    pv_ex = df_m['precio_vigente'] / IGV

    df_m['imu'] = np.where(
        pb_ex > 0,
        (pb_ex - df_m['costo']) / pb_ex,
        np.nan
    )
    df_m['margen_vigente'] = np.where(
        pv_ex > 0,
        (pv_ex - df_m['costo']) / pv_ex,
        np.nan
    )
    df_m['pct_descuento'] = np.where(
        df_m['precio_blanco'] > 0,
        1 - df_m['precio_vigente'] / df_m['precio_blanco'],
        np.nan
    )

    maestro_final_cols = ['sku', 'nombre', 'marca', 'categoria', 'departamento',
                          'precio_blanco', 'precio_vigente', 'costo',
                          'edad_semanas', 'imu', 'margen_vigente', 'pct_descuento']
    if 'temporada' in df_m.columns:
        df_m['temporada'] = df_m['temporada'].fillna('').astype(str).str.strip()
        maestro_final_cols.append('temporada')
    if 'rango_antiguedad' in df_m.columns:
        df_m['rango_antiguedad'] = df_m['rango_antiguedad'].fillna('Sin Rango').astype(str).str.strip()
        maestro_final_cols.append('rango_antiguedad')
    # Preservar stock_cd y vta semanal si fueron detectadas
    if 'stock_cd' in df_m.columns:
        maestro_final_cols.append('stock_cd')
    for _vs in ['vta_sem1_total', 'vta_sem2_total', 'vta_sem3_total', 'vta_sem4_total']:
        if _vs in df_m.columns:
            maestro_final_cols.append(_vs)
    # Ventana de compra (embarques A-F)
    if 'ventana_compra' in df_m.columns:
        df_m['ventana_compra'] = df_m['ventana_compra'].fillna('NOOS').astype(str).str.strip().str.upper()
        maestro_final_cols.append('ventana_compra')
    # Venta S/, Contribución S/ y Margen Efectivo (nivel SKU)
    for _mc in ['vta_soles_4sem', 'contrib_soles_4sem', 'margen_efectivo']:
        if _mc in df_m.columns:
            df_m[_mc] = pd.to_numeric(df_m[_mc], errors='coerce').fillna(0)
            maestro_final_cols.append(_mc)
    df_m = df_m[maestro_final_cols]

    # ── Tab 3: Ventas Recientes (WIDE) ────────────────────────
    df_v = pd.read_excel(path, sheet_name='3. Ventas Recientes (4 sem)', header=1)
    df_v = _normalize_cols(df_v)
    df_v = df_v.dropna(subset=[df_v.columns[0]])
    df_v = df_v[~df_v.iloc[:, 0].astype(str).str.startswith('=')]

    # FIX 3: Detectar columnas Uds por nombre (compatible con y sin columnas de fecha)
    # Formato CON fechas: cols 3,5,7,9 = fechas; 4,6,8,10 = uds
    # Formato SIN fechas (nuevo): cols 3,4,5,6 = uds directamente
    df_v = df_v.rename(columns={
        df_v.columns[0]: 'sku',
        df_v.columns[2]: 'tienda',
    })

    # Buscar columnas Uds por nombre normalizado
    uds_cols = [c for c in df_v.columns if 'vta uds' in c.lower() or 'vta\nuds' in c.lower()]
    if len(uds_cols) >= 4:
        # Detección por nombre (robusto ante cambios de posición)
        df_v = df_v.rename(columns={
            uds_cols[0]: 'vta_uds_sem1',
            uds_cols[1]: 'vta_uds_sem2',
            uds_cols[2]: 'vta_uds_sem3',
            uds_cols[3]: 'vta_uds_sem4',
        })
    else:
        # Fallback posicional para estructura original con fechas (cols 4,6,8,10)
        df_v = df_v.rename(columns={
            df_v.columns[4]:  'vta_uds_sem1',
            df_v.columns[6]:  'vta_uds_sem2',
            df_v.columns[8]:  'vta_uds_sem3',
            df_v.columns[10]: 'vta_uds_sem4',
        })

    for col in ['vta_uds_sem1', 'vta_uds_sem2', 'vta_uds_sem3', 'vta_uds_sem4']:
        df_v[col] = pd.to_numeric(df_v[col], errors='coerce').fillna(0)

    # Promedio venta semanal = solo Sem 1 (dato REAL por tienda).
    # Sem 2-4 por tienda son prorrateo estimado, no datos reales.
    # Para alertas de tendencia se usan los totales (sem1-4) a nivel SKU.
    df_v['prom_vta_uds'] = df_v['vta_uds_sem1'].astype(float)

    df_v = df_v[['sku', 'tienda',
                 'vta_uds_sem1', 'vta_uds_sem2', 'vta_uds_sem3', 'vta_uds_sem4',
                 'prom_vta_uds']]

    # ── Tab 4: Stock Actual ───────────────────────────────────
    df_s = pd.read_excel(path, sheet_name='4. Stock Actual', header=1)
    df_s = _normalize_cols(df_s)
    df_s = df_s.dropna(subset=[df_s.columns[0]])
    df_s = df_s[~df_s.iloc[:, 0].astype(str).str.startswith('=')]
    # Detectar si tiene columna Nombre (nuevo formato v2.1) o no (formato v2.0)
    # v2.1: SKU(0), Nombre(1), Tienda(2), FechaCorte(3), StockUds(4), StockTransito(5)
    # v2.0: SKU(0), Tienda(1), FechaCorte(2), StockUds(3), StockTransito(4)
    col1_name = str(df_s.columns[1]).lower().replace('\n', ' ')
    if 'nombre' in col1_name or 'descripci' in col1_name or 'producto' in col1_name:
        # Formato v2.1 con Nombre
        df_s = df_s.rename(columns={
            df_s.columns[0]: 'sku',
            df_s.columns[1]: 'nombre_prod',
            df_s.columns[2]: 'tienda',
            df_s.columns[3]: 'fecha_corte',
            df_s.columns[4]: 'stock_uds',
            df_s.columns[5]: 'stock_transito',
        })
    else:
        # Formato v2.0 sin Nombre
        df_s = df_s.rename(columns={
            df_s.columns[0]: 'sku',
            df_s.columns[1]: 'tienda',
            df_s.columns[2]: 'fecha_corte',
            df_s.columns[3]: 'stock_uds',
            df_s.columns[4]: 'stock_transito',
        })

    df_s['stock_uds']      = pd.to_numeric(df_s['stock_uds'],      errors='coerce').fillna(0)
    df_s['stock_transito'] = pd.to_numeric(df_s['stock_transito'],  errors='coerce').fillna(0)
    df_s['stock_total']    = df_s['stock_uds'] + df_s['stock_transito']

    # FIX 4a: Agregar descripción del producto desde maestro
    desc_map  = df_m.set_index('sku')['nombre'].to_dict()
    df_s['descripcion'] = df_s['sku'].map(desc_map)

    # FIX 4b: Stock valor a COSTO (no a precio de venta)
    costo_map = df_m.set_index('sku')['costo'].to_dict()
    df_s['costo_unit']       = df_s['sku'].map(costo_map)
    df_s['stock_valor_costo'] = df_s['stock_total'] * df_s['costo_unit']

    df_s = df_s[['sku', 'descripcion', 'tienda', 'fecha_corte',
                 'stock_uds', 'stock_transito', 'stock_total',
                 'costo_unit', 'stock_valor_costo']]

    return df_m, df_v, df_s, p


# ─────────────────────────────────────────────────────────────
#  CLASIFICACIÓN DE COBERTURA
# ─────────────────────────────────────────────────────────────

COLOR_MAP = {
    "CRÍTICO":          "#B71C1C",  # rojo oscuro
    "PRE-CRÍTICO":      "#E65100",  # naranja oscuro
    "ÓPTIMO":           "#1B5E20",  # verde oscuro
    "ALTO":             "#F57F17",  # ámbar
    "SOBRESTOCK":       "#BF360C",  # naranja-rojo
    "LIQUIDAR":         "#880E4F",  # púrpura-rojo
    "NUEVO SIN VENTA":  "#78909C",  # gris azulado (recién llegó, aún sin tracción)
    "DORMIDO":          "#5D4037",  # marrón (debería estar vendiendo)
    "MUERTO":           "#212121",  # gris muy oscuro (obsoleto sin venta)
}

ESTADO_ORDEN = {
    "CRÍTICO": 0, "PRE-CRÍTICO": 1, "ÓPTIMO": 2, "ALTO": 3,
    "SOBRESTOCK": 4, "LIQUIDAR": 5,
    "NUEVO SIN VENTA": 6, "DORMIDO": 7, "MUERTO": 8,
}

# Mapeo de rango_antiguedad a subcategoría SIN VENTA
_RANGO_SIN_VENTA = {
    "RANGO 0":    "NUEVO SIN VENTA",  # recién llegado
    "RANGO 0_3":  "NUEVO SIN VENTA",  # 0-3 meses
    "RANGO 3_6":  "DORMIDO",          # 3-6 meses, ya debería vender
    "RANGO 6_9":  "MUERTO",           # 6-9 meses, obsoleto
    "RANGO 9_12": "MUERTO",           # 9-12 meses
    "RANGO 12_99":"MUERTO",           # >12 meses
}


def classify_coverage(cob, edad_semanas, params, rango_antiguedad=None):
    """
    cob               : cobertura en semanas (None = sin venta)
    edad_semanas      : antigüedad del producto en semanas
    params            : dict de parámetros
    rango_antiguedad  : str del rango (RANGO 0_3, RANGO 3_6, etc.) para subdividir SIN VENTA

    Retorna: (estado_str, color_hex)
    """
    if cob is None or (isinstance(cob, float) and pd.isna(cob)):
        # Subdividir SIN VENTA por antigüedad del producto
        rango = str(rango_antiguedad or '').strip()
        sub = _RANGO_SIN_VENTA.get(rango, None)
        if sub is None:
            # Sin rango definido → usar edad en semanas como fallback
            if edad_semanas is not None:
                if edad_semanas < 13:    # ~3 meses
                    sub = "NUEVO SIN VENTA"
                elif edad_semanas < 26:  # ~6 meses
                    sub = "DORMIDO"
                else:
                    sub = "MUERTO"
            else:
                sub = "DORMIDO"  # default conservador
        return sub, COLOR_MAP[sub]

    uc  = params["umbral_critico"]
    upc = params["umbral_precritico"]
    uo  = params["umbral_optimo"]
    ua  = params["umbral_alto"]
    ue  = params["umbral_edad"]

    if cob > ua:
        if edad_semanas is not None and edad_semanas > ue:
            return "LIQUIDAR", COLOR_MAP["LIQUIDAR"]
        return "SOBRESTOCK", COLOR_MAP["SOBRESTOCK"]
    elif cob >= uo:
        return "ALTO", COLOR_MAP["ALTO"]
    elif cob >= upc:
        return "ÓPTIMO", COLOR_MAP["ÓPTIMO"]
    elif cob >= uc:
        return "PRE-CRÍTICO", COLOR_MAP["PRE-CRÍTICO"]
    else:
        return "CRÍTICO", COLOR_MAP["CRÍTICO"]


# ─────────────────────────────────────────────────────────────
#  1. COBERTURA GENERAL
# ─────────────────────────────────────────────────────────────

def build_cobertura(df_maestro, df_ventas, df_stock, params):
    """
    Produce tabla de cobertura por SKU × Tienda.
    Retorna DataFrame ordenado por criticidad.
    """
    # Join stock + ventas
    df = pd.merge(df_stock, df_ventas[['sku', 'tienda', 'prom_vta_uds']],
                  on=['sku', 'tienda'], how='left')

    # Join maestro (costo viene de maestro; df_stock tiene costo_unit pero lo unificamos)
    maestro_cols = ['sku', 'nombre', 'categoria', 'edad_semanas',
                    'precio_vigente', 'precio_blanco', 'costo',
                    'imu', 'margen_vigente', 'pct_descuento']
    if 'marca' in df_maestro.columns:
        maestro_cols.insert(2, 'marca')
    if 'departamento' in df_maestro.columns:
        maestro_cols.insert(maestro_cols.index('categoria') + 1, 'departamento')
    if 'tipo_precio' in df_maestro.columns:
        maestro_cols.append('tipo_precio')
    if 'stock_cd' in df_maestro.columns:
        maestro_cols.append('stock_cd')
    for _vs in ['vta_sem1_total', 'vta_sem2_total', 'vta_sem3_total', 'vta_sem4_total']:
        if _vs in df_maestro.columns:
            maestro_cols.append(_vs)
    if 'temporada' in df_maestro.columns:
        maestro_cols.append('temporada')
    if 'rango_antiguedad' in df_maestro.columns:
        maestro_cols.append('rango_antiguedad')
    if 'ventana_compra' in df_maestro.columns:
        maestro_cols.append('ventana_compra')
    # Margen efectivo (Contrib / VtasMF, nivel SKU)
    for _mc in ['vta_soles_4sem', 'contrib_soles_4sem', 'margen_efectivo']:
        if _mc in df_maestro.columns:
            maestro_cols.append(_mc)
    df = pd.merge(df, df_maestro[maestro_cols], on='sku', how='left')

    df['prom_vta_uds'] = df['prom_vta_uds'].fillna(0)

    # Excluir Tienda Virtual (no tenemos visibilidad real de su stock)
    df = df[~df['tienda'].str.contains('Virtual', case=False, na=False)].reset_index(drop=True)

    # Excluir filas con stock <= 0
    df = df[df['stock_total'] > 0].reset_index(drop=True)

    # Cobertura = stock_total / promedio_vta_semanal
    df['cobertura_sem'] = df.apply(
        lambda r: round(r['stock_total'] / r['prom_vta_uds'], 1)
        if r['prom_vta_uds'] > 0 else None,
        axis=1
    )

    # FIX 4b: Stock valor a costo (no a precio de venta)
    df['stock_valor_costo'] = df['stock_total'] * df['costo']

    # Clasificar
    estados = df.apply(
        lambda r: classify_coverage(
            r['cobertura_sem'], r['edad_semanas'], params,
            rango_antiguedad=r.get('rango_antiguedad', None)
        ),
        axis=1
    )
    df['estado'] = [e[0] for e in estados]
    df['color']  = [e[1] for e in estados]

    # Ordenar por criticidad primero, luego cobertura ascendente
    df['_orden'] = df['estado'].map(ESTADO_ORDEN)
    df = df.sort_values(['_orden', 'cobertura_sem']).drop(columns='_orden').reset_index(drop=True)

    cols = ['sku', 'nombre']
    if 'marca' in df.columns:
        cols.append('marca')
    cols += ['categoria', 'tienda',
             'stock_uds', 'stock_transito', 'stock_total',
             'stock_valor_costo', 'prom_vta_uds', 'cobertura_sem', 'estado',
             'edad_semanas', 'precio_vigente', 'precio_blanco', 'costo',
             'imu', 'margen_vigente', 'pct_descuento']
    if 'temporada' in df.columns:
        cols.append('temporada')
    if 'rango_antiguedad' in df.columns:
        cols.append('rango_antiguedad')
    if 'tipo_precio' in df.columns:
        cols.append('tipo_precio')
    if 'stock_cd' in df.columns:
        cols.append('stock_cd')
    for _vs in ['vta_sem1_total', 'vta_sem2_total', 'vta_sem3_total', 'vta_sem4_total']:
        if _vs in df.columns:
            cols.append(_vs)
    # Margen efectivo (nivel SKU, propagado desde maestro)
    for _mc in ['vta_soles_4sem', 'contrib_soles_4sem', 'margen_efectivo']:
        if _mc in df.columns:
            cols.append(_mc)

    # Sobrestock aparente: ratio stock_cd / stock_total > 0.6
    # Indica producto que probablemente no salió a piso de venta
    result = df[[c for c in cols if c in df.columns]].copy()
    if 'stock_cd' in result.columns:
        result['ratio_cd'] = np.where(
            result['stock_total'] > 0,
            result['stock_cd'] / result['stock_total'],
            0
        )
        result['sobrestock_aparente'] = (
            result['ratio_cd'] > 0.6
        ) & result['estado'].isin({'SOBRESTOCK', 'LIQUIDAR', 'ALTO'})
    else:
        result['ratio_cd'] = 0.0
        result['sobrestock_aparente'] = False

    return result


# ─────────────────────────────────────────────────────────────
#  2. REPOSICIONES
# ─────────────────────────────────────────────────────────────

# Tabla de curvas por línea (categoría de producto).
# Curva = unidades mínimas de envío — inventory management empuja en múltiplos de esta.
# Si un SKU no tiene su línea en esta tabla, se marca con warning y se skipea.
# Curvas de empuje por línea (categoría).
# Valor > 0 = curva activa, se redondea hacia arriba.
# Valor 0 = línea de verano (no reponer en invierno, sin stock en CD).
CURVAS_POR_LINEA = {
    "CASACAS":      6,
    "POLOS M/C":    8,
    "POLOS M/L":    8,
    "CHOMPAS":      6,
    "PANTALONES":  11,
    "JEANS":       11,
    "CAMISAS M/L":  8,
    "POLERONES":    6,
    "BLAZERS":      6,
    "ACCESORIOS":   5,   # sin curva formal, mínimo de empuje 5 uds
    # Líneas de verano — sin reposición en invierno (no hay stock en CD)
    "SHORTS":       0,
    "CAMISAS M/C":  0,
    "TRAJES DE BANO": 0,
}


def _redondear_a_curva(cantidad, curva):
    """
    Redondea `cantidad` hacia arriba al múltiplo de `curva` más cercano.

    Ej: _redondear_a_curva(100, 8) → ceil(100/8)*8 = 13*8 = 104
        _redondear_a_curva(96, 8)  → 96 (ya es múltiplo exacto)
        _redondear_a_curva(1, 8)   → 8 (mínimo 1 curva)
    """
    if curva <= 0 or cantidad <= 0:
        return int(cantidad)
    n_curvas = ceil(cantidad / curva)
    return n_curvas * curva


def build_reposiciones(df_cobertura, params):
    """
    Sugerencias de reposición para todos los SKUs cuya cobertura está
    por debajo del cob_target y tienen venta.

    a_reponer = ceil(cob_target × avg - stock_actual), redondeado hacia arriba
    a la curva del producto (si la línea tiene curva definida).

    Incluye SKUs en estado CRÍTICO, ÓPTIMO y ALTO siempre que su cobertura
    actual < cob_target. Excluye SOBRESTOCK, LIQUIDAR y SIN VENTA.

    SKUs cuya línea no tiene curva en CURVAS_POR_LINEA se marcan con
    warning_curva = True y se les asigna a_reponer = a_reponer_base (sin redondeo).

    Retorna DataFrame con columnas adicionales:
      curva, a_reponer_base, n_curvas, warning_curva
    """
    cob_target = params["cob_target"]
    uc         = params["umbral_critico"]
    # Regla 40% (Majo): no reponer SKUs con descuento >= umbral configurable
    # SOLO aplica a marcas terceras; marcas propias se reponen sin importar dscto
    excluir_dscto   = params.get("excluir_descuento_alto", True)
    umbral_dscto    = params.get("umbral_descuento_repo", 0.40)

    _MARCAS_PROPIAS_REPO = {'MARQUIS', 'NAVIGATA', 'CACHAREL', 'SPAVALDI',
                            'OSCAR DE LA RENTA', 'US POLO', 'NAUTICA'}

    # Todos los SKUs con cobertura por debajo del target que tienen venta
    _estados_excluir = {'SOBRESTOCK', 'LIQUIDAR', 'NUEVO SIN VENTA', 'DORMIDO', 'MUERTO'}
    _mask = (
        (~df_cobertura['estado'].isin(_estados_excluir)) &
        (df_cobertura['cobertura_sem'] < cob_target)
    )
    # Aplicar regla de descuento alto SOLO a marcas terceras
    if excluir_dscto and 'pct_descuento' in df_cobertura.columns:
        _es_propia = (df_cobertura['marca'].str.upper().str.strip()
                      .isin(_MARCAS_PROPIAS_REPO)
                      if 'marca' in df_cobertura.columns
                      else pd.Series(False, index=df_cobertura.index))
        _dscto_alto = df_cobertura['pct_descuento'].fillna(0) >= umbral_dscto
        # Excluir solo terceras con descuento alto
        _mask = _mask & ~(_dscto_alto & ~_es_propia)

    candidatos = df_cobertura[_mask].copy()

    rows = []
    warnings_sin_curva = set()

    for _, r in candidatos.iterrows():
        avg = r['prom_vta_uds']
        stk = r['stock_total']
        cob = r['cobertura_sem']

        if avg <= 0:
            continue

        stk_ideal = cob_target * avg
        a_reponer_base = ceil(max(0, stk_ideal - stk))

        if a_reponer_base <= 0:
            continue

        # Buscar curva de la línea (categoría)
        linea = str(r.get('categoria', '') or '').strip().upper()
        curva_val = CURVAS_POR_LINEA.get(linea, None)
        warning_curva = False

        if curva_val is None:
            # Línea no registrada → enviar sin redondear + warning
            a_reponer = a_reponer_base
            curva = 0
            n_curvas  = 0
            warning_curva = True
            warnings_sin_curva.add(linea)
        elif curva_val == 0:
            # Línea de verano → no reponer (sin stock en CD)
            continue
        else:
            curva = curva_val
            a_reponer = _redondear_a_curva(a_reponer_base, curva)
            n_curvas  = a_reponer // curva

        cob_post = round((stk + a_reponer) / avg, 1)

        # Nivel de urgencia según estado
        estado = r['estado']
        if estado == 'CRÍTICO':
            urgencia = '🔴 CRÍTICO'
        elif (cob or 999) < uc:
            urgencia = '🔴 CRÍTICO'
        elif cob < cob_target * 0.5:
            urgencia = '🟠 URGENTE'
        else:
            urgencia = '🟡 BAJO'

        rows.append({
            'sku':              r['sku'],
            'nombre':           r['nombre'],
            'marca':            r.get('marca', ''),
            'categoria':        r['categoria'],
            'tienda':           r['tienda'],
            'stock_actual':     int(stk),
            'prom_vta_sem':     round(avg, 1),
            'cobertura_actual': cob,
            'cob_target':       cob_target,
            'a_reponer_base':   int(a_reponer_base),
            'curva':            int(curva),
            'n_curvas':         int(n_curvas),
            'a_reponer':        int(a_reponer),
            'cob_post_rep':     cob_post,
            'precio_vigente':   r['precio_vigente'],
            'urgencia':         urgencia,
            'warning_curva':    warning_curva,
            'stock_cd':         int(r.get('stock_cd', 0)),
            'pct_descuento':    float(r.get('pct_descuento', 0)),
            'temporada':        r.get('temporada', ''),
            'stock_valor_costo': float(r.get('stock_valor_costo', 0)),
        })

    if warnings_sin_curva:
        import warnings
        warnings.warn(
            f"Capi: {len(warnings_sin_curva)} línea(s) sin curva definida "
            f"(skip redondeo): {sorted(warnings_sin_curva)}",
            UserWarning, stacklevel=2,
        )

    df_rep = pd.DataFrame(rows)
    if not df_rep.empty:
        # Excluir marcas propias sin stock en CD (no se puede reponer desde CD)
        # Marcas terceras/nacionales se mantienen aunque stock_cd=0 (repo va al proveedor)
        _MARCAS_PROPIAS = {'MARQUIS', 'NAVIGATA', 'CACHAREL', 'SPAVALDI',
                           'OSCAR DE LA RENTA', 'US POLO', 'NAUTICA'}
        _es_propia = df_rep['marca'].str.upper().str.strip().isin(_MARCAS_PROPIAS)
        _sin_cd = df_rep['stock_cd'] <= 0
        _excluir = _es_propia & _sin_cd
        if _excluir.any():
            print(f"[MOTOR] Reposición: excluidas {_excluir.sum()} líneas de marcas propias sin stock CD")
        df_rep = df_rep[~_excluir].reset_index(drop=True)
        df_rep = df_rep.sort_values('cobertura_actual').reset_index(drop=True)
    return df_rep


# ─────────────────────────────────────────────────────────────
#  3. TRANSFERENCIAS (sin matriz logística)
# ─────────────────────────────────────────────────────────────

def build_transferencias(df_cobertura, params):
    """
    Sugerencias de transferencia entre tiendas del mismo SKU.

    Lógica simplificada:
      - Fuentes: tiendas en SOBRESTOCK o LIQUIDAR del mismo SKU
      - Destinos: tiendas en CRÍTICO del mismo SKU
      - Sin restricción de zona ni distancia
      - Cantidad: min(exceso_fuente, déficit_destino)

    Retorna DataFrame.
    """
    cob_target = params["cob_target"]
    uds_min    = params["uds_min_trans"]
    ua         = params["umbral_alto"]

    rows = []

    for sku in df_cobertura['sku'].unique():
        df_sku = df_cobertura[df_cobertura['sku'] == sku].copy()

        # Fuentes: tiendas con exceso (SOBRESTOCK, LIQUIDAR, DORMIDO, MUERTO)
        fuentes  = df_sku[
            df_sku['estado'].isin(['SOBRESTOCK', 'LIQUIDAR', 'DORMIDO', 'MUERTO'])
        ]
        # Destinos: tiendas que necesitan stock (CRÍTICO, PRE-CRÍTICO)
        destinos = df_sku[df_sku['estado'].isin(['CRÍTICO', 'PRE-CRÍTICO'])]

        if fuentes.empty or destinos.empty:
            continue

        for _, src in fuentes.iterrows():
            avg_src = src['prom_vta_uds']
            stk_src = src['stock_total']

            # Exceso = stock - lo que necesitaría tener para cob_target semanas
            if avg_src > 0:
                exceso = floor(stk_src - cob_target * avg_src)
            else:
                exceso = int(stk_src)   # si no vende nada, todo el stock es "exceso"

            if exceso < uds_min:
                continue

            exceso_disponible = exceso
            uds_transferido_total = 0

            # Ordenar destinos por menor cobertura (más urgentes primero)
            dest_sorted = destinos.sort_values('cobertura_sem').copy()

            for _, dst in dest_sorted.iterrows():
                if exceso_disponible < uds_min:
                    break

                avg_dst = dst['prom_vta_uds']
                stk_dst = dst['stock_total']

                if avg_dst > 0:
                    deficit = ceil(cob_target * avg_dst - stk_dst)
                else:
                    deficit = 0

                if deficit <= 0:
                    continue

                uds_trans = min(exceso_disponible, deficit)
                if uds_trans < uds_min:
                    continue

                uds_transferido_total += uds_trans

                cob_src_post = (
                    round((stk_src - uds_transferido_total) / avg_src, 1)
                    if avg_src > 0 else None
                )
                cob_dst_post = (
                    round((stk_dst + uds_trans) / avg_dst, 1)
                    if avg_dst > 0 else None
                )

                rows.append({
                    'sku':             sku,
                    'nombre':          src['nombre'],
                    'categoria':       src['categoria'],
                    'tienda_origen':   src['tienda'],
                    'tienda_destino':  dst['tienda'],
                    'uds_transferir':  int(uds_trans),
                    'cob_origen_pre':  round(src['cobertura_sem'] or 0, 1),
                    'cob_destino_pre': round(dst['cobertura_sem'] or 0, 1),
                    'cob_origen_post': cob_src_post,
                    'cob_destino_post': cob_dst_post,
                    'precio_vigente':  src['precio_vigente'],
                    'motivo':          (
                        f"Origen {src['estado']} ({src['cobertura_sem']} sem) → "
                        f"Destino {dst['estado']} ({dst['cobertura_sem']} sem)"
                    ),
                })

                exceso_disponible -= uds_trans

    df_trans = pd.DataFrame(rows)
    if not df_trans.empty:
        df_trans = df_trans.sort_values('cob_destino_pre').reset_index(drop=True)
    return df_trans


# ─────────────────────────────────────────────────────────────
#  4. ACCIONES DE PRECIO
# ─────────────────────────────────────────────────────────────

def build_acciones_precio(df_cobertura, df_transferencias, df_maestro, params):
    """
    Sugerencias de descuento para SKUs en SOBRESTOCK o LIQUIDAR.

    Fix sobrestock residual: si una tienda tiene transferencia asignada,
    verifica cob_post_trans antes de suprimir la acción de precio.
    Si cob_post sigue > umbral_alto → se mantiene la acción con motivo 'residual'.

    Retorna DataFrame.
    """
    ua         = params["umbral_alto"]
    ue         = params["umbral_edad"]
    margen_min = params["margen_min"]

    # Unidades ya asignadas a transferencia por (sku, tienda_origen)
    transferidas = defaultdict(int)
    if not df_transferencias.empty:
        for _, t in df_transferencias.iterrows():
            transferidas[(t['sku'], t['tienda_origen'])] += t['uds_transferir']

    # Candidatos: SOBRESTOCK, LIQUIDAR, y sin venta (DORMIDO/MUERTO) con stock > 0
    # NUEVO SIN VENTA (0-3 meses) se excluye: son recién llegados, necesitan tiempo.
    # DORMIDO y MUERTO sí califican para acción de precio.
    _estados_sin_venta = {'DORMIDO', 'MUERTO'}
    candidatos = df_cobertura[
        df_cobertura['estado'].isin(['SOBRESTOCK', 'LIQUIDAR']) |
        ((df_cobertura['estado'].isin(_estados_sin_venta)) & (df_cobertura['stock_total'] > 0))
    ].copy()

    rows = []

    for _, r in candidatos.iterrows():
        sku    = r['sku']
        tienda = r['tienda']
        stk    = r['stock_total']
        avg    = r['prom_vta_uds']
        cob    = r['cobertura_sem']
        edad   = r['edad_semanas'] or 0
        estado = r['estado']

        # Cobertura post-transferencia
        uds_trans = transferidas.get((sku, tienda), 0)
        stk_post  = stk - uds_trans
        cob_post  = round(stk_post / avg, 1) if avg > 0 else None

        # Si post-transferencia el sobrestock se resuelve → no necesita acción de precio
        if cob_post is not None and cob_post <= ua:
            continue

        # Construir motivos
        motivos = []

        if estado in _estados_sin_venta:
            motivos.append(f"PRODUCTO {estado} — {int(stk)} uds en stock, 0 ventas en 4 semanas")
        elif estado == 'LIQUIDAR':
            motivos.append(f"LIQUIDAR — {cob} sem cobertura + {edad} sem de antigüedad")
        elif uds_trans > 0 and cob_post is not None and cob_post > ua:
            motivos.append(f"SOBRESTOCK residual post-transferencia ({cob_post} sem)")
        else:
            motivos.append(f"SOBRESTOCK — {cob} sem de cobertura")

        # Descuento base según severidad
        if estado in _estados_sin_venta:
            dscto_base = 0.30 if estado == 'DORMIDO' else 0.40  # MUERTO más agresivo
        elif estado == 'LIQUIDAR':
            dscto_base = 0.40
        elif cob is not None and cob > ua * 2:
            dscto_base = 0.30  # sobrestock severo
        else:
            dscto_base = 0.20  # sobrestock moderado

        # Amplificar por edad
        if edad > ue * 1.5:
            dscto_base = max(dscto_base, 0.50)
            motivos.append(f"Antigüedad crítica ({edad} sem) — liquidación urgente")
        elif edad > ue:
            dscto_base = max(dscto_base, 0.35)
            motivos.append(f"Producto viejo ({edad} sem)")

        # Precio mínimo que respeta margen mínimo
        # NOTA: precios incluyen IGV (18%), costo viene sin IGV
        # precio_min debe estar en la misma base (con IGV) para comparar correctamente
        IGV = 1.18
        precio_vigente = r['precio_vigente']
        costo          = r['costo']
        precio_min_exigv = costo / (1 - margen_min) if (1 - margen_min) > 0 else costo * 1.01
        precio_min       = round(precio_min_exigv * IGV, 2)  # llevar a base con IGV

        # Precio sugerido (no baja de precio_min, ambos con IGV)
        precio_sug  = max(round(precio_vigente * (1 - dscto_base), 2), precio_min)
        dscto_real  = round(1 - precio_sug / precio_vigente, 3) if precio_vigente > 0 else 0
        # Margen real: ex-IGV para que sea comparable con IMU y margen_vigente
        pv_sug_exigv = precio_sug / IGV
        margen_post  = round((pv_sug_exigv - costo) / pv_sug_exigv, 3) if pv_sug_exigv > 0 else 0

        rows.append({
            'sku':              sku,
            'nombre':           r['nombre'],
            'categoria':        r['categoria'],
            'tienda':           tienda,
            'estado':           estado,
            'stock_total':      int(stk),
            'uds_transferidas': int(uds_trans),
            'stock_post_trans': int(stk_post),
            'prom_vta_sem':     round(avg, 1),
            'cobertura_actual': cob,
            'cob_post_trans':   cob_post,
            'edad_semanas':     int(edad),
            'precio_vigente':   precio_vigente,
            'precio_sugerido':  precio_sug,
            'precio_minimo':    round(precio_min, 2),
            'dscto_sugerido':   dscto_real,
            'margen_post':      margen_post,
            'motivo':           ' | '.join(motivos),
        })

    df_precio = pd.DataFrame(rows)
    if not df_precio.empty:
        df_precio = df_precio.sort_values(
            ['estado', 'cobertura_actual'],
            ascending=[True, False]
        ).reset_index(drop=True)
    return df_precio


# ─────────────────────────────────────────────────────────────
#  5. PIVOT REPOSICIONES (formato buyer: SKU en filas, tiendas en columnas)
# ─────────────────────────────────────────────────────────────

def pivot_reposiciones(df_reposiciones, df_cobertura):
    """
    Transforma la tabla de reposiciones a formato pivotado:
      - Filas: SKU + nombre + categoría
      - Columnas: una por tienda → valor = unidades a reponer
      - Columna final: total a reponer

    Parámetros
    ----------
    df_reposiciones : DataFrame de build_reposiciones()
    df_cobertura    : DataFrame de build_cobertura() (para obtener lista completa de tiendas)

    Retorna
    -------
    DataFrame pivotado (vacío si no hay reposiciones)
    """
    if df_reposiciones.empty:
        return pd.DataFrame()

    # Todas las tiendas del dataset (para que aparezcan aunque no tengan reposición)
    todas_tiendas = sorted(df_cobertura['tienda'].unique())

    # Pivot: filas = sku, columnas = tienda, valores = a_reponer
    # Agregar stock_cd al index si existe
    idx_cols = ['sku', 'nombre', 'categoria', 'marca']
    has_stock_cd = 'stock_cd' in df_reposiciones.columns
    if has_stock_cd:
        # stock_cd es por SKU, tomar el primero (todos iguales por SKU)
        stock_cd_map = df_reposiciones.drop_duplicates('sku').set_index('sku')['stock_cd'].to_dict()

    pivot = df_reposiciones.pivot_table(
        index=idx_cols,
        columns='tienda',
        values='a_reponer',
        aggfunc='sum',
        fill_value=0
    )

    # Asegurar que todas las tiendas existan como columnas (0 si no hay reposición)
    for t in todas_tiendas:
        if t not in pivot.columns:
            pivot[t] = 0

    # Reordenar columnas alfabéticamente
    pivot = pivot[sorted(pivot.columns)]

    # Agregar total
    pivot['TOTAL'] = pivot.sum(axis=1)

    # Reset index para tener sku, nombre, categoria como columnas normales
    pivot = pivot.reset_index()

    # Agregar stock_cd como columna fija después de marca
    if has_stock_cd:
        pivot['stock_cd'] = pivot['sku'].map(stock_cd_map).fillna(0).astype(int)
        fijas = ['sku', 'nombre', 'categoria', 'marca', 'stock_cd']
        otras = [c for c in pivot.columns if c not in fijas]
        pivot = pivot[fijas + otras]
    else:
        fijas = ['sku', 'nombre', 'categoria', 'marca']
        otras = [c for c in pivot.columns if c not in fijas]
        pivot = pivot[fijas + otras]

    # Convertir columnas numéricas a int
    for col in pivot.columns:
        if col not in ['sku', 'nombre', 'categoria', 'marca']:
            pivot[col] = pivot[col].astype(int)

    # Ordenar por total descendente (más urgente primero)
    pivot = pivot.sort_values('TOTAL', ascending=False).reset_index(drop=True)

    return pivot


# ─────────────────────────────────────────────────────────────
#  6. ALERTAS IA — Aceleración / Freno / Anomalías
# ─────────────────────────────────────────────────────────────

def build_alertas(df_cobertura, df_ventas, params, df_maestro=None):
    """
    Genera alertas inteligentes basadas en tendencia de ventas.
    v3.0 — Consolidado a nivel SKU con filtros de materialidad.

    Tipos de alerta:
      ⚠️ SE DETUVO      — sem1_total = 0 pero sem2-4 tenían venta (posible quiebre)
      🔴 FRENANDO       — sem1 cayó significativamente vs promedio sem2-4
      🟢 ACELERANDO     — sem1 subió significativamente vs promedio sem2-4
      🆕 SIN TRACCIÓN   — edad 3-8 sem, vende <50% del promedio de su categoría
      🔮 RIESGO CRÍTICO — a ritmo actual, pasa a CRÍTICO en <= 3 semanas

    Cambios v3 vs v2:
      - Alertas consolidadas a nivel SKU (no por SKU×Tienda)
      - Filtro de materialidad: volumen mínimo y capital mínimo
      - Umbral dinámico de variación según volumen
      - Enriquecido con marca, temporada, capital en riesgo, n_tiendas
    """
    uc = params["umbral_critico"]
    umbral_accel_base = 0.30
    umbral_traccion = 0.50
    # Materialidad — umbrales mínimos para generar alerta
    vta_min_alerta = 3     # uds/sem totales del SKU (sum todas las tiendas)
    capital_min_alerta = 500  # S/ en stock para que valga la pena alertar

    # ── 1. Merge ventas con cobertura ──
    df = pd.merge(
        df_ventas[['sku', 'tienda', 'vta_uds_sem1', 'vta_uds_sem2',
                    'vta_uds_sem3', 'vta_uds_sem4', 'prom_vta_uds']],
        df_cobertura[['sku', 'tienda', 'nombre', 'categoria', 'estado',
                       'stock_total', 'cobertura_sem', 'edad_semanas',
                       'precio_vigente', 'costo']].drop_duplicates(),
        on=['sku', 'tienda'], how='inner'
    )

    # ── 2. Agregar a nivel SKU (datos REALES, no prorrateo) ──
    sku_agg = df.groupby('sku').agg(
        nombre=('nombre', 'first'),
        categoria=('categoria', 'first'),
        sem1_total=('vta_uds_sem1', 'sum'),
        sem2_total=('vta_uds_sem2', 'sum'),
        sem3_total=('vta_uds_sem3', 'sum'),
        sem4_total=('vta_uds_sem4', 'sum'),
        stock_total_red=('stock_total', 'sum'),
        n_tiendas=('tienda', 'nunique'),
        edad_semanas=('edad_semanas', 'first'),
        costo_unit=('costo', 'first'),
        precio_vigente=('precio_vigente', 'first'),
    ).reset_index()

    # Promedio 4 semanas (para alertas — más estable que solo sem1)
    sku_agg['prom_4sem'] = (
        sku_agg['sem1_total'] + sku_agg['sem2_total'] +
        sku_agg['sem3_total'] + sku_agg['sem4_total']
    ) / 4.0

    # Promedio sem2-4 (referencia para comparar con sem1)
    sku_agg['prom_sem2_4'] = (
        sku_agg['sem2_total'] + sku_agg['sem3_total'] + sku_agg['sem4_total']
    ) / 3.0

    # Variación: sem1 vs promedio sem2-4
    sku_agg['variacion_pct'] = sku_agg.apply(
        lambda r: round((r['sem1_total'] - r['prom_sem2_4']) / r['prom_sem2_4'], 3)
        if r['prom_sem2_4'] > 0 else (1.0 if r['sem1_total'] > 0 else 0.0),
        axis=1
    )

    # Tendencia: segunda mitad (sem1+2) vs primera mitad (sem3+4)
    sku_agg['vta_primera_mitad'] = sku_agg['sem3_total'] + sku_agg['sem4_total']
    sku_agg['vta_segunda_mitad'] = sku_agg['sem1_total'] + sku_agg['sem2_total']
    sku_agg['tendencia_pct'] = sku_agg.apply(
        lambda r: round((r['vta_segunda_mitad'] - r['vta_primera_mitad']) / r['vta_primera_mitad'], 3)
        if r['vta_primera_mitad'] > 0 else (1.0 if r['vta_segunda_mitad'] > 0 else 0.0),
        axis=1
    )

    # Capital en stock
    sku_agg['costo_unit'] = sku_agg['costo_unit'].fillna(0)
    sku_agg['capital_stock'] = sku_agg['stock_total_red'] * sku_agg['costo_unit']

    # Semanas hasta CRÍTICO (usando prom_4sem para estabilidad)
    sku_agg['sem_hasta_critico'] = sku_agg.apply(
        lambda r: round(max(0, (r['stock_total_red'] / r['prom_4sem']) - uc), 1)
        if r['prom_4sem'] > 0 else None,
        axis=1
    )

    # Estado predominante del SKU (el más grave)
    _estado_orden = {'CRÍTICO': 0, 'SIN VENTA': 1, 'PRE-CRÍTICO': 2, 'NUEVO SIN VENTA': 3,
                     'DORMIDO': 4, 'ÓPTIMO': 5, 'ALTO': 6, 'SOBRESTOCK': 7, 'LIQUIDAR': 8}
    _estado_sku = (df.assign(_ord=df['estado'].map(_estado_orden).fillna(99))
                     .sort_values('_ord')
                     .drop_duplicates('sku')[['sku', 'estado']])
    sku_agg = sku_agg.merge(_estado_sku, on='sku', how='left')

    # Enriquecer con marca y temporada desde maestro
    if df_maestro is not None:
        _enrich_cols = ['sku']
        if 'marca' in df_maestro.columns:
            _enrich_cols.append('marca')
        if 'temporada' in df_maestro.columns:
            _enrich_cols.append('temporada')
        _enrich = df_maestro[_enrich_cols].drop_duplicates('sku')
        sku_agg = sku_agg.merge(_enrich, on='sku', how='left')
    if 'marca' not in sku_agg.columns:
        sku_agg['marca'] = ''
    if 'temporada' not in sku_agg.columns:
        sku_agg['temporada'] = ''
    sku_agg['marca'] = sku_agg['marca'].fillna('').astype(str)
    sku_agg['temporada'] = sku_agg['temporada'].fillna('').astype(str)

    # Promedio por categoría (para SIN TRACCIÓN)
    cat_avg = sku_agg[sku_agg['prom_4sem'] > 0].groupby('categoria')['prom_4sem'].mean()

    # ── 3. Generar alertas a nivel SKU ──
    rows = []

    for _, r in sku_agg.iterrows():
        edad = r['edad_semanas'] or 0

        # ── Umbral dinámico: SKUs de bajo volumen necesitan mayor % ──
        prom_ref = r['prom_sem2_4'] if r['prom_sem2_4'] > 0 else r['prom_4sem']
        if prom_ref > 0:
            umbral_dinamico = max(umbral_accel_base, 1.5 / (prom_ref ** 0.5))
        else:
            umbral_dinamico = umbral_accel_base

        alertas_sku = []

        # ── ALERTA 1: SE DETUVO ──
        if (r['sem1_total'] == 0 and r['prom_sem2_4'] > vta_min_alerta and
                r['stock_total_red'] > 0):
            alertas_sku.append({
                'tipo': '⚠️ SE DETUVO',
                'severidad': 1,
                'detalle': (f"Sem1 = 0 uds, pero prom sem2-4 = {r['prom_sem2_4']:.0f} uds/sem. "
                           f"Stock en red: {int(r['stock_total_red'])} uds en {int(r['n_tiendas'])} tiendas. "
                           f"Capital: S/ {r['capital_stock']:,.0f}. Revisar quiebre o exhibición."),
            })

        # ── ALERTA 2: FRENANDO ──
        elif (r['prom_sem2_4'] > vta_min_alerta and
              r['variacion_pct'] < -umbral_dinamico and
              r['sem1_total'] > 0):
            alertas_sku.append({
                'tipo': '🔴 FRENANDO',
                'severidad': 2,
                'detalle': (f"Sem1 = {int(r['sem1_total'])} uds vs prom sem2-4 = {r['prom_sem2_4']:.0f} "
                           f"({r['variacion_pct']*100:+.0f}%). Tendencia 4 sem: {r['tendencia_pct']*100:+.0f}%. "
                           f"Stock: {int(r['stock_total_red'])} uds · S/ {r['capital_stock']:,.0f}."),
            })

        # ── ALERTA 3: ACELERANDO ──
        elif (r['prom_sem2_4'] > 0 and r['variacion_pct'] > umbral_dinamico and
              r['sem1_total'] >= vta_min_alerta):
            alertas_sku.append({
                'tipo': '🟢 ACELERANDO',
                'severidad': 3,
                'detalle': (f"Sem1 = {int(r['sem1_total'])} uds vs prom sem2-4 = {r['prom_sem2_4']:.0f} "
                           f"({r['variacion_pct']*100:+.0f}%). "
                           f"Stock: {int(r['stock_total_red'])} uds en {int(r['n_tiendas'])} tiendas. "
                           f"Verificar stock suficiente."),
            })

        # ── ALERTA 4: SIN TRACCIÓN ──
        cat = r['categoria']
        cat_prom = cat_avg.get(cat, 0)
        if (3 <= edad <= 8 and r['prom_4sem'] > 0 and cat_prom > 0 and
                r['prom_4sem'] < cat_prom * umbral_traccion and
                r['capital_stock'] >= capital_min_alerta):
            alertas_sku.append({
                'tipo': '🆕 SIN TRACCIÓN',
                'severidad': 4,
                'detalle': (f"Edad {int(edad)} sem. Vende {r['prom_4sem']:.0f} uds/sem vs "
                           f"promedio {cat} = {cat_prom:.0f} uds/sem "
                           f"({r['prom_4sem']/cat_prom*100:.0f}%). "
                           f"Capital: S/ {r['capital_stock']:,.0f}. Evaluar exhibición."),
            })

        # ── ALERTA 5: RIESGO CRÍTICO ──
        if (r['sem_hasta_critico'] is not None and 0 < r['sem_hasta_critico'] <= 3 and
                r['estado'] not in ('CRÍTICO', 'SIN VENTA', 'NUEVO SIN VENTA', 'DORMIDO') and
                r['prom_4sem'] >= vta_min_alerta):
            alertas_sku.append({
                'tipo': '🔮 RIESGO CRÍTICO',
                'severidad': 2,
                'detalle': (f"Estado: {r['estado']}. A ritmo actual ({r['prom_4sem']:.0f} uds/sem), "
                           f"pasa a CRÍTICO en ~{r['sem_hasta_critico']:.0f} sem. "
                           f"Stock: {int(r['stock_total_red'])} uds · S/ {r['capital_stock']:,.0f}."),
            })

        for alerta in alertas_sku:
            rows.append({
                'sku': r['sku'],
                'nombre': r['nombre'],
                'marca': r['marca'],
                'categoria': r['categoria'],
                'temporada': r['temporada'],
                'estado_actual': r['estado'],
                'stock_total': int(r['stock_total_red']),
                'n_tiendas': int(r['n_tiendas']),
                'capital_stock': round(r['capital_stock'], 0),
                'cobertura_sem': round(r['stock_total_red'] / r['prom_4sem'], 1) if r['prom_4sem'] > 0 else None,
                'edad_semanas': int(edad),
                'sem1_total': int(r['sem1_total']),
                'prom_sem2_4': round(r['prom_sem2_4'], 1),
                'prom_4sem': round(r['prom_4sem'], 1),
                'variacion_pct': r['variacion_pct'],
                'tendencia_pct': r['tendencia_pct'],
                'tipo_alerta': alerta['tipo'],
                'severidad': alerta['severidad'],
                'detalle': alerta['detalle'],
            })

    df_alertas = pd.DataFrame(rows)
    if not df_alertas.empty:
        df_alertas = df_alertas.sort_values(
            ['severidad', 'capital_stock'], ascending=[True, False]
        ).reset_index(drop=True)
    return df_alertas


def build_anomalias_tienda(df_cobertura, df_ventas, params):
    """
    Detecta SKUs con comportamiento anómalo en una tienda específica
    comparado con el resto de tiendas del mismo SKU.

    Lógica: si un SKU vende normalmente en la mayoría de tiendas pero
    frena o se detiene en una tienda específica, hay posible problema
    de exhibición, quiebre parcial, o factor local.

    Requiere: SKUs presentes en 2+ tiendas (mono-tienda no aplica).

    Retorna
    -------
    DataFrame de anomalías (vacío si todos los SKUs son mono-tienda)
    """
    # Materialidad: solo SKUs con volumen y capital significativos
    vta_min  = params.get('vta_min_alerta', 3)       # mín uds/sem a nivel SKU
    cap_min  = params.get('capital_min_alerta', 500)  # mín soles en stock

    df = pd.merge(
        df_ventas[['sku', 'tienda', 'prom_vta_uds']],
        df_cobertura[['sku', 'tienda', 'nombre', 'categoria', 'estado', 'stock_total', 'costo']],
        on=['sku', 'tienda'], how='inner'
    )

    # Filtrar materialidad a nivel SKU antes de iterar
    sku_agg = df.groupby('sku').agg(
        vta_total=('prom_vta_uds', 'sum'),
        stock_total_sku=('stock_total', 'sum'),
        costo_medio=('costo', 'mean'),
    )
    sku_agg['capital_sku'] = sku_agg['stock_total_sku'] * sku_agg['costo_medio']
    skus_material = set(sku_agg[
        (sku_agg['vta_total'] >= vta_min) | (sku_agg['capital_sku'] >= cap_min)
    ].index)

    rows = []

    for sku in df['sku'].unique():
        if sku not in skus_material:
            continue

        df_sku = df[df['sku'] == sku]

        # Solo aplica si hay 2+ tiendas con el mismo SKU
        if len(df_sku) < 2:
            continue

        tiendas_con_venta = df_sku[df_sku['prom_vta_uds'] > 0]
        tiendas_sin_venta = df_sku[(df_sku['prom_vta_uds'] == 0) & (df_sku['stock_total'] > 0)]

        # Caso 1: tienda sin venta mientras otras venden
        if not tiendas_con_venta.empty and not tiendas_sin_venta.empty:
            prom_otras = tiendas_con_venta['prom_vta_uds'].mean()
            for _, t in tiendas_sin_venta.iterrows():
                rows.append({
                    'sku': sku,
                    'nombre': t['nombre'],
                    'categoria': t['categoria'],
                    'tienda_anomala': t['tienda'],
                    'vta_tienda': 0,
                    'vta_prom_otras': round(prom_otras, 1),
                    'desviacion': -1.0,  # -100%
                    'stock_disponible': int(t['stock_total']),
                    'tipo': '🔴 SIN VENTA vs otras tiendas',
                    'detalle': (f"0 uds vendidas con {int(t['stock_total'])} en stock, "
                               f"mientras otras tiendas venden {prom_otras:.1f} uds/sem en promedio. "
                               f"Posible problema de exhibición."),
                })

        # Caso 2: tienda vende significativamente menos que las demás (>50% debajo)
        if len(tiendas_con_venta) >= 2:
            prom_general = tiendas_con_venta['prom_vta_uds'].mean()
            std_general  = tiendas_con_venta['prom_vta_uds'].std()

            if prom_general > 0 and std_general > 0:
                for _, t in tiendas_con_venta.iterrows():
                    desv = (t['prom_vta_uds'] - prom_general) / prom_general
                    if desv < -0.50:  # vende >50% menos que el promedio
                        rows.append({
                            'sku': sku,
                            'nombre': t['nombre'],
                            'categoria': t['categoria'],
                            'tienda_anomala': t['tienda'],
                            'vta_tienda': round(t['prom_vta_uds'], 1),
                            'vta_prom_otras': round(prom_general, 1),
                            'desviacion': round(desv, 3),
                            'stock_disponible': int(t['stock_total']),
                            'tipo': '🟡 VENTA BAJA vs otras tiendas',
                            'detalle': (f"Vende {t['prom_vta_uds']:.1f} uds/sem vs promedio {prom_general:.1f} "
                                       f"({desv*100:+.0f}%). Revisar posición, facing, o stock de tallas."),
                        })

    df_anomalias = pd.DataFrame(rows)
    if not df_anomalias.empty:
        df_anomalias = df_anomalias.sort_values('desviacion').reset_index(drop=True)
    return df_anomalias


# ─────────────────────────────────────────────────────────────
#  6.5 ALERTAS POR TIENDA — Reportes accionables para personal de piso
# ─────────────────────────────────────────────────────────────

# Helpers de lenguaje coloquial.
# Lógica rediseñada según Franco (abril 2026):
#   Disparador: cobertura_sem ≥ alertas_tienda_cob_min (default 16)
#              Y edad_semanas ≥ alertas_tienda_edad_min (default 2, por leadtime).
#   Cada SKU que dispare muestra DOS revisiones simultáneas (sin jerarquía):
#     1) Exhibición — siempre.
#     2) Precio — solo si pct_descuento > 0, mensaje según tipo_precio:
#          MD1 → verificar etiqueta Pathfinder.
#          PTR → colocar marca precio.
#          MTR → no alerta de precio (precio de origen sin modificación).

def _producto_display(row):
    """Arma un nombre de producto legible: '<Marca> — <Nombre>' o solo nombre."""
    marca  = str(row.get('marca', '') or '').strip()
    nombre = str(row.get('nombre', '') or '').strip()
    if marca and marca.lower() != 'nan':
        return f"{marca} — {nombre}"
    return nombre


def _mensaje_oportunidad(stock_actual, vta_sem, cob_actual, stock_transito):
    """
    Mensaje coloquial para 🟢 Oportunidad.
    Usa cobertura real para hablar en días/semanas, no jerga.
    """
    # Cobertura en semanas, clamp para mensaje natural
    if cob_actual is None or cob_actual != cob_actual:  # NaN check
        cob_actual = 0
    if stock_transito > 0:
        sufijo = f" Ya viene reposición en camino ({int(stock_transito)} uds)."
    else:
        sufijo = ""
    if cob_actual < 0.5:
        horizonte = "Se puede acabar en los próximos días."
    elif cob_actual < 1:
        horizonte = "Se va a acabar esta semana."
    elif cob_actual < 2:
        horizonte = f"Alcanza para ~{cob_actual:.1f} semana."
    else:
        horizonte = f"Alcanza para ~{cob_actual:.1f} semanas."
    return (f"Se vendieron {int(round(vta_sem))} uds la semana pasada y quedan {int(stock_actual)} "
            f"en tienda. {horizonte}{sufijo}")


def _mensaje_lento(stock_total, edad, capital_parado, estado):
    """Mensaje coloquial para 🔴 Mercadería lenta."""
    if estado in ('SIN VENTA', 'NUEVO SIN VENTA', 'DORMIDO', 'MUERTO'):
        cuerpo = f"No ha registrado venta en las últimas 4 semanas. Tienes {int(stock_total)} uds en tienda"
    else:
        cuerpo = f"Lleva {int(edad)} semanas en catálogo y tienes {int(stock_total)} uds en tienda"
    return f"{cuerpo}. Capital inmovilizado: S/ {capital_parado:,.0f}."


def _accion_sugerida(estado, edad, stock_transito, umbral_edad_liquidar=20):
    """
    Decide la acción concreta según estado + edad + tránsito.
    Lógica explícita, documentada en el plan aprobado.
    """
    if estado == 'CRÍTICO':
        if stock_transito > 0:
            return "Refuerza exhibición en vitrina principal. Reposición en camino."
        return "Pide reposición urgente al CD."
    if estado == 'LIQUIDAR' or (edad is not None and edad >= umbral_edad_liquidar):
        return "Consulta al buyer si aplicamos marcado de precio."
    if estado == 'SOBRESTOCK':
        return "Reubica al piso de venta, zona de alto tráfico."
    if estado in ('SIN VENTA', 'NUEVO SIN VENTA', 'DORMIDO', 'MUERTO'):
        return "Reubica al piso de venta. Si no mueve, consulta al buyer."
    # Fallback defensivo
    return "Revisa con el buyer."


def _mensaje_exhibicion(stock_total, edad, cob):
    """Bloque de revisión de exhibición — siempre presente cuando dispara alerta."""
    return (f"Tienes {int(stock_total)} uds en tienda ({int(edad)} sem en catálogo, "
            f"{cob:.0f} sem de cobertura). Revisa que esté bien exhibido, "
            f"no guardado en bodega ni oculto en percha.")


def _mensaje_precio(tipo_precio, pct_descuento, precio_vigente, precio_blanco):
    """
    Bloque de revisión de precio — solo si hay descuento.
    Retorna None si no aplica (MTR, sin descuento, o tipo desconocido).
    """
    tp = str(tipo_precio or '').strip().upper()
    dscto = float(pct_descuento or 0)

    # Sin descuento o precio de origen → no hay nada que comunicar
    if dscto <= 0 or tp == 'MTR' or tp == '':
        return None

    pct_txt = f"{dscto * 100:.0f}%"
    precio_txt = (f"S/ {precio_vigente:.0f}"
                  f" (antes S/ {precio_blanco:.0f})") if precio_blanco and precio_blanco > 0 \
                 else f"S/ {precio_vigente:.0f}"

    if tp == 'MD1':
        return (f"Descuento {pct_txt} cargado — precio vigente {precio_txt}. "
                f"Verifica que el producto tenga etiqueta Pathfinder con el precio actualizado.")
    if tp == 'PTR':
        return (f"Descuento {pct_txt} cargado — precio vigente {precio_txt}. "
                f"Coloca marca precio visible con el descuento en el producto o percha.")
    # Tipo de precio desconocido pero con descuento — aviso genérico
    return (f"Descuento {pct_txt} cargado — precio vigente {precio_txt}. "
            f"Verifica que el descuento esté comunicado correctamente al cliente.")


def build_alertas_tienda(df_cobertura, df_reposiciones, df_acciones_precio,
                         df_stock, params):
    """
    Genera alertas accionables POR TIENDA para personal de piso.

    Disparador (rediseño abril-2026):
      cobertura_sem ≥ alertas_tienda_cob_min (default 16)
      Y edad_semanas ≥ alertas_tienda_edad_min (default 2)

    Cobertura crítica (<6 sem) y bajo stock (<8 sem) NO van al reporte de tienda —
    esas son alertas del buyer y viven en otras tabs del motor.

    Por cada SKU que dispare se generan 2 bloques de revisión:
      • Exhibición — siempre.
      • Precio — solo si pct_descuento > 0 y tipo_precio ∈ {MD1, PTR}.
        MTR no genera alerta de precio (precio de origen, sin modificación).

    Los ítems se ordenan por capital parado (stock × costo) desc, con tope
    alertas_tienda_top_n (default 30) para no saturar el WhatsApp.

    Parámetros relevantes:
      alertas_tienda_cob_min  : cobertura mín (sem) para disparar. Default 16.
      alertas_tienda_edad_min : edad mín (sem), cubre leadtime. Default 2.
      alertas_tienda_top_n    : tope de ítems por tienda. Default 30.

    Retorna
    -------
    dict[str, dict] — clave = nombre de tienda, valor = payload listo para renderizar.
      {
        "<tienda>": {
          "tienda": str,
          "fecha_corte": str,
          "resumen": {n_items, capital_parado_sol, n_con_descuento},
          "items": [
            {
              sku, producto, marca, nombre, categoria,
              stock_actual, edad_semanas, cobertura_sem,
              capital_parado_sol,
              tipo_precio, pct_descuento, precio_vigente, precio_blanco,
              mensaje_exhibicion,  # str, siempre
              mensaje_precio,      # str | None
            },
            ...
          ]
        },
        ...
      }
    """
    p        = {**DEFAULT_PARAMS, **(params or {})}
    COB_MIN  = float(p.get('alertas_tienda_cob_min', 16))
    EDAD_MIN = float(p.get('alertas_tienda_edad_min', 2))
    TOP_N    = int(p.get('alertas_tienda_top_n', 30))

    # Fecha de corte (del archivo de stock)
    try:
        fecha_corte = str(df_stock['fecha_corte'].iloc[0])
    except Exception:
        fecha_corte = ''

    if df_cobertura is None or df_cobertura.empty:
        return {}

    df = df_cobertura.copy()

    # Columnas opcionales (según formato de plantilla)
    if 'tipo_precio' not in df.columns:
        df['tipo_precio'] = ''
    df['tipo_precio']     = df['tipo_precio'].fillna('').astype(str).str.upper().str.strip()
    df['pct_descuento']   = pd.to_numeric(df.get('pct_descuento', 0), errors='coerce').fillna(0)
    df['precio_vigente']  = pd.to_numeric(df.get('precio_vigente', 0), errors='coerce').fillna(0)
    df['precio_blanco']   = pd.to_numeric(df.get('precio_blanco', 0), errors='coerce').fillna(0)
    df['cobertura_sem']   = pd.to_numeric(df.get('cobertura_sem'), errors='coerce')
    df['edad_semanas']    = pd.to_numeric(df.get('edad_semanas'), errors='coerce').fillna(0)

    # Filtro disparador: cob >= COB_MIN Y edad >= EDAD_MIN
    # NOTA: cobertura_sem puede ser NaN (SIN VENTA). Esos NO entran aquí — son
    # otra pathología que el buyer maneja aparte.
    mask = (df['cobertura_sem'].fillna(-1) >= COB_MIN) & (df['edad_semanas'] >= EDAD_MIN)
    df_trigger = df[mask].copy()

    if df_trigger.empty:
        # Igual devolvemos estructura por todas las tiendas, aunque vacía
        out = {}
        for tienda in sorted(df['tienda'].dropna().unique().tolist()):
            out[tienda] = {
                'tienda': tienda,
                'fecha_corte': fecha_corte,
                'resumen': {'n_items': 0, 'capital_parado_sol': 0.0, 'n_con_descuento': 0},
                'items': [],
            }
        return out

    # Capital parado para ordenamiento (stock × costo — ya viene en cobertura)
    df_trigger['capital_parado'] = df_trigger['stock_valor_costo'].fillna(0)

    out = {}
    tiendas = sorted(df['tienda'].dropna().unique().tolist())

    for tienda in tiendas:
        g = df_trigger[df_trigger['tienda'] == tienda].copy()
        if len(g):
            g = g.sort_values('capital_parado', ascending=False).head(TOP_N)

        items = []
        for _, r in g.iterrows():
            stock_total = int(r.get('stock_total', 0) or 0)
            edad        = int(r['edad_semanas'])
            cob         = float(r['cobertura_sem']) if r['cobertura_sem'] == r['cobertura_sem'] else 0.0
            tipo_precio = str(r.get('tipo_precio', '') or '')
            pct_dscto   = float(r['pct_descuento'])

            msg_exh = _mensaje_exhibicion(stock_total, edad, cob)
            msg_pre = _mensaje_precio(
                tipo_precio=tipo_precio,
                pct_descuento=pct_dscto,
                precio_vigente=float(r['precio_vigente']),
                precio_blanco=float(r['precio_blanco']),
            )

            items.append({
                'sku':                r['sku'],
                'producto':           _producto_display(r),
                'marca':              str(r.get('marca', '') or ''),
                'nombre':             str(r.get('nombre', '') or ''),
                'categoria':          str(r.get('categoria', '') or ''),
                'stock_actual':       stock_total,
                'edad_semanas':       edad,
                'cobertura_sem':      round(cob, 1),
                'capital_parado_sol': float(r['capital_parado']),
                'tipo_precio':        tipo_precio,
                'pct_descuento':      round(pct_dscto, 3),
                'precio_vigente':     float(r['precio_vigente']),
                'precio_blanco':      float(r['precio_blanco']),
                'mensaje_exhibicion': msg_exh,
                'mensaje_precio':     msg_pre,  # None si no aplica
            })

        resumen = {
            'n_items':            len(items),
            'capital_parado_sol': round(sum(x['capital_parado_sol'] for x in items), 2),
            'n_con_descuento':   sum(1 for x in items if x['mensaje_precio']),
        }

        out[tienda] = {
            'tienda':      tienda,
            'fecha_corte': fecha_corte,
            'resumen':     resumen,
            'items':       items,
        }

    return out


# ─────────────────────────────────────────────────────────────
#  6b. ALERTAS VENTA CERO — SKUs con stock relevante y venta 0
# ─────────────────────────────────────────────────────────────

def build_alertas_venta_cero(df_cobertura, df_stock, params):
    """
    Genera alertas por tienda de SKUs con venta = 0 la semana pasada
    y stock a costo relevante (> umbral).

    Criterios:
      - prom_vta_uds == 0 (venta semana pasada = 0)
      - stock_total > 0 (tiene stock en tienda)
      - stock_valor_costo >= CAPITAL_MIN (capital parado relevante)
      - Top N SKUs por marca, ordenados por capital parado

    Agrupación: Tienda → Marca → Categoría (línea) → SKU

    Parámetros relevantes:
      alertas_vta_cero_capital_min : S/ mín de stock a costo. Default 1000.
      alertas_vta_cero_top_por_marca : tope de SKUs por marca×tienda. Default 15.

    Retorna
    -------
    dict[str, dict] — clave = nombre de tienda, valor = payload.
      {
        "<tienda>": {
          "tienda": str,
          "fecha_corte": str,
          "resumen": {n_skus, n_marcas, capital_parado_total},
          "por_marca": {
            "<marca>": {
              "n_skus": int,
              "capital": float,
              "items": [
                {sku, nombre, categoria, stock_total, stock_valor_costo,
                 edad_semanas, estado, pct_descuento, precio_vigente,
                 mensaje},
                ...
              ]
            },
            ...
          }
        },
        ...
      }
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    CAPITAL_MIN = float(p.get('alertas_vta_cero_capital_min', 1000))
    TOP_POR_MARCA = int(p.get('alertas_vta_cero_top_por_marca', 15))

    # Fecha de corte
    try:
        fecha_corte = str(df_stock['fecha_corte'].iloc[0])
    except Exception:
        fecha_corte = ''

    if df_cobertura is None or df_cobertura.empty:
        return {}

    df = df_cobertura.copy()

    # Asegurar columnas numéricas
    df['prom_vta_uds'] = pd.to_numeric(df.get('prom_vta_uds', 0), errors='coerce').fillna(0)
    df['stock_total'] = pd.to_numeric(df.get('stock_total', 0), errors='coerce').fillna(0)
    df['stock_valor_costo'] = pd.to_numeric(df.get('stock_valor_costo', 0), errors='coerce').fillna(0)
    df['edad_semanas'] = pd.to_numeric(df.get('edad_semanas', 0), errors='coerce').fillna(0)
    df['pct_descuento'] = pd.to_numeric(df.get('pct_descuento', 0), errors='coerce').fillna(0)
    df['precio_vigente'] = pd.to_numeric(df.get('precio_vigente', 0), errors='coerce').fillna(0)

    # Filtro: venta 0 + stock > 0 + capital >= umbral
    mask = (
        (df['prom_vta_uds'] == 0) &
        (df['stock_total'] > 0) &
        (df['stock_valor_costo'] >= CAPITAL_MIN)
    )
    df_trigger = df[mask].copy()

    if df_trigger.empty:
        out = {}
        for tienda in sorted(df['tienda'].dropna().unique().tolist()):
            out[tienda] = {
                'tienda': tienda,
                'fecha_corte': fecha_corte,
                'resumen': {'n_skus': 0, 'n_marcas': 0, 'capital_parado_total': 0.0},
                'por_marca': {},
            }
        return out

    out = {}
    tiendas = sorted(df['tienda'].dropna().unique().tolist())

    for tienda in tiendas:
        g = df_trigger[df_trigger['tienda'] == tienda].copy()

        por_marca = {}
        if not g.empty:
            for marca, gm in g.groupby('marca'):
                marca_str = str(marca or '').strip()
                if not marca_str:
                    continue
                # Top N por capital parado dentro de la marca
                gm_top = gm.sort_values('stock_valor_costo', ascending=False).head(TOP_POR_MARCA)

                items = []
                for _, r in gm_top.iterrows():
                    pct_d = float(r['pct_descuento'])
                    msg = "Revisar exhibición y comunicación de precio." if pct_d > 0 else "Revisar exhibición del producto."
                    items.append({
                        'sku': r['sku'],
                        'nombre': str(r.get('nombre', '') or ''),
                        'categoria': str(r.get('categoria', '') or ''),
                        'stock_total': int(r['stock_total']),
                        'stock_valor_costo': float(r['stock_valor_costo']),
                        'edad_semanas': int(r['edad_semanas']),
                        'estado': str(r.get('estado', '') or ''),
                        'pct_descuento': round(pct_d, 3),
                        'precio_vigente': float(r['precio_vigente']),
                        'mensaje': msg,
                    })

                por_marca[marca_str] = {
                    'n_skus': len(items),
                    'capital': round(sum(x['stock_valor_costo'] for x in items), 2),
                    'items': items,
                }

        n_skus_total = sum(m['n_skus'] for m in por_marca.values())
        capital_total = sum(m['capital'] for m in por_marca.values())

        out[tienda] = {
            'tienda': tienda,
            'fecha_corte': fecha_corte,
            'resumen': {
                'n_skus': n_skus_total,
                'n_marcas': len(por_marca),
                'capital_parado_total': round(capital_total, 2),
            },
            'por_marca': dict(sorted(por_marca.items(), key=lambda x: x[1]['capital'], reverse=True)),
        }

    return out


# ─────────────────────────────────────────────────────────────
#  7. BRIEFING EJECUTIVO — Resumen semanal automático
# ─────────────────────────────────────────────────────────────

def build_briefing(df_cobertura, df_ventas, summary, params):
    """
    Briefing ejecutivo semanal — resumen accionable para el buyer.

    Secciones:
      1. Situación general (KPIs macro)
      2. Top tiendas con mayor cobertura promedio (sobrestock sistémico)
      3. Top 5 SKUs por marca — mejores ventas (4 sem consolidado + sem 1)
      4. Top 5 SKUs por marca — mejor contribución (4 sem consolidado + sem 1)

    Retorna
    -------
    dict con claves:
      items       : list de dicts {prioridad, icono, titulo, mensaje}
      tablas      : dict de DataFrames para mostrar en expanders
    """
    items = []
    tablas = {}
    s = summary
    IGV = 1.18

    # ── 1. SITUACIÓN GENERAL ──────────────────────────────────
    total = s['total_combos']
    pct_critico = round(s['n_critico'] / total * 100, 0) if total > 0 else 0
    pct_sobre = round((s['n_sobrestock'] + s['n_liquidar']) / total * 100, 0) if total > 0 else 0
    stock_valor = df_cobertura['stock_valor_costo'].sum()

    situacion = (f"{total} combinaciones SKU×Tienda analizadas. "
                 f"Stock total valorizado: S/ {stock_valor:,.0f} a costo.")
    if pct_critico > 20:
        situacion += f" ⚠️ {pct_critico:.0f}% del portafolio en estado CRÍTICO — situación de desabastecimiento."
    elif pct_critico > 0:
        situacion += f" {s['n_critico']} combo(s) en CRÍTICO requieren reposición inmediata."
    if pct_sobre > 30:
        situacion += f" {pct_sobre:.0f}% en SOBRESTOCK/LIQUIDAR — capital inmovilizado importante."

    items.append({
        'prioridad': 0,
        'icono': '📊',
        'titulo': 'Situación General',
        'mensaje': situacion,
    })

    # ── 2. TIENDAS CON MAYOR COBERTURA ───────────────────────
    # Cobertura real por tienda: stock_total_tienda / vta_sem1_tienda
    df_con_venta = df_cobertura[df_cobertura['prom_vta_uds'] > 0].copy()
    if not df_con_venta.empty:
        tienda_cob = df_con_venta.groupby('tienda').agg(
            stock_total=('stock_total', 'sum'),
            vta_sem1=('prom_vta_uds', 'sum'),
            stock_valor=('stock_valor_costo', 'sum'),
            n_skus=('sku', 'nunique'),
            n_sobrestock=('estado', lambda x: (x.isin(['SOBRESTOCK', 'LIQUIDAR'])).sum()),
        ).reset_index()
        tienda_cob['cobertura_sem'] = (tienda_cob['stock_total'] / tienda_cob['vta_sem1']).round(1)
        tienda_cob = tienda_cob.sort_values('cobertura_sem', ascending=False).head(10)
        tienda_cob['pct_sobrestock'] = (tienda_cob['n_sobrestock'] / tienda_cob['n_skus'] * 100).round(0).astype(int)

        top1 = tienda_cob.iloc[0]
        items.append({
            'prioridad': 1,
            'icono': '🏪',
            'titulo': 'Tiendas con mayor cobertura',
            'mensaje': (f"La tienda con mayor cobertura es *{top1['tienda']}* "
                       f"({top1['cobertura_sem']:.0f} sem), con {top1['pct_sobrestock']}% de sus SKUs "
                       f"en sobrestock. Stock valorizado: S/ {top1['stock_valor']:,.0f}."),
        })
        tablas['tiendas_cobertura'] = tienda_cob[['tienda', 'cobertura_sem', 'stock_total',
            'vta_sem1', 'stock_valor', 'n_skus', 'n_sobrestock', 'pct_sobrestock']].rename(columns={
            'tienda': 'Tienda', 'cobertura_sem': 'Cobertura (sem)',
            'stock_total': 'Stock Total', 'vta_sem1': 'Vta Sem1',
            'stock_valor': 'Stock (S/ costo)', 'n_skus': 'SKUs activos',
            'n_sobrestock': 'En Sobrestock', 'pct_sobrestock': '% Sobrestock',
        })

    # ── 3/4. TOP SKUs POR MARCA — VENTAS Y CONTRIBUCIÓN ──────
    # Consolidar ventas a nivel SKU (sumar todas las tiendas)
    # Merge ventas con maestro para tener marca, nombre, precio, costo
    df_venta_sku = df_ventas.groupby('sku').agg(
        vta_sem1=('vta_uds_sem1', 'sum'),
        vta_sem2=('vta_uds_sem2', 'sum'),
        vta_sem3=('vta_uds_sem3', 'sum'),
        vta_sem4=('vta_uds_sem4', 'sum'),
    ).reset_index()
    df_venta_sku['vta_4sem'] = (df_venta_sku['vta_sem1'] + df_venta_sku['vta_sem2']
                                + df_venta_sku['vta_sem3'] + df_venta_sku['vta_sem4'])

    # Maestro por SKU (desduplicado en cobertura)
    maestro_cols = ['sku', 'nombre', 'marca', 'categoria', 'precio_vigente', 'costo']
    df_maestro_unico = df_cobertura[
        [c for c in maestro_cols if c in df_cobertura.columns]
    ].drop_duplicates('sku')

    df_rank = pd.merge(df_venta_sku, df_maestro_unico, on='sku', how='left')
    df_rank['marca'] = df_rank['marca'].fillna('SIN MARCA').str.strip()
    df_rank = df_rank[df_rank['marca'] != ''].copy()

    # Venta en soles
    df_rank['vta_sol_sem1'] = (df_rank['vta_sem1'] * df_rank['precio_vigente']).round(0)
    df_rank['vta_sol_4sem'] = (df_rank['vta_4sem'] * df_rank['precio_vigente']).round(0)

    # Contribución = (precio_exIGV - costo) × unidades
    df_rank['contrib_sem1'] = ((df_rank['precio_vigente'] / IGV - df_rank['costo'])
                                * df_rank['vta_sem1']).round(0)
    df_rank['contrib_4sem'] = ((df_rank['precio_vigente'] / IGV - df_rank['costo'])
                                * df_rank['vta_4sem']).round(0)

    marcas = sorted(df_rank['marca'].unique())
    top_n = 5

    # ── 3. TOP VENTAS POR MARCA ──
    tablas_venta = {}
    for marca in marcas:
        dm = df_rank[df_rank['marca'] == marca].copy()
        if dm.empty or dm['vta_4sem'].sum() == 0:
            continue
        top_4sem = dm.nlargest(top_n, 'vta_4sem')[
            ['sku', 'nombre', 'categoria', 'vta_4sem', 'vta_sol_4sem', 'vta_sem1', 'vta_sol_sem1']
        ].rename(columns={
            'sku': 'SKU', 'nombre': 'Producto', 'categoria': 'Línea',
            'vta_4sem': 'Uds 4sem', 'vta_sol_4sem': 'S/ 4sem',
            'vta_sem1': 'Uds Sem1', 'vta_sol_sem1': 'S/ Sem1',
        })
        tablas_venta[marca] = top_4sem
    tablas['top_venta_por_marca'] = tablas_venta

    # Resumen card: marca más vendedora
    if tablas_venta:
        venta_por_marca = df_rank.groupby('marca')['vta_sol_4sem'].sum().sort_values(ascending=False)
        top_marca = venta_por_marca.index[0]
        top_vta = venta_por_marca.iloc[0]
        items.append({
            'prioridad': 2,
            'icono': '🏆',
            'titulo': 'Top ventas por marca (4 semanas)',
            'mensaje': (f"Marca líder en venta: *{top_marca}* con S/ {top_vta:,.0f} en 4 semanas. "
                       f"Revisa el detalle por marca para ver los top 5 SKUs en venta y contribución."),
        })

    # ── 4. TOP CONTRIBUCIÓN POR MARCA ──
    tablas_contrib = {}
    for marca in marcas:
        dm = df_rank[df_rank['marca'] == marca].copy()
        if dm.empty or dm['contrib_4sem'].sum() == 0:
            continue
        top_4sem = dm.nlargest(top_n, 'contrib_4sem')[
            ['sku', 'nombre', 'categoria', 'contrib_4sem', 'contrib_sem1', 'vta_4sem']
        ].rename(columns={
            'sku': 'SKU', 'nombre': 'Producto', 'categoria': 'Línea',
            'contrib_4sem': 'Contrib S/ 4sem', 'contrib_sem1': 'Contrib S/ Sem1',
            'vta_4sem': 'Uds 4sem',
        })
        tablas_contrib[marca] = top_4sem
    tablas['top_contrib_por_marca'] = tablas_contrib

    # Resumen card: marca más rentable
    if tablas_contrib:
        contrib_por_marca = df_rank.groupby('marca')['contrib_4sem'].sum().sort_values(ascending=False)
        top_marca_c = contrib_por_marca.index[0]
        top_contrib = contrib_por_marca.iloc[0]
        items.append({
            'prioridad': 3,
            'icono': '💰',
            'titulo': 'Top contribución por marca (4 semanas)',
            'mensaje': (f"Marca líder en contribución: *{top_marca_c}* con S/ {top_contrib:,.0f}. "
                       f"Expande para ver el top 5 por marca."),
        })

    # ── 5. SELL-THROUGH POR MARCA ────────────────────────────────
    # ST = vta_4sem / (stock_total + vta_4sem) — a nivel SKU total (sin tienda)
    df_st = df_rank.copy()
    if not df_st.empty and 'vta_4sem' in df_st.columns:
        # Stock total por SKU (sumado en todas las tiendas)
        stock_por_sku = df_cobertura.groupby('sku')['stock_total'].sum().reset_index()
        df_st = pd.merge(df_st, stock_por_sku, on='sku', how='left')
        df_st['stock_total'] = df_st['stock_total'].fillna(0)
        df_st['recibido_aprox'] = df_st['stock_total'] + df_st['vta_4sem']

        st_marca = df_st.groupby('marca').agg(
            vta_uds_4sem=('vta_4sem', 'sum'),
            stock_total=('stock_total', 'sum'),
            vta_sol_4sem=('vta_sol_4sem', 'sum'),
            n_skus=('sku', 'nunique'),
        ).reset_index()
        st_marca['recibido'] = st_marca['stock_total'] + st_marca['vta_uds_4sem']
        st_marca['sell_through'] = np.where(
            st_marca['recibido'] > 0,
            (st_marca['vta_uds_4sem'] / st_marca['recibido'] * 100).round(1),
            0
        )
        st_marca = st_marca.sort_values('sell_through', ascending=False)

        best_st = st_marca.iloc[0]
        worst_st = st_marca.iloc[-1]
        items.append({
            'prioridad': 4,
            'icono': '📈',
            'titulo': 'Sell-Through por marca (4 semanas)',
            'mensaje': (f"Mejor ST: *{best_st['marca']}* ({best_st['sell_through']:.0f}%). "
                       f"Peor ST: *{worst_st['marca']}* ({worst_st['sell_through']:.0f}%). "
                       f"Expande para ver todas las marcas."),
        })
        tablas['sell_through_marca'] = st_marca[
            ['marca', 'sell_through', 'vta_uds_4sem', 'vta_sol_4sem', 'stock_total', 'n_skus']
        ].rename(columns={
            'marca': 'Marca', 'sell_through': 'ST %',
            'vta_uds_4sem': 'Vta Uds 4sem', 'vta_sol_4sem': 'Vta S/ 4sem',
            'stock_total': 'Stock Uds', 'n_skus': 'SKUs',
        })

    # ── 6. SKUs DORMIDOS POR TIENDA (stock>12, vta=0 última sem) ─
    df_dormidos = df_cobertura[
        (df_cobertura['prom_vta_uds'] == 0) &
        (df_cobertura['stock_total'] > 12)
    ].copy()
    if not df_dormidos.empty:
        dormidos_tienda = df_dormidos.groupby('tienda').agg(
            n_dormidos=('sku', 'nunique'),
            capital_parado=('stock_valor_costo', 'sum'),
            stock_uds=('stock_total', 'sum'),
        ).reset_index().sort_values('capital_parado', ascending=False)

        top_dorm = dormidos_tienda.iloc[0]
        total_capital_dorm = dormidos_tienda['capital_parado'].sum()
        items.append({
            'prioridad': 5,
            'icono': '💤',
            'titulo': f'SKUs dormidos: S/ {total_capital_dorm:,.0f} capital parado',
            'mensaje': (f"{len(df_dormidos)} SKUs con stock >12 uds y 0 ventas última semana. "
                       f"Tienda más afectada: *{top_dorm['tienda']}* "
                       f"({int(top_dorm['n_dormidos'])} SKUs, S/ {top_dorm['capital_parado']:,.0f})."),
        })
        tablas['dormidos_por_tienda'] = dormidos_tienda.rename(columns={
            'tienda': 'Tienda', 'n_dormidos': 'SKUs Dormidos',
            'capital_parado': 'Capital Parado S/', 'stock_uds': 'Stock Uds',
        })

    # ── 7. EFICIENCIA DEL MARKDOWN ────────────────────────────
    # SKUs con descuento vigente que siguen sin vender → descuento no sirve
    df_md = df_cobertura[
        (df_cobertura['pct_descuento'].fillna(0) > 0) &
        (df_cobertura['stock_total'] > 0)
    ].copy()
    if not df_md.empty:
        n_con_dscto = len(df_md)
        md_sin_venta = df_md[df_md['prom_vta_uds'] == 0]
        n_sin_venta = len(md_sin_venta)
        capital_md_sin_venta = md_sin_venta['stock_valor_costo'].sum()
        pct_fallo = round(n_sin_venta / n_con_dscto * 100, 0) if n_con_dscto > 0 else 0

        if n_sin_venta > 0:
            items.append({
                'prioridad': 6,
                'icono': '🏷️',
                'titulo': f'Markdown sin efecto: {n_sin_venta} SKUs ({pct_fallo:.0f}%)',
                'mensaje': (f"De {n_con_dscto} SKUs con descuento activo, {n_sin_venta} no generaron "
                           f"venta última semana (S/ {capital_md_sin_venta:,.0f} capital parado). "
                           f"Revisar si el descuento está comunicado en tienda o si necesita más rebaja."),
            })

            # Top 10 markdown fallidos por capital
            md_top = md_sin_venta.nlargest(10, 'stock_valor_costo')[
                ['sku', 'nombre', 'marca', 'tienda', 'stock_total',
                 'pct_descuento', 'precio_vigente', 'stock_valor_costo']
            ].copy()
            md_top['pct_descuento'] = (md_top['pct_descuento'] * 100).round(0).astype(int)
            tablas['markdown_sin_efecto'] = md_top.rename(columns={
                'sku': 'SKU', 'nombre': 'Producto', 'marca': 'Marca',
                'tienda': 'Tienda', 'stock_total': 'Stock',
                'pct_descuento': 'Dscto %', 'precio_vigente': 'Precio S/',
                'stock_valor_costo': 'Capital S/',
            })

    # ── 8. CONCENTRACIÓN DE VENTA (PARETO) ────────────────────
    if not df_rank.empty and df_rank['vta_sol_4sem'].sum() > 0:
        df_pareto = df_rank[df_rank['vta_sol_4sem'] > 0].sort_values('vta_sol_4sem', ascending=False).copy()
        total_venta = df_pareto['vta_sol_4sem'].sum()
        n_total = len(df_pareto)

        df_pareto['vta_acum'] = df_pareto['vta_sol_4sem'].cumsum()
        df_pareto['pct_acum'] = (df_pareto['vta_acum'] / total_venta * 100).round(1)

        # ¿Cuántos SKUs hacen el 80%?
        n_80 = (df_pareto['pct_acum'] <= 80).sum() + 1
        pct_skus_80 = round(n_80 / n_total * 100, 0)

        items.append({
            'prioridad': 7,
            'icono': '📊',
            'titulo': f'Pareto: {pct_skus_80:.0f}% de SKUs genera el 80% de venta',
            'mensaje': (f"Los top {n_80} SKUs (de {n_total} con venta) concentran el 80% "
                       f"de la venta total (S/ {total_venta:,.0f}). "
                       f"El resto son cola larga con bajo aporte individual."),
        })

        # Top 20 SKUs que más venden
        tablas['pareto_top20'] = df_pareto.head(20)[
            ['sku', 'nombre', 'marca', 'categoria', 'vta_4sem', 'vta_sol_4sem', 'pct_acum']
        ].rename(columns={
            'sku': 'SKU', 'nombre': 'Producto', 'marca': 'Marca',
            'categoria': 'Línea', 'vta_4sem': 'Uds 4sem',
            'vta_sol_4sem': 'Vta S/ 4sem', 'pct_acum': '% Acum',
        })

    items.sort(key=lambda x: x['prioridad'])
    return {'items': items, 'tablas': tablas}


# ─────────────────────────────────────────────────────────────
#  AGING ANALYSIS — Ventana de Mercadería
# ─────────────────────────────────────────────────────────────

_MARCAS_PROPIAS_SET = {'MARQUIS', 'NAVIGATA', 'CACHAREL', 'SPAVALDI',
                       'OSCAR DE LA RENTA', 'US POLO', 'NAUTICA'}

def _classify_aging_action(row):
    """
    Clasifica un SKU×Tienda en una de 4 acciones de aging.

    Criterios (orden de prioridad):
      LIQUIDAR        — >26 sem, ST <2%, ya con 30%+ dscto
      NEGOCIAR PROV.  — marca tercera, >16 sem, capital >S/50K (evalúa a nivel marca después)
      MARKDOWN PROG.  — 16-26 sem, ST 2-5%, dscto <40%, capital >S/5K
      EMPUJE A PISO   — 8-16 sem, ST >5%, dscto <10%, capital >S/10K

    Retorna (accion, color, mensaje, sugerencia).
    """
    edad  = float(row.get('edad_semanas', 0) or 0)
    stk   = float(row.get('stock_total', 0) or 0)
    avg   = float(row.get('prom_vta_uds', 0) or 0)
    dscto = float(row.get('pct_descuento', 0) or 0)
    cap   = float(row.get('stock_valor_costo', 0) or 0)
    marca = str(row.get('marca', '')).strip().upper()
    tienda = str(row.get('tienda', '')).strip()
    margen = float(row.get('margen_efectivo', 0) or 0)
    es_tercera = marca not in _MARCAS_PROPIAS_SET

    # Sell-through rate (proxy: avg / (avg + stock))
    st_rate = avg / (avg + stk) if (avg + stk) > 0 else 0

    # ── LIQUIDAR: >26 sem, ST <2%, ya con descuento 30%+ ──
    if edad > 26 and st_rate < 0.02:
        if es_tercera:
            sug = f"Cortar pérdida: negociar devolución o nota de crédito con proveedor."
        else:
            sug = f"Enviar a outlet, saldo o destruir. No reasignar a tienda."
        msg = (f"{int(edad)} sem · S/{cap:,.0f} · {dscto*100:.0f}% dscto · ST {st_rate*100:.1f}%")
        return ('LIQUIDAR', '#ef4444', msg, sug)

    # ── NEGOCIAR PROVEEDOR: tercera, >16 sem (capital se evalúa a nivel marca) ──
    if es_tercera and edad > 16:
        sug = f"Plan de salida con proveedor: devolución, swap temporada o descuento conjunto."
        msg = (f"{int(edad)} sem · S/{cap:,.0f} · ST {st_rate*100:.1f}%"
               + (f" · {dscto*100:.0f}% dscto" if dscto > 0.05 else ""))
        return ('NEGOCIAR', '#f97316', msg, sug)

    # ── MARKDOWN PROGRESIVO: 16-26 sem, ST 2-5%, dscto <40%, cap >S/5K ──
    if edad > 16 and edad <= 26 and st_rate >= 0.02 and st_rate < 0.05 and dscto < 0.40 and cap > 5000:
        dscto_sugerido = min(0.50, max(dscto + 0.10, 0.20))
        sug = (f"{'Subir' if dscto > 0.05 else 'Aplicar'} a {dscto_sugerido*100:.0f}%, evaluar en 2 sem.")
        msg = (f"{int(edad)} sem · S/{cap:,.0f} · {dscto*100:.0f}% dscto · ST {st_rate*100:.1f}%")
        return ('MARKDOWN', '#f59e0b', msg, sug)

    # ── Marca propia >26 sem catchall → LIQUIDAR o MARKDOWN ──
    # (BUG FIX: sin esto, propias viejas con ST>=2% o dscto>=40% caían a OK)
    if not es_tercera and edad > 26:
        if st_rate < 0.02:
            sug = "Enviar a outlet o saldo. No reasignar a tienda."
            msg = f"{int(edad)} sem · S/{cap:,.0f} · {dscto*100:.0f}% dscto · ST {st_rate*100:.1f}%"
            return ('LIQUIDAR', '#ef4444', msg, sug)
        else:
            dscto_sugerido = min(0.50, max(dscto + 0.10, 0.30))
            sug = f"Markdown agresivo a {dscto_sugerido*100:.0f}%. Producto viejo con algo de tracción."
            msg = f"{int(edad)} sem · S/{cap:,.0f} · {dscto*100:.0f}% dscto · ST {st_rate*100:.1f}%"
            return ('MARKDOWN', '#f59e0b', msg, sug)

    # ── Marcas propias 16-26 sem sin sell-through → MARKDOWN ──
    if not es_tercera and edad > 16 and st_rate < 0.05 and dscto < 0.40:
        dscto_sugerido = min(0.50, max(dscto + 0.10, 0.20))
        sug = (f"{'Subir' if dscto > 0.05 else 'Aplicar'} descuento a {dscto_sugerido*100:.0f}%, última ventana.")
        msg = (f"{int(edad)} sem · S/{cap:,.0f} · {dscto*100:.0f}% dscto · ST {st_rate*100:.1f}%")
        return ('MARKDOWN', '#f59e0b', msg, sug)

    # ── EMPUJE A PISO: 8-16 sem, ST >5%, sin/poco dscto, margen positivo ──
    if edad >= 8 and edad <= 16 and st_rate > 0.05 and dscto < 0.10:
        # Si margen es negativo, no empujar — necesita markdown o revisión de precio
        if margen < 0:
            sug = f"⚠️ Margen negativo ({margen*100:.1f}%). Revisar precio antes de empujar."
            msg = (f"{int(edad)} sem · S/{cap:,.0f} · margen {margen*100:.1f}% · ST {st_rate*100:.1f}%")
            return ('MARKDOWN', '#f59e0b', msg, sug)
        sug = f"Revisar exhibición en piso. Validar ubicación. Aún vendible a precio lleno."
        msg = (f"{int(edad)} sem · S/{cap:,.0f} · {dscto*100:.0f}% dscto · ST {st_rate*100:.1f}%")
        return ('EMPUJE', '#84cc16', msg, sug)

    # ── EMPUJE relajado: 8-16 sem, algo de venta, sin descuento fuerte ──
    if edad >= 8 and edad <= 16 and (st_rate > 0.03 or dscto < 0.05):
        # Si margen es negativo, reclasificar como MARKDOWN
        if margen < 0:
            sug = f"⚠️ Margen negativo ({margen*100:.1f}%). Aplicar markdown o renegociar costo."
            msg = (f"{int(edad)} sem · S/{cap:,.0f} · margen {margen*100:.1f}% · ST {st_rate*100:.1f}%")
            return ('MARKDOWN', '#f59e0b', msg, sug)
        sug = f"Validar que esté exhibido. Considerar WhatsApp a tienda para empuje."
        msg = (f"{int(edad)} sem · S/{cap:,.0f} · {dscto*100:.0f}% dscto · ST {st_rate*100:.1f}%")
        return ('EMPUJE', '#84cc16', msg, sug)

    # ── Sin acción requerida (fresco o ya cubierto) ──
    return ('OK', '#10b981', '', '')


def build_aging_analysis(df_cob, params):
    """
    Construye el análisis de Ventana de Mercadería.

    Clasifica cada SKU×Tienda en 4 acciones de aging:
      EMPUJE    — 8-16 sem, aún rescatable con exhibición
      MARKDOWN  — 16-26 sem, necesita descuento progresivo
      NEGOCIAR  — tercera >16 sem con capital alto, reunión con proveedor
      LIQUIDAR  — >26 sem, ST <2%, cortar pérdida

    Retorna dict con:
      df_aging        — DataFrame completo con columna accion_aging
      por_categoria   — resumen agrupado por categoría × rango_edad
      por_accion      — resumen agrupado por tipo de acción
      kpis            — KPIs ejecutivos de aging
    """
    df = df_cob.copy()

    # Clasificar cada fila (ahora retorna 4 valores)
    _results = df.apply(_classify_aging_action, axis=1, result_type='expand')
    df['accion_aging']    = _results[0]
    df['color_aging']     = _results[1]
    df['mensaje_aging']   = _results[2]
    df['sugerencia_aging'] = _results[3]

    # Marca propia vs tercera
    df['tipo_marca'] = np.where(
        df['marca'].str.upper().str.strip().isin(_MARCAS_PROPIAS_SET),
        'Marca Propia', 'Marca Tercera'
    )

    # Rango de edad para stacked bar
    bins  = [0, 4, 8, 16, 26, 999]
    labels = ['0-4 sem', '4-8 sem', '8-16 sem', '16-26 sem', '26+ sem']
    df['rango_edad_aging'] = pd.cut(
        df['edad_semanas'], bins=bins, labels=labels, right=True, include_lowest=True
    )

    # Rango de descuento para filtro
    dscto_bins = [-0.01, 0.001, 0.20, 0.40, 1.01]
    dscto_labels = ['Sin descuento', '1-20%', '20-40%', '40%+']
    df['rango_descuento'] = pd.cut(
        df['pct_descuento'].fillna(0), bins=dscto_bins, labels=dscto_labels,
        right=True, include_lowest=True
    )

    # ── Resumen por categoría × rango_edad (para stacked bar) ──
    por_categoria = df.groupby(
        ['categoria', 'rango_edad_aging'], observed=True
    ).agg(
        capital=('stock_valor_costo', 'sum'),
        n_skus=('sku', 'nunique'),
        stock_uds=('stock_total', 'sum'),
    ).reset_index()

    # ── Resumen por acción ──
    _acciones_activas = df[df['accion_aging'] != 'OK']
    por_accion = _acciones_activas.groupby('accion_aging').agg(
        n_combos=('sku', 'count'),
        n_skus=('sku', 'nunique'),
        capital=('stock_valor_costo', 'sum'),
    ).reset_index()

    # ── KPIs ejecutivos ──
    _capital_total = df['stock_valor_costo'].sum()
    _capital_viejo = df.loc[df['edad_semanas'] > 16, 'stock_valor_costo'].sum()
    _valid_edad = df['edad_semanas'].notna() & df['stock_valor_costo'].notna()
    _edad_prom = np.average(df.loc[_valid_edad, 'edad_semanas'], weights=df.loc[_valid_edad, 'stock_valor_costo']) if _valid_edad.sum() > 0 and _capital_total > 0 else 0
    _n_riesgo = ((df['edad_semanas'] >= 8) & (df['edad_semanas'] <= 16)).sum()

    # ── Distribución de capital por rango de edad (para barra horizontal) ──
    _dist_edad = {}
    for _lbl in labels:
        _mask_rango = df['rango_edad_aging'] == _lbl
        _dist_edad[_lbl] = float(df.loc[_mask_rango, 'stock_valor_costo'].sum())

    kpis = {
        'capital_viejo':     float(_capital_viejo),
        'pct_viejo':         float(_capital_viejo / _capital_total * 100) if _capital_total > 0 else 0,
        'edad_prom_pond':    float(_edad_prom),
        'n_zona_riesgo':     int(_n_riesgo),
        'capital_total':     float(_capital_total),
        'dist_edad':         _dist_edad,
        'n_empuje':          int((df['accion_aging'] == 'EMPUJE').sum()),
        'capital_empuje':    float(df.loc[df['accion_aging'] == 'EMPUJE', 'stock_valor_costo'].sum()),
        'n_markdown':        int((df['accion_aging'] == 'MARKDOWN').sum()),
        'capital_markdown':  float(df.loc[df['accion_aging'] == 'MARKDOWN', 'stock_valor_costo'].sum()),
        'n_negociar':        int((df['accion_aging'] == 'NEGOCIAR').sum()),
        'n_negociar_marcas': int(df.loc[df['accion_aging'] == 'NEGOCIAR', 'marca'].nunique()),
        'capital_negociar':  float(df.loc[df['accion_aging'] == 'NEGOCIAR', 'stock_valor_costo'].sum()),
        'n_liquidar':        int((df['accion_aging'] == 'LIQUIDAR').sum()),
        'capital_liquidar':  float(df.loc[df['accion_aging'] == 'LIQUIDAR', 'stock_valor_costo'].sum()),
    }

    # ── Top ejemplos por acción (para Capa 3 de UI) ──
    # Sell-through proxy para deduplicación a nivel SKU
    _st_col = 'prom_vta_uds'
    top_ejemplos = {}
    for _acc in ['EMPUJE', 'MARKDOWN', 'NEGOCIAR', 'LIQUIDAR']:
        _df_acc = df[df['accion_aging'] == _acc].copy()
        if _df_acc.empty:
            top_ejemplos[_acc] = []
            continue

        if _acc == 'NEGOCIAR':
            # Agrupar por MARCA (no por SKU) — el negocio es con el proveedor
            _neg_marca = _df_acc.groupby('marca').agg(
                n_modelos=('sku', 'nunique'),
                capital=('stock_valor_costo', 'sum'),
                edad_prom=('edad_semanas', 'mean'),
                st_prom=('prom_vta_uds', 'mean'),
                stock_total=('stock_total', 'sum'),
            ).reset_index()
            # Solo marcas con capital significativo (>S/50K acumulado)
            _neg_marca = _neg_marca[_neg_marca['capital'] > 50000]
            _neg_marca = _neg_marca.sort_values('capital', ascending=False).head(5)
            _ejemplos = []
            for _, _r in _neg_marca.iterrows():
                _st_r = _r['st_prom'] / (_r['st_prom'] + _r['stock_total'] / max(_r['n_modelos'], 1)) if (_r['st_prom'] + _r['stock_total'] / max(_r['n_modelos'], 1)) > 0 else 0
                _ejemplos.append({
                    'nombre': f"{_r['marca']} — {int(_r['n_modelos'])} modelos acumulados",
                    'detalle': f"Prom {int(_r['edad_prom'])} sem · S/{_r['capital']:,.0f} total · ST {_st_r*100:.1f}%",
                    'sugerencia': "Plan de salida con proveedor",
                    'capital': float(_r['capital']),
                })
            top_ejemplos[_acc] = _ejemplos
        else:
            # Top SKUs por capital (deduplicar a nivel SKU, tomar la tienda principal)
            _sku_agg = _df_acc.groupby(['sku', 'nombre', 'marca']).agg(
                capital=('stock_valor_costo', 'sum'),
                edad=('edad_semanas', 'first'),
                dscto=('pct_descuento', 'first'),
                prom_vta=('prom_vta_uds', 'sum'),
                stock=('stock_total', 'sum'),
                tienda_top=('tienda', 'first'),
                sugerencia=('sugerencia_aging', 'first'),
            ).reset_index()
            # Filtro de materialidad según tipo de acción
            if _acc == 'EMPUJE':
                _sku_agg = _sku_agg[_sku_agg['capital'] > 10000]
            elif _acc == 'MARKDOWN':
                _sku_agg = _sku_agg[_sku_agg['capital'] > 5000]
            _sku_agg = _sku_agg.sort_values('capital', ascending=False).head(3)
            _ejemplos = []
            for _, _r in _sku_agg.iterrows():
                _st_r = _r['prom_vta'] / (_r['prom_vta'] + _r['stock']) if (_r['prom_vta'] + _r['stock']) > 0 else 0
                _tienda_str = _r['tienda_top'] if isinstance(_r['tienda_top'], str) else ''
                _loc = f" — {_tienda_str}" if _tienda_str and _sku_agg.shape[0] > 0 else ""
                _n = str(_r['nombre'])[:40]
                _ejemplos.append({
                    'nombre': f"{_n}{_loc}",
                    'detalle': f"{int(_r['edad'])} sem · S/{_r['capital']:,.0f} · {_r['dscto']*100:.0f}% dscto · ST {_st_r*100:.1f}%",
                    'sugerencia': str(_r['sugerencia']),
                    'capital': float(_r['capital']),
                })
            top_ejemplos[_acc] = _ejemplos

    # Conteos a nivel SKU (no SKU×tienda)
    _n_skus_acc = {}
    _n_marcas_neg = 0
    for _acc in ['EMPUJE', 'MARKDOWN', 'LIQUIDAR']:
        _n_skus_acc[_acc] = int(df.loc[df['accion_aging'] == _acc, 'sku'].nunique())
    # Para NEGOCIAR contar marcas con >S/50K
    _df_neg = df[df['accion_aging'] == 'NEGOCIAR']
    if not _df_neg.empty:
        _neg_m = _df_neg.groupby('marca')['stock_valor_costo'].sum()
        _n_marcas_neg = int((_neg_m > 50000).sum())
    kpis['n_skus_empuje'] = _n_skus_acc.get('EMPUJE', 0)
    kpis['n_skus_markdown'] = _n_skus_acc.get('MARKDOWN', 0)
    kpis['n_skus_liquidar'] = _n_skus_acc.get('LIQUIDAR', 0)
    kpis['n_marcas_negociar'] = _n_marcas_neg

    return {
        'df_aging':       df,
        'por_categoria':  por_categoria,
        'por_accion':     por_accion,
        'kpis':           kpis,
        'top_ejemplos':   top_ejemplos,
    }


# ─────────────────────────────────────────────────────────────
#  ANÁLISIS POR VENTANA DE COMPRA (EMBARQUES A-F)
# ─────────────────────────────────────────────────────────────

_VENTANA_ORDER = ['A', 'B', 'C', 'D', 'E', 'F', 'NOOS']
_VENTANA_LABELS = {
    'A': 'A — Avance temprano',
    'B': 'B — Avance',
    'C': 'C — Core temporada',
    'D': 'D — Core medio',
    'E': 'E — Reacción',
    'F': 'F — Tardío',
    'NOOS': 'NOOS — Continuidad',
}

def build_embarque_analysis(df_cob, params):
    """
    Análisis de performance por ventana de compra (embarques A-F).

    Para cada ventana calcula:
      - sell_through %
      - cobertura promedio ponderada
      - capital invertido (stock_valor_costo)
      - venta acumulada (4 semanas × costo)
      - descuento promedio ponderado
      - edad promedio ponderada
      - retorno por semana de exposición

    Clasifica con semáforo ajustado por antigüedad esperada de la ventana.
    Genera recomendaciones contextuales por ventana.

    Retorna dict con:
      df_embarque     — DataFrame enriquecido con ventana_compra
      por_ventana     — resumen agrupado por ventana
      kpis            — KPIs ejecutivos globales
      recomendaciones — dict ventana → texto de recomendación
      top_problemas   — dict ventana → DataFrame top 5 SKUs problemáticos
    """
    if 'ventana_compra' not in df_cob.columns:
        return None

    df = df_cob.copy()

    # Asegurar que ventana_compra es string limpio
    df['ventana_compra'] = df['ventana_compra'].fillna('NOOS').astype(str).str.strip().str.upper()

    # Calcular venta acumulada 4 semanas a costo (proxy de retorno)
    # NOTA: df_cob usa 'prom_vta_uds', no 'prom_vta_sem'
    _vta_col = 'prom_vta_uds' if 'prom_vta_uds' in df.columns else 'prom_vta_sem'
    if _vta_col in df.columns and 'costo' in df.columns:
        df['vta_4sem_costo'] = df[_vta_col].fillna(0) * 4 * df['costo'].fillna(0)
    else:
        df['vta_4sem_costo'] = 0

    # Sell-through proxy: uds vendidas / (vendidas + stock)
    # prom_vta × edad_semanas ≈ uds vendidas acumuladas
    df['uds_vendidas_est'] = df[_vta_col].fillna(0) * df['edad_semanas'].clip(lower=1) if _vta_col in df.columns else 0
    df['sell_through'] = np.where(
        (df['uds_vendidas_est'] + df['stock_total']) > 0,
        df['uds_vendidas_est'] / (df['uds_vendidas_est'] + df['stock_total']),
        0
    )

    # ── Resumen por ventana ──
    ventanas_presentes = [v for v in _VENTANA_ORDER if v in df['ventana_compra'].values]

    rows = []
    for v in ventanas_presentes:
        mask = df['ventana_compra'] == v
        dv = df[mask]
        _capital = dv['stock_valor_costo'].sum()
        _stock_uds = dv['stock_total'].sum()
        _vta_costo = dv['vta_4sem_costo'].sum()
        _uds_vendidas = dv['uds_vendidas_est'].sum()
        _n_skus = dv['sku'].nunique()

        # Promedios ponderados por capital
        _w = dv['stock_valor_costo']
        _w_sum = _w.sum()
        _cob_prom = np.average(dv['cobertura_sem'].fillna(0), weights=_w) if _w_sum > 0 else 0
        _edad_prom = np.average(dv['edad_semanas'].fillna(0), weights=_w) if _w_sum > 0 else 0
        _dscto_prom = np.average(dv['pct_descuento'].fillna(0), weights=_w) if _w_sum > 0 else 0
        _st_prom = np.average(dv['sell_through'], weights=_w) if _w_sum > 0 else 0

        # Retorno por semana de exposición = venta_costo / (semanas_prom × capital)
        _retorno_sem = (_vta_costo / (_edad_prom * _capital)) if (_edad_prom > 0 and _capital > 0) else 0

        rows.append({
            'ventana': v,
            'label': _VENTANA_LABELS.get(v, v),
            'n_skus': _n_skus,
            'n_combos': int(mask.sum()),
            'capital': float(_capital),
            'stock_uds': int(_stock_uds),
            'vta_4sem_costo': float(_vta_costo),
            'uds_vendidas_est': float(_uds_vendidas),
            'sell_through': float(_st_prom),
            'cobertura_prom': float(_cob_prom),
            'edad_prom': float(_edad_prom),
            'descuento_prom': float(_dscto_prom),
            'retorno_sem': float(_retorno_sem),
        })

    por_ventana = pd.DataFrame(rows)

    # ── Semáforo por ventana ──
    # Benchmark: sell-through esperado ajustado por edad promedio de la ventana
    # Lógica: a más edad, más sell-through debería tener
    def _semaforo(row):
        st = row['sell_through']
        edad = row['edad_prom']
        v = row['ventana']
        if v == 'NOOS':
            # NOOS es continuidad, evaluar solo cobertura
            if row['cobertura_prom'] > 12:
                return 'rojo'
            elif row['cobertura_prom'] > 8:
                return 'amarillo'
            return 'verde'
        # ST esperado: ~3% por semana acumulado (benchmark conservador retail moda)
        st_esperado = min(0.03 * edad, 0.85)
        ratio = st / st_esperado if st_esperado > 0 else 0
        if ratio >= 0.9:
            return 'verde'
        elif ratio >= 0.6:
            return 'amarillo'
        return 'rojo'

    if not por_ventana.empty:
        por_ventana['semaforo'] = por_ventana.apply(_semaforo, axis=1)

    # ── KPIs globales ──
    _capital_total = df['stock_valor_costo'].sum()
    _ventanas_activas = por_ventana[por_ventana['ventana'] != 'NOOS']
    _mejor = _ventanas_activas.loc[_ventanas_activas['retorno_sem'].idxmax()] if not _ventanas_activas.empty else None
    _peor = _ventanas_activas.loc[_ventanas_activas['retorno_sem'].idxmin()] if not _ventanas_activas.empty else None
    _n_rojos = int((por_ventana['semaforo'] == 'rojo').sum()) if 'semaforo' in por_ventana.columns else 0

    kpis = {
        'n_ventanas': int(len(ventanas_presentes)),
        'capital_total': float(_capital_total),
        'mejor_ventana': _mejor['ventana'] if _mejor is not None else '',
        'mejor_retorno': float(_mejor['retorno_sem']) if _mejor is not None else 0,
        'peor_ventana': _peor['ventana'] if _peor is not None else '',
        'peor_retorno': float(_peor['retorno_sem']) if _peor is not None else 0,
        'n_rojos': _n_rojos,
    }

    # ── Recomendaciones por ventana ──
    recomendaciones = {}
    for _, row in por_ventana.iterrows():
        v = row['ventana']
        st = row['sell_through']
        dscto = row['descuento_prom']
        cob = row['cobertura_prom']
        capital = row['capital']
        sem = row.get('semaforo', 'verde')

        if v == 'NOOS':
            if cob > 12:
                recomendaciones[v] = (
                    f"⚠️ Continuidad con cobertura excesiva ({cob:.0f} sem). "
                    f"Capital: S/{capital:,.0f}. Revisar niveles de reposición — "
                    f"posible sobrecompra en básicos. Reducir próxima OC."
                )
            else:
                recomendaciones[v] = (
                    f"✅ Continuidad saludable. Cobertura {cob:.0f} sem, "
                    f"capital S/{capital:,.0f}. Mantener ritmo de reposición actual."
                )
        elif v in ('A', 'B'):
            # Avance — debería estar más vendido
            if sem == 'rojo':
                if dscto > 0.30:
                    recomendaciones[v] = (
                        f"🔴 Ventana {v} (avance) con ST bajo ({st:.0%}) a pesar de {dscto:.0%} descuento. "
                        f"Capital atrapado: S/{capital:,.0f}. Mercadería que no responde a precio. "
                        f"Candidata a liquidación agresiva o devolución si es tercera."
                    )
                else:
                    recomendaciones[v] = (
                        f"🔴 Ventana {v} (avance) con ST {st:.0%} — debería estar más vendida a esta altura. "
                        f"Capital: S/{capital:,.0f}. Aplicar markdown inmediato. "
                        f"Cada semana adicional destruye margen."
                    )
            elif sem == 'amarillo':
                recomendaciones[v] = (
                    f"🟡 Ventana {v} (avance) en zona de atención. ST {st:.0%}, "
                    f"capital S/{capital:,.0f}. Reforzar exhibición y evaluar "
                    f"descuento selectivo en SKUs más lentos."
                )
            else:
                recomendaciones[v] = (
                    f"✅ Ventana {v} (avance) con buen sell-through ({st:.0%}). "
                    f"Capital restante: S/{capital:,.0f}. Validar patrón para repetir en próxima temporada."
                )
        elif v in ('C', 'D'):
            # Core
            if sem == 'rojo':
                recomendaciones[v] = (
                    f"🔴 Ventana {v} (core) con ST bajo ({st:.0%}). Capital: S/{capital:,.0f}. "
                    f"Sobrestock en core de temporada — redistribuir a tiendas con cobertura baja "
                    f"antes de aplicar descuento. Revisar mix de compra."
                )
            elif sem == 'amarillo':
                recomendaciones[v] = (
                    f"🟡 Ventana {v} (core) necesita empuje. ST {st:.0%}, "
                    f"capital S/{capital:,.0f}. Priorizar transferencias "
                    f"a tiendas con alta rotación."
                )
            else:
                recomendaciones[v] = (
                    f"✅ Ventana {v} (core) vendiendo bien ({st:.0%}). "
                    f"Capital: S/{capital:,.0f}. Mantener estrategia."
                )
        else:
            # E, F — tardío/reactivo
            if sem == 'rojo':
                recomendaciones[v] = (
                    f"🔴 Ventana {v} (tardía) con bajo rendimiento ({st:.0%}). "
                    f"Capital: S/{capital:,.0f}. Siendo compra reactiva, revisar "
                    f"criterio de selección — ¿qué falló en la decisión de compra?"
                )
            elif st > 0.5:
                recomendaciones[v] = (
                    f"✅ Ventana {v} (tardía) con excelente respuesta ({st:.0%}). "
                    f"Documentar para replicar — esto valida buying reactivo. "
                    f"Capital: S/{capital:,.0f}."
                )
            else:
                recomendaciones[v] = (
                    f"🟡 Ventana {v} (tardía) con tiempo aún por madurar. ST {st:.0%}, "
                    f"capital S/{capital:,.0f}. Monitorear semanalmente."
                )

    # ── Top 5 SKUs problemáticos por ventana ──
    top_problemas = {}
    for v in ventanas_presentes:
        mask = df['ventana_compra'] == v
        dv = df[mask].copy()
        # Score: peor sell-through × mayor capital = más urgente
        dv['_score_problema'] = (1 - dv['sell_through']) * dv['stock_valor_costo']
        top = dv.nlargest(5, '_score_problema')[[
            'sku', 'nombre', 'marca', 'tienda', 'stock_total',
            'stock_valor_costo', 'sell_through', 'cobertura_sem',
            'pct_descuento', 'edad_semanas'
        ]].copy()
        top_problemas[v] = top

    return {
        'df_embarque':      df,
        'por_ventana':      por_ventana,
        'kpis':             kpis,
        'recomendaciones':  recomendaciones,
        'top_problemas':    top_problemas,
    }


# ─────────────────────────────────────────────────────────────
#  PREDISTRIBUCIÓN: Retenidos en CD + Gaps de distribución
# ─────────────────────────────────────────────────────────────

def build_predistribucion(df_maestro, df_stock, params):
    """
    Detecta problemas de predistribución en marcas propias.

    Alerta 1 — Retenidos en CD:
      SKUs con 100% del stock en CD y NADA en tiendas (stock=0 AND on_order=0
      en TODAS las tiendas). Producto llegó sin predistribución.

    Alerta 2 — Gaps de distribución:
      SKUs que están en algunas tiendas pero faltan en otras según la matriz
      configurable de Línea × Tienda.

    Parámetros
    ----------
    df_maestro : DataFrame con sku, marca, categoria (=Línea), stock_cd
    df_stock   : DataFrame con sku, tienda, stock_uds, stock_transito
    params     : dict de parámetros (incluye ruta a config_matriz_tiendas.json)

    Retorna
    -------
    dict con:
      'retenidos_cd'       : DataFrame de SKUs 100% en CD
      'gaps_distribucion'  : DataFrame de SKUs con tiendas faltantes
      'kpis'               : dict con conteos y métricas de resumen
    """
    import json
    import os

    _MARCAS_PROPIAS = {'MARQUIS', 'NAVIGATA', 'CACHAREL', 'SPAVALDI',
                       'OSCAR DE LA RENTA', 'US POLO', 'NAUTICA'}

    # ── Filtrar solo marcas propias ──────────────────────────
    if 'marca' not in df_maestro.columns:
        return {'retenidos_cd': pd.DataFrame(), 'gaps_distribucion': pd.DataFrame(),
                'kpis': {'n_retenidos': 0, 'n_gaps': 0, 'uds_retenidas_cd': 0}}

    maestro_propias = df_maestro[
        df_maestro['marca'].str.upper().str.strip().isin(_MARCAS_PROPIAS)
    ].copy()

    if maestro_propias.empty:
        return {'retenidos_cd': pd.DataFrame(), 'gaps_distribucion': pd.DataFrame(),
                'kpis': {'n_retenidos': 0, 'n_gaps': 0, 'uds_retenidas_cd': 0}}

    skus_propias = set(maestro_propias['sku'].unique())

    # ── Excluir tiendas virtuales y no-físicas del análisis ──
    _excluir_tiendas = {'Tienda Virtual', 'Tienda Virtual PI', 'Ventas Corporativas',
                        'FSF Mac LO', 'Boutique BBB', 'Asia',
                        'Estacional Chiclayo', 'Estacional Trujillo', 'Estacional VES',
                        'Mac Larco', 'Mac Plaza Norte',
                        'Outlet Plaza Norte', 'Outlet San Isidro'}
    stock_propias = df_stock[
        (df_stock['sku'].isin(skus_propias)) &
        (~df_stock['tienda'].isin(_excluir_tiendas))
    ].copy()

    # ── ALERTA 1: Retenidos en CD ────────────────────────────
    # SKUs con stock_cd > 0 y NADA en ninguna tienda (ni stock ni tránsito)
    stock_por_sku = stock_propias.groupby('sku').agg(
        total_stock_tiendas=('stock_uds', 'sum'),
        total_transito_tiendas=('stock_transito', 'sum'),
        n_tiendas_con_stock=('stock_uds', lambda x: (x > 0).sum()),
    ).reset_index()

    # Join con maestro para tener stock_cd, marca, categoria, nombre
    retenidos = pd.merge(
        stock_por_sku, maestro_propias[['sku', 'nombre', 'marca', 'categoria',
                                         'stock_cd', 'costo', 'precio_vigente']],
        on='sku', how='left'
    )

    # Condición Alerta 1: stock_cd > 0, cero en tiendas, cero en tránsito
    if 'stock_cd' in retenidos.columns:
        retenidos['stock_cd'] = pd.to_numeric(retenidos['stock_cd'], errors='coerce').fillna(0)
        mask_retenido = (
            (retenidos['stock_cd'] > 0) &
            (retenidos['total_stock_tiendas'] == 0) &
            (retenidos['total_transito_tiendas'] == 0)
        )
        df_retenidos = retenidos[mask_retenido].copy()
        df_retenidos['capital_retenido'] = df_retenidos['stock_cd'] * df_retenidos['costo']
        df_retenidos = df_retenidos.sort_values('capital_retenido', ascending=False).reset_index(drop=True)
        df_retenidos = df_retenidos[['sku', 'nombre', 'marca', 'categoria',
                                      'stock_cd', 'costo', 'precio_vigente',
                                      'capital_retenido']].copy()
    else:
        df_retenidos = pd.DataFrame()

    # ── ALERTA 2: Gaps de distribución ───────────────────────
    # Cargar la matriz por MARCA (fuente principal: config_marca_tiendas.json)
    # Fallback a matriz por Línea (config_matriz_tiendas.json) si la marca no está
    _base = os.path.dirname(os.path.abspath(__file__))

    # Matriz por marca (fuente principal)
    marca_path = params.get('matriz_marca_path', None)
    if marca_path is None:
        marca_path = os.path.join(_base, 'config_marca_tiendas.json')
    matriz_marca = {}
    if os.path.exists(marca_path):
        with open(marca_path, 'r', encoding='utf-8') as f:
            matriz_marca = json.load(f)

    # Matriz por línea (fallback)
    linea_path = params.get('matriz_tiendas_path', None)
    if linea_path is None:
        linea_path = os.path.join(_base, 'config_matriz_tiendas.json')
    matriz_linea = {}
    if os.path.exists(linea_path):
        with open(linea_path, 'r', encoding='utf-8') as f:
            matriz_linea = json.load(f)

    if not matriz_marca and not matriz_linea:
        # Sin ninguna matriz no podemos detectar gaps
        return {
            'retenidos_cd': df_retenidos,
            'gaps_distribucion': pd.DataFrame(),
            'kpis': {
                'n_retenidos': len(df_retenidos),
                'n_gaps': 0,
                'uds_retenidas_cd': int(df_retenidos['stock_cd'].sum()) if not df_retenidos.empty else 0,
                'capital_retenido': float(df_retenidos['capital_retenido'].sum()) if not df_retenidos.empty else 0,
            }
        }

    # Para Alerta 2: SKUs que SÍ tienen presencia en al menos 1 tienda
    # (los de Alerta 1 ya están 100% retenidos, no aplican aquí)
    skus_retenidos = set(df_retenidos['sku']) if not df_retenidos.empty else set()

    # Presencia real por SKU: tiendas donde stock_uds > 0 OR stock_transito > 0
    stock_presencia = stock_propias[
        (stock_propias['stock_uds'] > 0) | (stock_propias['stock_transito'] > 0)
    ].copy()

    presencia_por_sku = stock_presencia.groupby('sku')['tienda'].apply(set).to_dict()

    # Construir gaps
    gaps_rows = []
    for _, row in maestro_propias.iterrows():
        sku = row['sku']
        if sku in skus_retenidos:
            continue  # ya está en Alerta 1

        marca_sku = str(row.get('marca', '') or '').strip().upper()
        linea = str(row.get('categoria', '') or '').strip().upper()

        # Determinar tiendas esperadas: primero por marca, luego fallback por línea
        tiendas_esperadas = set()
        _fuente = ''
        if marca_sku in matriz_marca:
            tiendas_esperadas = set(matriz_marca[marca_sku].get('tiendas', []))
            _fuente = 'marca'
        elif linea in matriz_linea:
            tiendas_esperadas = set(matriz_linea[linea].get('tiendas', []))
            _fuente = 'línea'

        if not tiendas_esperadas:
            continue

        tiendas_presentes = presencia_por_sku.get(sku, set())
        if not tiendas_presentes:
            # Sin stock ni tránsito en ninguna tienda — pero stock_cd podría ser 0
            stock_cd_val = pd.to_numeric(row.get('stock_cd', 0), errors='coerce') or 0
            if stock_cd_val <= 0:
                continue  # Sin stock en ningún lado, nada que distribuir
            # Tiene CD pero no calificó como retenido (edge case), incluir
            tiendas_faltantes = tiendas_esperadas
        else:
            tiendas_faltantes = tiendas_esperadas - tiendas_presentes

        if not tiendas_faltantes:
            continue  # Está en todas las tiendas esperadas

        # Calcular % de cobertura de distribución
        n_esperadas = len(tiendas_esperadas)
        n_presentes = len(tiendas_presentes & tiendas_esperadas)  # solo contar las que están en la matriz
        pct_cobertura = n_presentes / n_esperadas if n_esperadas > 0 else 0

        stock_cd_val = pd.to_numeric(row.get('stock_cd', 0), errors='coerce') or 0

        gaps_rows.append({
            'sku': sku,
            'nombre': row.get('nombre', ''),
            'marca': row.get('marca', ''),
            'categoria': row.get('categoria', ''),
            'edad_semanas': int(row.get('edad_semanas', 0)),
            'stock_cd': stock_cd_val,
            'costo': row.get('costo', 0),
            'precio_vigente': row.get('precio_vigente', 0),
            'n_tiendas_esperadas': n_esperadas,
            'n_tiendas_presentes': n_presentes,
            'n_tiendas_faltantes': len(tiendas_faltantes),
            'pct_cobertura_dist': round(pct_cobertura, 2),
            'tiendas_faltantes': ', '.join(sorted(tiendas_faltantes)),
        })

    df_gaps = pd.DataFrame(gaps_rows) if gaps_rows else pd.DataFrame()

    if not df_gaps.empty:
        # Priorizar: más faltantes primero, luego por stock CD disponible
        df_gaps = df_gaps.sort_values(
            ['n_tiendas_faltantes', 'stock_cd'],
            ascending=[False, False]
        ).reset_index(drop=True)

    # ── KPIs ─────────────────────────────────────────────────
    kpis = {
        'n_retenidos':       len(df_retenidos),
        'uds_retenidas_cd':  int(df_retenidos['stock_cd'].sum()) if not df_retenidos.empty else 0,
        'capital_retenido':  float(df_retenidos['capital_retenido'].sum()) if not df_retenidos.empty else 0,
        'n_gaps':            len(df_gaps),
        'n_gaps_con_cd':     int((df_gaps['stock_cd'] > 0).sum()) if not df_gaps.empty else 0,
        'prom_cobertura_dist': float(df_gaps['pct_cobertura_dist'].mean()) if not df_gaps.empty else 1.0,
    }

    return {
        'retenidos_cd':      df_retenidos,
        'gaps_distribucion': df_gaps,
        'kpis':              kpis,
    }


# ─────────────────────────────────────────────────────────────
#  FUNCIÓN PRINCIPAL
# ─────────────────────────────────────────────────────────────

def run_analysis(path, params=None, formato='plantilla'):
    """
    Corre el análisis completo desde un Excel de input.

    Parámetros
    ----------
    path    : ruta al .xlsx
    params  : dict de overrides a DEFAULT_PARAMS (opcional)
    formato : 'plantilla' (4 pestañas Capi) | 'ripley' (Base Profundidad wide)

    Retorna
    -------
    dict con claves:
      maestro, ventas, stock          — DataFrames de input (normalizados)
      cobertura, reposiciones,
      transferencias, acciones_precio — DataFrames de output
      params                          — parámetros efectivos usados
      summary                         — dict de KPIs de resumen
    """
    # Auto-detectar formato si se pide 'auto', o respetar el explícito
    if formato == 'auto':
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True)
        sheets = wb.sheetnames
        wb.close()
        if 'Base' in sheets:
            formato = 'ripley'
        else:
            formato = 'plantilla'

    if formato == 'ripley':
        from transformador_base_ripley import load_from_base_ripley
        df_m, df_v, df_s, p = load_from_base_ripley(path, params)
    else:
        df_m, df_v, df_s, p = load_from_plantilla(path, params)

    df_cob   = build_cobertura(df_m, df_v, df_s, p)
    df_rep   = build_reposiciones(df_cob, p)
    df_trans = build_transferencias(df_cob, p)
    df_prec  = build_acciones_precio(df_cob, df_trans, df_m, p)
    df_rep_pivot = pivot_reposiciones(df_rep, df_cob)
    df_alertas   = build_alertas(df_cob, df_v, p, df_maestro=df_m)
    df_anomalias = build_anomalias_tienda(df_cob, df_v, p)
    alertas_tienda = build_alertas_tienda(df_cob, df_rep, df_prec, df_s, p)
    alertas_venta_cero = build_alertas_venta_cero(df_cob, df_s, p)

    # KPIs de resumen (se construye antes del briefing)
    summary = {
        'total_combos':      len(df_cob),
        'n_critico':         int((df_cob['estado'] == 'CRÍTICO').sum()),
        'n_precritico':      int((df_cob['estado'] == 'PRE-CRÍTICO').sum()),
        'n_optimo':          int((df_cob['estado'] == 'ÓPTIMO').sum()),
        'n_alto':            int((df_cob['estado'] == 'ALTO').sum()),
        'n_sobrestock':      int((df_cob['estado'] == 'SOBRESTOCK').sum()),
        'n_liquidar':        int((df_cob['estado'] == 'LIQUIDAR').sum()),
        'n_nuevo_sv':        int((df_cob['estado'] == 'NUEVO SIN VENTA').sum()),
        'n_dormido':         int((df_cob['estado'] == 'DORMIDO').sum()),
        'n_muerto':          int((df_cob['estado'] == 'MUERTO').sum()),
        'uds_reponer':       int(df_rep['a_reponer'].sum()) if not df_rep.empty else 0,
        'uds_transferir':    int(df_trans['uds_transferir'].sum()) if not df_trans.empty else 0,
        'n_acciones_precio': len(df_prec),
        'n_alertas':         len(df_alertas),
        'n_anomalias':       len(df_anomalias),
        # Sobrestock aparente (proxy CD)
        'n_sobrestock_aparente': int(df_cob['sobrestock_aparente'].sum()) if 'sobrestock_aparente' in df_cob.columns else 0,
        'capital_sobrestock': float(df_cob.loc[df_cob['estado'].isin({'SOBRESTOCK', 'LIQUIDAR'}), 'stock_valor_costo'].sum()),
        'capital_sobrestock_aparente': float(df_cob.loc[df_cob.get('sobrestock_aparente', False) == True, 'stock_valor_costo'].sum()) if 'sobrestock_aparente' in df_cob.columns else 0,
    }

    # Margen efectivo global (Contribución / VtasMF)
    if 'vta_soles_4sem' in df_cob.columns and 'contrib_soles_4sem' in df_cob.columns:
        # Deduplicate to SKU level (estos valores son por SKU, repetidos en cada tienda)
        _df_sku_margen = df_cob.drop_duplicates('sku')[['sku', 'vta_soles_4sem', 'contrib_soles_4sem', 'marca']].copy()
        _vta_total = _df_sku_margen['vta_soles_4sem'].sum()
        _contrib_total = _df_sku_margen['contrib_soles_4sem'].sum()
        _margen_global = _contrib_total / _vta_total if _vta_total > 0 else 0
        # Margen por marca
        _margen_marca = _df_sku_margen.groupby('marca').agg(
            vta_soles=('vta_soles_4sem', 'sum'),
            contrib_soles=('contrib_soles_4sem', 'sum'),
        ).reset_index()
        _margen_marca['margen_efectivo'] = np.where(
            _margen_marca['vta_soles'] > 0,
            _margen_marca['contrib_soles'] / _margen_marca['vta_soles'],
            0
        )
        _margen_marca = _margen_marca.sort_values('vta_soles', ascending=False)
        summary['margen_efectivo_global'] = float(_margen_global)
        summary['vta_soles_4sem_total'] = float(_vta_total)
        summary['contrib_soles_4sem_total'] = float(_contrib_total)
        summary['margen_por_marca'] = _margen_marca.to_dict('records')
    else:
        summary['margen_efectivo_global'] = None

    # Predistribución: Retenidos en CD + Gaps de distribución (solo marcas propias)
    predist = build_predistribucion(df_m, df_s, p)

    # Aging analysis (Ventana de Mercadería)
    aging = build_aging_analysis(df_cob, p)

    # Agregar KPIs de aging al summary
    summary.update({
        'aging_capital_viejo':  aging['kpis']['capital_viejo'],
        'aging_pct_viejo':      aging['kpis']['pct_viejo'],
        'aging_edad_prom':      aging['kpis']['edad_prom_pond'],
        'aging_n_zona_riesgo':  aging['kpis']['n_zona_riesgo'],
    })

    # Agregar KPIs de predistribución al summary
    summary.update({
        'predist_n_retenidos':       predist['kpis']['n_retenidos'],
        'predist_uds_retenidas':     predist['kpis']['uds_retenidas_cd'],
        'predist_capital_retenido':  predist['kpis']['capital_retenido'],
        'predist_n_gaps':            predist['kpis']['n_gaps'],
        'predist_n_gaps_con_cd':     predist['kpis']['n_gaps_con_cd'],
        'predist_prom_cobertura':    predist['kpis']['prom_cobertura_dist'],
    })

    # Análisis por ventana de compra (embarques)
    embarque = build_embarque_analysis(df_cob, p)
    if embarque:
        summary.update({
            'embarque_n_ventanas':   embarque['kpis']['n_ventanas'],
            'embarque_mejor':        embarque['kpis']['mejor_ventana'],
            'embarque_peor':         embarque['kpis']['peor_ventana'],
            'embarque_n_rojos':      embarque['kpis']['n_rojos'],
        })

    # Briefing ejecutivo
    briefing = build_briefing(df_cob, df_v, summary, p)

    return {
        'maestro':              df_m,
        'ventas':               df_v,
        'stock':                df_s,
        'cobertura':            df_cob,
        'reposiciones':         df_rep,
        'reposiciones_pivot':   df_rep_pivot,
        'transferencias':       df_trans,
        'acciones_precio':      df_prec,
        'alertas':              df_alertas,
        'anomalias_tienda':     df_anomalias,
        'alertas_tienda':       alertas_tienda,
        'alertas_venta_cero':   alertas_venta_cero,
        'aging':                aging,
        'embarque':             embarque,
        'predistribucion':      predist,
        'briefing':             briefing,
        'params':               p,
        'summary':              summary,
    }


# ─────────────────────────────────────────────────────────────
#  SNAPSHOT BASELINE & COMPARATIVO SEMANAL
# ─────────────────────────────────────────────────────────────

import json
from datetime import datetime

def snapshot_kpis(results, semana_label=None, output_dir=None):
    """
    Guarda un snapshot JSON de los KPIs principales para comparación semanal.

    Parámetros
    ----------
    results     : dict devuelto por run_analysis()
    semana_label: str como 'semana_0', 'semana_1', etc. Si None, auto-detecta.
    output_dir  : directorio donde guardar. Default: mismo dir del archivo.

    Retorna
    -------
    dict con los KPIs snapshot + ruta del archivo guardado.
    """
    import os
    s = results['summary']
    df_cob = results['cobertura']

    # KPIs del Triángulo de Oro: Sell-through, Margen, Obsoletos
    # 1. Sell-through
    _total_stock = df_cob['stock_total'].sum() if 'stock_total' in df_cob.columns else 0
    _total_vta = df_cob['prom_vta_uds'].sum() if 'prom_vta_uds' in df_cob.columns else 0
    _sell_through = _total_vta / (_total_vta + _total_stock) * 100 if (_total_vta + _total_stock) > 0 else 0

    # 2. Margen efectivo
    _margen = s.get('margen_efectivo_global', None)
    _margen_pct = float(_margen * 100) if _margen is not None else None

    # 3. Obsoletos
    _n_obsoletos = s.get('n_dormido', 0) + s.get('n_muerto', 0) + s.get('n_liquidar', 0)
    _pct_obsoletos = _n_obsoletos / s['total_combos'] * 100 if s['total_combos'] > 0 else 0
    _capital_obsoleto = float(df_cob.loc[
        df_cob['estado'].isin({'DORMIDO', 'MUERTO', 'LIQUIDAR'}), 'stock_valor_costo'
    ].sum()) if 'estado' in df_cob.columns else 0

    # Cobertura promedio
    _cob_prom = float(df_cob['cobertura_sem'].mean()) if 'cobertura_sem' in df_cob.columns else 0

    # Quiebres
    _n_quiebres = int((df_cob['stock_total'] == 0).sum()) if 'stock_total' in df_cob.columns else 0

    snapshot = {
        'timestamp': datetime.now().isoformat(),
        'semana': semana_label or f"semana_{datetime.now().strftime('%Y%m%d')}",

        # Triángulo de Oro
        'sell_through_pct': round(_sell_through, 2),
        'margen_efectivo_pct': round(_margen_pct, 2) if _margen_pct is not None else None,
        'pct_obsoletos': round(_pct_obsoletos, 2),
        'capital_obsoleto': round(_capital_obsoleto, 0),

        # Estados
        'total_combos': s['total_combos'],
        'n_critico': s['n_critico'],
        'n_precritico': s.get('n_precritico', 0),
        'n_optimo': s.get('n_optimo', 0),
        'n_alto': s.get('n_alto', 0),
        'n_sobrestock': s.get('n_sobrestock', 0),
        'n_liquidar': s.get('n_liquidar', 0),
        'n_nuevo_sv': s.get('n_nuevo_sv', 0),
        'n_dormido': s.get('n_dormido', 0),
        'n_muerto': s.get('n_muerto', 0),

        # Operativos
        'cobertura_prom_sem': round(_cob_prom, 2),
        'n_quiebres': _n_quiebres,
        'uds_reponer': s.get('uds_reponer', 0),
        'uds_transferir': s.get('uds_transferir', 0),
        'n_acciones_precio': s.get('n_acciones_precio', 0),
        'capital_sobrestock': s.get('capital_sobrestock', 0),
        'n_sobrestock_aparente': s.get('n_sobrestock_aparente', 0),

        # Aging
        'aging_capital_viejo': s.get('aging_capital_viejo', 0),
        'aging_pct_viejo': s.get('aging_pct_viejo', 0),
        'aging_edad_prom': s.get('aging_edad_prom', 0),

        # Ventas
        'vta_soles_4sem_total': s.get('vta_soles_4sem_total', 0),
        'contrib_soles_4sem_total': s.get('contrib_soles_4sem_total', 0),
    }

    # Guardar JSON
    if output_dir is None:
        output_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(os.path.join(output_dir, 'snapshots'), exist_ok=True)
    fname = f"snapshot_{snapshot['semana']}.json"
    fpath = os.path.join(output_dir, 'snapshots', fname)
    with open(fpath, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    snapshot['_path'] = fpath
    return snapshot


def load_snapshots(snapshots_dir=None):
    """
    Carga todos los snapshots desde el directorio snapshots/.

    Retorna lista de dicts ordenada por timestamp.
    """
    import os, glob
    if snapshots_dir is None:
        snapshots_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'snapshots')
    if not os.path.isdir(snapshots_dir):
        return []
    files = sorted(glob.glob(os.path.join(snapshots_dir, 'snapshot_*.json')))
    snapshots = []
    for fp in files:
        with open(fp, 'r', encoding='utf-8') as f:
            snapshots.append(json.load(f))
    return snapshots


def comparativo_semanal(snapshot_actual, snapshot_baseline=None, snapshots_dir=None):
    """
    Compara el snapshot actual vs el baseline (semana 0) y calcula deltas.

    Parámetros
    ----------
    snapshot_actual   : dict del snapshot actual
    snapshot_baseline : dict del baseline. Si None, carga el primero del directorio.
    snapshots_dir     : directorio de snapshots (para auto-carga)

    Retorna
    -------
    dict con:
      baseline, actual, deltas (absolutos y porcentuales), semanas_transcurridas
    """
    if snapshot_baseline is None:
        all_snaps = load_snapshots(snapshots_dir)
        if not all_snaps:
            return None
        snapshot_baseline = all_snaps[0]

    # KPIs a comparar con dirección esperada
    # positive_is_good=True → delta positivo es mejora
    # positive_is_good=False → delta positivo es peor
    kpis_config = {
        'sell_through_pct':     {'label': 'Sell-through (%)',          'positive_is_good': True,  'format': '{:+.2f}pp'},
        'margen_efectivo_pct':  {'label': 'Margen efectivo (%)',       'positive_is_good': True,  'format': '{:+.2f}pp'},
        'pct_obsoletos':        {'label': '% Obsoletos',              'positive_is_good': False, 'format': '{:+.2f}pp'},
        'capital_obsoleto':     {'label': 'Capital obsoleto (S/)',     'positive_is_good': False, 'format': 'S/ {:+,.0f}'},
        'cobertura_prom_sem':   {'label': 'Cobertura promedio (sem)',  'positive_is_good': True,  'format': '{:+.1f} sem'},
        'n_quiebres':           {'label': 'Quiebres (#)',             'positive_is_good': False, 'format': '{:+d}'},
        'n_critico':            {'label': 'SKUs en crítico (#)',      'positive_is_good': False, 'format': '{:+d}'},
        'capital_sobrestock':   {'label': 'Capital en sobrestock (S/)', 'positive_is_good': False, 'format': 'S/ {:+,.0f}'},
        'uds_reponer':          {'label': 'Uds a reponer (#)',        'positive_is_good': False, 'format': '{:+,d}'},
        'n_acciones_precio':    {'label': 'Acciones de precio (#)',   'positive_is_good': False, 'format': '{:+d}'},
        'aging_capital_viejo':  {'label': 'Capital envejecido (S/)',   'positive_is_good': False, 'format': 'S/ {:+,.0f}'},
        'aging_pct_viejo':      {'label': '% inventario viejo',       'positive_is_good': False, 'format': '{:+.1f}pp'},
    }

    deltas = {}
    for kpi, cfg in kpis_config.items():
        base_val = snapshot_baseline.get(kpi)
        curr_val = snapshot_actual.get(kpi)
        if base_val is None or curr_val is None:
            deltas[kpi] = {'label': cfg['label'], 'baseline': base_val, 'actual': curr_val,
                           'delta': None, 'delta_pct': None, 'mejora': None}
            continue
        delta = curr_val - base_val
        delta_pct = (delta / abs(base_val) * 100) if base_val != 0 else None
        mejora = (delta > 0) == cfg['positive_is_good'] if delta != 0 else None

        deltas[kpi] = {
            'label': cfg['label'],
            'baseline': base_val,
            'actual': curr_val,
            'delta': round(delta, 2),
            'delta_pct': round(delta_pct, 2) if delta_pct is not None else None,
            'delta_fmt': cfg['format'].format(delta) if delta is not None else '—',
            'mejora': mejora,
            'icono': '✅' if mejora else ('⚠️' if mejora is False else '—'),
        }

    # Contar mejoras vs empeoramientos
    n_mejoras = sum(1 for d in deltas.values() if d['mejora'] is True)
    n_peores = sum(1 for d in deltas.values() if d['mejora'] is False)

    return {
        'baseline_semana': snapshot_baseline.get('semana', '?'),
        'actual_semana': snapshot_actual.get('semana', '?'),
        'baseline_timestamp': snapshot_baseline.get('timestamp', '?'),
        'actual_timestamp': snapshot_actual.get('timestamp', '?'),
        'deltas': deltas,
        'n_mejoras': n_mejoras,
        'n_peores': n_peores,
        'score': f"{n_mejoras}/{n_mejoras + n_peores}",
    }


# ─────────────────────────────────────────────────────────────
#  REPORTE DE CONSOLA
# ─────────────────────────────────────────────────────────────

def print_report(results):
    """Imprime reporte formateado en consola."""
    s  = results['summary']
    p  = results['params']
    df_rep   = results['reposiciones']
    df_trans = results['transferencias']
    df_prec  = results['acciones_precio']

    print("\n" + "═" * 62)
    print("   Capi — Inventory Engine v2 — REPORTE")
    print("═" * 62)
    print(f"\n  Thresholds: CRÍTICO<{p['umbral_critico']} | ÓPTIMO {p['umbral_critico']}–{p['umbral_optimo']}"
          f" | ALTO {p['umbral_optimo']}–{p['umbral_alto']} | SOBRESTOCK>{p['umbral_alto']} sem")

    print(f"\n📊 COBERTURA GENERAL — {s['total_combos']} combinaciones SKU×Tienda")
    print(f"   🔴 CRÍTICO     : {s['n_critico']:>4}")
    print(f"   🟢 ÓPTIMO      : {s['n_optimo']:>4}")
    print(f"   🟡 ALTO        : {s['n_alto']:>4}")
    print(f"   🟠 SOBRESTOCK  : {s['n_sobrestock']:>4}")
    print(f"   💀 LIQUIDAR    : {s['n_liquidar']:>4}")
    print(f"   ⚪ NUEVO S/V    : {s['n_nuevo_sv']:>4}")
    print(f"   😴 DORMIDO     : {s['n_dormido']:>4}")
    print(f"   💤 MUERTO      : {s['n_muerto']:>4}")

    print(f"\n📦 REPOSICIONES — {len(df_rep)} ítems · {s['uds_reponer']} uds totales")
    if not df_rep.empty:
        for _, r in df_rep.iterrows():
            print(f"   {r['urgencia']}  {r['sku']:<10} | {str(r['tienda'])[:22]:<22} | "
                  f"Stock: {r['stock_actual']:>4} uds | Cob: {r['cobertura_actual']} sem → "
                  f"Reponer {r['a_reponer']} uds → {r['cob_post_rep']} sem")
    else:
        print("   (sin reposiciones necesarias)")

    print(f"\n🔄 TRANSFERENCIAS — {len(df_trans)} movimientos · {s['uds_transferir']} uds")
    if not df_trans.empty:
        for _, t in df_trans.iterrows():
            print(f"   {t['sku']:<10} | {t['uds_transferir']:>3} uds | "
                  f"{str(t['tienda_origen'])[:18]:<18} ({t['cob_origen_pre']} sem) → "
                  f"{str(t['tienda_destino'])[:18]:<18} ({t['cob_destino_pre']} sem)")
    else:
        print("   (sin transferencias sugeridas)")

    print(f"\n💰 ACCIONES DE PRECIO — {s['n_acciones_precio']} productos")
    if not df_prec.empty:
        for _, a in df_prec.iterrows():
            print(f"   {a['estado']:<11} {a['sku']:<10} | {str(a['tienda'])[:22]:<22} | "
                  f"Cob: {a['cobertura_actual']} sem | "
                  f"S/{a['precio_vigente']:.0f} → S/{a['precio_sugerido']:.0f} "
                  f"(-{a['dscto_sugerido']*100:.0f}%)")
    else:
        print("   (sin acciones de precio recomendadas)")

    print("\n" + "═" * 62 + "\n")


# ─────────────────────────────────────────────────────────────
#  ENTRY POINT — testing directo
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "mnt/uploads/Plantilla-Input-Herramienta-Retail.xlsx"
    print(f"Leyendo plantilla: {path}\n")
    results = run_analysis(path)
    print_report(results)

    # Mostrar DataFrames de detalle
    print("── COBERTURA DETALLE ──")
    print(results['cobertura'][['sku', 'tienda', 'stock_total', 'prom_vta_uds',
                                 'cobertura_sem', 'estado']].to_string(index=False))
