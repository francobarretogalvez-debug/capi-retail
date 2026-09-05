"""Snapshot liviano SKU×tienda (sprint Chile 2026-09-05)."""
import os

import pandas as pd
import pytest

from conftest import SNAPSHOTS_DIR

from snapshots_engine import tienda


def test_build_from_base_sintetico():
    raw = pd.DataFrame({
        "Cód. Prod.": ["A", "B", "C"], "Costo S/.": [10.0, 20.0, 5.0],
        "JP Stk": [5, 0, 0], "JP UME": [2, 2, 2], "JP On Order": [0, 3, 0], "JP Unidades": [1, 0, 0],
        "SM Stk": [0, 4, 0], "SM UME": [2, 2, 2], "SM On Order": [0, 0, 0], "SM Vta": [0, 2, 0],
        "Vta. Unidades": [9, 9, 9],   # columna que NO es tienda (sin UME/On Order) → se ignora
    })
    t = tienda.build_from_base(raw, "2026-99")
    assert set(t["tienda"]) == {"JP", "SM"} and len(t) == 3          # C no tiene stock ni OO en ninguna
    jp_b = t[(t.sku == "B") & (t.tienda == "JP")].iloc[0]
    assert jp_b.stock_uds == 0 and jp_b.on_order == 3                  # entra por on-order
    sm_b = t[(t.sku == "B") & (t.tienda == "SM")].iloc[0]
    assert sm_b.vta_uds_sem == 2 and sm_b.stock_costo == 80.0          # alias '{t} Vta' del formato viejo


@pytest.mark.skipif(not os.path.exists(os.path.join(SNAPSHOTS_DIR, "2026-35", "tienda.parquet")),
                    reason="sin snapshot tienda real")
def test_real_35_vs_34():
    m = tienda.stock_tienda_dos_semanas("2026-34", "2026-35")
    assert m["recibido"].sum() > 0 and m["tienda"].nunique() >= 30
    cap = tienda.capital_por_tienda("2026-35")
    assert cap.iloc[0]["stock_costo"] > cap.iloc[-1]["stock_costo"]
