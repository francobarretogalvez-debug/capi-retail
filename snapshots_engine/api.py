"""
API de consulta para snapshots.

Funciones de utilidad que las demás secciones pueden llamar fácilmente
para consultar datos históricos.

Prompt F+: funciones de análisis comparativo semanal.
"""
import numpy as np
import pandas as pd
from .storage import load_snapshot, load_all_snapshots, list_available_weeks


def get_snapshot(semana: str) -> pd.DataFrame:
    """
    Obtener snapshot de una semana específica.

    Args:
        semana: identificador YYYY-WW (ej: '2026-19').

    Returns:
        DataFrame con datos del snapshot.
    """
    return load_snapshot(semana)


def get_venta_ultimas_n_semanas(n: int = 4, hasta_semana: str = None) -> pd.DataFrame:
    """
    Obtener venta acumulada de las últimas N semanas por SKU.

    Args:
        n: número de semanas hacia atrás (default 4).
        hasta_semana: semana tope (YYYY-WW). Si None, usa la más reciente.

    Returns:
        DataFrame con columnas: sku, marca, vta_total_unidades, vta_total_soles,
        n_semanas_con_data, vta_promedio_unidades.
    """
    weeks = list_available_weeks()
    if not weeks:
        return pd.DataFrame()

    if hasta_semana is None:
        hasta_semana = weeks[-1]

    # Filtrar semanas <= hasta_semana y tomar últimas N
    weeks_filtradas = [w for w in weeks if w <= hasta_semana]
    weeks_ventana = weeks_filtradas[-n:]

    if not weeks_ventana:
        return pd.DataFrame()

    # Cargar y concatenar
    frames = []
    for w in weeks_ventana:
        try:
            df = load_snapshot(w)
            # Fix B1 (auditoría 2026-08-23): unidades_vendidas / venta_soles son
            # ACUMULADOS de temporada — sumarlos entre semanas multiplica el
            # conteo. La venta SEMANAL real es vta_u_sem_1ant; los soles
            # semanales se estiman con el precio realizado promedio.
            _f = df[['sku', 'marca']].copy()
            _col_sem = df['vta_u_sem_1ant'] if 'vta_u_sem_1ant' in df.columns \
                else pd.Series(0, index=df.index)
            _f['vta_uds_semana'] = pd.to_numeric(_col_sem, errors='coerce').fillna(0)
            _uds_acum = pd.to_numeric(df.get('unidades_vendidas'), errors='coerce')
            _soles_acum = pd.to_numeric(df.get('venta_soles'), errors='coerce')
            _precio_real = (_soles_acum / _uds_acum).where(_uds_acum > 0)
            _f['vta_soles_semana'] = (_f['vta_uds_semana'] * _precio_real.fillna(0)).round(2)
            frames.append(_f)
        except Exception:
            continue

    if not frames:
        return pd.DataFrame()

    df_all = pd.concat(frames, ignore_index=True)

    # Agregar por SKU
    result = df_all.groupby(['sku', 'marca'], as_index=False).agg(
        vta_total_unidades=('vta_uds_semana', 'sum'),
        vta_total_soles=('vta_soles_semana', 'sum'),
        n_semanas_con_data=('vta_uds_semana', 'count'),
    )
    result['vta_promedio_unidades'] = (
        result['vta_total_unidades'] / result['n_semanas_con_data']
    ).round(1)

    return result


def get_velocidad_venta(sku: int = None, n_semanas: int = 4,
                        hasta_semana: str = None) -> pd.DataFrame:
    """
    Calcular velocidad de venta semanal promedio.

    Args:
        sku: filtrar por SKU específico (None = todos).
        n_semanas: ventana de semanas (default 4).
        hasta_semana: semana tope.

    Returns:
        DataFrame con: sku, marca, velocidad_unidades, velocidad_soles,
        tendencia (ratio semanas recientes vs antiguas).
    """
    df = get_venta_ultimas_n_semanas(n=n_semanas, hasta_semana=hasta_semana)
    if df.empty:
        return pd.DataFrame()

    if sku is not None:
        df = df[df['sku'] == sku]

    df = df.rename(columns={
        'vta_promedio_unidades': 'velocidad_unidades',
    })
    df['velocidad_soles'] = (df['vta_total_soles'] / df['n_semanas_con_data']).round(1)

    # Tendencia: comparar mitad reciente vs mitad antigua
    # Para esto necesitamos los datos semanales — se calcula aparte
    # Por ahora solo velocidad promedio
    df['tendencia'] = None  # Se implementará con más semanas disponibles

    return df[['sku', 'marca', 'velocidad_unidades', 'velocidad_soles',
               'n_semanas_con_data', 'tendencia']]


def get_evolucion_stock(sku: int, desde: str = None, hasta: str = None) -> pd.DataFrame:
    """
    Obtener evolución de stock de un SKU a lo largo del tiempo.

    Args:
        sku: código del SKU.
        desde: semana inicio (YYYY-WW). Si None, desde la primera disponible.
        hasta: semana fin (YYYY-WW). Si None, hasta la última.

    Returns:
        DataFrame con: semana_iso, fecha_cierre, stock_total, stock_tiendas,
        stock_cd, unidades_vendidas, delta_stock.
    """
    weeks = list_available_weeks()
    if desde:
        weeks = [w for w in weeks if w >= desde]
    if hasta:
        weeks = [w for w in weeks if w <= hasta]

    if not weeks:
        return pd.DataFrame()

    rows = []
    for w in weeks:
        try:
            df = load_snapshot(w)
            fila = df[df['sku'] == sku]
            if not fila.empty:
                r = fila.iloc[0]
                rows.append({
                    'semana_iso': w,
                    'fecha_cierre': r.get('fecha_cierre', ''),
                    'stock_total': int(r.get('stock_total', 0)),
                    'stock_tiendas': int(r.get('stock_tiendas', 0)),
                    'stock_cd': int(r.get('stock_cd', 0)),
                    'unidades_vendidas': int(r.get('unidades_vendidas', 0)),
                })
        except Exception:
            continue

    if not rows:
        return pd.DataFrame()

    df_result = pd.DataFrame(rows)

    # Calcular delta de stock (cambio entre semanas)
    df_result['delta_stock'] = df_result['stock_total'].diff()

    return df_result


def detect_reposiciones(sku: int, n_semanas: int = 4) -> pd.DataFrame:
    """
    Detectar reposiciones (incrementos de stock entre semanas) para un SKU.

    Una reposición se detecta cuando:
    stock_semana_N > stock_semana_N-1 + venta_semana_N
    (el stock subió más de lo que se vendió → ingresó mercadería)

    Args:
        sku: código del SKU.
        n_semanas: ventana de análisis.

    Returns:
        DataFrame con semanas donde hubo reposición y unidades estimadas.
    """
    weeks = list_available_weeks()[-n_semanas:]
    evol = get_evolucion_stock(sku, desde=weeks[0] if weeks else None)

    if evol.empty or len(evol) < 2:
        return pd.DataFrame()

    repos = []
    for i in range(1, len(evol)):
        prev = evol.iloc[i - 1]
        curr = evol.iloc[i]
        # stock esperado = stock anterior - venta actual
        stock_esperado = prev['stock_total'] - curr['unidades_vendidas']
        # Si el stock real > esperado, hubo reposición
        diff = curr['stock_total'] - stock_esperado
        if diff > 0:
            repos.append({
                'semana_iso': curr['semana_iso'],
                'stock_anterior': prev['stock_total'],
                'venta': curr['unidades_vendidas'],
                'stock_actual': curr['stock_total'],
                'unidades_repuestas': diff,
            })

    return pd.DataFrame(repos)


# ─────────────────────────────────────────────────────────────
#  FUNCIONES DE ANÁLISIS COMPARATIVO (Prompt F+)
# ─────────────────────────────────────────────────────────────

def compare_weeks(sem_a: str, sem_b: str) -> dict:
    """
    Compara KPIs agregados entre dos semanas.

    Args:
        sem_a: semana anterior (YYYY-WW).
        sem_b: semana posterior (YYYY-WW).

    Returns:
        dict con KPIs de ambas semanas y deltas absolutos/porcentuales.
    """
    try:
        df_a = load_snapshot(sem_a)
        df_b = load_snapshot(sem_b)
    except FileNotFoundError:
        return {}

    def _kpis(df, sem):
        # Fix B1 (auditoría 2026-08-23): la venta comparable entre semanas es
        # la SEMANAL (vta_u_sem_1ant), no los acumulados de temporada — el Δ%
        # que se mostraba antes comparaba acumulado vs acumulado y salía mal.
        _col_sem = df['vta_u_sem_1ant'] if 'vta_u_sem_1ant' in df.columns \
            else pd.Series(0, index=df.index)
        _uds_sem = pd.to_numeric(_col_sem, errors='coerce').fillna(0)
        _uds_acum = pd.to_numeric(df.get('unidades_vendidas'), errors='coerce')
        _soles_acum = pd.to_numeric(df.get('venta_soles'), errors='coerce')
        _precio_real = (_soles_acum / _uds_acum).where(_uds_acum > 0)
        _soles_sem = float((_uds_sem * _precio_real.fillna(0)).sum())
        return {
            'semana': sem,
            'n_skus': int(df['sku'].nunique()),
            'stock_total': int(df['stock_total'].sum()),
            'stock_valorizado': float(df['stock_valor_costo'].sum()) if 'stock_valor_costo' in df.columns else 0,
            'venta_unidades': int(_uds_sem.sum()),
            'venta_soles': round(_soles_sem, 2),
            'contribucion': float(df['contribucion_soles'].sum()) if 'contribucion_soles' in df.columns else 0,
            'cob_promedio': float(df['cobertura_sem'].mean()) if 'cobertura_sem' in df.columns else 0,
        }

    ka = _kpis(df_a, sem_a)
    kb = _kpis(df_b, sem_b)

    deltas = {}
    for key in ['stock_total', 'stock_valorizado', 'venta_unidades', 'venta_soles',
                'contribucion', 'cob_promedio']:
        va, vb = ka[key], kb[key]
        deltas[f'{key}_delta'] = round(vb - va, 2)
        deltas[f'{key}_pct'] = round((vb - va) / va * 100, 1) if va else 0

    return {'semana_a': ka, 'semana_b': kb, 'deltas': deltas}


def detect_state_changes(sem_a: str, sem_b: str) -> pd.DataFrame:
    """
    Detecta SKUs que cambiaron de estado entre dos semanas.

    Clasifica cada SKU con taxonomia.classify_series() (vectorizado) y compara.

    Args:
        sem_a: semana anterior.
        sem_b: semana posterior.

    Returns:
        DataFrame con: sku, marca, estado_a, estado_b, cambio (mejora/empeora/lateral).
    """
    from taxonomia import classify_series, ESTADO_ORDEN

    try:
        df_a = load_snapshot(sem_a)
        df_b = load_snapshot(sem_b)
    except FileNotFoundError:
        return pd.DataFrame()

    def _classify_df(df):
        df = df.copy()
        df['estado'] = classify_series(
            df['cobertura_sem'],
            df.get('edad_semanas'),
            df.get('rango_antiguedad'),
        )
        return df[['sku', 'marca', 'estado']]

    ea = _classify_df(df_a).rename(columns={'estado': 'estado_a'})
    eb = _classify_df(df_b).rename(columns={'estado': 'estado_b'})

    merged = ea.merge(eb, on=['sku', 'marca'], how='outer')
    merged = merged.dropna(subset=['estado_a', 'estado_b'])
    # Solo los que cambiaron
    changed = merged[merged['estado_a'] != merged['estado_b']].copy()

    if changed.empty:
        return changed

    # Determinar dirección del cambio
    orden = {e: i for i, e in enumerate(ESTADO_ORDEN)}

    def _dir(row):
        ia = orden.get(row['estado_a'], 99)
        ib = orden.get(row['estado_b'], 99)
        if ib < ia:
            return 'mejora'
        elif ib > ia:
            return 'empeora'
        return 'lateral'

    changed['cambio'] = changed.apply(_dir, axis=1)
    return changed.sort_values('cambio', ascending=True).reset_index(drop=True)


def evolucion_marca(desde: str = None, hasta: str = None) -> pd.DataFrame:
    """
    Tendencias por marca a lo largo de las semanas disponibles.

    Usa classify_series() vectorizado para clasificar estados (~10x más rápido).

    Args:
        desde: semana inicio (YYYY-WW). None = primera disponible.
        hasta: semana fin (YYYY-WW). None = última disponible.

    Returns:
        DataFrame con: semana_iso, marca, cob_promedio, pct_quiebre,
        venta_unidades, venta_soles, stock_total, n_skus.
    """
    from taxonomia import classify_series

    weeks = list_available_weeks()
    if desde:
        weeks = [w for w in weeks if w >= desde]
    if hasta:
        weeks = [w for w in weeks if w <= hasta]

    if not weeks:
        return pd.DataFrame()

    rows = []
    for w in weeks:
        try:
            df = load_snapshot(w)
        except FileNotFoundError:
            continue

        # Clasificar vectorizado
        df = df.copy()
        df['estado'] = classify_series(
            df['cobertura_sem'],
            df.get('edad_semanas'),
            df.get('rango_antiguedad'),
        )

        for marca, g in df.groupby('marca'):
            n = len(g)
            n_quiebre = int((g['estado'] == 'QUIEBRE').sum())
            rows.append({
                'semana_iso': w,
                'marca': marca,
                'cob_promedio': round(float(g['cobertura_sem'].mean()), 1) if not g['cobertura_sem'].isna().all() else 0,
                'pct_quiebre': round(n_quiebre / n * 100, 1) if n else 0,
                'venta_unidades': int(g['unidades_vendidas'].sum()),
                'venta_soles': float(g['venta_soles'].sum()),
                'stock_total': int(g['stock_total'].sum()),
                'n_skus': n,
            })

    return pd.DataFrame(rows)


def detect_repo_cumplimiento(sem_a: str, sem_b: str) -> pd.DataFrame:
    """
    Detecta si repos sugeridas en sem_a se cumplieron en sem_b.

    Lógica: si un SKU tenía stock_cd=0 en sem_a y stock_cd>0 en sem_b,
    hubo reposición al CD. Si stock_tiendas subió más que lo esperado
    por la venta, hubo despacho a tiendas.

    Args:
        sem_a: semana donde se detectó necesidad.
        sem_b: semana posterior para verificar cumplimiento.

    Returns:
        DataFrame con: sku, marca, stock_cd_a, stock_cd_b, stock_tiendas_a,
        stock_tiendas_b, venta_b, repo_cd (bool), despacho_tiendas (bool).
    """
    try:
        df_a = load_snapshot(sem_a)
        df_b = load_snapshot(sem_b)
    except FileNotFoundError:
        return pd.DataFrame()

    cols = ['sku', 'marca', 'stock_cd', 'stock_tiendas', 'stock_total', 'unidades_vendidas']
    a = df_a[cols].rename(columns={
        'stock_cd': 'stock_cd_a', 'stock_tiendas': 'stock_tiendas_a',
        'stock_total': 'stock_total_a', 'unidades_vendidas': 'venta_a',
    })
    b = df_b[cols].rename(columns={
        'stock_cd': 'stock_cd_b', 'stock_tiendas': 'stock_tiendas_b',
        'stock_total': 'stock_total_b', 'unidades_vendidas': 'venta_b',
    })

    merged = a.merge(b, on=['sku', 'marca'], how='inner')

    # Repo al CD: stock_cd subió
    merged['repo_cd'] = merged['stock_cd_b'] > merged['stock_cd_a']

    # Despacho a tiendas: stock_tiendas subió más de lo que debería si solo vendió
    # stock_tiendas esperado = stock_tiendas_a - venta_b (consumo)
    # Si stock_tiendas_b > esperado → hubo despacho
    merged['stock_tiendas_esperado'] = merged['stock_tiendas_a'] - merged['venta_b']
    merged['despacho_tiendas'] = merged['stock_tiendas_b'] > merged['stock_tiendas_esperado']

    # Filtrar solo casos donde hubo algún movimiento de repo
    result = merged[merged['repo_cd'] | merged['despacho_tiendas']].copy()
    result['unidades_repo_cd'] = (result['stock_cd_b'] - result['stock_cd_a']).clip(lower=0)
    result['unidades_despacho'] = (result['stock_tiendas_b'] - result['stock_tiendas_esperado']).clip(lower=0)

    return result[['sku', 'marca', 'stock_cd_a', 'stock_cd_b', 'stock_tiendas_a',
                    'stock_tiendas_b', 'venta_b', 'repo_cd', 'despacho_tiendas',
                    'unidades_repo_cd', 'unidades_despacho']].reset_index(drop=True)


def detect_aceleracion(n_semanas: int = 4, umbral_ratio: float = 1.3) -> pd.DataFrame:
    """
    Detecta SKUs cuya velocidad de venta se está acelerando.

    Usa las columnas vta_u_sem_1ant..4ant del snapshot más reciente
    para calcular tendencia intra-snapshot, y compara con snapshots
    anteriores para detectar aceleración sostenida.

    Método: ratio = venta_reciente / venta_antigua
      - ratio > umbral_ratio → ACELERANDO
      - ratio < 1/umbral_ratio → DESACELERANDO
      - else → ESTABLE

    Args:
        n_semanas: cuántos snapshots considerar.
        umbral_ratio: ratio mínimo para considerar aceleración (default 1.3 = +30%).

    Returns:
        DataFrame con: sku, marca, vta_reciente, vta_antigua, ratio,
        tendencia (ACELERANDO/DESACELERANDO/ESTABLE), semanas_data.
    """
    weeks = list_available_weeks()
    if not weeks:
        return pd.DataFrame()

    # Tomar el snapshot más reciente que tenga vta_u_sem_Xant
    df_latest = load_snapshot(weeks[-1])

    vta_cols = [c for c in df_latest.columns if c.startswith('vta_u_sem_') and c.endswith('ant')]
    if len(vta_cols) < 2:
        return pd.DataFrame()

    # Ordenar: sem_1ant es más reciente, sem_4ant es más antigua
    vta_cols_sorted = sorted(vta_cols, key=lambda c: int(c.replace('vta_u_sem_', '').replace('ant', '')))

    # Dividir en mitad reciente y mitad antigua
    mid = len(vta_cols_sorted) // 2
    cols_recientes = vta_cols_sorted[:mid]  # sem_1ant, sem_2ant
    cols_antiguas = vta_cols_sorted[mid:]   # sem_3ant, sem_4ant

    df = df_latest.copy()
    df['vta_reciente'] = df[cols_recientes].sum(axis=1)
    df['vta_antigua'] = df[cols_antiguas].sum(axis=1)

    # Filtrar SKUs con algo de venta (evitar div by 0 y noise)
    df = df[(df['vta_reciente'] > 0) | (df['vta_antigua'] > 0)].copy()

    # ── P1-2: Filtrar ruido de ratio=99 ──
    # SKUs con vta_antigua=0 generan ratio inflado artificialmente.
    # Solución: marcar como REACTIVADO (no como ACELERANDO) y dar ratio real solo
    # cuando hay suficiente historial de venta antigua.
    _min_vta_antigua = 0.5  # mínimo 0.5 uds en las 2 sem antiguas para ratio válido

    df['ratio'] = np.where(
        df['vta_antigua'] >= _min_vta_antigua,
        df['vta_reciente'] / df['vta_antigua'],
        np.where(df['vta_reciente'] > 0, np.nan, 1.0)  # NaN = reactivado
    )

    # Clasificar tendencia
    df['tendencia'] = np.where(
        df['ratio'].isna(), 'REACTIVADO',  # era 0 → ahora vende, sin historial para ratio
        np.where(df['ratio'] >= umbral_ratio, 'ACELERANDO',
                 np.where(df['ratio'] <= 1 / umbral_ratio, 'DESACELERANDO', 'ESTABLE'))
    )

    # Para REACTIVADO, poner ratio=0 para que no distorsione ordenamiento
    df['ratio'] = df['ratio'].fillna(0.0)

    # Enriquecer con comparación cross-snapshot si hay suficientes semanas
    df['semanas_data'] = len(vta_cols_sorted)
    df['semana_snapshot'] = weeks[-1]

    # Si hay snapshot anterior, agregar velocidad previa para confirmar tendencia
    if len(weeks) >= 2:
        df_prev = load_snapshot(weeks[-2])
        prev_vta_cols = [c for c in df_prev.columns if c.startswith('vta_u_sem_') and c.endswith('ant')]
        if prev_vta_cols:
            df_prev['vta_total_prev_snap'] = df_prev[prev_vta_cols].sum(axis=1)
            prev_vel = df_prev[['sku', 'vta_total_prev_snap']]
            df = df.merge(prev_vel, on='sku', how='left')
            df['vta_total_curr_snap'] = df[vta_cols_sorted].sum(axis=1)
            df['aceleracion_cross_snap'] = np.where(
                df['vta_total_prev_snap'] > 0,
                df['vta_total_curr_snap'] / df['vta_total_prev_snap'],
                np.nan
            )
        else:
            df['aceleracion_cross_snap'] = np.nan
    else:
        df['aceleracion_cross_snap'] = np.nan

    result = df[['sku', 'marca', 'vta_reciente', 'vta_antigua', 'ratio',
                  'tendencia', 'semanas_data', 'semana_snapshot',
                  'aceleracion_cross_snap']].copy()

    result['ratio'] = result['ratio'].round(2)
    result['aceleracion_cross_snap'] = result['aceleracion_cross_snap'].round(2)

    return result.sort_values('ratio', ascending=False).reset_index(drop=True)


def predict_stockout(semana: str = None, min_venta_semanal: float = 0.5) -> pd.DataFrame:
    """
    Predice en cuántas semanas cada SKU podría quebrar stock,
    manteniendo la tendencia actual de venta.

    Método:
      1. Calcula velocidad de venta semanal (promedio de vta_u_sem_1..4ant)
      2. Ajusta por tendencia (si acelerando, usa velocidad ponderada reciente)
      3. semanas_hasta_quiebre = stock_total / velocidad_ajustada
      4. Resta lead_time del proveedor para calcular margen_real
      5. Clasifica riesgo sobre margen_real: CRÍTICO (<2 sem), ALTO (2-4), MEDIO (4-8), BAJO (>8)

    Args:
        semana: YYYY-WW del snapshot a analizar. None = más reciente.
        min_venta_semanal: mínimo de venta para considerar (evita ruido).

    Returns:
        DataFrame con: sku, marca, stock_total, velocidad_semanal,
        velocidad_ajustada, semanas_hasta_quiebre, lead_time_sem,
        margen_real_sem, riesgo, tendencia_vta, venta_riesgo_soles.
    """
    import json, os

    weeks = list_available_weeks()
    if not weeks:
        return pd.DataFrame()

    if semana is None:
        semana = weeks[-1]

    try:
        df = load_snapshot(semana)
    except FileNotFoundError:
        return pd.DataFrame()

    vta_cols = sorted(
        [c for c in df.columns if c.startswith('vta_u_sem_') and c.endswith('ant')],
        key=lambda c: int(c.replace('vta_u_sem_', '').replace('ant', ''))
    )

    if not vta_cols:
        return pd.DataFrame()

    df = df.copy()

    # Velocidad promedio simple
    df['velocidad_semanal'] = df[vta_cols].mean(axis=1)

    # Filtrar SKUs con venta relevante y stock positivo
    df = df[(df['velocidad_semanal'] >= min_venta_semanal) & (df['stock_total'] > 0)].copy()

    if df.empty:
        return pd.DataFrame()

    # Velocidad ajustada por tendencia (peso mayor a semanas recientes)
    # Pesos: sem_1ant=4, sem_2ant=3, sem_3ant=2, sem_4ant=1
    pesos = list(range(len(vta_cols), 0, -1))
    peso_total = sum(pesos)

    weighted_sum = sum(df[col] * w for col, w in zip(vta_cols, pesos))
    df['velocidad_ajustada'] = (weighted_sum / peso_total).round(2)

    # Evitar velocidad ajustada = 0
    df['velocidad_ajustada'] = df['velocidad_ajustada'].clip(lower=0.1)

    # Tendencia de venta (ratio sem recientes vs antiguas)
    mid = len(vta_cols) // 2
    df['vta_rec'] = df[vta_cols[:mid]].mean(axis=1)
    df['vta_ant'] = df[vta_cols[mid:]].mean(axis=1)
    df['tendencia_vta'] = np.where(
        df['vta_ant'] > 0,
        (df['vta_rec'] / df['vta_ant']).round(2),
        np.where(df['vta_rec'] > 0, 99.0, 1.0)
    )

    # Semanas hasta quiebre (bruto)
    df['semanas_hasta_quiebre'] = (df['stock_total'] / df['velocidad_ajustada']).round(1)

    # ── P1-1: Restar lead time del proveedor ──
    # Cargar config_lead_times.json
    _lt_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config_lead_times.json')
    _lt_default = 14  # 2 semanas default
    _lt_map = {}
    try:
        with open(_lt_path, 'r') as f:
            _lt_raw = json.load(f)
        _lt_default = _lt_raw.get('_default', 14)
        _lt_map = {k: v for k, v in _lt_raw.items() if not k.startswith('_')}
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Lead time en semanas (config está en días)
    df['lead_time_sem'] = df['marca'].map(
        {m: round(d / 7, 1) for m, d in _lt_map.items()}
    ).fillna(round(_lt_default / 7, 1))

    # Margen real = semanas hasta quiebre - lead time
    # Si margen_real < 0, ya es tarde para pedir
    df['margen_real_sem'] = (df['semanas_hasta_quiebre'] - df['lead_time_sem']).round(1)

    # Clasificar riesgo sobre margen_real (no sobre semanas brutas)
    df['riesgo'] = np.where(
        df['margen_real_sem'] < 2, 'CRÍTICO',
        np.where(df['margen_real_sem'] < 4, 'ALTO',
                 np.where(df['margen_real_sem'] < 8, 'MEDIO', 'BAJO'))
    )

    # ── P2-6: Venta en riesgo (soles) ──
    # Cuánto $ se pierde si este SKU quiebra: velocidad × precio × semanas_quiebre
    if 'precio_actual' in df.columns:
        df['venta_riesgo_soles'] = (df['velocidad_ajustada'] * df['precio_actual'] * df['semanas_hasta_quiebre']).round(0)
    elif 'venta_soles' in df.columns and 'unidades_vendidas' in df.columns:
        _precio_est = np.where(df['unidades_vendidas'] > 0,
                               df['venta_soles'] / df['unidades_vendidas'], 0)
        df['venta_riesgo_soles'] = (df['velocidad_ajustada'] * _precio_est * df['semanas_hasta_quiebre']).round(0)
    else:
        df['venta_riesgo_soles'] = 0

    result = df[['sku', 'marca', 'stock_total', 'velocidad_semanal',
                  'velocidad_ajustada', 'semanas_hasta_quiebre', 'lead_time_sem',
                  'margen_real_sem', 'riesgo', 'tendencia_vta',
                  'venta_riesgo_soles']].copy()

    return result.sort_values('margen_real_sem', ascending=True).reset_index(drop=True)


def get_resumen_semanal(semana: str = None) -> dict:
    """
    Genera un resumen de KPIs para una semana específica.

    Args:
        semana: YYYY-WW. Si None, usa la más reciente.

    Returns:
        dict con KPIs agregados.
    """
    weeks = list_available_weeks()
    if not weeks:
        return {}

    if semana is None:
        semana = weeks[-1]

    try:
        df = load_snapshot(semana)
    except FileNotFoundError:
        return {}

    # Fix A4 (2026-09-05): `unidades_vendidas` / `venta_soles` son ACUMULADOS de
    # temporada, no venta de la semana. La venta semanal real vive en vta_u_sem_1ant.
    _vta_sem = int(df['vta_u_sem_1ant'].sum()) if 'vta_u_sem_1ant' in df.columns else None
    return {
        'semana_iso': semana,
        'n_skus': int(df['sku'].nunique()),
        'stock_total_unidades': int(df['stock_total'].sum()),
        'stock_total_valorizado': float(df['stock_valor_costo'].sum()) if 'stock_valor_costo' in df.columns else 0,
        # Semanal (lo que un buyer llama "venta de la semana")
        'venta_semana_unidades': _vta_sem,
        # Acumulados de temporada (nombre explícito para que nadie los lea como semanales)
        'venta_acum_unidades': int(df['unidades_vendidas'].sum()),
        'venta_acum_soles': float(df['venta_soles'].sum()) if 'venta_soles' in df.columns else 0,
        'contribucion_acum': float(df['contribucion_soles'].sum()) if 'contribucion_soles' in df.columns else 0,
        # Compat con consumidores viejos (mismo significado acumulado que antes)
        'venta_total_unidades': int(df['unidades_vendidas'].sum()),
        'venta_total_soles': float(df['venta_soles'].sum()) if 'venta_soles' in df.columns else 0,
        'contribucion_total': float(df['contribucion_soles'].sum()) if 'contribucion_soles' in df.columns else 0,
        'marcas': int(df['marca'].nunique()) if 'marca' in df.columns else 0,
    }


def estimate_lost_sales(hasta_semana: str = None, marcas: set = None,
                        min_semanas_velocidad: int = 2,
                        tasa_recaptura: float = 0.30,
                        ajuste_estacional: bool = False, excluir_liquidacion_obsoleto=True) -> dict:  # v1 confunde quiebre con baja estación — ver nota
    """
    Estima la venta perdida (S/) por quiebres de stock en la ventana de
    snapshots disponibles. Devuelve una BANDA (conservadora-optimista),
    nunca un número falso-preciso.

    Metodología:
      1. Gate de actividad: solo SKUs con venta > 0 en la ventana
         (excluye mercadería MUERTA/DORMIDA que no es quiebre).
      2. Semanas en quiebre:
         - cierre de snapshot con stock_total == 0 → cuenta 0.5 semanas
           (convención V2: "si stock_cierre=0, semana cuenta como 0.5")
         - semana gap entre snapshots → cuenta solo si stock == 0 en ambos
           extremos Y la venta reconstruida (vta_u_sem_Nant) de esa semana
           es 0 → banda 0.5 (conservadora) / 1.0 (optimista)
      3. Velocidad de venta: promedio de la serie SEMANAL reconstruida
         (vta_u_sem_1..4ant de filas válidas — 'unidades_vendidas' es un
         acumulado y no se usa). Banda: promedio simple vs ponderado reciente.
      4. Precio: precio_vigente válido más reciente; guard contra precios
         basura (< S/5 o < 20% del precio blanco) con fallback a
         venta_soles/unidades_vendidas.
      5. perdida = velocidad × semanas_quiebre × precio (por banda).

    Args:
        hasta_semana: YYYY-WW límite superior de la ventana. None = todas.
        marcas: set de marcas (MAYÚSCULAS) a incluir. None = todas.
        min_semanas_velocidad: mínimo de semanas con data para estimar
            velocidad; SKUs bajo el mínimo se excluyen y se cuentan.

    Returns:
        dict con: banda_min, banda_max, semanas_analizadas, df_detalle,
        n_skus_afectados, n_skus_excluidos, supuestos (list[str]).
    """
    import datetime as _dt

    _empty = {
        'banda_min': 0.0, 'banda_max': 0.0, 'semanas_analizadas': [],
        'df_detalle': pd.DataFrame(), 'n_skus_afectados': 0,
        'n_skus_excluidos': 0, 'supuestos': ['Sin snapshots suficientes para estimar.'],
    }

    weeks = list_available_weeks()
    if hasta_semana:
        weeks = [w for w in weeks if w <= hasta_semana]
    if len(weeks) < 2:
        return _empty

    def _week_ord(iso: str) -> int:
        """YYYY-WW → índice absoluto de semana (ordinal del domingo // 7)."""
        y, w = iso.split('-')
        return _dt.date.fromisocalendar(int(y), int(w), 7).toordinal() // 7

    # ── Cargar snapshots de la ventana ──
    snaps = {}
    for w in weeks:
        try:
            _df = load_snapshot(w)
        except FileNotFoundError:
            continue
        if _df is None or _df.empty:
            continue
        if marcas is not None and 'marca' in _df.columns:
            _df = _df[_df['marca'].astype(str).str.upper().isin(marcas)]
        snaps[w] = _df
    if len(snaps) < 2:
        return _empty

    sem_list = sorted(snaps.keys(), key=_week_ord)
    ords = {w: _week_ord(w) for w in sem_list}

    # ── Series por SKU ──
    # IMPORTANTE (verificado contra data real): 'unidades_vendidas' es un ACUMULADO
    # histórico, NO venta semanal. La venta semanal real solo existe en las columnas
    # reconstruidas vta_u_sem_1ant..4ant. Además, las filas con stock 0 vienen
    # "zeroed" (ants en 0 aunque hubo venta antes del quiebre): sus ants NO sirven
    # para velocidad, solo para confirmar que un gap entre dos cierres en 0 no tuvo venta.
    # ventas_vel[sku][wk] = (prioridad N, uds)  — solo de filas válidas → velocidad
    # ventas_any[sku][wk] = uds                 — todas las filas → check de gaps
    ventas_vel, ventas_any, stocks, meta, precio_valido = {}, {}, {}, {}, {}
    for w in sem_list:
        df = snaps[w]
        wo = ords[w]
        vta_ant_cols = [c for c in df.columns if c.startswith('vta_u_sem_') and c.endswith('ant')]
        for row in df.itertuples(index=False):
            sku = getattr(row, 'sku', None)
            if sku is None:
                continue
            stk_row = float(getattr(row, 'stock_total', 0) or 0)
            ant_vals = {}
            for c in vta_ant_cols:
                n = int(c.replace('vta_u_sem_', '').replace('ant', ''))
                val = getattr(row, c, None)
                if val is None or (isinstance(val, float) and np.isnan(val)):
                    continue
                ant_vals[n] = float(val)
            cum = float(getattr(row, 'unidades_vendidas', 0) or 0)
            fila_zeroed = stk_row == 0 and cum == 0 and all(x == 0 for x in ant_vals.values())

            v_any = ventas_any.setdefault(sku, {})
            for n, val in ant_vals.items():
                v_any.setdefault(wo - n, val)
            if not fila_zeroed:
                v_vel = ventas_vel.setdefault(sku, {})
                for n, val in ant_vals.items():
                    wk = wo - n
                    prev = v_vel.get(wk)
                    if prev is None or prev[0] > n:
                        v_vel[wk] = (n, val)  # reconstrucción más cercana gana
            stocks.setdefault(sku, {})[wo] = stk_row
            # metadata del snapshot más reciente donde aparece el SKU
            _pv = float(getattr(row, 'precio_vigente', 0) or 0)
            _pb = float(getattr(row, 'precio_blanco', 0) or 0)
            meta[sku] = {
                'descripcion': getattr(row, 'descripcion', ''),
                'marca': getattr(row, 'marca', ''),
                'linea': getattr(row, 'linea', '') or '',
                'venta_soles_w': float(getattr(row, 'venta_soles', 0) or 0),
                'uds_w': float(getattr(row, 'unidades_vendidas', 0) or 0),
                'contrib_w': float(getattr(row, 'contribucion_soles', 0) or 0),
                'costo': float(getattr(row, 'costo_unitario', 0) or 0),
            }
            # último precio VÁLIDO de la historia (guard contra basura tipo S/ 0.01)
            if _pv >= 5 and (_pb <= 0 or _pv >= 0.2 * _pb):
                precio_valido[sku] = _pv

    # ── Índice estacional por línea: nivel de demanda semanal de la línea ──
    # Escala la velocidad histórica del SKU a la demanda que habría tenido EN la(s)
    # semana(s) del quiebre. Una línea que cae hacia invierno vendía menos cuando
    # quebró que cuando se midió su velocidad → no toda la velocidad histórica aplica.
    linea_sem = {}
    if ajuste_estacional:
        for _sku, _wkmap in ventas_any.items():
            _ln = meta.get(_sku, {}).get('linea', '') or '—'
            _d = linea_sem.setdefault(_ln, {})
            for _wk, _val in _wkmap.items():
                _d[_wk] = _d.get(_wk, 0.0) + float(_val or 0.0)

    def _factor_estacional(linea, vel_weeks, q_weeks):
        """Razón nivel-de-línea en semanas de quiebre vs semanas de velocidad,
        acotada a [0.5, 1.5] para no amplificar ruido. Solo usa semanas con data."""
        if not ajuste_estacional:
            return 1.0
        d = linea_sem.get(linea or '—')
        if not d:
            return 1.0
        base_vals = [d[w] for w in vel_weeks if w in d]
        targ_vals = [d[w] for w in q_weeks if w in d]
        if not base_vals or not targ_vals:
            return 1.0
        base, target = float(np.mean(base_vals)), float(np.mean(targ_vals))
        if base <= 0:
            return 1.0
        return float(min(1.5, max(0.5, target / base)))

    _recap = float(min(0.9, max(0.0, tasa_recaptura)))  # recaptura por sustitución

    # ── Candidatos: stock 0 en algún cierre Y venta semanal real > 0 (gate) ──
    n_excluidos = 0
    detalle = []
    for sku, stk in stocks.items():
        if min(stk.values()) > 0:
            continue  # nunca quebró en un cierre observado
        v = ventas_vel.get(sku, {})
        venta_total = sum(x[1] for x in v.values())
        if venta_total <= 0:
            continue  # mercadería muerta, no quiebre

        # Velocidad: serie semanal reconstruida de filas válidas
        vel_weeks = sorted(v.keys())
        if len(vel_weeks) < min_semanas_velocidad:
            n_excluidos += 1
            continue

        vel_vals = [v[wk][1] for wk in vel_weeks]
        vel_simple = float(np.mean(vel_vals))
        pesos = list(range(1, len(vel_vals) + 1))  # más reciente pesa más
        vel_pond = float(np.average(vel_vals, weights=pesos))
        vel_low, vel_high = min(vel_simple, vel_pond), max(vel_simple, vel_pond)
        if vel_high <= 0:
            n_excluidos += 1
            continue

        # Semanas en quiebre (+ ordinales donde hubo quiebre, para estacionalidad)
        sem_q_min = sem_q_max = 0.0
        q_weeks = []
        cierres = sorted(stk.keys())
        for wk in cierres:
            if stk[wk] == 0:
                sem_q_min += 0.5
                sem_q_max += 0.5
                q_weeks.append(wk)
        v_any = ventas_any.get(sku, {})
        for a, b in zip(cierres, cierres[1:]):
            if stk[a] == 0 and stk[b] == 0:
                for gap_wk in range(a + 1, b):
                    if v_any.get(gap_wk) == 0:
                        sem_q_min += 0.5
                        sem_q_max += 1.0
                        q_weeks.append(gap_wk)
        if sem_q_max <= 0:
            continue

        # Precio NETO (sin IGV) y margen — de la economía realizada del sistema.
        # venta_soles/unidades y contribucion/venta son acumulados de temporada →
        # precio promedio realizado y margen contable, ambos SIN IGV (consistentes
        # entre sí y con cómo Ripley reporta venta/margen). Evita el desfase de IGV
        # de precio_vigente y el costo_unitario faltante (~48% de SKUs).
        m = meta[sku]
        precio = (m['venta_soles_w'] / m['uds_w']) if m['uds_w'] > 0 else 0.0
        if precio <= 0:  # fallback: precio vigente desinflado de IGV
            _pv = precio_valido.get(sku, 0.0)
            precio = _pv / 1.18 if _pv > 0 else 0.0
        if precio <= 0:
            n_excluidos += 1
            continue
        margen_pct = (m['contrib_w'] / m['venta_soles_w']) if m['venta_soles_w'] > 0 else 0.35
        margen_pct = float(min(0.90, max(0.0, margen_pct)))  # margen contable acotado
        margen_unit = precio * margen_pct

        # Ajuste estacional de la velocidad (refinamiento #3)
        factor = _factor_estacional(m.get('linea', ''), vel_weeks, q_weeks)
        vlow_aj, vhigh_aj = vel_low * factor, vel_high * factor

        # Unidades perdidas (banda) y variantes de pérdida
        uds_min, uds_max = vlow_aj * sem_q_min, vhigh_aj * sem_q_max
        ing_bruto_min, ing_bruto_max = uds_min * precio, uds_max * precio        # ingreso (demanda)
        ing_neto_min, ing_neto_max = ing_bruto_min * (1 - _recap), ing_bruto_max * (1 - _recap)  # neto negocio
        mg_bruto_min, mg_bruto_max = uds_min * margen_unit, uds_max * margen_unit  # margen perdido
        mg_neto_min, mg_neto_max = mg_bruto_min * (1 - _recap), mg_bruto_max * (1 - _recap)

        detalle.append({
            'sku': sku, 'descripcion': m['descripcion'], 'marca': m['marca'],
            'linea': m.get('linea', ''),
            'velocidad_uds_sem': round(vel_simple, 2),
            'factor_estacional': round(factor, 2),
            'velocidad_ajustada': round(vel_simple * factor, 2),
            'semanas_quiebre_min': sem_q_min, 'semanas_quiebre_max': sem_q_max,
            'precio_usado': round(precio, 2), 'margen_pct': round(margen_pct * 100, 1),
            'uds_perdidas_min': round(uds_min, 0), 'uds_perdidas_max': round(uds_max, 0),
            # ingreso (revenue) — bruto = señal de demanda
            'perdida_min_soles': round(ing_bruto_min, 0),
            'perdida_max_soles': round(ing_bruto_max, 0),
            'ingreso_neto_min': round(ing_neto_min, 0),
            'ingreso_neto_max': round(ing_neto_max, 0),
            # margen perdido — lo que mueve EBITDA
            'margen_bruto_min': round(mg_bruto_min, 0), 'margen_bruto_max': round(mg_bruto_max, 0),
            'margen_neto_min': round(mg_neto_min, 0), 'margen_neto_max': round(mg_neto_max, 0),
        })

    df_detalle = pd.DataFrame(detalle)
    n_excluidos_liq = 0
    if not df_detalle.empty and excluir_liquidacion_obsoleto:
        # Regla Franco 2026-09-06: liquidación (dscto ≥40%), temporada en liquidación y >6 meses NO cuentan como quiebre
        try:
            from venta_perdida_semanal import exclusiones_quiebre
            _excl, _ = exclusiones_quiebre(sem_list[-1])
            _mask = df_detalle['sku'].astype(str).str.strip().isin(_excl)
            n_excluidos_liq = int(_mask.sum())
            df_detalle = df_detalle[~_mask]
        except Exception:
            pass
    if not df_detalle.empty:
        df_detalle = df_detalle.sort_values('perdida_max_soles', ascending=False).reset_index(drop=True)

    def _sum(col):
        return float(df_detalle[col].sum()) if (not df_detalle.empty and col in df_detalle) else 0.0

    supuestos = [
        f"Ventana: {sem_list[0]} a {sem_list[-1]} ({len(sem_list)} cierres de snapshot).",
        "Semana de cierre con stock 0 = 0.5 sem de quiebre; semanas gap entre snapshots 0.5-1.0 (confirmadas).",
        "Velocidad = serie semanal reconstruida (banda: promedio simple vs ponderado reciente).",
        ("Ajuste estacional ON: velocidad escalada por el nivel de demanda de la línea en la semana del quiebre (acotado 0.5-1.5x)."
         if ajuste_estacional else "Ajuste estacional OFF."),
        f"Recaptura por sustitución: {int(_recap*100)}% de la demanda en quiebre se recupera con otro SKU → 'neto al negocio'.",
        "Precio e ingreso SIN IGV (venta_soles/uds realizado); margen perdido = ingreso × margen contable (contribución/venta).",
        f"{n_excluidos} SKUs excluidos por historia o precio insuficientes." if n_excluidos else "Sin SKUs excluidos.",
        "Nivel SKU agregado cadena (el detalle por tienda usa la base actual).",
        (f"Excluidos {n_excluidos_liq} SKUs en liquidación (dscto ≥40%), temporada en liquidación u >6 meses (regla 2026-09-06)."
         if excluir_liquidacion_obsoleto else "Sin exclusión de liquidación/obsoletos."),
    ]

    return {
        # Compat: banda_min/max = INGRESO BRUTO (señal de demanda, nivel SKU)
        'banda_min': _sum('perdida_min_soles'),
        'banda_max': _sum('perdida_max_soles'),
        # Ingreso NETO al negocio (descontada sustitución) — titular recomendado
        'ingreso_neto_min': _sum('ingreso_neto_min'),
        'ingreso_neto_max': _sum('ingreso_neto_max'),
        # Margen perdido (lo que mueve EBITDA)
        'margen_bruto_min': _sum('margen_bruto_min'),
        'margen_bruto_max': _sum('margen_bruto_max'),
        'margen_neto_min': _sum('margen_neto_min'),
        'margen_neto_max': _sum('margen_neto_max'),
        'tasa_recaptura': _recap,
        'semanas_analizadas': sem_list,
        'df_detalle': df_detalle,
        'n_skus_afectados': int(len(df_detalle)),
        'n_skus_excluidos': int(n_excluidos),
        'supuestos': supuestos,
    }


def estimate_cd_reliability(umbral_volatil: float = 0.3, cd_min_relevante: int = 50) -> pd.DataFrame:
    """
    Confiabilidad del stock CD por SKU, a partir del histórico de snapshots.

    El reporte semanal de CD no es tiempo real (problema flagueado por Franco:
    "no muestra realmente cuál es el stock disponible para empujar"). Esta
    función mide, por SKU:
      - cd_deriva_sem: drenaje promedio del CD en uds/semana (solo bajadas;
        es lo que típicamente "desaparece" del CD entre corte y corte)
      - cd_volatil: True si entre cortes consecutivos hubo algún salto
        relativo > umbral_volatil (con base previa >= cd_min_relevante uds)

    Returns:
        DataFrame con: sku, cd_deriva_sem, cd_volatil. Vacío si no hay
        snapshots suficientes con columna stock_cd.
    """
    import datetime as _dt

    weeks = list_available_weeks()
    if len(weeks) < 2:
        return pd.DataFrame(columns=['sku', 'cd_deriva_sem', 'cd_volatil'])

    def _week_ord(iso: str) -> int:
        y, w = iso.split('-')
        return _dt.date.fromisocalendar(int(y), int(w), 7).toordinal() // 7

    frames = []
    for w in weeks:
        try:
            _df = load_snapshot(w)
        except FileNotFoundError:
            continue
        if _df is None or _df.empty or 'stock_cd' not in _df.columns:
            continue
        frames.append(_df[['sku', 'stock_cd']].assign(_word=_week_ord(w)))
    if len(frames) < 2:
        return pd.DataFrame(columns=['sku', 'cd_deriva_sem', 'cd_volatil'])

    piv = pd.concat(frames).pivot_table(index='sku', columns='_word', values='stock_cd')
    cols = sorted(piv.columns)

    rows = []
    for sku, serie in piv.iterrows():
        bajadas, volatil = [], False
        for a, b in zip(cols, cols[1:]):
            va, vb = serie.get(a), serie.get(b)
            if pd.isna(va) or pd.isna(vb):
                continue
            delta_sem = (vb - va) / max(b - a, 1)
            if delta_sem < 0:
                bajadas.append(-delta_sem)
            if va >= cd_min_relevante and abs(vb - va) / va > umbral_volatil:
                volatil = True
        rows.append({
            'sku': sku,
            'cd_deriva_sem': round(float(np.mean(bajadas)), 1) if bajadas else 0.0,
            'cd_volatil': volatil,
        })
    return pd.DataFrame(rows)


# ─────────────────────────────────────────────────────────────
#  CAPITAL POR ESTADO — serie semanal (Caso de Éxito, G2)
#  Auditoría integral 2026-08-23: la métrica titular para gerencia
#  es el capital en EXCESO, no el DORMIDO puro (ruidoso).
# ─────────────────────────────────────────────────────────────

ESTADOS_EXCESO = ["DORMIDO", "ESTANCADO", "SOBRESTOCK", "LIQUIDAR", "MUERTO"]


def capital_por_estado(semana: str) -> pd.DataFrame:
    """Capital a costo, SKUs y unidades por estado para una semana.

    El estado se deriva con taxonomia.classify_series sobre el snapshot
    (cobertura + edad + rango), igual que detect_state_changes.
    """
    from taxonomia import classify_series

    try:
        df = load_snapshot(semana)
    except FileNotFoundError:
        return pd.DataFrame()
    if df.empty or 'stock_valor_costo' not in df.columns:
        return pd.DataFrame()

    df = df.copy()
    df['estado'] = classify_series(
        df['cobertura_sem'],
        edad=df.get('edad_semanas'),
        rango=df.get('rango_antiguedad'),
    )
    out = df.groupby('estado', as_index=False).agg(
        n_skus=('sku', 'nunique'),
        uds=('stock_total', 'sum'),
        capital=('stock_valor_costo', 'sum'),
    )
    out['semana_iso'] = semana
    return out


def serie_capital_estados(desde: str = None, hasta: str = None) -> pd.DataFrame:
    """Serie semanal de capital por estado (formato largo) para todas las
    semanas disponibles en snapshots."""
    weeks = list_available_weeks()
    if desde:
        weeks = [w for w in weeks if w >= desde]
    if hasta:
        weeks = [w for w in weeks if w <= hasta]
    frames = [capital_por_estado(w) for w in weeks]
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def serie_capital_exceso(desde: str = None, hasta: str = None) -> pd.DataFrame:
    """Serie semanal de la métrica titular del caso de éxito:
    capital en exceso (DORMIDO+ESTANCADO+SOBRESTOCK+LIQUIDAR+MUERTO)
    vs capital total, con % y delta WoW."""
    serie = serie_capital_estados(desde, hasta)
    if serie.empty:
        return pd.DataFrame()
    total = serie.groupby('semana_iso')['capital'].sum().rename('capital_total')
    exceso = (serie[serie['estado'].isin(ESTADOS_EXCESO)]
              .groupby('semana_iso')
              .agg(capital_exceso=('capital', 'sum'),
                   skus_exceso=('n_skus', 'sum'),
                   uds_exceso=('uds', 'sum')))
    out = exceso.join(total).reset_index().sort_values('semana_iso')
    out['pct_exceso'] = (out['capital_exceso'] / out['capital_total']).round(4)
    out['delta_exceso'] = out['capital_exceso'].diff().round(0)
    out['delta_exceso_pct'] = (out['capital_exceso'].pct_change() * 100).round(1)
    return out.reset_index(drop=True)
