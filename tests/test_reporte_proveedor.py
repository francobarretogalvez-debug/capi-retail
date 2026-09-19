"""Reporte semanal al proveedor (decisión 2026-09-18): motor puro de los 3 bloques por marca.

C1: `reportes_marcas.transferencias_por_sku` es la única fuente de "Uds a mover" (hoja 4 y sub-bloque 2b).
El test reimplementa la fórmula inline que tenía la hoja 4 antes de la extracción y exige igualdad exacta.
"""
import numpy as np
import pandas as pd

import reportes_marcas as rm


def _trans():
    # 4 SKUs: 10 pasa umbral con ganancia; 20 tiene uds pero ganancia ≤ 0; 30 no llega a 12 uds;
    # 40 pasa umbral repartido en 2 movimientos / 2 destinos.
    return pd.DataFrame({
        "sku":            [10, 10, 20, 30, 40, 40],
        "nombre":         ["A", "A", "B", "C", "D", "D"],
        "tienda_origen":  ["X", "X", "X", "X", "Y", "Y"],
        "tienda_destino": ["P", "Q", "P", "P", "P", "Q"],
        "uds_transferir": [8, 6, 15, 5, 7, 7],
        "precio_vigente": [100.0, 100.0, 50.0, 80.0, 60.0, 60.0],
        "ganancia_esperada": [120.0, 30.0, -5.0, 40.0, 10.0, 15.0],
    })


def _inline_hoja4(df_trans, skus):
    """Fórmula que vivía dentro de generar_reporte_marca antes de C1."""
    tr_m = df_trans[df_trans["sku"].isin(skus)].copy()
    tr_m["valor"] = tr_m["uds_transferir"] * tr_m["precio_vigente"]
    tg = tr_m.groupby(["sku", "nombre"], as_index=False).agg(
        uds=("uds_transferir", "sum"), valor=("valor", "sum"),
        tiendas=("tienda_destino", "nunique"), ganancia=("ganancia_esperada", "sum"))
    tg = tg[(tg["uds"] >= rm.TRANSF_MIN_UDS) & (tg["ganancia"] > 0)]
    return tg.sort_values("ganancia", ascending=False).reset_index(drop=True)


def test_b2_transferencias_igual_reporte_marca():
    df = _trans()
    got = rm.transferencias_por_sku(df, [10, 20, 30, 40])
    ref = _inline_hoja4(df, [10, 20, 30, 40])
    assert list(got["sku"]) == list(ref["sku"]) == [10, 40]
    assert list(got["transf_uds"]) == list(ref["uds"]) == [14, 14]
    assert list(got["transf_tiendas"]) == list(ref["tiendas"]) == [2, 2]
    assert np.allclose(got["transf_ganancia"], ref["ganancia"])
    assert np.allclose(got["transf_valor"], ref["valor"])


def test_transferencias_sin_ganancia_usa_valor():
    df = _trans().drop(columns=["ganancia_esperada"])
    got = rm.transferencias_por_sku(df, [10, 20, 30, 40])
    # sin ganancia: umbral por valor de venta (≥12 uds y ≥S/1.000) → 10 (S/1.400) sí, 20 (S/750) no, 40 (S/840) no
    assert list(got["sku"]) == [10]
    assert got["transf_ganancia"].isna().all()


def test_transferencias_vacio_y_fuera_de_universo():
    assert rm.transferencias_por_sku(pd.DataFrame(), [1]).empty
    assert rm.transferencias_por_sku(None, [1]).empty
    assert rm.transferencias_por_sku(_trans(), [99]).empty
