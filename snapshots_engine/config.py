"""
Configuración del engine de snapshots.
"""
import os

# ── Paths ──
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOTS_DIR = os.path.join(BASE_DIR, 'snapshots')
INDEX_PATH = os.path.join(SNAPSHOTS_DIR, 'snapshots_index.json')

# ── Schema canónico del snapshot ──
# Cada fila = 1 SKU (agregado todas las tiendas) en 1 semana
SNAPSHOT_SCHEMA = {
    'sku':                  'int64',      # Cód. Prod.
    'descripcion':          'object',     # Descripción
    'marca':                'object',     # Marca
    'linea':                'object',     # Línea
    'sublinea':             'object',     # Sub Línea
    'categoria':            'object',     # Dpto (usado como categoría)
    'temporada':            'object',     # Temp. (OI/PV/TT)
    'edad_semanas':         'float64',    # Antigüedad semanal
    'rango_antiguedad':     'object',     # Rango textual de antigüedad
    'cobertura_sem':        'float64',    # Cobertura Semanal (numérica)
    'unidades_vendidas':    'int64',      # Vta. Unidades (semana actual)
    'venta_soles':          'float64',    # Vta. S/.
    'contribucion_soles':   'float64',    # Contrib. S/.
    'stock_total':          'int64',      # Total Unidades
    'stock_tiendas':        'int64',      # Total Tiendas Unidades
    'stock_cd':             'int64',      # Total CD+Bodega Unidades
    'stock_valor_costo':    'float64',    # Total Valorizado S/.
    'precio_blanco':        'float64',    # P.Blanco
    'precio_vigente':       'float64',    # P.Vigente PMM
    'pct_descuento':        'float64',    # % Dscto
    'costo_unitario':       'float64',    # Costo S/.
    'vta_u_sem_1ant':       'int64',      # Unid. Vendidas Sem. 1ant
    'vta_u_sem_2ant':       'int64',      # Unid. Vendidas Sem. 2ant
    'vta_u_sem_3ant':       'int64',      # Unid. Vendidas Sem. 3ant
    'vta_u_sem_4ant':       'int64',      # Unid. Vendidas Sem. 4ant
    'semana_iso':           'object',     # YYYY-WW (ej: 2026-19)
    'fecha_cierre':         'object',     # YYYY-MM-DD del domingo de cierre
}

# Columnas requeridas (sin ellas el snapshot se rechaza)
REQUIRED_COLUMNS = [
    'sku', 'marca', 'unidades_vendidas', 'stock_total', 'semana_iso',
]

# ── Mapeo de columnas del micro/profundidad al schema ──
COLUMN_MAP = {
    'Cód. Prod.':                          'sku',
    'Descripción':                         'descripcion',
    'Marca':                               'marca',
    'Línea':                               'linea',
    'Sub Línea':                           'sublinea',
    'Dpto':                                'categoria',
    'Temp.':                               'temporada',
    'Antigüedad semanal':                  'edad_semanas',
    'Antiguedad':                          'rango_antiguedad',
    'Cobertura Semanal':                   'cobertura_sem',
    'Vta. Unidades':                       'unidades_vendidas',
    'Vta. S/.':                            'venta_soles',
    'Contrib. S/.':                        'contribucion_soles',
    'Total Unidades':                      'stock_total',
    'Total Tiendas Unidades':              'stock_tiendas',
    'Total  CD+Bodega Unidades':           'stock_cd',
    'Total Valorizado S/.':                'stock_valor_costo',
    'P.Blanco':                            'precio_blanco',
    'P.Vigente PMM':                       'precio_vigente',
    '% Dscto':                             'pct_descuento',
    'Costo S/.':                           'costo_unitario',
    'Unid. Vendidas Sem. 1ant':            'vta_u_sem_1ant',
    'Unid. Vendidas Sem. 2ant':            'vta_u_sem_2ant',
    'Unid. Vendidas Sem. 3ant':            'vta_u_sem_3ant',
    'Unid. Vendidas Sem. 4ant':            'vta_u_sem_4ant',
}
