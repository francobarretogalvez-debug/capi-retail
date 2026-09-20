"""K1 activación de venta cero: enviados vs no enviados, misma semana, con data sintética."""
import pandas as pd

from kpi_venta_cero import activacion_df


def _t(rows):
    return pd.DataFrame(rows, columns=["sku", "tienda", "stock_uds", "vta_uds_sem", "stock_costo"])


def test_activacion_enviados_vs_control():
    cur = _t([("1", "JP", 5, 0, 100), ("2", "JP", 5, 0, 100), ("3", "JP", 5, 0, 50), ("4", "JP", 5, 0, 50),
              ("5", "JP", 5, 2, 50),      # vendía: no es venta cero
              ("6", "SM", 0, 0, 0)])      # sin stock: no es venta cero
    nxt = _t([("1", "JP", 3, 2, 60), ("2", "JP", 5, 0, 100), ("3", "JP", 4, 1, 40)])   # el 4 desaparece (=0 venta)
    det = pd.DataFrame({"sku": ["1", "2"], "tienda_cod": ["JP", "JP"]})
    r = activacion_df(det, cur, nxt)
    assert r["n_enviados"] == 2 and r["n_no_enviados"] == 2
    assert r["activacion_enviados"] == 0.5 and r["activacion_no_enviados"] == 0.5
    assert r["lift_pp"] == 0.0 and r["enviados_no_encontrados"] == 0


def test_enviado_que_no_era_venta_cero_no_cuenta():
    cur = _t([("1", "JP", 5, 0, 100), ("9", "JP", 5, 3, 100)])
    nxt = _t([("1", "JP", 4, 1, 80), ("9", "JP", 4, 1, 80)])
    det = pd.DataFrame({"sku": ["1", "9"], "tienda_cod": ["JP", "JP"]})
    r = activacion_df(det, cur, nxt)
    assert r["n_enviados"] == 1 and r["enviados_no_encontrados"] == 1
    assert r["activacion_enviados"] == 1.0
