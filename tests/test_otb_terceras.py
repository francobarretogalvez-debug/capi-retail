"""S10 (2026-09-05): reparto del OTB entre terceras — venta a costo como base, factores acotados,
cobertura alta castiga salvo destallado, argumento de negociación."""
import numpy as np
import pandas as pd

import otb_terceras as ot


def _cob():
    rows = []
    def marca(m, n, vta, contr, stock, rango, estado="OPTIMO"):
        for i in range(n):
            rows.append(dict(sku=f"{m}{i}", marca=m, tienda="JP", vta_soles_4sem=vta, contrib_soles_4sem=contr,
                             stock_valor_costo=stock, rango_antiguedad=rango, estado=estado))
    marca("A", 10, 1000, 400, 500, "RANGO 0_3")            # sana: cob = 5000/(6000/4)=3.3 sem
    marca("B", 10, 1000, 400, 6000, "RANGO 9_12")          # cobertura 40 sem + 100% obsoleto
    marca("C", 10, 1000, 400, 6000, "RANGO 0_3")           # cobertura 40 sem pero destallada
    marca("D", 10, 1000, 500, 300, "RANGO 0_3", "QUIEBRE")  # margen alto + quiebre
    return pd.DataFrame(rows)


def test_metricas():
    m = ot.metricas_marca(_cob(), {"A", "B", "C", "D"}).set_index("marca")
    assert abs(m.loc["A", "cobertura_sem"] - 5000 / (6000 / 4)) < 1e-6
    assert m.loc["B", "pct_obsoleto"] == 1.0 and m.loc["A", "pct_obsoleto"] == 0.0
    assert m.loc["D", "pct_skus_quiebre"] == 1.0 and abs(m.loc["D", "margen_pct"] - 0.5) < 1e-9


def test_reparto_castiga_cobertura_salvo_destallado():
    m = ot.metricas_marca(_cob(), {"A", "B", "C", "D"}, destallado={"C": 0.6})
    r = ot.repartir_otb(m, 100000).set_index("marca")
    assert abs(r["reparto_capi"].sum() - 100000) <= 2 and abs(r["reparto_venta"].sum() - 100000) <= 2
    # misma venta a costo en A/B/C → mismo reparto por venta; Capi castiga a B (cobertura + obsoleto), no tanto a C (destallada)
    assert r.loc["A", "reparto_venta"] == r.loc["B", "reparto_venta"] == r.loc["C", "reparto_venta"]
    assert r.loc["B", "reparto_capi"] < r.loc["C", "reparto_capi"] < r.loc["A", "reparto_capi"]
    assert r.loc["B", "f_cobertura"] < 1 and r.loc["C", "f_cobertura"] >= 1.0 - 1e-9
    assert r.loc["D", "reparto_capi"] > r.loc["D", "reparto_venta"]        # margen alto + quiebre → gana OTB
    assert "obsoleto" in r.loc["B", "argumento_negociacion"].lower() or "retire" in r.loc["B", "argumento_negociacion"].lower()
    assert "curva rota" in r.loc["C", "argumento_negociacion"].lower()
    assert (r["reparto_capi_min"] <= r["reparto_capi"]).all() and (r["reparto_capi_max"] >= r["reparto_capi"]).all()


def test_tope_por_marca():
    m = ot.metricas_marca(_cob(), {"A", "B", "C", "D"})
    r = ot.repartir_otb(m, 100000, max_delta_pct=20).set_index("marca")
    assert abs(r["reparto_capi"].sum() - 100000) <= 2
    assert (r["reparto_capi"] <= r["reparto_venta"] * 1.2 + 1).all() and (r["reparto_capi"] >= r["reparto_venta"] * 0.8 - 1).all()
