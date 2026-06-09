"""
Lectura y escritura de snapshots Parquet + índice maestro.
"""
import os
import json
import hashlib
import pandas as pd
from datetime import datetime
from .config import SNAPSHOTS_DIR, INDEX_PATH
from .validators import validate_snapshot_schema


def _ensure_dir():
    """Crea el directorio de snapshots si no existe."""
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)


def save_snapshot(df: pd.DataFrame, semana_iso: str, fecha_cierre: str = None,
                  force: bool = False) -> dict:
    """
    Guarda un snapshot como Parquet.

    Args:
        df: DataFrame con schema canónico.
        semana_iso: identificador YYYY-WW.
        fecha_cierre: fecha YYYY-MM-DD del domingo de cierre (opcional).
        force: si True, sobrescribe snapshot existente.

    Returns:
        dict con metadata del snapshot guardado.

    Raises:
        ValueError: si schema inválido o semana ya existe (sin force).
    """
    _ensure_dir()

    # Validar schema
    is_valid, errors = validate_snapshot_schema(df)
    if not is_valid:
        raise ValueError(f"Schema inválido para snapshot {semana_iso}: {errors}")

    # Anti-duplicados
    week_dir = os.path.join(SNAPSHOTS_DIR, semana_iso)
    fpath = os.path.join(week_dir, 'snapshot.parquet')
    if os.path.exists(fpath) and not force:
        raise ValueError(
            f"Snapshot {semana_iso} ya existe. Usa force=True para sobrescribir."
        )

    # Asegurar semana_iso en los datos
    df = df.copy()
    df['semana_iso'] = semana_iso
    if fecha_cierre:
        df['fecha_cierre'] = fecha_cierre

    # Guardar
    os.makedirs(week_dir, exist_ok=True)
    df.to_parquet(fpath, index=False)

    # Metadata
    meta = {
        'semana_iso': semana_iso,
        'fecha_cierre': fecha_cierre or '',
        'n_filas': len(df),
        'n_skus': int(df['sku'].nunique()),
        'timestamp': datetime.now().isoformat(),
        'path': fpath,
        'hash': hashlib.md5(open(fpath, 'rb').read()).hexdigest()[:12],
    }

    # Actualizar índice
    _update_index(meta)

    return meta


def load_snapshot(semana_iso: str) -> pd.DataFrame:
    """Carga el snapshot de una semana específica."""
    fpath = os.path.join(SNAPSHOTS_DIR, semana_iso, 'snapshot.parquet')
    if not os.path.exists(fpath):
        raise FileNotFoundError(f"No existe snapshot para semana {semana_iso}")
    return pd.read_parquet(fpath)


def load_all_snapshots() -> pd.DataFrame:
    """
    Carga todos los snapshots disponibles y los concatena.
    Útil para cálculos que necesitan ventana temporal.

    Returns:
        DataFrame con todos los snapshots concatenados, columna semana_iso
        para distinguir semanas.
    """
    weeks = list_available_weeks()
    if not weeks:
        return pd.DataFrame()

    frames = []
    for w in weeks:
        try:
            df = load_snapshot(w)
            frames.append(df)
        except Exception as e:
            print(f"⚠️ Error cargando snapshot {w}: {e}")
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def list_available_weeks() -> list:
    """
    Lista las semanas disponibles en orden cronológico.

    Returns:
        list de strings YYYY-WW ordenados.
    """
    if not os.path.isdir(SNAPSHOTS_DIR):
        return []
    weeks = []
    for d in os.listdir(SNAPSHOTS_DIR):
        fpath = os.path.join(SNAPSHOTS_DIR, d, 'snapshot.parquet')
        if os.path.exists(fpath):
            weeks.append(d)
    return sorted(weeks)


def _update_index(meta: dict):
    """Actualiza el índice maestro de snapshots."""
    _ensure_dir()
    index = []
    if os.path.exists(INDEX_PATH):
        try:
            with open(INDEX_PATH, 'r') as f:
                index = json.load(f)
        except (json.JSONDecodeError, IOError):
            index = []

    # Reemplazar si existe, agregar si no
    index = [e for e in index if e.get('semana_iso') != meta['semana_iso']]
    index.append(meta)
    index.sort(key=lambda x: x['semana_iso'])

    with open(INDEX_PATH, 'w') as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
