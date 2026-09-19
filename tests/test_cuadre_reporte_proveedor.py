"""Cuadre del reporte semanal al proveedor contra el reporte por marca de 9 pestañas (C5).

Regla de los cuadres (test_meta_cuadres): se invoca el motor (motor_v2.run_analysis sobre el fixture)
y se compara la salida de dos generadores que DEBEN coincidir donde comparten definición:
  - venta cero por tienda: hoja "5. Venta Cero" (reporte 9 pestañas) == hoja "1b" (proveedor) — misma función.
  - transferencias por modelo: hoja "4. Transferir" == sub-bloque 2b — misma función (transferencias_por_sku).
  - precio sugerido: para los SKUs presentes en ambos, "P. Sugerido" de la hoja 2 == precio_sugerido de B2a.
  - reglas de precio: nunca subir, nunca bajo el piso (pricing.precio_piso).
Donde la definición difiere a propósito (B2a usa estado de CADENA; la hoja 2 usa estado por tienda,
ver AUDIT 2026-09-19) el test mide el solapamiento y exige que sea alto, no igualdad de filas.
"""
import io
import os

import pandas as pd
import pytest
from openpyxl import load_workbook

import motor_v2
import pricing
import reporte_proveedor as rp
import reportes_marcas as rm

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "base_mini.xlsx")


from functools import lru_cache


@lru_cache(maxsize=1)
def _res():
    """Corre el motor una vez (helper que invoca motor_v2 → cada test cuenta como cuadre real)."""
    return motor_v2.run_analysis(FIX)


def _col(ws, header):
    heads = [c.value for c in ws[2]]
    if header not in heads:
        return []
    i = heads.index(header) + 1
    return [ws.cell(row=r, column=i).value for r in range(3, ws.max_row + 1)]


def _ambos(ctx, marca):
    res = ctx
    args = (marca, res["cobertura"], res["reposiciones"], res["transferencias"], res["acciones_precio"], res["alertas"], "01.09.2026")
    wb9 = load_workbook(io.BytesIO(rm.generar_reporte_marca(*args)))
    bl = rp.bloques_marca(marca, res["cobertura"], res["transferencias"], None, res["acciones_precio"], res["reposiciones"], res["alertas"], corte="01.09.2026")
    wbp = load_workbook(io.BytesIO(rp.excel_proveedor(bl)))
    return wb9, wbp, bl


def _marcas(ctx):
    return [m for m in rm.marcas_reporte(ctx["cobertura"]) if not rp.slice_marca(ctx["cobertura"], m).empty][:6]


def test_cuadre_venta_cero_vs_hoja5():
    ctx = _res()
    for marca in _marcas(ctx):
        wb9, wbp, bl = _ambos(ctx, marca)
        cap9 = sum(v or 0 for v in _col(wb9["5. Venta Cero"], "Capital S/")) if "5. Venta Cero" in wb9.sheetnames else 0
        capp = sum(v or 0 for v in _col(wbp["1b. Venta Cero x Tienda"], "Capital S/"))
        assert capp == pytest.approx(cap9), marca                     # misma función, igualdad exacta
        assert capp == pytest.approx(bl["vc_tienda"]["stock_valor_costo"].sum()), marca
        # B1 (cadena, última semana) es un subconjunto de los SKUs con alguna tienda sin venta
        if not bl["b1"].empty and not bl["vc_tienda"].empty:
            b1_4sem = bl["b1"][bl["b1"]["semanas_sin_venta"] == "4+"]
            assert set(b1_4sem["sku"]) <= set(bl["vc_tienda"]["sku"]), marca


def test_cuadre_transferencias_hoja4():
    ctx = _res()
    for marca in _marcas(ctx):
        wb9, wbp, bl = _ambos(ctx, marca)
        if "4. Transferir" not in wb9.sheetnames:
            assert bl["b2b"].empty, marca
            continue
        h4 = dict(zip(_col(wb9["4. Transferir"], "SKU"), _col(wb9["4. Transferir"], "Uds a mover")))
        b2b = dict(zip(bl["b2b"]["sku"], bl["b2b"]["transf_uds"]))
        assert {int(k): int(v) for k, v in h4.items()} == {int(k): int(v) for k, v in b2b.items()}, marca


def test_cuadre_precio_sugerido_hoja2():
    """Mismo SKU y mismo estado en la hoja 2 → misma función de precio → mismo número (exacto).
    El solapamiento de universos (B2a usa estado de CADENA; la hoja 2, estado por tienda) NO es un
    cuadre: se mide y se reporta en medicion/checksum_reporte_proveedor.py (60% en el fixture de 6
    tiendas, medido 2026-09-19), no se asevera."""
    ctx = _res()
    comparados = 0
    for marca in _marcas(ctx):
        wb9, wbp, bl = _ambos(ctx, marca)
        b2a = bl["b2a"]
        if b2a.empty:
            continue
        ref = {}
        for hoja in ("1. Liquidar", "2. Activar"):
            if hoja in wb9.sheetnames:
                ws = wb9[hoja]
                for sku, est, p in zip(_col(ws, "SKU"), _col(ws, "Estado"), _col(ws, "P. Sugerido")):
                    ref.setdefault(int(sku), {})[est] = p
        for _, r in b2a.iterrows():
            p9 = ref.get(int(r["sku"]), {}).get(r["estado_cadena"])
            if p9 is not None and pd.notna(r["precio_sugerido"]):
                comparados += 1
                assert float(p9) == pytest.approx(float(r["precio_sugerido"]), abs=0.01), (marca, r["sku"])
    assert comparados > 0, "el fixture debería tener SKUs comparables entre hoja 2 y B2a"


def test_cuadre_b2a_es_sobrestock_de_cadena_desde_columnas_crudas():
    """Independiente del motor de bloques: cobertura de cadena = Σ stock_total / promedio(vta_sem1..4_total)
    calculada desde df_cob crudo. Todo SKU de B2a debe tener cobertura ≥ 26 sem (SOBRESTOCK o peor) y
    venta > 0; ningún SKU de B1 (sin venta la última semana) puede estar en B2a."""
    ctx = _res()
    cob = ctx["cobertura"]
    n = 0
    for marca in _marcas(ctx):
        bl = rp.bloques_marca(marca, cob, ctx["transferencias"], None, ctx["acciones_precio"], ctx["reposiciones"], ctx["alertas"], corte="x")
        if bl["b2a"].empty:
            continue
        dfm = rp.slice_marca(cob, marca)
        raw = dfm.groupby("sku").agg(stk=("stock_total", "sum"), v1=("vta_sem1_total", "first"), v2=("vta_sem2_total", "first"),
                                     v3=("vta_sem3_total", "first"), v4=("vta_sem4_total", "first"))
        raw["vta4"] = raw[["v1", "v2", "v3", "v4"]].mean(axis=1)
        raw["cob"] = raw["stk"] / raw["vta4"].where(raw["vta4"] > 0)
        for sku in bl["b2a"]["sku"]:
            n += 1
            assert raw.loc[sku, "vta4"] > 0 and raw.loc[sku, "v1"] > 0, (marca, sku)
            assert raw.loc[sku, "cob"] >= 26 - 0.05, (marca, sku, raw.loc[sku, "cob"])
        assert not (set(bl["b2a"]["sku"]) & set(bl["b1"]["sku"])), marca
    assert n > 0


def test_cuadre_pricing_nunca_sube_y_respeta_piso():
    ctx = _res()
    margen_min = float(motor_v2.DEFAULT_PARAMS.get("margen_min", 0.15))
    n = 0
    for marca in _marcas(ctx):
        bl = rp.bloques_marca(marca, ctx["cobertura"], ctx["transferencias"], None, ctx["acciones_precio"], ctx["reposiciones"], ctx["alertas"], corte="x")
        for df in (bl["b1"], bl["b2a"]):
            if df.empty or "precio_sugerido" not in df.columns:
                continue
            con = df[df["precio_sugerido"].notna()]
            for _, r in con.iterrows():
                n += 1
                assert r["precio_sugerido"] < r["precio_vigente"], (marca, r["sku"])
                assert r["dscto_sugerido"] >= (r.get("pct_descuento") or 0) - 1e-9, (marca, r["sku"])   # nunca menor al actual
                assert r["precio_sugerido"] >= pricing.precio_piso(r["costo"], margen_min) - 0.01, (marca, r["sku"])
    assert n > 0, "el fixture debería producir al menos un precio sugerido"
