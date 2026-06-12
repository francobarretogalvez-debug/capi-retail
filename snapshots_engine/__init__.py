"""
Snapshots Engine — Sistema de snapshots semanales para Capi.

Almacena datos a nivel SKU (agregado todas las tiendas) por semana,
usando Parquet como formato de almacenamiento.

Convención: semana ISO (YYYY-WW), cierre domingo.
"""

from .storage import save_snapshot, load_snapshot, load_all_snapshots, list_available_weeks
from .api import (
    get_snapshot,
    get_venta_ultimas_n_semanas,
    get_velocidad_venta,
    get_evolucion_stock,
    detect_reposiciones,
    get_resumen_semanal,
    compare_weeks,
    detect_state_changes,
    evolucion_marca,
    detect_repo_cumplimiento,
    detect_aceleracion,
    predict_stockout,
    estimate_lost_sales,
)
from .loader import load_base_antigua, process_micro_profundidad
from .validators import validate_snapshot_schema

__all__ = [
    'save_snapshot', 'load_snapshot', 'load_all_snapshots', 'list_available_weeks',
    'get_snapshot', 'get_venta_ultimas_n_semanas', 'get_velocidad_venta',
    'get_evolucion_stock', 'detect_reposiciones', 'get_resumen_semanal',
    'compare_weeks', 'detect_state_changes', 'evolucion_marca',
    'detect_repo_cumplimiento', 'detect_aceleracion', 'predict_stockout',
    'estimate_lost_sales',
    'load_base_antigua', 'process_micro_profundidad',
    'validate_snapshot_schema',
]
