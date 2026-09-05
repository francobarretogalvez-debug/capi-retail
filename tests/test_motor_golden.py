"""Test de oro del motor (S1, 2026-09-05): run_analysis sobre una base fixture anonimizada
(302 SKUs × 6 tiendas, derivada del corte 30.08) comparada contra totales guardados.
Si una regla del motor cambia un número en silencio, esto se pone rojo. Cuando el cambio
es intencional, regenerar tests/fixtures/golden_motor.json y explicarlo en el commit."""
import json
import os

import pandas as pd
import pytest

from conftest import REPO

FIX = os.path.join(REPO, "tests", "fixtures", "base_mini.xlsx")
GOLD = os.path.join(REPO, "tests", "fixtures", "golden_motor.json")


@pytest.fixture(scope="module")
def res():
    import motor_v2
    return motor_v2.run_analysis(FIX)


def _num(df, col):
    return pd.to_numeric(df.get(col), errors="coerce").fillna(0)


def test_totales_de_oro(res):
    g = json.load(open(GOLD))
    cob, rep, tr = res["cobertura"], res.get("reposiciones", pd.DataFrame()), res.get("transferencias", pd.DataFrame())
    assert int(cob["sku"].nunique()) == g["n_skus"]
    assert int(len(cob)) == g["n_filas_cobertura"]
    assert float(cob["stock_valor_costo"].sum()) == pytest.approx(g["capital_total"], rel=1e-6)
    por_estado = cob.groupby("estado")["stock_valor_costo"].sum()
    for estado, cap in g["capital_por_estado"].items():
        assert float(por_estado.get(estado, 0)) == pytest.approx(cap, rel=1e-6), estado
    assert set(por_estado.index) == set(g["capital_por_estado"])
    assert int(len(rep)) == g["n_reposiciones"]
    assert int(len(tr)) == g["n_transferencias"]
    assert int(tr["uds_transferir"].sum()) == g["uds_transferencias"]
    assert float(_num(tr, "ganancia_esperada").sum()) == pytest.approx(g["ganancia_transferencias"], rel=1e-6)
    if g.get("margen_efectivo_global") is not None:
        assert float(res["summary"]["margen_efectivo_global"]) == pytest.approx(g["margen_efectivo_global"], abs=1e-6)


def test_identidades_basicas(res):
    cob = res["cobertura"]
    assert (cob["stock_valor_costo"] >= 0).all()
    assert cob["estado"].notna().all()
    tr = res.get("transferencias", pd.DataFrame())
    if len(tr):
        assert (tr["uds_transferir"] > 0).all()
