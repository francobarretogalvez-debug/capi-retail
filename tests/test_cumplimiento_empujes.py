"""S6 v1 (2026-09-05): el cumplimiento de empujes cruza lo pedido (acciones_log) con lo
observado (stock en tiendas subió más de lo que la venta explica). Antes la métrica
filtraba por una columna inexistente y reportaba 100% por construcción."""
import pandas as pd

from analisis_estados import cumplimiento_empujes_df


def _snap(rows):
    return pd.DataFrame(rows, columns=['sku', 'marca', 'stock_cd', 'stock_tiendas', 'unidades_vendidas'])


def test_cuatro_cuadrantes_con_caso_sintetico():
    a = _snap([('A', 'M', 100, 50, 200),   # pedido y llega: tiendas 50 → 70 con venta 10
               ('B', 'M', 100, 50, 200),   # pedido y NO llega: tiendas 50 → 40 (solo venta)
               ('C', 'M', 100, 50, 200),   # nadie pidió pero llegó: 50 → 65
               ('D', 'M', 100, 50, 200)])  # nada
    b = _snap([('A', 'M', 80, 70, 210), ('B', 'M', 100, 40, 210),
               ('C', 'M', 85, 65, 200), ('D', 'M', 100, 45, 205)])
    log = pd.DataFrame([{'sku': 'A', 'tipo': 'Reposición / Empuje', 'semana_iso': '2026-34', 'estado': 'Ejecutada'},
                        {'sku': 'B', 'tipo': 'Reposición / Empuje', 'semana_iso': '2026-34', 'estado': 'Ejecutada'},
                        {'sku': 'D', 'tipo': 'Markdown / Precio', 'semana_iso': '2026-34', 'estado': 'Ejecutada'}])
    df = cumplimiento_empujes_df(a, b, log)
    cuad = dict(zip(df['sku'], df['cuadrante']))
    assert cuad['A'].startswith('✅') and cuad['B'].startswith('⚠️') and cuad['C'].startswith('ℹ️')
    assert 'D' not in cuad, "un markdown no es un empuje y sin movimiento no se lista"
    assert float(df.loc[df['sku'] == 'A', 'unidades_despacho'].iloc[0]) == 30  # 70 − (50 − 10)


def test_sin_pedidos_no_inventa_100():
    a = _snap([('A', 'M', 100, 50, 200)])
    b = _snap([('A', 'M', 100, 45, 205)])
    df = cumplimiento_empujes_df(a, b, pd.DataFrame(columns=['sku', 'tipo', 'semana_iso']))
    assert df.empty
