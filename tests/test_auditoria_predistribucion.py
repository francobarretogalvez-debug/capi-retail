"""S15 (2026-09-06): causa raíz por SKU×tienda y auditoría preliminar por tienda × línea."""
import os

import pandas as pd
import pytest

from conftest import SNAPSHOTS_DIR

import auditoria_predistribucion as ap

pytestmark = pytest.mark.skipif(not os.path.exists(os.path.join(SNAPSHOTS_DIR, "2026-35", "tienda.parquet")),
                                reason="sin snapshots de tienda")


def test_causa_raiz_precedencia():
    import venta_perdida_semanal as vp
    r = vp.venta_perdida_semana("2026-35")
    d = ap.enriquecer(r["detalle"], "2026-35")
    assert set(d["causa_key"]) <= set(ap.CAUSAS)
    assert d.loc[d["stock_cd"] > 0, "causa_key"].isin(["1_cd", "1b_liq_outlet"]).all()      # el CD manda
    assert (d.loc[(d["stock_cd"] > 0) & d["liquidacion"], "causa_key"] == "1b_liq_outlet").all()   # liquidación con CD → outlet
    assert (d.loc[(d["stock_cd"] <= 0) & (d["on_order"] > 0), "causa_key"] == "2_transito").all()
    pc = ap.por_causa(d)
    assert abs(pc["pct"].sum() - 1) < 1e-9 and pc["neto_max"].sum() == pytest.approx(d["neto_max"].sum())
    tl = ap.tienda_linea(d)
    assert tl["neto_max"].sum() == pytest.approx(d["neto_max"].sum()) and "causa_dominante" in tl.columns
    rec = ap.skus_recurrentes(d)
    assert (rec["sem_prom"] >= ap.SEM_RECURRENTE - 1e-9).all()


def test_indice_acierto_diagnostico():
    d = pd.DataFrame([
        dict(tienda="JP", linea="CASACAS", sku="1", neto_max=800, causa_key="1_cd", recurrente=True, stock_cd=5, on_order=0, semanas_en_quiebre=4),
        dict(tienda="SM", linea="POLOS", sku="2", neto_max=50, causa_key="5_imp", recurrente=False, stock_cd=0, on_order=0, semanas_en_quiebre=1),
    ])
    cob = pd.DataFrame([
        dict(tienda="Jockey Plaza", categoria="CASACAS", sku="9", stock_valor_costo=1000, estado="OPTIMO", prom_vta_uds=2),
        dict(tienda="San Miguel", categoria="POLOS", sku="8", stock_valor_costo=1000, estado="SOBRESTOCK", prom_vta_uds=1),
        dict(tienda="San Miguel", categoria="POLOS", sku="7", stock_valor_costo=200, estado="OPTIMO", prom_vta_uds=3),
    ])
    m = ap.indice_acierto(d, cob).set_index(["tienda", "linea"])
    assert m.loc[("JP", "CASACAS"), "diagnostico"].startswith("📉")     # faltó con CD, no sobró
    assert m.loc[("SM", "POLOS"), "diagnostico"].startswith("📈")       # sobró 83%, no faltó relevante (causa IMP no es predistribución)
