"""
clima_engine.py — Motor de clima para módulo Fenómeno del Niño
==============================================================
Funciones:
  - fetch_temperature(): Trae temperatura diaria de Open-Meteo (gratis, sin key)
  - get_weekly_temperature(): Agrega a promedio semanal (para cruzar con snapshots)
  - get_historical_comparison(): Compara año actual vs años anteriores
  - cache local en JSON para no repetir requests

Ciudades: Lima como proxy nacional (sin dimensión tienda en snapshots).
Cuando se agregue data por tienda, expandir a ciudades de provincias.
"""

import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from collections import defaultdict

# ─────────────────────────────────────────────────────────────
#  CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────

_CACHE_DIR = os.path.join(os.path.dirname(__file__), 'data', 'clima_cache')

# Ciudades con tiendas Ripley — coordenadas para Open-Meteo
# Lima es proxy principal (~70% del negocio)
CIUDADES = {
    "LIMA":       {"lat": -12.05, "lon": -77.04, "peso": 0.70},
    "TRUJILLO":   {"lat": -8.11,  "lon": -79.03, "peso": 0.08},
    "AREQUIPA":   {"lat": -16.40, "lon": -71.54, "peso": 0.06},
    "CHICLAYO":   {"lat": -6.77,  "lon": -79.84, "peso": 0.05},
    "PIURA":      {"lat": -5.19,  "lon": -80.63, "peso": 0.04},
    "CAJAMARCA":  {"lat": -7.16,  "lon": -78.51, "peso": 0.03},
    "HUANCAYO":   {"lat": -12.07, "lon": -75.21, "peso": 0.02},
    "CHIMBOTE":   {"lat": -9.07,  "lon": -78.59, "peso": 0.02},
}
# Pesos suman 1.0 — distribución estimada de venta por ciudad

_API_BASE = "https://archive-api.open-meteo.com/v1/archive"


# ─────────────────────────────────────────────────────────────
#  CACHE
# ─────────────────────────────────────────────────────────────

def _ensure_cache_dir():
    os.makedirs(_CACHE_DIR, exist_ok=True)


def _cache_key(ciudad, start_date, end_date):
    return f"{ciudad}_{start_date}_{end_date}.json"


def _read_cache(key):
    path = os.path.join(_CACHE_DIR, key)
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return None


def _write_cache(key, data):
    _ensure_cache_dir()
    path = os.path.join(_CACHE_DIR, key)
    with open(path, 'w') as f:
        json.dump(data, f)


# ─────────────────────────────────────────────────────────────
#  FETCH TEMPERATURA
# ─────────────────────────────────────────────────────────────

def fetch_temperature(ciudad="LIMA", start_date=None, end_date=None,
                      use_cache=True):
    """
    Trae temperatura diaria (max, min) de Open-Meteo para una ciudad.

    Parámetros
    ----------
    ciudad     : str — clave de CIUDADES
    start_date : str 'YYYY-MM-DD' — inicio del rango
    end_date   : str 'YYYY-MM-DD' — fin del rango
    use_cache  : bool — usar cache local

    Retorna
    -------
    list of dict: [{'fecha': '2026-05-01', 'temp_max': 22.5, 'temp_min': 18.4, 'temp_avg': 20.45}, ...]
    """
    if ciudad not in CIUDADES:
        raise ValueError(f"Ciudad '{ciudad}' no está en CIUDADES. Opciones: {list(CIUDADES.keys())}")

    coords = CIUDADES[ciudad]

    # Defaults: últimos 90 días
    if end_date is None:
        end_date = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d')  # API delay 2 días
    if start_date is None:
        start_date = (datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=90)).strftime('%Y-%m-%d')

    # Cache check
    cache_k = _cache_key(ciudad, start_date, end_date)
    if use_cache:
        cached = _read_cache(cache_k)
        if cached is not None:
            return cached

    # API call
    url = (
        f"{_API_BASE}?"
        f"latitude={coords['lat']}&longitude={coords['lon']}"
        f"&start_date={start_date}&end_date={end_date}"
        f"&daily=temperature_2m_max,temperature_2m_min"
        f"&timezone=America/Lima"
    )

    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Capi-RetailAI/1.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"[clima_engine] Error fetching {ciudad}: {e}")
        return []

    daily = raw.get('daily', {})
    fechas = daily.get('time', [])
    t_max = daily.get('temperature_2m_max', [])
    t_min = daily.get('temperature_2m_min', [])

    result = []
    for i, fecha in enumerate(fechas):
        mx = t_max[i] if i < len(t_max) and t_max[i] is not None else None
        mn = t_min[i] if i < len(t_min) and t_min[i] is not None else None
        avg = round((mx + mn) / 2, 1) if mx is not None and mn is not None else None
        result.append({
            'fecha': fecha,
            'temp_max': mx,
            'temp_min': mn,
            'temp_avg': avg,
            'ciudad': ciudad,
        })

    # Cache
    if use_cache and result:
        _write_cache(cache_k, result)

    return result


# ─────────────────────────────────────────────────────────────
#  TEMPERATURA SEMANAL (para cruzar con snapshots)
# ─────────────────────────────────────────────────────────────

def get_weekly_temperature(start_date=None, end_date=None, ponderado=True):
    """
    Temperatura promedio semanal, opcionalmente ponderada por ciudad.

    Parámetros
    ----------
    start_date : str 'YYYY-MM-DD'
    end_date   : str 'YYYY-MM-DD'
    ponderado  : bool — True = promedio ponderado por peso de ciudades

    Retorna
    -------
    list of dict: [{'semana_iso': '2026-18', 'temp_avg': 21.3, 'temp_max': 24.1, 'temp_min': 18.5}, ...]
    """
    if ponderado:
        # Traer todas las ciudades y ponderar
        all_daily = {}  # fecha → {'temp_avg': weighted_sum, ...}
        for ciudad, info in CIUDADES.items():
            days = fetch_temperature(ciudad, start_date, end_date)
            for d in days:
                f = d['fecha']
                if f not in all_daily:
                    all_daily[f] = {'temp_avg': 0.0, 'temp_max': 0.0, 'temp_min': 0.0}
                if d['temp_avg'] is not None:
                    all_daily[f]['temp_avg'] += d['temp_avg'] * info['peso']
                    all_daily[f]['temp_max'] += d['temp_max'] * info['peso']
                    all_daily[f]['temp_min'] += d['temp_min'] * info['peso']
    else:
        # Solo Lima
        days = fetch_temperature("LIMA", start_date, end_date)
        all_daily = {}
        for d in days:
            all_daily[d['fecha']] = {
                'temp_avg': d['temp_avg'] or 0,
                'temp_max': d['temp_max'] or 0,
                'temp_min': d['temp_min'] or 0,
            }

    # Agrupar por semana ISO
    weeks = defaultdict(lambda: {'temps': [], 'maxs': [], 'mins': []})
    for fecha_str, temps in sorted(all_daily.items()):
        dt = datetime.strptime(fecha_str, '%Y-%m-%d')
        iso_year, iso_week, _ = dt.isocalendar()
        week_key = f"{iso_year}-{iso_week:02d}"
        weeks[week_key]['temps'].append(temps['temp_avg'])
        weeks[week_key]['maxs'].append(temps['temp_max'])
        weeks[week_key]['mins'].append(temps['temp_min'])

    result = []
    for week_key in sorted(weeks.keys()):
        w = weeks[week_key]
        result.append({
            'semana_iso': week_key,
            'temp_avg': round(sum(w['temps']) / len(w['temps']), 1) if w['temps'] else None,
            'temp_max': round(sum(w['maxs']) / len(w['maxs']), 1) if w['maxs'] else None,
            'temp_min': round(sum(w['mins']) / len(w['mins']), 1) if w['mins'] else None,
            'n_dias': len(w['temps']),
        })

    return result


# ─────────────────────────────────────────────────────────────
#  COMPARACIÓN HISTÓRICA
# ─────────────────────────────────────────────────────────────

def get_historical_comparison(years=None, month_start=3, month_end=5, ciudad="LIMA"):
    """
    Compara temperatura promedio del mismo período (mes_start–mes_end) entre años.
    Útil para comparar Niño 2023 vs Normal 2024 vs Normal 2025 vs Niño 2026.

    Parámetros
    ----------
    years       : list of int — años a comparar (default: [2023, 2024, 2025, 2026])
    month_start : int — mes inicio (default: 3 = marzo)
    month_end   : int — mes fin (default: 5 = mayo)
    ciudad      : str — ciudad de referencia

    Retorna
    -------
    list of dict: [{'year': 2023, 'temp_avg': 23.4, 'label': 'Niño'}, ...]
    """
    if years is None:
        years = [2023, 2024, 2025, 2026]

    nino_years = {2023, 2026}  # Años con Fenómeno del Niño confirmado

    result = []
    for year in years:
        start = f"{year}-{month_start:02d}-01"
        # Último día del mes
        if month_end == 12:
            end = f"{year}-12-31"
        else:
            end_dt = datetime(year, month_end + 1, 1) - timedelta(days=1)
            end = end_dt.strftime('%Y-%m-%d')

        # Para 2026, no pedir más allá de hoy - 2 días
        if year == datetime.now().year:
            max_end = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d')
            if end > max_end:
                end = max_end

        days = fetch_temperature(ciudad, start, end)
        if days:
            avgs = [d['temp_avg'] for d in days if d['temp_avg'] is not None]
            avg_temp = round(sum(avgs) / len(avgs), 1) if avgs else None
            max_temp = round(max(d['temp_max'] for d in days if d['temp_max'] is not None), 1)
            min_temp = round(min(d['temp_min'] for d in days if d['temp_min'] is not None), 1)
        else:
            avg_temp = max_temp = min_temp = None

        result.append({
            'year': year,
            'temp_avg': avg_temp,
            'temp_max_periodo': max_temp,
            'temp_min_periodo': min_temp,
            'n_dias': len(days),
            'label': 'Niño' if year in nino_years else 'Normal',
            'ciudad': ciudad,
            'periodo': f"{month_start:02d}-{month_end:02d}",
        })

    return result


# ─────────────────────────────────────────────────────────────
#  UTILIDADES
# ─────────────────────────────────────────────────────────────

def clear_cache():
    """Borra todos los archivos de cache de clima."""
    if os.path.exists(_CACHE_DIR):
        for f in os.listdir(_CACHE_DIR):
            os.remove(os.path.join(_CACHE_DIR, f))
        print(f"[clima_engine] Cache limpiado: {_CACHE_DIR}")


def get_snapshot_date_range(snapshots_dir=None):
    """
    Lee los snapshots disponibles y retorna el rango de fechas para fetch de temperatura.

    Retorna
    -------
    tuple: (start_date, end_date) como strings 'YYYY-MM-DD'
    """
    if snapshots_dir is None:
        snapshots_dir = os.path.join(os.path.dirname(__file__), 'snapshots')

    import pandas as pd
    weeks = []
    for d in sorted(os.listdir(snapshots_dir)):
        parquet = os.path.join(snapshots_dir, d, 'snapshot.parquet')
        if os.path.exists(parquet):
            df = pd.read_parquet(parquet, columns=['fecha_cierre'])
            if 'fecha_cierre' in df.columns and len(df) > 0:
                weeks.append(pd.to_datetime(df['fecha_cierre'].iloc[0]))

    if not weeks:
        return None, None

    # Expandir un poco el rango para tener contexto
    start = (min(weeks) - timedelta(days=7)).strftime('%Y-%m-%d')
    end = (max(weeks) + timedelta(days=7)).strftime('%Y-%m-%d')
    # No pasar de hoy - 2 días
    max_end = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d')
    if end > max_end:
        end = max_end

    return start, end
