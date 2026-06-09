"""
transformador_base_ripley.py — Adapter para la base interna de Ripley
=====================================================================

La plantilla original del motor espera un Excel con 4 pestañas
(maestro/ventas/stock en formato largo). La base que sale del
sistema Ripley ("Base Profundidad al DD.MM.xlsx") está en formato
wide: una fila por SKU con 4 columnas por tienda embebidas en el
header (`<TIENDA> Stk`, `<TIENDA> Vta`, `<TIENDA> On Order`,
`<TIENDA> UME`).

Este módulo convierte el formato wide al schema largo que consume
motor_v2, SIN modificar el motor. Además extrae la columna
`tipo_precio` (MD1/PTR/MTR) desde "Tipo de Evento Vigente" que no
existe en la plantilla original — se usa para las alertas nuevas.

Uso:
    from transformador_base_ripley import load_from_base_ripley
    df_m, df_v, df_s, params = load_from_base_ripley(path_xlsx, params)

Devuelve la misma tupla que motor_v2.load_from_plantilla, para que
sea un drop-in replacement desde run_analysis.
"""

import re
import numpy as np
import pandas as pd
from datetime import datetime


# Sufijos que marcan columnas por tienda
SUFIJOS_TIENDA = (' Stk', ' Vta', ' On Order', ' UME')

# Tiendas a excluir (venta no-tienda, corporativo, virtual)
TIENDAS_EXCLUIR_PATTERNS = (
    'virtual', 'vtas corp', 'tv pi', ' tv', 'fsf',  # canales no-retail
)


# ─────────────────────────────────────────────────────────────
#  Descubrir tiendas desde headers
# ─────────────────────────────────────────────────────────────

def _descubrir_tiendas(columns):
    """
    Recorre los headers y extrae nombres únicos de tienda detectando
    sufijos " Stk", " Vta", " On Order", " UME".

    Retorna lista ordenada de nombres de tienda.
    """
    tiendas = set()
    for c in columns:
        if not isinstance(c, str):
            continue
        for suf in SUFIJOS_TIENDA:
            if c.endswith(suf):
                tiendas.add(c[:-len(suf)].strip())
                break
    return sorted(tiendas)


def _tienda_valida(nombre):
    """Filtra tiendas que no son retail (virtual, corporativo, etc.)."""
    low = nombre.lower().strip()
    for pat in TIENDAS_EXCLUIR_PATTERNS:
        if pat in low:
            return False
    # "TV" solo sin más texto → canal virtual
    if low == 'tv':
        return False
    return True


# ─────────────────────────────────────────────────────────────
#  Parseo de fecha de corte desde nombre de archivo
# ─────────────────────────────────────────────────────────────

def _fecha_desde_path(path):
    """
    Extrae "DD.MM" del nombre del archivo (ej. 'Base Profundidad al 08.04.xlsx')
    y devuelve string ISO 'YYYY-MM-DD'. Si falla, usa hoy.
    """
    m = re.search(r'(\d{1,2})\.(\d{1,2})', str(path))
    if m:
        dia, mes = int(m.group(1)), int(m.group(2))
        anio = datetime.now().year
        try:
            return datetime(anio, mes, dia).strftime('%Y-%m-%d')
        except ValueError:
            pass
    return datetime.now().strftime('%Y-%m-%d')


# ─────────────────────────────────────────────────────────────
#  Loader principal
# ─────────────────────────────────────────────────────────────

def load_from_base_ripley(path, params=None):
    """
    Lee la base wide de Ripley y devuelve (df_maestro, df_ventas,
    df_stock, params) con el mismo schema que motor_v2.load_from_plantilla.

    Agrega la columna `tipo_precio` (MD1/PTR/MTR) en df_maestro,
    disponible para el sistema de alertas nuevo.
    """
    # Import local para evitar ciclo
    from motor_v2 import DEFAULT_PARAMS

    p = {**DEFAULT_PARAMS, **(params or {})}
    IGV = 1.18

    df = pd.read_excel(path, sheet_name='Base', header=0)
    # Normalizar nombres de columna
    df.columns = [str(c).strip() for c in df.columns]

    # Filtrar filas sin SKU
    df = df.dropna(subset=['Cód. Prod.']).copy()
    df['Cód. Prod.'] = df['Cód. Prod.'].astype(str).str.strip()

    # Descubrir tiendas
    tiendas_todas = _descubrir_tiendas(df.columns)
    tiendas = [t for t in tiendas_todas if _tienda_valida(t)]

    # ── df_maestro ─────────────────────────────────────────────
    df_m = pd.DataFrame({
        'sku':            df['Cód. Prod.'],
        'nombre':         df['Descripción'].astype(str).str.strip(),
        'marca':          df['Marca'].fillna('').astype(str).str.strip(),
        'categoria':      df['Línea'].fillna('').astype(str).str.strip(),
        'departamento':   df['Dpto'].fillna('').astype(str).str.strip(),
        'edad_semanas':   pd.to_numeric(df['Antigüedad semanal'], errors='coerce').fillna(0).astype(int),
        'precio_blanco':  pd.to_numeric(df['P.Blanco'], errors='coerce').fillna(0.0),
        'precio_vigente': pd.to_numeric(df['P.Vigente PMM'], errors='coerce').fillna(0.0),
        'costo':          pd.to_numeric(df['Costo S/.'], errors='coerce').fillna(0.0),
        'tipo_precio':    df['Tipo de Evento Vigente'].fillna('').astype(str).str.strip().str.upper(),
        'stock_cd':       pd.to_numeric(df.get('Total  CD+Bodega Unid. (On-hand disponible)', 0), errors='coerce').fillna(0).astype(int),
    })

    # Derivados: IMU, margen, descuento (mismo cálculo del motor)
    pb_ex = df_m['precio_blanco']  / IGV
    pv_ex = df_m['precio_vigente'] / IGV
    df_m['imu'] = np.where(pb_ex > 0, (pb_ex - df_m['costo']) / pb_ex, np.nan)
    df_m['margen_vigente'] = np.where(pv_ex > 0, (pv_ex - df_m['costo']) / pv_ex, np.nan)
    df_m['pct_descuento'] = np.where(
        df_m['precio_blanco'] > 0,
        1 - df_m['precio_vigente'] / df_m['precio_blanco'],
        np.nan
    )

    # Excluir SKUs sin sentido para análisis (REBATES, extragarantías)
    df_m = df_m[~df_m['categoria'].str.upper().isin(['REBATES', 'EXTRAGARANTIA', ''])].copy()
    # Dedup por SKU (mantiene primera fila)
    df_m = df_m.drop_duplicates('sku').reset_index(drop=True)

    skus_validos = set(df_m['sku'])

    # ── df_stock (long por SKU × tienda) ───────────────────────
    # Necesitamos una fila por (sku, tienda) con stock_uds y stock_transito.
    costo_map = df_m.set_index('sku')['costo'].to_dict()
    nombre_map = df_m.set_index('sku')['nombre'].to_dict()
    fecha_corte = _fecha_desde_path(path)

    # Filtrar df original a SKUs válidos
    df_valid = df[df['Cód. Prod.'].isin(skus_validos)].copy()

    filas_stock = []
    filas_ventas = []

    # Total de unidades vendidas sem1 a nivel SKU (col 42: "Unid. Vendidas Sem. 1ant")
    total_sem1_col = 'Unid. Vendidas Sem. 1ant'
    total_sem2_col = 'Unid. Vendidas Sem. 2ant'
    total_sem3_col = 'Unid. Vendidas Sem. 3ant'
    total_sem4_col = 'Unid. Vendidas Sem. 4ant'

    # Para prorratear sem2-4 por tienda usamos proporción: tienda_sem1 / total_sem1 × total_semX
    for _, row in df_valid.iterrows():
        sku = row['Cód. Prod.']
        total_sem1 = float(pd.to_numeric(row.get(total_sem1_col, 0), errors='coerce') or 0)
        total_sem2 = float(pd.to_numeric(row.get(total_sem2_col, 0), errors='coerce') or 0)
        total_sem3 = float(pd.to_numeric(row.get(total_sem3_col, 0), errors='coerce') or 0)
        total_sem4 = float(pd.to_numeric(row.get(total_sem4_col, 0), errors='coerce') or 0)

        for t in tiendas:
            stk = float(pd.to_numeric(row.get(f'{t} Stk', 0), errors='coerce') or 0)
            on_order = float(pd.to_numeric(row.get(f'{t} On Order', 0), errors='coerce') or 0)
            vta_sem1 = float(pd.to_numeric(row.get(f'{t} Vta', 0), errors='coerce') or 0)

            # Skip filas vacías (sin stock ni venta ni tránsito) — optimización
            if stk <= 0 and on_order <= 0 and vta_sem1 <= 0:
                continue

            # Stock
            filas_stock.append({
                'sku':            sku,
                'tienda':         t,
                'fecha_corte':    fecha_corte,
                'stock_uds':      stk,
                'stock_transito': on_order,
            })

            # Ventas — sem2-4 prorrateadas desde totales SKU por proporción sem1
            if total_sem1 > 0 and vta_sem1 > 0:
                pct = vta_sem1 / total_sem1
                s2 = total_sem2 * pct
                s3 = total_sem3 * pct
                s4 = total_sem4 * pct
            else:
                s2 = s3 = s4 = 0.0

            filas_ventas.append({
                'sku':          sku,
                'tienda':       t,
                'vta_uds_sem1': vta_sem1,
                'vta_uds_sem2': s2,
                'vta_uds_sem3': s3,
                'vta_uds_sem4': s4,
                'prom_vta_uds': vta_sem1,  # mismo criterio que motor original
            })

    df_s = pd.DataFrame(filas_stock)
    df_v = pd.DataFrame(filas_ventas)

    if df_s.empty:
        df_s = pd.DataFrame(columns=[
            'sku', 'descripcion', 'tienda', 'fecha_corte',
            'stock_uds', 'stock_transito', 'stock_total',
            'costo_unit', 'stock_valor_costo'
        ])
    else:
        df_s['stock_total'] = df_s['stock_uds'] + df_s['stock_transito']
        df_s['descripcion'] = df_s['sku'].map(nombre_map)
        df_s['costo_unit'] = df_s['sku'].map(costo_map).fillna(0.0)
        df_s['stock_valor_costo'] = df_s['stock_total'] * df_s['costo_unit']
        df_s = df_s[['sku', 'descripcion', 'tienda', 'fecha_corte',
                     'stock_uds', 'stock_transito', 'stock_total',
                     'costo_unit', 'stock_valor_costo']]

    if df_v.empty:
        df_v = pd.DataFrame(columns=[
            'sku', 'tienda',
            'vta_uds_sem1', 'vta_uds_sem2', 'vta_uds_sem3', 'vta_uds_sem4',
            'prom_vta_uds'
        ])
    else:
        df_v = df_v[['sku', 'tienda',
                     'vta_uds_sem1', 'vta_uds_sem2', 'vta_uds_sem3', 'vta_uds_sem4',
                     'prom_vta_uds']]

    # df_m final con columnas ordenadas (tipo_precio al final para no romper motor)
    df_m = df_m[['sku', 'nombre', 'marca', 'categoria', 'departamento',
                 'precio_blanco', 'precio_vigente', 'costo',
                 'edad_semanas', 'imu', 'margen_vigente', 'pct_descuento',
                 'tipo_precio', 'stock_cd']]

    return df_m, df_v, df_s, p


# ─────────────────────────────────────────────────────────────
#  Smoke test directo
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else \
        '/Users/francobarreto/Downloads/Base Profundidad al 08.04.xlsx'

    print(f"Leyendo: {path}")
    df_m, df_v, df_s, p = load_from_base_ripley(path)

    print(f"\n✅ Maestro:  {len(df_m):>7,} SKUs únicos")
    print(f"   Tipos de precio: {df_m['tipo_precio'].value_counts().to_dict()}")
    print(f"   Líneas top 10: {df_m['categoria'].value_counts().head(10).to_dict()}")

    print(f"\n✅ Stock:    {len(df_s):>7,} filas (SKU × tienda con actividad)")
    print(f"   Tiendas:  {df_s['tienda'].nunique()} únicas")
    print(f"   Top tiendas por stock: {df_s.groupby('tienda')['stock_uds'].sum().nlargest(5).to_dict()}")

    print(f"\n✅ Ventas:   {len(df_v):>7,} filas")
    print(f"   Total uds sem1: {df_v['vta_uds_sem1'].sum():,.0f}")
