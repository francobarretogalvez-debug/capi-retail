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

# ── Filtro margen destino (2026-08-25): rotación de remate no recibe empuje ──
emp = r["empujes_df"]
if "margen_destino_pct" in emp.columns:
    _con = emp[emp["margen_destino_pct"].notna()]
    assert (_con["margen_destino_pct"] >= 25).all(), \
        f"min margen destino: {_con['margen_destino_pct'].min()}"
    print("✅ test_margen_destino OK: ningún empuje a línea×tienda con margen < 25%")

# ── Filtro descuento (2026-08-25, regla Majo 40%) ──
if "dscto_destino_pct" in emp.columns:
    _cd = emp[emp["dscto_destino_pct"].notna()]
    assert (_cd["dscto_destino_pct"] < 40).all(), \
        f"max dscto destino: {_cd['dscto_destino_pct'].max()}"
    print("✅ test_dscto OK: ningún empuje hacia línea×tienda con dscto realizado ≥40%")
