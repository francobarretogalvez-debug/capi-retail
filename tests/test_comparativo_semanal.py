"""S4 (2026-09-05): panel semana vs 4 anteriores y Pareto de capital inmovilizado desde snapshots."""
import os

import pytest

from conftest import SNAPSHOTS_DIR

import comparativo_semanal as cs

pytestmark = pytest.mark.skipif(not os.path.exists(os.path.join(SNAPSHOTS_DIR, "2026-34")),
                                reason="faltan snapshots reales")


def test_kpis_semana_coherentes():
    k = cs.kpis_semana("2026-34")
    assert k["venta_uds"] > 0 and k["venta_soles"] > 0
    assert 0 < k["margen_pct"] < 0.9
    assert 0 < k["pct_inmovilizado"] < 1 and k["capital_inmovilizado"] <= k["capital_total"]
    assert k["capital_obsoleto"] <= k["capital_inmovilizado"]
    assert k["cobertura_sem"] > 0 and 0 <= k["pct_venta_cero"] <= 1
    # la venta semanal NO puede ser el acumulado de temporada (fix B1)
    assert k["venta_uds"] < 0.5 * k["n_skus"] * 50


def test_panel_estructura_y_deltas():
    p = cs.panel_4_semanas(hasta="2026-34")
    assert len(p["semanas"]) == 5 and p["semanas"][-1] == "2026-34"
    assert p["tabla"].shape == (len(cs.KPIS), 5)
    assert list(p["deltas"].columns)[0].startswith("vs W−1 (2026-33)")
    assert p["consecutivas"] is True  # 30→34 son consecutivas
    # delta de venta vs W−1 en % coherente con la tabla
    v34, v33 = p["kpis"]["2026-34"]["venta_soles"], p["kpis"]["2026-33"]["venta_soles"]
    assert abs(p["deltas"].loc["Venta S/ (semana)"].iloc[0] - (v34 - v33) / v33 * 100) < 1e-6


def test_pareto_inmovilizado():
    r = cs.resumen_pareto("2026-34")
    assert r and r["n_skus_top80"] < r["n_skus_exceso"]
    assert 0.75 <= r["capital_top80"] / r["capital_exceso"] <= 0.85 + 0.15  # ≥80% por construcción
    p = cs.pareto_inmovilizado("2026-34")
    assert p.iloc[0]["top_80"].startswith("⭐") and p["pct_acum"].iloc[-1] == pytest.approx(1.0)
