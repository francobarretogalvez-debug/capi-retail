"""
Carga y normalización de bases antiguas y micro/profundidad nuevo.
"""
import os
import re
import pandas as pd
from datetime import datetime, timedelta
from .config import COLUMN_MAP, SNAPSHOT_SCHEMA
from .storage import save_snapshot


def _fecha_to_semana_iso(fecha_str: str) -> tuple:
    """
    Convierte fecha de nombre de archivo (ej: '11.05', '08.04') a semana ISO.

    Asume año 2026 si no se especifica.
    Retorna (semana_iso: str, fecha_cierre: str).
    """
    # Extraer fecha del nombre
    # Soporta: "08.04", "16.04", "28.04", "04.05.26", "11.05"
    parts = fecha_str.replace(',', '.').split('.')
    dia = int(parts[0])
    mes = int(parts[1])
    anio = int(parts[2]) if len(parts) > 2 else 26
    if anio < 100:
        anio += 2000

    fecha = datetime(anio, mes, dia)

    # Buscar el domingo más cercano (cierre de semana)
    # Si la fecha es domingo, usar esa. Si no, ir al domingo anterior.
    days_since_sunday = fecha.weekday()  # lunes=0 ... domingo=6
    if days_since_sunday == 6:  # ya es domingo
        domingo = fecha
    else:
        domingo = fecha - timedelta(days=days_since_sunday + 1)

    # Semana ISO del domingo
    iso_cal = domingo.isocalendar()
    semana_iso = f"{iso_cal[0]}-{iso_cal[1]:02d}"
    fecha_cierre = domingo.strftime('%Y-%m-%d')

    return semana_iso, fecha_cierre


def _normalizar_cobertura(valor):
    """Convierte 'Sin Venta', strings vacíos, etc. a float o None."""
    if pd.isna(valor):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    s = str(valor).strip()
    if s.lower() in ('sin venta', 'sin ventas', '', '-', 'n/a'):
        return None
    try:
        return float(s.replace(',', '.'))
    except (ValueError, TypeError):
        return None


def _normalizar_base(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normaliza un DataFrame del micro/profundidad al schema canónico de snapshot.
    """
    # 1. Seleccionar y renombrar columnas
    cols_disponibles = {k: v for k, v in COLUMN_MAP.items() if k in df.columns}
    df_norm = df[list(cols_disponibles.keys())].copy()
    df_norm.rename(columns=cols_disponibles, inplace=True)

    # 2. Cobertura: convertir "Sin Venta" → None, numérico → float
    if 'cobertura_sem' in df_norm.columns:
        df_norm['cobertura_sem'] = df_norm['cobertura_sem'].apply(_normalizar_cobertura)

    # 3. Limpiar tipos numéricos
    int_cols = ['sku', 'unidades_vendidas', 'stock_total', 'stock_tiendas', 'stock_cd',
                'vta_u_sem_1ant', 'vta_u_sem_2ant', 'vta_u_sem_3ant', 'vta_u_sem_4ant',
                # Planificación (2026-09-02): el on-order es lo que se resta de
                # la compra requerida para llegar al OTB.
                'on_order_cd_tiendas', 'on_order_ordenes']
    for col in int_cols:
        if col in df_norm.columns:
            df_norm[col] = pd.to_numeric(df_norm[col], errors='coerce').fillna(0).astype(int)

    float_cols = ['edad_semanas', 'venta_soles', 'contribucion_soles', 'stock_valor_costo',
                  'precio_blanco', 'precio_vigente', 'pct_descuento', 'costo_unitario',
                  'agotamiento_calc', 'duracion_ciclo_total']
    for col in float_cols:
        if col in df_norm.columns:
            df_norm[col] = pd.to_numeric(df_norm[col], errors='coerce')

    # 4. Eliminar filas sin SKU
    df_norm = df_norm.dropna(subset=['sku'])
    df_norm['sku'] = df_norm['sku'].astype(int)

    # 5. Eliminar duplicados de SKU (mantener primero)
    df_norm = df_norm.drop_duplicates(subset=['sku'], keep='first')

    return df_norm


def load_base_antigua(filepath: str, force: bool = False) -> dict:
    """
    Carga una base antigua, la normaliza y la guarda como snapshot.

    Args:
        filepath: ruta al archivo Excel (.xlsx).
        force: si True, sobrescribe snapshot existente.

    Returns:
        dict con metadata del snapshot guardado.
    """
    # Extraer fecha del nombre del archivo
    filename = os.path.basename(filepath)
    # Patrones: "Base al 11.05.xlsx", "Base al 04.05.26.xlsx", "Base Profundidad al 08.04.xlsx"
    match = re.search(r'(\d{2}\.\d{2}(?:\.\d{2,4})?)', filename)
    if not match:
        raise ValueError(f"No se puede extraer fecha del nombre: {filename}")

    fecha_str = match.group(1)
    semana_iso, fecha_cierre = _fecha_to_semana_iso(fecha_str)

    print(f"📂 Cargando {filename} → semana {semana_iso} (cierre {fecha_cierre})")

    # Leer Excel
    df = pd.read_excel(filepath)
    print(f"   Filas raw: {len(df)}, Columnas: {len(df.columns)}")

    # Normalizar
    df_norm = _normalizar_base(df)
    df_norm['semana_iso'] = semana_iso
    df_norm['fecha_cierre'] = fecha_cierre

    print(f"   Filas normalizadas: {len(df_norm)}, SKUs únicos: {df_norm['sku'].nunique()}")

    # Guardar como snapshot
    meta = save_snapshot(df_norm, semana_iso, fecha_cierre, force=force)
    print(f"   ✅ Guardado en {meta['path']}")

    return meta


def process_micro_profundidad(filepath: str, semana_iso: str = None,
                               force: bool = False) -> dict:
    """
    Procesa un nuevo micro/profundidad semanal y lo guarda como snapshot.

    Si semana_iso no se proporciona, se calcula automáticamente como la
    semana ISO del domingo más reciente.

    Args:
        filepath: ruta al archivo Excel/CSV.
        semana_iso: semana ISO forzada (ej: '2026-20'). Si None, auto-calcula.
        force: si True, sobrescribe snapshot existente.

    Returns:
        dict con metadata.
    """
    # Leer archivo
    ext = os.path.splitext(filepath)[1].lower()
    if ext in ('.xlsx', '.xls'):
        df = pd.read_excel(filepath)
    elif ext == '.csv':
        df = pd.read_csv(filepath)
    else:
        raise ValueError(f"Formato no soportado: {ext}. Usa .xlsx o .csv")

    # Auto-detectar semana
    if semana_iso is None:
        # Intentar extraer del nombre del archivo
        match = re.search(r'(\d{2}\.\d{2}(?:\.\d{2,4})?)', os.path.basename(filepath))
        if match:
            semana_iso, fecha_cierre = _fecha_to_semana_iso(match.group(1))
        else:
            # Usar domingo más reciente
            hoy = datetime.now()
            days_since_sunday = hoy.weekday()
            if days_since_sunday == 6:
                domingo = hoy
            else:
                domingo = hoy - timedelta(days=days_since_sunday + 1)
            iso_cal = domingo.isocalendar()
            semana_iso = f"{iso_cal[0]}-{iso_cal[1]:02d}"
            fecha_cierre = domingo.strftime('%Y-%m-%d')
    else:
        fecha_cierre = ''

    print(f"📄 Procesando micro/profundidad → semana {semana_iso}")

    # Normalizar
    df_norm = _normalizar_base(df)
    df_norm['semana_iso'] = semana_iso
    df_norm['fecha_cierre'] = fecha_cierre

    # Guardar
    meta = save_snapshot(df_norm, semana_iso, fecha_cierre, force=force)
    print(f"   ✅ Snapshot {semana_iso}: {meta['n_filas']} SKUs guardados")

    # Snapshot liviano SKU×tienda (sprint Chile 2026-09-05): mismo cierre, segundo
    # parquet. No bloquea el flujo si la base no trae la firma de tiendas.
    try:
        from . import tienda as _tienda
        meta_t = _tienda.save_tienda(_tienda.build_from_base(df, semana_iso), semana_iso, force=True)
        meta['tienda'] = meta_t
        print(f"   ✅ Snapshot tienda {semana_iso}: {meta_t['n_filas']:,} filas SKU×tienda")
    except Exception as _e_t:
        meta['tienda_error'] = str(_e_t)
        print(f"   ⚠️ Snapshot tienda {semana_iso} no generado: {_e_t}")

    return meta


def load_all_bases_antiguas(directory: str, force: bool = False) -> list:
    """
    Carga todas las bases antiguas de un directorio.

    Args:
        directory: ruta al directorio con archivos Excel.
        force: si True, sobrescribe snapshots existentes.

    Returns:
        lista de dicts con metadata de cada snapshot.
    """
    import glob
    files = sorted(glob.glob(os.path.join(directory, '*.xlsx')))
    if not files:
        print(f"⚠️ No se encontraron archivos .xlsx en {directory}")
        return []

    results = []
    for f in files:
        try:
            meta = load_base_antigua(f, force=force)
            results.append(meta)
        except Exception as e:
            print(f"❌ Error procesando {os.path.basename(f)}: {e}")

    print(f"\n{'═'*50}")
    print(f"Resumen: {len(results)}/{len(files)} bases procesadas exitosamente")
    return results
