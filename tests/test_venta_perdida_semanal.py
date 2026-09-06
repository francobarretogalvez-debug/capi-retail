"""Venta perdida de la última semana por SKU×tienda (feedback Franco 2026-09-06)."""
import os

import pytest

from conftest import SNAPSHOTS_DIR

import venta_perdida_semanal as vp

pytestmark = pytest.mark.skipif(not os.path.exists(os.path.join(SNAPSHOTS_DIR, "2026-35", "tienda.parquet")),
                                reason="sin snapshots de tienda")


def test_ultima_semana_coherente():
    r = vp.venta_perdida_semana("2026-35")
    assert r["prev"] == ["2026-31", "2026-32", "2026-33", "2026-34"]
    assert r["n_combos"] > 0 and r["n_tiendas"] >= 5
    q = r["quiebre"]
    assert q["n_combos_cob4"] >= r["n_combos"] and q["semanas_promedio"] >= 1.0
    assert q["exclusiones"]["total"] > 0 and "temporada_OI" in q["exclusiones"]   # sep-2026: se liquida el OI
    assert (r["en_quiebre"]["cobertura_sem"] <= 4).all()
    assert 0 < r["neto_min"] <= r["neto_max"] < r["bruto_max"]
    assert r["neto_max"] == pytest.approx(r["bruto_max"] * 0.7)
    assert r["margen_max"] < r["neto_max"]
    d = r["detalle"]
    assert (d["uds_max"] >= d["uds_min"]).all() and (d["uds_max"] > 0).all()
    # cada combo en quiebre cerró la semana sin stock en esa tienda
    from snapshots_engine import tienda
    cur = tienda.load_tienda("2026-35"); cur["sku"] = cur["sku"].astype(str)
    con_stock = cur[cur["stock_uds"] > 0].set_index(["sku", "tienda"]).index
    assert not d.set_index(["sku", "tienda"]).index.isin(con_stock).any()
    assert r["por_tienda"]["neto_max"].sum() == pytest.approx(r["neto_max"])


def test_serie():
    s = vp.serie_semanas(3)
    assert len(s) == 3 and s["semana"].iloc[-1] == "2026-35" and (s["neto_max"] >= s["neto_min"]).all()


def test_temporada_en_liquidacion_inferida():
    import pandas as pd
    snap = pd.DataFrame({"temporada": ["OI"] * 60 + ["PV"] * 60, "pct_descuento": [0.5] * 60 + [0.1] * 60})
    assert vp.temporada_en_liquidacion("2026-35", snapshot=snap) == "OI"
    assert vp.temporada_en_liquidacion("2026-20") == "PV" and vp.temporada_en_liquidacion("2026-40") == "OI"
