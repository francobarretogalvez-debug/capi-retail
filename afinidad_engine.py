"""
afinidad_engine.py — Motor de Afinidad Producto × Plaza
========================================================
Analiza qué productos funcionan en qué tiendas, agrupa tiendas por
comportamiento de venta, y genera acciones: empujes CD→tienda,
redistribución entre tiendas, y señales de producción nacional.

Inputs:
  - Base Profundidad (Excel) con columnas SKU×tienda (Stk, Vta, On Order, UME)
  - config_afinidad.json (umbrales, tiendas excluidas, params clustering)
  - config_calorico.json (categoría calórica por línea)

Outputs (6):
  1. Heatmap rotación Línea×Tienda
  2. Clusters de tiendas por comportamiento
  3. Anomalías cruzadas (producto malo vs mal match plaza)
  4. Empujes inteligentes CD→Tiendas
  5. Redistribución entre tiendas
  6. Señales de producción nacional
"""

import json
import os
import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────

_DIR = os.path.dirname(__file__)

def _load_config():
    path = os.path.join(_DIR, 'config_afinidad.json')
    with open(path, 'r') as f:
        return json.load(f)

def _load_calorico():
    path = os.path.join(_DIR, 'config_calorico.json')
    with open(path, 'r') as f:
        return json.load(f)['mapeo']


# ─────────────────────────────────────────────────────────────
#  LOAD & TRANSFORM
# ─────────────────────────────────────────────────────────────

def detect_stores(columns, excluir=None):
    """Detecta tiendas activas en las columnas del Excel."""
    if excluir is None:
        excluir = []
    stores = []
    for c in columns:
        if c.endswith(' Stk'):
            prefix = c[:-4]
            if f'{prefix} Vta' in columns and prefix not in excluir:
                stores.append(prefix)
    return stores


def _get_lineas_excluidas(config):
    """
    Retorna set de líneas a excluir según la temporada configurada.

    Lee filtro_estacional del config:
      - temporada_actual: "INVIERNO" o "VERANO"
      - lineas_excluir_invierno: líneas irrelevantes en invierno (desde 1-May)
      - lineas_excluir_verano: líneas irrelevantes en verano (desde 1-Nov)
      - lineas_excluir_siempre: líneas sin relevancia para Afinidad (OTROS, REBATES)
    """
    filtro = config.get('filtro_estacional', {})
    temporada = filtro.get('temporada_actual', 'INVIERNO').upper()

    siempre = set(filtro.get('lineas_excluir_siempre', []))

    if temporada == 'INVIERNO':
        estacional = set(filtro.get('lineas_excluir_invierno', []))
    elif temporada == 'VERANO':
        estacional = set(filtro.get('lineas_excluir_verano', []))
    else:
        estacional = set()

    return siempre | estacional


def load_tienda_data(excel_path, config=None):
    """
    Lee Base Profundidad y retorna DataFrame long-form:
    sku | descripcion | marca | linea | tienda | stk | vta | on_order | ume
        | margen_pct | es_liquidacion

    Filtra tiendas excluidas, tiendas sin venta, y líneas fuera de temporada.
    Agrega columnas de margen para flag anti-liquidación.
    """
    if config is None:
        config = _load_config()

    df = pd.read_excel(excel_path)

    excluir = config.get('tiendas_excluir', [])
    min_vta = config.get('clustering', {}).get('min_venta_tienda', 50)
    margen_por_marca = config.get('umbrales', {}).get('margen_min_por_marca', {})
    margen_default = margen_por_marca.get('_default', 30)

    stores = detect_stores(df.columns, excluir)

    # Columnas base del SKU
    base_cols = {
        'Cód. Prod.': 'sku',
        'Descripción': 'descripcion',
        'Marca': 'marca',
        'Línea': 'linea',
        'Proced.': 'procedencia',
        'Cobertura Semanal': 'cobertura_sem',
        'Total  CD+Bodega Unidades': 'stock_cd',
        'Vta. Unidades': 'vta_total',
        'STOCK ACTUAL (UNIDADES)': 'stock_total',
    }

    # Columnas de margen (nivel SKU global — pricing es omnicanal)
    margen_cols = {
        'Contrib. S/.': 'contrib_soles',
        'Vta. S/.': 'vta_soles',
        'Dscto. % Promedio': 'dscto_pct',
    }

    # Columna de antigüedad
    edad_cols = {
        'Antigüedad semanal': 'antiguedad_sem',
    }

    available = {k: v for k, v in base_cols.items() if k in df.columns}
    available_margen = {k: v for k, v in margen_cols.items() if k in df.columns}
    available_edad = {k: v for k, v in edad_cols.items() if k in df.columns}

    all_extra = {**available_margen, **available_edad}
    df_base = df[list(available.keys()) + list(all_extra.keys())].rename(
        columns={**available, **all_extra}
    ).copy()

    # Convertir cobertura: "Sin Venta" → NaN
    if 'cobertura_sem' in df_base.columns:
        df_base['cobertura_sem'] = pd.to_numeric(df_base['cobertura_sem'], errors='coerce')

    # Calcular margen % y flag de liquidación (nivel SKU global)
    # Umbral de margen es POR MARCA:
    #   MARQUIS/NAVIGATA/CACHAREL/SPAVALDI: 40% (propias alto margen)
    #   OSCAR DE LA RENTA/NAUTICA/US POLO: 30% (propias licenciadas)
    #   Terceras: 30% (default)
    if 'contrib_soles' in df_base.columns and 'vta_soles' in df_base.columns:
        df_base['contrib_soles'] = pd.to_numeric(df_base['contrib_soles'], errors='coerce').fillna(0)
        df_base['vta_soles'] = pd.to_numeric(df_base['vta_soles'], errors='coerce').fillna(0)
        df_base['margen_pct'] = np.where(
            df_base['vta_soles'] > 0,
            (df_base['contrib_soles'] / df_base['vta_soles'] * 100).round(2),
            np.nan
        )
        # Mapear umbral por marca (vectorizado)
        df_base['_margen_umbral'] = df_base['marca'].map(
            lambda m: margen_por_marca.get(m, margen_default)
        )
        df_base['es_liquidacion'] = df_base['margen_pct'] < df_base['_margen_umbral']
        # NaN en margen (sin venta) no es liquidación por margen
        df_base.loc[df_base['margen_pct'].isna(), 'es_liquidacion'] = False
        df_base = df_base.drop(columns='_margen_umbral')
    else:
        df_base['margen_pct'] = np.nan
        df_base['es_liquidacion'] = False

    # Antigüedad >= 26 semanas (6 meses) → liquidación, sin importar margen
    antiguedad_umbral_sem = config.get('umbrales', {}).get('antiguedad_liquidacion_sem', 26)
    if 'antiguedad_sem' in df_base.columns:
        df_base['antiguedad_sem'] = pd.to_numeric(df_base['antiguedad_sem'], errors='coerce')
        es_viejo = df_base['antiguedad_sem'] >= antiguedad_umbral_sem
        n_por_edad = es_viejo.sum() - (es_viejo & df_base['es_liquidacion']).sum()
        df_base['es_liquidacion'] = df_base['es_liquidacion'] | es_viejo
        if n_por_edad > 0:
            print(f"  [Afinidad] {n_por_edad} SKUs adicionales flagged liquidación "
                  f"por antigüedad >= {antiguedad_umbral_sem} sem")
    else:
        df_base['antiguedad_sem'] = np.nan

    if 'dscto_pct' in df_base.columns:
        df_base['dscto_pct'] = pd.to_numeric(df_base['dscto_pct'], errors='coerce').fillna(0)

    # Unpivot: una fila por SKU × tienda
    rows = []
    for store in stores:
        stk_col = f'{store} Stk'
        vta_col = f'{store} Vta'
        oo_col = f'{store} On Order'
        ume_col = f'{store} UME'

        temp = df_base.copy()
        temp['tienda'] = store
        temp['stk'] = df[stk_col].fillna(0).astype(int) if stk_col in df.columns else 0
        temp['vta'] = df[vta_col].fillna(0).astype(int) if vta_col in df.columns else 0
        temp['on_order'] = df[oo_col].fillna(0).astype(int) if oo_col in df.columns else 0
        temp['ume'] = df[ume_col].fillna(0).astype(int) if ume_col in df.columns else 0
        rows.append(temp)

    df_long = pd.concat(rows, ignore_index=True)

    # Filtrar tiendas con poca venta total
    vta_por_tienda = df_long.groupby('tienda')['vta'].sum()
    tiendas_activas = vta_por_tienda[vta_por_tienda >= min_vta].index.tolist()
    df_long = df_long[df_long['tienda'].isin(tiendas_activas)].copy()

    # Filtrar líneas fuera de temporada
    lineas_excluidas = _get_lineas_excluidas(config)
    if lineas_excluidas:
        # Normalizar a mayúsculas para comparación robusta
        df_long['_linea_upper'] = df_long['linea'].str.upper().str.strip()
        lineas_excluidas_upper = {l.upper().strip() for l in lineas_excluidas}
        n_antes = len(df_long)
        df_long = df_long[~df_long['_linea_upper'].isin(lineas_excluidas_upper)].copy()
        df_long = df_long.drop(columns='_linea_upper')
        n_filtrados = n_antes - len(df_long)
        if n_filtrados > 0:
            print(f"  [Afinidad] Filtro estacional: {len(lineas_excluidas)} líneas excluidas "
                  f"({', '.join(sorted(lineas_excluidas))}), {n_filtrados:,} filas removidas")

    return df_long, tiendas_activas


# ─────────────────────────────────────────────────────────────
#  OUTPUT 1: HEATMAP ROTACIÓN LÍNEA × TIENDA
# ─────────────────────────────────────────────────────────────

def build_rotation_matrix(df_long):
    """
    Calcula rotación (vta / stk) por línea × tienda.

    Retorna:
      - matrix: DataFrame pivot (filas=líneas, cols=tiendas, valores=rotación)
      - stats: DataFrame con stk, vta, rotación por celda
    """
    agg = df_long.groupby(['linea', 'tienda']).agg(
        stk=('stk', 'sum'),
        vta=('vta', 'sum'),
    ).reset_index()

    agg['rotacion'] = np.where(agg['stk'] > 0, agg['vta'] / agg['stk'], 0.0)
    agg['rotacion'] = agg['rotacion'].round(4)

    matrix = agg.pivot_table(
        index='linea', columns='tienda', values='rotacion', fill_value=0
    )

    # Ordenar líneas por rotación promedio desc
    matrix['_avg'] = matrix.mean(axis=1)
    matrix = matrix.sort_values('_avg', ascending=False)
    matrix = matrix.drop(columns='_avg')

    # Ordenar tiendas por venta total desc
    vta_tienda = df_long.groupby('tienda')['vta'].sum().sort_values(ascending=False)
    cols_order = [c for c in vta_tienda.index if c in matrix.columns]
    matrix = matrix[cols_order]

    return matrix, agg


# ─────────────────────────────────────────────────────────────
#  OUTPUT 2: CLUSTERS DE TIENDAS
# ─────────────────────────────────────────────────────────────

def _kmeans_manual(X, k, max_iter=50, seed=42):
    """K-Means sin scikit-learn. X es numpy array (n_samples, n_features)."""
    rng = np.random.RandomState(seed)
    n = X.shape[0]

    # Inicialización: k-means++
    centroids = np.empty((k, X.shape[1]))
    idx = rng.randint(0, n)
    centroids[0] = X[idx]
    for c in range(1, k):
        dists = np.min([np.sum((X - centroids[j])**2, axis=1) for j in range(c)], axis=0)
        probs = dists / dists.sum()
        idx = rng.choice(n, p=probs)
        centroids[c] = X[idx]

    labels = np.zeros(n, dtype=int)
    for _ in range(max_iter):
        # Assign
        dists = np.array([np.sum((X - centroids[j])**2, axis=1) for j in range(k)])
        new_labels = np.argmin(dists, axis=0)
        if np.array_equal(labels, new_labels):
            break
        labels = new_labels
        # Update
        for j in range(k):
            mask = labels == j
            if mask.sum() > 0:
                centroids[j] = X[mask].mean(axis=0)

    return labels, centroids


def cluster_tiendas(df_long, config=None):
    """
    Agrupa tiendas por comportamiento de venta (vector de rotación por línea).

    Retorna:
      - clusters_df: DataFrame con tienda, cluster_id, cluster_nombre, perfil
      - perfiles: dict {cluster_id: {linea: rotacion_promedio}}
      - rotation_vectors: DataFrame (tiendas × líneas) usado para clustering
    """
    if config is None:
        config = _load_config()

    n_clusters = config.get('clustering', {}).get('n_clusters', 4)

    # Construir vector de rotación por tienda
    agg = df_long.groupby(['tienda', 'linea']).agg(
        stk=('stk', 'sum'), vta=('vta', 'sum')
    ).reset_index()
    agg['rotacion'] = np.where(agg['stk'] > 0, agg['vta'] / agg['stk'], 0.0)

    pivot = agg.pivot_table(index='tienda', columns='linea', values='rotacion', fill_value=0)

    # Normalizar cada feature (z-score) para que líneas con más varianza no dominen
    means = pivot.mean(axis=0)
    stds = pivot.std(axis=0).replace(0, 1)
    X_norm = ((pivot - means) / stds).values

    tiendas = pivot.index.tolist()
    lineas = pivot.columns.tolist()

    # Ajustar n_clusters si hay pocas tiendas
    k = min(n_clusters, len(tiendas) - 1)
    if k < 2:
        k = 2

    labels, centroids = _kmeans_manual(X_norm, k)

    # Desnormalizar centroides para interpretación
    centroids_real = centroids * stds.values + means.values

    # Generar perfiles y nombres descriptivos
    calorico = _load_calorico()

    perfiles = {}
    nombres = {}
    for cid in range(k):
        centroid = dict(zip(lineas, centroids_real[cid]))
        perfiles[cid] = centroid

        # Nombre descriptivo: top 2 líneas por rotación
        sorted_lineas = sorted(centroid.items(), key=lambda x: -x[1])
        top2 = [l[0] for l in sorted_lineas[:2]]

        # Determinar si es más caliente o más frío
        grueso_rot = np.mean([centroid.get(l, 0) for l in lineas
                              if calorico.get(l, 'NEUTRO') == 'GRUESO'])
        ligero_rot = np.mean([centroid.get(l, 0) for l in lineas
                              if calorico.get(l, 'NEUTRO') == 'LIGERO'])

        if ligero_rot > grueso_rot * 2:
            temp_label = "clima cálido"
        elif grueso_rot > ligero_rot * 1.5:
            temp_label = "clima frío"
        else:
            temp_label = "mix equilibrado"

        nombres[cid] = f"Cluster {cid+1}: {temp_label} ({', '.join(top2)})"

    # Construir DataFrame resultado
    clusters_df = pd.DataFrame({
        'tienda': tiendas,
        'cluster_id': labels,
        'cluster_nombre': [nombres[l] for l in labels],
    })

    # Agregar venta total por tienda
    vta_tienda = df_long.groupby('tienda')['vta'].sum()
    clusters_df['vta_total'] = clusters_df['tienda'].map(vta_tienda).fillna(0).astype(int)
    clusters_df = clusters_df.sort_values(['cluster_id', 'vta_total'], ascending=[True, False])

    return clusters_df, perfiles, pivot


# ─────────────────────────────────────────────────────────────
#  OUTPUT 3: ANOMALÍAS CRUZADAS
# ─────────────────────────────────────────────────────────────

def find_anomalies(df_long, config=None):
    """
    Clasifica SKUs en:
      - 'en_liquidacion': margen bajo según umbral de su marca
      - 'producto_malo': no rota en ninguna tienda (stk > 0, vta == 0 en todas)
      - 'mal_match_plaza': no rota en tienda X pero sí en tienda Y
      - 'ok': rota normalmente

    Retorna:
      - anomalias_df: DataFrame con sku, tipo_anomalia, tiendas_sin_venta,
        tiendas_con_venta, accion_sugerida
    """
    if config is None:
        config = _load_config()

    min_stk = config.get('umbrales', {}).get('stock_alto_unidades', 10)

    # Agrupar por SKU × tienda
    sku_tienda = df_long.groupby(['sku', 'descripcion', 'marca', 'linea', 'tienda']).agg(
        stk=('stk', 'sum'), vta=('vta', 'sum'),
        es_liquidacion=('es_liquidacion', 'first'),
        margen_pct=('margen_pct', 'first'),
    ).reset_index()

    # Solo SKUs que tienen stock en al menos una tienda
    skus_con_stock = sku_tienda[sku_tienda['stk'] >= min_stk]['sku'].unique()
    sku_tienda = sku_tienda[sku_tienda['sku'].isin(skus_con_stock)]

    results = []
    for sku in skus_con_stock:
        sub = sku_tienda[sku_tienda['sku'] == sku]
        desc = sub['descripcion'].iloc[0]
        marca = sub['marca'].iloc[0]
        linea = sub['linea'].iloc[0]
        es_liq = sub['es_liquidacion'].iloc[0]
        margen = sub['margen_pct'].iloc[0]

        con_stock = sub[sub['stk'] >= min_stk]
        if len(con_stock) == 0:
            continue

        stk_total_sku = con_stock['stk'].sum()

        # Prioridad 1: flag de liquidación por margen
        if es_liq:
            margen_str = f"{margen:.1f}%" if pd.notna(margen) else "N/A"
            results.append({
                'sku': sku,
                'descripcion': desc,
                'marca': marca,
                'linea': linea,
                'tipo_anomalia': 'en_liquidacion',
                'tiendas_sin_venta': '',
                'tiendas_con_venta': '',
                'n_tiendas_sin_venta': 0,
                'n_tiendas_con_venta': len(con_stock),
                'stk_parado': stk_total_sku,
                'margen_pct': margen,
                'accion': f'Margen {margen_str} — posible liquidación. Evaluar precio/salida.',
            })
            continue

        sin_venta = con_stock[con_stock['vta'] == 0]['tienda'].tolist()
        con_venta = con_stock[con_stock['vta'] > 0]['tienda'].tolist()

        if len(sin_venta) == 0:
            continue  # OK, vende en todas donde tiene stock

        if len(con_venta) == 0:
            tipo = 'producto_malo'
            accion = 'Considerar descuento/liquidación'
        else:
            tipo = 'mal_match_plaza'
            accion = f'Redistribuir de {", ".join(sin_venta[:3])} a tiendas con demanda'

        stk_parado = con_stock[con_stock['vta'] == 0]['stk'].sum()

        results.append({
            'sku': sku,
            'descripcion': desc,
            'marca': marca,
            'linea': linea,
            'tipo_anomalia': tipo,
            'tiendas_sin_venta': ', '.join(sin_venta),
            'tiendas_con_venta': ', '.join(con_venta[:5]),
            'n_tiendas_sin_venta': len(sin_venta),
            'n_tiendas_con_venta': len(con_venta),
            'stk_parado': stk_parado,
            'margen_pct': margen,
            'accion': accion,
        })

    anomalias_df = pd.DataFrame(results)
    if len(anomalias_df) > 0:
        anomalias_df = anomalias_df.sort_values('stk_parado', ascending=False)

    return anomalias_df


# ─────────────────────────────────────────────────────────────
#  OUTPUT 4: EMPUJES INTELIGENTES CD → TIENDAS
# ─────────────────────────────────────────────────────────────

def build_empujes_cd(df_long, rotation_matrix, clusters_df, config=None):
    """
    Genera empujes del CD a tiendas basados en cobertura de semanas de venta.

    Lógica:
      1. SKU tiene stock en CD > min_stock_cd
      2. La línea del SKU tiene rotación alta en la tienda destino
      3. Estima demanda semanal del SKU en esa tienda:
         - Si tiene venta propia → vta_4sem / 4
         - Si no (stock 0, sin ventas) → promedio de la línea en esa tienda
      4. Target stock = max(UME, demanda_semanal × semanas_cobertura_target)
      5. Empuje = target_stock - stock_actual (si positivo)
      6. Priorizar por rotación esperada × marca propia

    Retorna:
      - empujes_df: DataFrame con sku, tienda, unidades_sugeridas,
        vta_semanal_est, target_stock, rotacion_esperada, prioridad
    """
    if config is None:
        config = _load_config()

    emp_config = config.get('empujes', {})
    min_stock_cd = emp_config.get('min_stock_cd', 5)
    max_empuje = emp_config.get('max_empuje_por_tienda', 200)
    semanas_cob = emp_config.get('semanas_cobertura_target', 12)
    priorizar_propias = emp_config.get('priorizar_marcas_propias', True)
    marcas_propias = config.get('marcas_propias', [])
    rot_alta = config.get('umbrales', {}).get('rotacion_alta', 0.04)

    # SKUs con stock CD disponible, excluyendo liquidación
    sku_info = df_long.groupby(['sku', 'descripcion', 'marca', 'linea']).agg(
        stock_cd=('stock_cd', 'first'),
        es_liquidacion=('es_liquidacion', 'first'),
        margen_pct=('margen_pct', 'first'),
    ).reset_index()
    sku_info = sku_info[sku_info['stock_cd'] >= min_stock_cd].copy()
    # No empujar SKUs en liquidación (margen < umbral)
    n_liquidacion = sku_info['es_liquidacion'].sum()
    sku_info = sku_info[~sku_info['es_liquidacion']].copy()
    if n_liquidacion > 0:
        print(f"  [Empujes] {n_liquidacion} SKUs excluidos por margen bajo (liquidación)")

    if len(sku_info) == 0:
        return pd.DataFrame()

    # Stock + venta actual por SKU × tienda
    stk_tienda = df_long.groupby(['sku', 'tienda']).agg(
        stk=('stk', 'sum'), vta=('vta', 'sum'), ume=('ume', 'first')
    ).reset_index()

    # Demanda semanal promedio por línea × tienda (fallback para SKUs sin venta propia)
    _linea_vta = df_long.groupby(['linea', 'tienda']).agg(
        vta_linea_total=('vta', 'sum'),
    ).reset_index()
    _skus_con_vta = df_long[df_long['vta'] > 0].groupby(
        ['linea', 'tienda']
    )['sku'].nunique().reset_index().rename(columns={'sku': 'n_skus_con_vta'})
    _linea_vta = _linea_vta.merge(_skus_con_vta, on=['linea', 'tienda'], how='left')
    _linea_vta['n_skus_con_vta'] = _linea_vta['n_skus_con_vta'].fillna(1).clip(lower=1)
    # Vta semanal promedio por SKU de esa línea en esa tienda
    _linea_vta['vta_sem_linea_por_sku'] = (
        _linea_vta['vta_linea_total'] / (4 * _linea_vta['n_skus_con_vta'])
    ).round(2)

    # Rotación long-form para merge
    rot_long = rotation_matrix.reset_index().melt(
        id_vars='linea', var_name='tienda', value_name='rotacion_linea_tienda'
    )
    rot_long = rot_long[rot_long['rotacion_linea_tienda'] >= rot_alta]

    tiendas_activas = set(clusters_df['tienda'].tolist())

    # Cross join: SKU × tiendas donde su línea rota bien
    candidates = sku_info.merge(rot_long, on='linea', how='inner')
    candidates = candidates[candidates['tienda'].isin(tiendas_activas)]

    # Merge stock + venta actual en tienda
    candidates = candidates.merge(
        stk_tienda[['sku', 'tienda', 'stk', 'vta', 'ume']],
        on=['sku', 'tienda'], how='left'
    )
    candidates['stk'] = candidates['stk'].fillna(0).astype(int)
    candidates['vta'] = candidates['vta'].fillna(0).astype(int)
    candidates['ume'] = candidates['ume'].fillna(0).astype(int)

    # Merge demanda promedio de la línea (fallback)
    candidates = candidates.merge(
        _linea_vta[['linea', 'tienda', 'vta_sem_linea_por_sku']],
        on=['linea', 'tienda'], how='left'
    )
    candidates['vta_sem_linea_por_sku'] = candidates['vta_sem_linea_por_sku'].fillna(0)

    # Estimar demanda semanal por SKU × tienda
    # Si el SKU tiene venta propia en esa tienda → usar su dato real
    # Si no (stock 0, venta 0) → usar promedio de línea como proxy
    candidates['vta_semanal_est'] = np.where(
        candidates['vta'] > 0,
        candidates['vta'] / 4,              # dato propio: vta 4 sem / 4
        candidates['vta_sem_linea_por_sku']  # fallback: promedio de la línea
    ).round(2)

    # Target stock = demanda semanal × semanas de cobertura, mínimo = UME
    candidates['target_stock'] = np.ceil(
        candidates['vta_semanal_est'] * semanas_cob
    ).astype(int)
    candidates['target_stock'] = np.maximum(
        candidates['target_stock'],
        candidates['ume'].clip(lower=3)
    )

    # Filtrar: solo donde stock actual < target
    candidates = candidates[candidates['stk'] < candidates['target_stock']]

    # Calcular unidades a empujar
    candidates['unidades_sugeridas'] = (
        (candidates['target_stock'] - candidates['stk'])
        .clip(upper=candidates['stock_cd'])
        .clip(upper=max_empuje)
        .astype(int)
    )
    candidates = candidates[candidates['unidades_sugeridas'] > 0]

    # Prioridad
    candidates['es_marca_propia'] = candidates['marca'].isin(marcas_propias)
    candidates['prioridad'] = (
        candidates['rotacion_linea_tienda'] *
        np.where(priorizar_propias & candidates['es_marca_propia'], 1.2, 1.0)
    ).round(4)
    candidates['rotacion_linea_tienda'] = candidates['rotacion_linea_tienda'].round(4)

    empujes_df = candidates[[
        'sku', 'descripcion', 'marca', 'linea', 'tienda',
        'stk', 'ume', 'stock_cd', 'rotacion_linea_tienda',
        'vta_semanal_est', 'target_stock',
        'unidades_sugeridas', 'es_marca_propia', 'prioridad'
    ]].rename(columns={'stk': 'stk_actual_tienda'})

    empujes_df = empujes_df.sort_values('prioridad', ascending=False).reset_index(drop=True)

    print(f"  [Empujes] {len(empujes_df):,} empujes generados | "
          f"Cobertura target: {semanas_cob} sem | "
          f"Uds totales: {empujes_df['unidades_sugeridas'].sum():,}")

    return empujes_df


# ─────────────────────────────────────────────────────────────
#  OUTPUT 5: REDISTRIBUCIÓN ENTRE TIENDAS
# ─────────────────────────────────────────────────────────────

def find_redistribution(df_long, rotation_matrix, clusters_df, config=None):
    """
    Encuentra oportunidades de redistribución (vectorizado):
    SKU con stock alto + venta baja en tienda A → mover a tienda B
    donde la línea rota bien y tiene stock bajo.

    Retorna:
      - redist_df: DataFrame con sku, tienda_origen, tienda_destino,
        unidades, rotacion_destino, impacto_estimado
    """
    if config is None:
        config = _load_config()

    rot_alta = config.get('umbrales', {}).get('rotacion_alta', 0.04)
    rot_baja = config.get('umbrales', {}).get('rotacion_baja', 0.015)
    min_stk = config.get('umbrales', {}).get('stock_alto_unidades', 10)

    # Stock por SKU × tienda
    sku_tienda = df_long.groupby(['sku', 'descripcion', 'marca', 'linea', 'tienda']).agg(
        stk=('stk', 'sum'), vta=('vta', 'sum'),
        es_liquidacion=('es_liquidacion', 'first'),
    ).reset_index()
    sku_tienda['rotacion_sku'] = np.where(sku_tienda['stk'] > 0, sku_tienda['vta'] / sku_tienda['stk'], 0)

    # Cluster lookup
    cluster_map = dict(zip(clusters_df['tienda'], clusters_df['cluster_id']))

    # ORIGENES: stock alto + rotación baja, excluyendo liquidación
    origenes = sku_tienda[
        (sku_tienda['stk'] >= min_stk) &
        (sku_tienda['rotacion_sku'] < rot_baja) &
        (~sku_tienda['es_liquidacion'])
    ].copy()
    origenes = origenes.rename(columns={'tienda': 'tienda_origen', 'stk': 'stk_origen', 'vta': 'vta_origen'})

    if len(origenes) == 0:
        return pd.DataFrame()

    # Rotación long-form: líneas × tiendas donde la línea rota bien
    rot_long = rotation_matrix.reset_index().melt(
        id_vars='linea', var_name='tienda_destino', value_name='rotacion_destino'
    )
    rot_long = rot_long[rot_long['rotacion_destino'] >= rot_alta]

    # Cross: origen × destinos donde su línea rota bien
    candidates = origenes.merge(rot_long, on='linea', how='inner')
    # No redistribuir a sí mismo
    candidates = candidates[candidates['tienda_origen'] != candidates['tienda_destino']]

    if len(candidates) == 0:
        return pd.DataFrame()

    # Merge stock destino
    dest_stk = sku_tienda[['sku', 'tienda', 'stk']].rename(
        columns={'tienda': 'tienda_destino', 'stk': 'stk_destino'}
    )
    candidates = candidates.merge(dest_stk, on=['sku', 'tienda_destino'], how='left')
    candidates['stk_destino'] = candidates['stk_destino'].fillna(0).astype(int)

    # Solo donde destino tiene poco stock
    candidates = candidates[candidates['stk_destino'] < min_stk]

    # Calcular unidades
    candidates['unidades_sugeridas'] = np.minimum(
        candidates['stk_origen'] // 2,
        np.maximum(min_stk - candidates['stk_destino'], 3)
    ).astype(int)
    candidates = candidates[candidates['unidades_sugeridas'] > 0]

    # Mismo cluster?
    candidates['cluster_origen'] = candidates['tienda_origen'].map(cluster_map)
    candidates['cluster_destino'] = candidates['tienda_destino'].map(cluster_map)
    candidates['mismo_cluster'] = candidates['cluster_origen'] == candidates['cluster_destino']

    # Prioridad
    candidates['rotacion_destino'] = candidates['rotacion_destino'].round(4)
    candidates['prioridad'] = (candidates['rotacion_destino'] * candidates['unidades_sugeridas']).round(2)

    # Solo quedarnos con el mejor destino por SKU×origen
    candidates = candidates.sort_values('prioridad', ascending=False)
    redist_df = candidates.drop_duplicates(subset=['sku', 'tienda_origen'], keep='first')

    redist_df = redist_df[[
        'sku', 'descripcion', 'marca', 'linea',
        'tienda_origen', 'stk_origen', 'vta_origen',
        'tienda_destino', 'stk_destino', 'rotacion_destino',
        'unidades_sugeridas', 'mismo_cluster', 'prioridad'
    ]].reset_index(drop=True)

    return redist_df


# ─────────────────────────────────────────────────────────────
#  OUTPUT 6: SEÑALES DE PRODUCCIÓN NACIONAL
# ─────────────────────────────────────────────────────────────

def find_production_signals(df_long, rotation_matrix, clusters_df, config=None):
    """
    Detecta líneas/marcas donde hay alta rotación + baja cobertura
    en 3+ tiendas del mismo cluster → señal de producción.

    Prioriza marcas propias (donde hay control de producción).

    Retorna:
      - signals_df: DataFrame con marca, linea, cluster, n_tiendas,
        tiendas, vta_total, stk_total, cobertura_prom, accion
    """
    if config is None:
        config = _load_config()

    rot_alta = config.get('umbrales', {}).get('rotacion_alta', 0.04)
    cob_baja = config.get('umbrales', {}).get('cobertura_baja_sem', 4)
    min_tiendas = config.get('umbrales', {}).get('min_tiendas_senal_produccion', 3)
    marcas_propias = config.get('marcas_propias', [])

    cluster_map = dict(zip(clusters_df['tienda'], clusters_df['cluster_id']))

    # Agrupar por marca × línea × tienda
    agg = df_long.groupby(['marca', 'linea', 'tienda']).agg(
        stk=('stk', 'sum'),
        vta=('vta', 'sum'),
        cobertura_sem=('cobertura_sem', 'mean'),
    ).reset_index()

    agg['rotacion'] = np.where(agg['stk'] > 0, agg['vta'] / agg['stk'], 0)
    agg['cluster_id'] = agg['tienda'].map(cluster_map)

    # Filtrar: alta rotación + baja cobertura
    señales_raw = agg[(agg['rotacion'] >= rot_alta) &
                      ((agg['cobertura_sem'] <= cob_baja) | (agg['cobertura_sem'].isna()))]

    # Agrupar por marca × línea × cluster: contar tiendas
    if len(señales_raw) == 0:
        return pd.DataFrame()

    grouped = señales_raw.groupby(['marca', 'linea', 'cluster_id']).agg(
        n_tiendas=('tienda', 'count'),
        tiendas=('tienda', lambda x: ', '.join(sorted(x))),
        vta_total=('vta', 'sum'),
        stk_total=('stk', 'sum'),
        cobertura_prom=('cobertura_sem', 'mean'),
        rotacion_prom=('rotacion', 'mean'),
    ).reset_index()

    # Filtrar: mínimo N tiendas con el patrón
    signals = grouped[grouped['n_tiendas'] >= min_tiendas].copy()

    if len(signals) == 0:
        return pd.DataFrame()

    signals['es_marca_propia'] = signals['marca'].isin(marcas_propias)
    signals['cobertura_prom'] = signals['cobertura_prom'].round(1)
    signals['rotacion_prom'] = signals['rotacion_prom'].round(4)

    # Prioridad: marcas propias primero, luego por n_tiendas × vta
    signals['prioridad'] = (
        signals['n_tiendas'] * signals['vta_total'] *
        (2.0 if signals['es_marca_propia'].any() else 1.0)
    )
    # Recalcular por fila
    signals['prioridad'] = signals.apply(
        lambda r: r['n_tiendas'] * r['vta_total'] * (2.0 if r['es_marca_propia'] else 1.0),
        axis=1
    )

    signals['accion'] = signals.apply(
        lambda r: f"Producir {r['linea']} {r['marca']} — {r['n_tiendas']} tiendas con demanda insatisfecha"
        if r['es_marca_propia']
        else f"Negociar reposición {r['linea']} {r['marca']} — {r['n_tiendas']} tiendas",
        axis=1
    )

    signals = signals.sort_values('prioridad', ascending=False)

    return signals


# ─────────────────────────────────────────────────────────────
#  ORQUESTADOR PRINCIPAL
# ─────────────────────────────────────────────────────────────

def build_afinidad(excel_path):
    """
    Ejecuta el análisis completo de Afinidad Producto × Plaza.

    Retorna dict con los 6 outputs:
      - rotation_matrix: DataFrame pivot
      - rotation_stats: DataFrame con detalle
      - clusters_df: DataFrame asignación cluster
      - perfiles: dict {cluster_id: {linea: rotacion}}
      - rotation_vectors: DataFrame (tiendas × líneas)
      - anomalias_df: DataFrame anomalías
      - empujes_df: DataFrame empujes CD→tienda
      - redistribucion_df: DataFrame redistribución
      - produccion_df: DataFrame señales producción
      - tiendas_activas: list
      - config: dict
    """
    config = _load_config()

    # 1. Cargar datos
    df_long, tiendas_activas = load_tienda_data(excel_path, config)

    # 2. Heatmap rotación
    rotation_matrix, rotation_stats = build_rotation_matrix(df_long)

    # 3. Clusters
    clusters_df, perfiles, rotation_vectors = cluster_tiendas(df_long, config)

    # 4. Anomalías
    anomalias_df = find_anomalies(df_long, config)

    # 5. Empujes CD→Tiendas
    empujes_df = build_empujes_cd(df_long, rotation_matrix, clusters_df, config)

    # 6. Redistribución
    redistribucion_df = find_redistribution(df_long, rotation_matrix, clusters_df, config)

    # 7. Señales producción
    produccion_df = find_production_signals(df_long, rotation_matrix, clusters_df, config)

    return {
        'rotation_matrix': rotation_matrix,
        'rotation_stats': rotation_stats,
        'clusters_df': clusters_df,
        'perfiles': perfiles,
        'rotation_vectors': rotation_vectors,
        'anomalias_df': anomalias_df,
        'empujes_df': empujes_df,
        'redistribucion_df': redistribucion_df,
        'produccion_df': produccion_df,
        'tiendas_activas': tiendas_activas,
        'df_long': df_long,
        'config': config,
    }
