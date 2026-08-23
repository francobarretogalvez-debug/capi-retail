#!/usr/bin/env python3
"""Suite de regresión de exports (vistas_excel + reportes_marcas) contra la
data real del corte 04.08. Origen: auditoría de formato 2026-08-05 + Fase 0
2026-08-23. Correr:  python3 tests/test_exports.py

Requiere los archivos de referencia en la carpeta local de Franco; si no
están, hace skip limpio (para no romper en otros entornos)."""
import io
import os
import re
import sys
import time
import zipfile

import numpy as np
import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import vistas_excel  # noqa: E402
import reportes_marcas  # noqa: E402

BASE = "/Users/francobarreto/Claude_Context/Ripley.md/Reportes Capi"
REF = f"{BASE}/Capi_Analisis al 04.08.xlsx"
if not os.path.exists(REF):
    print(f"SKIP: no existe el archivo de referencia {REF}")
    sys.exit(0)

fails = []
def check(nombre, cond, det=""):
    print(("  ✅ " if cond else "  ❌ ") + nombre + (f" — {det}" if det else ""))
    if not cond:
        fails.append(nombre)

# _add_pricing_cols real extraído del app (sin importar streamlit)
src = open(os.path.join(REPO, "app_streamlit.py")).read()
m = re.search(r"(def _add_pricing_cols.*?\n    return df_out\n)", src, re.S)
assert m, "no se encontró _add_pricing_cols"
ns = {"np": np, "pd": pd, "get_column_letter": get_column_letter, "vistas_excel": vistas_excel}
exec(m.group(1), ns)
_add_pricing_cols = ns["_add_pricing_cols"]

print("Cargando data de referencia 04.08...")
xl = pd.ExcelFile(REF)
df = pd.read_excel(xl, "Cobertura").rename(columns={
    "vta_sem_ult": "prom_vta_uds", "Precio Vigente": "precio_vigente",
    "Precio Blanco": "precio_blanco", "Costo": "costo"})
df = df.drop(columns=[c for c in ("Nuevo Precio", "Nuevo Margen") if c in df.columns])
df_rep = pd.read_excel(xl, "Reposiciones Detalle")
df_trans = pd.read_excel(xl, "Transferencias")
df_prec = pd.read_excel(xl, "Acciones Precio")
df_alertas = pd.read_excel(xl, "Alertas IA")

# ── vistas_excel: export todos_estados ──
print("\n[1/2] vistas_excel (Capi_todos_estados)...")
t0 = time.time()
_dl_cols = ["sku", "nombre", "marca", "temporada", "rango_antiguedad", "tienda",
            "stock_total", "prom_vta_uds", "cobertura_sem", "stock_valor_costo",
            "edad_semanas", "estado", "pct_descuento"]
buf = io.BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as w:
    vistas_excel.hoja_resumen_ejecutivo(w, df)
    vistas_excel.hoja_liquidacion_sku(w, df, _add_pricing_cols)
    vistas_excel.hojas_terceras_por_marca(w, df)
    vistas_excel.hoja_cascada_dscto(w, df)
    vistas_excel.hoja_estados_temporada(w, df)
    _add_pricing_cols(df[_dl_cols].sort_values(["estado", "stock_valor_costo"],
                                               ascending=[True, False]),
                      df, "Todos los estados", w)
t_gen = time.time() - t0
wb = load_workbook(io.BytesIO(buf.getvalue()))
check("orden de hojas", wb.sheetnames[0] == "Resumen Ejecutivo"
      and wb.sheetnames[-1] == "Todos los estados")
ws = wb["Resumen Ejecutivo"]
tot = ws.cell(row=ws.max_row, column=ws.max_column).value
check("Resumen Ejecutivo total = 20,268,342", abs(tot - 20268342) < 5, f"{tot:,.0f}")
check("hojas terceras (9)", sum(1 for s in wb.sheetnames if s.startswith("T. ")) == 9)
ws = wb["Liquidación x SKU"]
hdrs = [c.value for c in ws[1]]
check("liquidación: pricing + temporada", all(h in hdrs for h in
      ["Nuevo Precio", "Nuevo Margen", "Nuevo Dscto", "temporada"]))
check(f"generación ≤8s ({t_gen:.1f}s)", t_gen <= 8)

# ── reportes_marcas: zip 9 marcas + reglas de negocio ──
print("\n[2/2] reportes_marcas (zip 9 marcas)...")
zbytes = reportes_marcas.generar_zip_reportes(df, df_rep, df_trans, df_prec,
                                              df_alertas, corte="04.08.2026")
z = zipfile.ZipFile(io.BytesIO(zbytes))
check("zip con 9 archivos", len(z.namelist()) == 9, str(len(z.namelist())))
n_sug, n_malos, n_sin_piso = 0, 0, 0
for n in z.namelist():
    wbx = load_workbook(io.BytesIO(z.read(n)), read_only=True)
    if not ("Resumen" in wbx.sheetnames and "Leyenda" in wbx.sheetnames):
        fails.append(f"estructura {n}")
    for hoja in ("1. Liquidar", "2. Activar"):
        if hoja not in wbx.sheetnames:
            continue
        rows = wbx[hoja].iter_rows(min_row=2, values_only=True)
        hd = list(next(rows))
        if "P. Sugerido" not in hd:
            continue
        i_s, i_v = hd.index("P. Sugerido"), hd.index("P. Vigente")
        i_p = hd.index("P. Mínimo (piso)") if "P. Mínimo (piso)" in hd else None
        i_a = hd.index("Acción") if "Acción" in hd else None
        for r in rows:
            if (i_p is not None and r[i_p] is None
                    and not (i_a is not None and "Sin costo" in str(r[i_a] or ""))):
                n_sin_piso += 1
            if r[i_s] is not None:
                n_sug += 1
                if r[i_v] is not None and r[i_s] > r[i_v] + 0.01:
                    n_malos += 1
                if i_p is not None and r[i_p] is not None and r[i_s] < r[i_p] - 0.01:
                    n_malos += 1
check("estructura 9 marcas", not any(f.startswith("estructura") for f in fails))
check("NINGÚN P. Sugerido > vigente ni < piso", n_malos == 0,
      f"{n_sug} sugerencias, {n_malos} violaciones")
check("piso universal (fix B7): 0 filas sin P. Mínimo", n_sin_piso == 0, str(n_sin_piso))

print(f"\n{'❌ FALLAS: ' + str(fails) if fails else '✅ TODO OK'}")
sys.exit(1 if fails else 0)
