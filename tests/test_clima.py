#!/usr/bin/env python3
"""Regresión del filtro clima: GRUESO no se sugiere a tiendas de calor (2026-08-25)."""
import os, sys
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from afinidad_engine import _load_tiendas_calor, _load_calorico

hot = _load_tiendas_calor()
assert {"IQT", "PUCALPA I", "PIU2", "CHIC", "CHII"} <= hot, hot
cal = _load_calorico()
assert all(cal.get(l) == "GRUESO" for l in
           ["CASACAS", "CHOMPAS", "POLERONES", "BLAZERS", "CHAQUETAS"]), cal

BASE = "/Users/francobarreto/Downloads/Base al 23.08.xlsx"
if not os.path.exists(BASE):
    print("SKIP E2E: base no disponible"); sys.exit(0)
from afinidad_engine import build_afinidad
r = build_afinidad(BASE)
for nombre, df, col in [("empujes", r["empujes_df"], "tienda"),
                        ("redistribución", r["redistribucion_df"], "tienda_destino")]:
    if df.empty or "linea" not in df.columns:
        continue
    viol = df[df["linea"].map(lambda l: cal.get(l, "NEUTRO") == "GRUESO") & df[col].isin(hot)]
    assert viol.empty, f"{nombre}: {len(viol)} GRUESO→calor"
print("✅ test_clima OK: 0 sugerencias GRUESO hacia tiendas de calor")
