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
            frames.append(df[['sku', 'marca', 'unidades_vendidas', 'venta_soles']].copy())
        except Exception:
            continue

    if not frames:
        return pd.DataFrame()

    df_all = pd.concat(frames, ignore_index=True)

    # Agregar por SKU
    result = df_all.groupby(['sku', 'marca'], as_index=False).agg(
        vta_total_unidades=('unidades_vendidas', 'sum'),
        vta_total_soles=('venta_soles', 'sum'),
        n_semanas_con_data=('unidades_vendidas', 'count'),
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
        return {
            'semana': sem,
            'n_skus': int(df['sku'].nunique()),
            'stock_total': int(df['stock_total'].sum()),
            'stock_valorizado': float(df['stock_valor_costo'].sum()) if 'stock_valor_costo' in df.columns else 0,
            'venta_unidades': int(df['unidades_vendidas'].sum()),
            'venta_soles': float(df['venta_soles'].sum()) if 'venta_soles' in df.columns else 0,
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

    return {
        'semana_iso': semana,
        'n_skus': int(df['sku'].nunique()),
        'stock_total_unidades': int(df['stock_total'].sum()),
        'stock_total_valorizado': float(df['stock_valor_costo'].sum()) if 'stock_valor_costo' in df.columns else 0,
        'venta_total_unidades': int(df['unidades_vendidas'].sum()),
        'venta_total_soles': float(df['venta_soles'].sum()) if 'venta_soles' in df.columns else 0,
        'contribucion_total': float(df['contribucion_soles'].sum()) if 'contribucion_soles' in df.columns else 0,
        'marcas': int(df['marca'].nunique()) if 'marca' in df.columns else 0,
    }
