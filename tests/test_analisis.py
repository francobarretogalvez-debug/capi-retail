#!/usr/bin/env python3
"""Regresión del motor de conclusiones (analisis_estados) sobre snapshots reales."""
import os, sys
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import analisis_estados

if not os.path.exists(os.path.join(REPO, "snapshots", "2026-33")):
    print("SKIP: faltan snapshots de referencia"); sys.exit(0)

c = analisis_estados.conclusiones("2026-32", "2026-33")
titulos = " | ".join(x["titulo"] for x in c)
assert any("ESTANCADO" in t["titulo"] for t in c), titulos
assert any("Capital en exceso" in t["titulo"] for t in c), titulos
assert all(t["nivel"] in ("positivo", "atencion", "critico", "info") for t in c)
c2 = analisis_estados.conclusiones("2026-31", "2026-32")
assert any(t["nivel"] == "positivo" and "exceso" in t["titulo"].lower() for t in c2)
c3 = analisis_estados.conclusiones("2026-99", "2026-98")
assert c3 and c3[0]["titulo"] == "Sin datos suficientes"
print("✅ test_analisis OK:", len(c), "conclusiones 32→33 ·", len(c2), "en 31→32")

# ── Migraciones (pedido Franco 2026-08-24) ──
m = analisis_estados.matriz_migraciones("2026-32", "2026-33")
assert not m.empty and set(m["clase"]) <= {"mejora", "deterioro", "lateral"}
assert (m["capital"] >= 0).all()
s = analisis_estados.serie_migraciones()
assert len(s) >= 9 and {"capital_mejora", "capital_deterioro", "neto"} <= set(s.columns)
d = analisis_estados.detalle_migracion("2026-32", "2026-33",
                                       m.iloc[0]["estado_a"], m.iloc[0]["estado_b"])
assert not d.empty and "stock_valor_costo" in d.columns
print("✅ test_migraciones OK:", len(m), "flujos ·", len(s), "pares en la serie")
