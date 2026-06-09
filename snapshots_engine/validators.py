"""
Validación de schema para snapshots.
"""
import pandas as pd
from .config import REQUIRED_COLUMNS, SNAPSHOT_SCHEMA


def validate_snapshot_schema(df: pd.DataFrame) -> tuple:
    """
    Valida que un DataFrame cumple con el schema canónico de snapshot.

    Returns:
        (is_valid: bool, errors: list[str])
    """
    errors = []

    # 1. Columnas requeridas
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        errors.append(f"Columnas requeridas faltantes: {missing}")

    # 2. No vacío
    if df.empty:
        errors.append("DataFrame está vacío")

    # 3. SKU no nulo
    if 'sku' in df.columns and df['sku'].isna().any():
        n_null = df['sku'].isna().sum()
        errors.append(f"{n_null} filas con SKU nulo")

    # 4. Semana ISO presente y válida
    if 'semana_iso' in df.columns:
        if df['semana_iso'].isna().all():
            errors.append("semana_iso está completamente vacía")
        else:
            # Verificar formato YYYY-WW
            sample = df['semana_iso'].dropna().iloc[0]
            if not (isinstance(sample, str) and len(sample) >= 6 and '-' in sample):
                errors.append(f"semana_iso no tiene formato YYYY-WW: '{sample}'")

    # 5. Duplicados de SKU dentro de la misma semana
    if 'sku' in df.columns and 'semana_iso' in df.columns:
        dupes = df.duplicated(subset=['sku', 'semana_iso'], keep=False).sum()
        if dupes > 0:
            errors.append(f"{dupes} filas duplicadas (mismo SKU + semana)")

    # 6. Valores negativos en stock/venta (warning, no bloqueante)
    for col in ['unidades_vendidas', 'stock_total']:
        if col in df.columns:
            neg = (df[col] < 0).sum()
            if neg > 0:
                errors.append(f"⚠️ {neg} valores negativos en {col} (warning)")

    is_valid = len([e for e in errors if not e.startswith('⚠️')]) == 0
    return is_valid, errors
