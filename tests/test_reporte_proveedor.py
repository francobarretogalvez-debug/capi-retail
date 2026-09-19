"""Reporte semanal al proveedor (decisión 2026-09-18): motor puro de los 3 bloques por marca.

C1: `reportes_marcas.transferencias_por_sku` es la única fuente de "Uds a mover" (hoja 4 y sub-bloque 2b).
El test reimplementa la fórmula inline que tenía la hoja 4 antes de la extracción y exige igualdad exacta.
"""
import numpy as np
import pandas as pd

import reportes_marcas as rm


def _trans():
    # 4 SKUs: 10 pasa umbral con ganancia; 20 tiene uds pero ganancia ≤ 0; 30 no llega a 12 uds;
    # 40 pasa umbral repartido en 2 movimientos / 2 destinos.
    return pd.DataFrame({
        "sku":            [10, 10, 20, 30, 40, 40],
        "nombre":         ["A", "A", "B", "C", "D", "D"],
        "tienda_origen":  ["X", "X", "X", "X", "Y", "Y"],
        "tienda_destino": ["P", "Q", "P", "P", "P", "Q"],
        "uds_transferir": [8, 6, 15, 5, 7, 7],
        "precio_vigente": [100.0, 100.0, 50.0, 80.0, 60.0, 60.0],
        "ganancia_esperada": [120.0, 30.0, -5.0, 40.0, 10.0, 15.0],
    })


def _inline_hoja4(df_trans, skus):
    """Fórmula que vivía dentro de generar_reporte_marca antes de C1."""
    tr_m = df_trans[df_trans["sku"].isin(skus)].copy()
    tr_m["valor"] = tr_m["uds_transferir"] * tr_m["precio_vigente"]
    tg = tr_m.groupby(["sku", "nombre"], as_index=False).agg(
        uds=("uds_transferir", "sum"), valor=("valor", "sum"),
        tiendas=("tienda_destino", "nunique"), ganancia=("ganancia_esperada", "sum"))
    tg = tg[(tg["uds"] >= rm.TRANSF_MIN_UDS) & (tg["ganancia"] > 0)]
    return tg.sort_values("ganancia", ascending=False).reset_index(drop=True)


def test_b2_transferencias_igual_reporte_marca():
    df = _trans()
    got = rm.transferencias_por_sku(df, [10, 20, 30, 40])
    ref = _inline_hoja4(df, [10, 20, 30, 40])
    assert list(got["sku"]) == list(ref["sku"]) == [10, 40]
    assert list(got["transf_uds"]) == list(ref["uds"]) == [14, 14]
    assert list(got["transf_tiendas"]) == list(ref["tiendas"]) == [2, 2]
    assert np.allclose(got["transf_ganancia"], ref["ganancia"])
    assert np.allclose(got["transf_valor"], ref["valor"])


def test_transferencias_sin_ganancia_usa_valor():
    df = _trans().drop(columns=["ganancia_esperada"])
    got = rm.transferencias_por_sku(df, [10, 20, 30, 40])
    # sin ganancia: umbral por valor de venta (≥12 uds y ≥S/1.000) → 10 (S/1.400) sí, 20 (S/750) no, 40 (S/840) no
    assert list(got["sku"]) == [10]
    assert got["transf_ganancia"].isna().all()


def test_transferencias_vacio_y_fuera_de_universo():
    assert rm.transferencias_por_sku(pd.DataFrame(), [1]).empty
    assert rm.transferencias_por_sku(None, [1]).empty
    assert rm.transferencias_por_sku(_trans(), [99]).empty


# ══════════════════════════════════════════════════════════════════════════════
#  C3 — bloques por marca sobre una base sintética (cada SKU prueba una regla)
# ══════════════════════════════════════════════════════════════════════════════
import json
import pytest

import reporte_proveedor as rp


def _cob():
    """Marca M, tiendas T1..T3. Precios: blanco 100, costo 20 (piso = 20/0.85*1.18 = 27.76).
      101 VC-LIQ   venta cero 4 sem, edad 30, stock 40/30/10 (S/ 1.600)   → B1, liquidar 40% (pirámide 30-34 sem)
      102 VC-1SEM  sin venta SOLO la última semana, vendía 4/sem, stock 100 → B1 (gana a B2a)
      103 VC-NEW   venta cero, edad 5, stock 5                              → B1, lanzamiento
      201 SOB-MKD  5/sem, stock 150 (cob 30) SOBRESTOCK, dscto 10%, edad 20 → B2a markdown 30% → S/ 70
      202 SOB-CANJ 2/sem, stock 120 (cob 60) ESTANCADO, dscto 60%           → B2a canje
      203 SOB-FREN 4/sem, stock 130 (cob 32.5) SOBRESTOCK, edad 6 (pirám 0%)→ B2a frenar ingreso
      301 GAN-CD   20/sem, stock 60 (cob 3), 2 de 3 tiendas en quiebre, CD 50 → B3 reponer desde CD
      302 GAN-SIN  10/sem, stock 70 (cob 7), CD 0                            → B3 reorden proveedor
      303 OPT      3/sem, stock 36 (cob 12) ÓPTIMO; alerta ACELERANDO        → B3 solo por tendencia
    """
    rows = []
    def add(sku, nombre, cat, stocks, vtas_tienda, sem, edad, dscto, estado_t, stock_cd, rango):
        for t, stk, v, est in zip(("T1", "T2", "T3"), stocks, vtas_tienda, estado_t):
            if stk == 0 and v == 0:
                continue
            rows.append(dict(sku=sku, nombre=nombre, marca="M", categoria=cat, tienda=t, temporada="OI",
                             stock_total=stk, stock_valor_costo=stk * 20.0, prom_vta_uds=v,
                             cobertura_sem=(stk / v if v > 0 else None), estado=est, edad_semanas=edad,
                             rango_antiguedad=rango, vta_sem1_total=sem[0], vta_sem2_total=sem[1],
                             vta_sem3_total=sem[2], vta_sem4_total=sem[3], precio_blanco=100.0,
                             precio_vigente=round(100.0 * (1 - dscto), 2), costo=20.0, pct_descuento=dscto,
                             stock_cd=stock_cd, vta_soles_4sem=sum(sem) * 80.0, contrib_soles_4sem=sum(sem) * 30.0))
    add(101, "VC-LIQ", "CAMISAS", (40, 30, 10), (0, 0, 0), (0, 0, 0, 0), 30, 0.0, ("DORMIDO",) * 3, 0, "RANGO 6_9")
    add(102, "VC-1SEM", "CAMISAS", (100, 0, 0), (3, 0, 0), (0, 4, 4, 4), 12, 0.0, ("SOBRESTOCK", "", ""), 0, "RANGO 3_6")
    add(103, "VC-NEW", "POLOS", (5, 0, 0), (0, 0, 0), (0, 0, 0, 0), 5, 0.0, ("NUEVO SIN VENTA", "", ""), 0, "RANGO 0_3")
    add(201, "SOB-MKD", "POLOS", (100, 50, 0), (3, 2, 0), (5, 5, 5, 5), 20, 0.10, ("SOBRESTOCK", "SOBRESTOCK", ""), 0, "RANGO 3_6")
    add(202, "SOB-CANJ", "POLOS", (120, 0, 0), (2, 0, 0), (2, 2, 2, 2), 20, 0.60, ("ESTANCADO", "", ""), 0, "RANGO 3_6")
    add(203, "SOB-FREN", "PANTALONES", (80, 50, 0), (2, 2, 0), (4, 4, 4, 4), 6, 0.0, ("SOBRESTOCK", "SOBRESTOCK", ""), 0, "RANGO 0_3")
    add(301, "GAN-CD", "PANTALONES", (20, 20, 20), (10, 8, 2), (20, 20, 20, 20), 10, 0.0, ("QUIEBRE", "QUIEBRE", "ÓPTIMO"), 50, "RANGO 0_3")
    add(302, "GAN-SIN", "PANTALONES", (35, 35, 0), (5, 5, 0), (10, 10, 10, 10), 10, 0.0, ("PRE-QUIEBRE", "PRE-QUIEBRE", ""), 0, "RANGO 0_3")
    add(303, "OPT", "CAMISAS", (18, 18, 0), (1.5, 1.5, 0), (3, 3, 3, 3), 10, 0.0, ("ÓPTIMO", "ÓPTIMO", ""), 10, "RANGO 0_3")
    return pd.DataFrame(rows)


def _trans_sint():
    return pd.DataFrame({"sku": [201, 201, 303], "nombre": ["SOB-MKD", "SOB-MKD", "OPT"], "tienda_origen": ["T1", "T1", "T1"],
                         "tienda_destino": ["T3", "T2", "T2"], "uds_transferir": [8, 6, 3], "precio_vigente": [90.0, 90.0, 100.0],
                         "ganancia_esperada": [60.0, 40.0, 5.0]})


def _rep_sint():
    return pd.DataFrame({"sku": [301, 302], "a_reponer": [30, 40], "desde_cd": [30, 0], "pendiente": [0, 40], "marca": ["M", "M"]})


def _vp_sint():
    return pd.DataFrame({"sku": ["0301", "301"], "tienda": ["T1", "T2"], "marca": ["M", "M"], "neto_min": [100.0, 50.0],
                         "neto_max": [150.0, 80.0], "semanas_en_quiebre": [2, 3], "on_order": [0, 0], "evitable": [True, True]})


def _alertas_sint():
    return pd.DataFrame({"sku": [303, 202], "marca": ["M", "M"], "tipo_alerta": ["🟢 ACELERANDO", "🔴 FRENANDO"]})


@pytest.fixture(scope="module")
def bl():
    return rp.bloques_marca("M", _cob(), _trans_sint(), _vp_sint(), None, _rep_sint(), _alertas_sint(),
                            corte="30.08.2026", semana_iso="2026-35")


def test_b1_cadena_ultima_semana(bl):
    b1, dfm = bl["b1"], bl["dfm"]
    assert set(b1["sku"]) == {101, 102, 103}
    # definición independiente: sin venta en toda la cadena la última semana y con stock
    ref = dfm.groupby("sku").agg(v1=("vta_sem1_total", "first"), s=("stock_total", "sum"))
    assert set(ref[(ref.v1 <= 0) & (ref.s > 0)].index) == set(b1["sku"])
    assert b1["capital_costo"].sum() == pytest.approx((80 + 100 + 5) * 20.0)
    fila = b1.set_index("sku")
    assert fila.loc[101, "semanas_sin_venta"] == "4+" and fila.loc[102, "semanas_sin_venta"] == "1"
    assert fila.loc[101, "accion"].startswith("🏷️ Liquidar: cofinanciar 40%") and "S/ 60.00" in fila.loc[101, "accion"]  # pirámide 30-34 sem = 40%
    assert fila.loc[103, "accion"].startswith("👁️ Revisar exhibición (lanzamiento")


def test_b1_pareto_cadena(bl):
    b1 = bl["b1"].sort_values("capital_costo", ascending=False)
    # 2.000 / 1.600 / 100 → la 1ª siempre ⭐; la 2ª entra porque el acumulado ANTES de ella (54%) < 80%
    assert list(b1["top_80"]) == [True, True, False]
    assert b1["pct_acum"].is_monotonic_increasing and b1["pct_acum"].iloc[-1] == pytest.approx(1.0)
    assert bl["hechos"]["b1"]["n_top"] == 2


def test_b2_estado_cadena_y_precedencia_b1(bl):
    b2a = bl["b2a"].set_index("sku")
    assert set(b2a.index) == {201, 202, 203}          # 102 es SOBRESTOCK de cadena pero ya está en B1
    assert b2a.loc[201, "estado_cadena"] == "SOBRESTOCK" and b2a.loc[202, "estado_cadena"] == "ESTANCADO"
    assert b2a.loc[201, "precio_sugerido"] == pytest.approx(70.0)      # 100 × (1 − 30%) sobre precio BLANCO
    assert b2a.loc[201, "accion"].startswith("⬇️ Markdown cofinanciado 50/50: 30%")
    assert pd.isna(b2a.loc[202, "precio_sugerido"]) and b2a.loc[202, "accion"].startswith("↩️ Canje")
    assert b2a.loc[203, "accion"].startswith("⏸️ Frenar ingreso")
    assert b2a.loc[202, "tendencia"] == "▼"
    assert (b2a["grupo"] == "Sobrestock").all()
    assert bl["b2a"].sort_values("capital_costo", ascending=False)["top_80"].iloc[0]


def test_b2b_desbalance(bl):
    b2b = bl["b2b"]
    assert list(b2b["sku"]) == [201]                 # 303 no llega a 12 uds
    assert int(b2b["transf_uds"].iloc[0]) == 14 and b2b["accion"].iloc[0].startswith("🔄 Mover 14 uds a 2 tienda")


def test_b3_criterio_y_enriquecimiento(bl):
    b3 = bl["b3"].set_index("sku")
    assert set(b3.index) == {301, 302, 303}
    assert bl["umbral_b3"] == 2.0                    # < 4 SKUs vendiendo tras excluir B1/B2a → mínimo
    assert b3.loc[303, "entra_por"] == "tendencia" and b3.loc[301, "entra_por"] == "cobertura"
    assert b3.loc[301, "n_tiendas_quiebre"] == 2 and b3.loc[301, "accion"].startswith("📦 Reponer desde CD (50 uds")
    assert b3.loc[302, "accion"].startswith("🏭 Reorden") and "faltan 40 uds sin CD" in b3.loc[302, "accion"]
    assert b3.loc[301, "vp_neto_min"] == 150 and b3.loc[301, "vp_neto_max"] == 230 and b3.loc[301, "sem_en_quiebre_max"] == 3
    assert b3.loc[301, "necesidad_uds"] == 30 and pd.isna(b3.loc[303, "necesidad_uds"])


def test_bloques_no_solapan_capital(bl):
    s1, s2, s3 = set(bl["b1"]["sku"]), set(bl["b2a"]["sku"]), set(bl["b3"]["sku"])
    assert not (s1 & s2) and not (s1 & s3) and not (s2 & s3)
    h = bl["hechos"]
    assert h["b1"]["capital"] + h["b2a"]["capital"] <= h["foto"]["capital_total"]


def test_hechos_cuadran_con_bloques(bl):
    h = bl["hechos"]
    assert h["b1"]["capital"] == round(bl["b1"]["capital_costo"].sum())
    assert h["b2a"]["capital"] == round(bl["b2a"]["capital_costo"].sum())
    assert h["b2b"]["uds"] == int(bl["b2b"]["transf_uds"].sum())
    assert h["b3"]["n_skus"] == len(bl["b3"]) and h["b3"]["n_sin_cd"] == 1
    assert h["b1"]["por_linea"] == {"CAMISAS": 3600, "POLOS": 100}
    assert sum(h["b2a"]["por_linea"].values()) == h["b2a"]["capital"]
    json.dumps(h)                                     # serializable para Claude y Notion


def test_detalle_lote_es_b1_por_tienda(bl):
    d = rp.detalle_lote(bl)
    assert list(d.columns[:3]) == ["sku", "tienda", "uds"]
    assert len(d) == 3 + 1 + 1 and int(d["uds"].sum()) == 80 + 100 + 5
    import acciones_log
    assert len(acciones_log.normalizar_detalle(d)) == len(d)


def test_tablas_texto_cuadran(bl):
    import re
    t = rp.tablas_texto(bl)
    h = bl["hechos"]
    m = re.search(r"TOTAL VENTA CERO: (\d+) modelos · ([\d,]+) uds · S/ ([\d,]+)", t["b1"])
    assert m and int(m[1]) == h["b1"]["n_skus"] and int(m[3].replace(",", "")) == h["b1"]["capital"]
    m = re.search(r"TOTAL SOBRESTOCK: (\d+) modelos · ([\d,]+) uds · S/ ([\d,]+)", t["b2a"])
    assert m and int(m[3].replace(",", "")) == h["b2a"]["capital"]
    assert "▸ CAMISAS — S/ 3,600 en 2 modelo(s)" in t["b1"]     # agrupado por línea con subtotal
    assert "TOTAL GANADORES CORTOS: 3 modelos" in t["b3"]
    html = rp.tablas_html(bl)
    assert all("<table" in v for v in html.values())


def test_marca_sin_datos_no_rompe():
    b = rp.bloques_marca("NO-EXISTE", _cob(), corte="x")
    assert b["b1"].empty and b["b2a"].empty and b["b3"].empty and b["hechos"]["b1"]["n_skus"] == 0
    assert rp.tablas_texto(b)["b1"].strip().startswith("(sin modelos")
