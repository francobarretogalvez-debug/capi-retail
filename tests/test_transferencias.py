"""Regresión del criterio económico de transferencias (fórmula Ripley 2026-08-24)."""
from motor_v2 import evaluar_transferencia


def test_replica_exacta_hoja_ripley():
    # 10 uds · dscto 60% · master 39.9 → vig 15.96 · costo 20 · flete 3.5 → −99.75
    contrib, flete, pot, esp = evaluar_transferencia(10, 15.96, 20, 3.5)
    assert abs(contrib - (-6.4746)) < 1e-3, contrib
    assert flete == 35.0 and abs(pot - (-99.75)) < 0.01, (flete, pot)
    assert esp == pot


def test_esperada_menor_que_potencial_si_destino_no_vende_todo():
    _, _, p2, e2 = evaluar_transferencia(40, 59.99, 23.9, 3.5, uds_vendibles=32)
    assert p2 > 0 and 0 < e2 < p2


def test_uds_vendibles_se_capea():
    _, _, p3, e3 = evaluar_transferencia(10, 59.99, 23.9, 3.5, uds_vendibles=99)
    assert e3 == p3


def test_guards_no_revientan():
    assert evaluar_transferencia(10, 0, 20, 3.5) == (None, None, None, None)
    assert evaluar_transferencia(10, 15.96, None, 3.5) == (None, None, None, None)


# ── Reglas de origen (Franco 2026-09-19): edad mínima 4 sem y peor cobertura primero ──
import numpy as np
import pandas as pd
import motor_v2


def _cob_transf():
    """SKU 1: destino T0 en QUIEBRE (stock 2, vende 4/sem → necesita 46). Orígenes:
       A cob 20 (SOBRESTOCK, stock 60, vende 3), B cob 120 (ESTANCADO, stock 120, vende 1),
       C cob 40 pero edad 3 (recién llegado) → no puede ser origen."""
    rows = [
        dict(sku=1, tienda="T0", estado="QUIEBRE",    stock_total=2,   prom_vta_uds=4.0, cobertura_sem=0.5, edad_semanas=10),
        dict(sku=1, tienda="A",  estado="SOBRESTOCK", stock_total=60,  prom_vta_uds=3.0, cobertura_sem=20.0, edad_semanas=10),
        dict(sku=1, tienda="B",  estado="ESTANCADO",  stock_total=120, prom_vta_uds=1.0, cobertura_sem=120.0, edad_semanas=10),
        dict(sku=1, tienda="C",  estado="SOBRESTOCK", stock_total=40,  prom_vta_uds=1.0, cobertura_sem=40.0, edad_semanas=3),
    ]
    df = pd.DataFrame(rows)
    df["nombre"] = "X"; df["categoria"] = "POLOS"; df["precio_vigente"] = 100.0; df["costo"] = 30.0
    return df


def test_origen_peor_cobertura_primero_y_edad_minima():
    tr = motor_v2.build_transferencias(_cob_transf(), dict(motor_v2.DEFAULT_PARAMS))
    assert set(tr["tienda_origen"]) <= {"A", "B"} and "C" not in set(tr["tienda_origen"])   # edad 3 < 4: fuera
    # el destino necesita 12×4 − 2 = 46; B (cob 120) va primero: exceso 120 − 12 = 108 → cubre las 46 solo
    assert tr.iloc[0]["tienda_origen"] == "B" and int(tr.iloc[0]["uds_transferir"]) == 46
    assert int(tr["uds_transferir"].sum()) == 46
    # con edad mínima 0, C entra como origen posible pero B sigue primero por cobertura
    tr2 = motor_v2.build_transferencias(_cob_transf(), {**motor_v2.DEFAULT_PARAMS, "edad_min_trans": 0})
    assert tr2.iloc[0]["tienda_origen"] == "B"
