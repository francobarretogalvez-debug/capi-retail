"""Liquidación con CD → outlet (regla Franco 2026-09-06)."""
import os

import pytest

from conftest import SNAPSHOTS_DIR

import outlets

pytestmark = pytest.mark.skipif(not os.path.exists(os.path.join(SNAPSHOTS_DIR, "2026-35", "tienda.parquet")), reason="sin snapshots")


def test_plan_outlet_reparte_todo_el_cd():
    r = outlets.plan_outlet("2026-35")
    p = r["plan"]
    assert r["n_skus"] > 0 and (p["stock_cd"] > 0).all()
    assert ((p["uds_OPLN"] + p["uds_OSI"]) == p["stock_cd"]).all()          # se reparte todo el CD, sin inventar unidades
    assert set(p["base_reparto"]) <= {"velocidad del SKU en el outlet", "velocidad de la marca×línea en el outlet", "mitades (sin historia en outlets)"}
    assert r["por_marca"]["capital_cd"].sum() == pytest.approx(r["capital"])
