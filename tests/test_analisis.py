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
