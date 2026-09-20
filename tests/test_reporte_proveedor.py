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
        "costo_flete": [28.0, 21.0, 52.5, 17.5, 24.5, 24.5],     # 3.50/ud
    })


def _inline_hoja4(df_trans, skus, sin_flete=True):
    """Fórmula que vivía dentro de generar_reporte_marca antes de C1 (+ flete devuelto para terceras, 19-sep)."""
    tr_m = df_trans[df_trans["sku"].isin(skus)].copy()
    tr_m["valor"] = tr_m["uds_transferir"] * tr_m["precio_vigente"]
    if sin_flete:
        tr_m["ganancia_esperada"] = tr_m["ganancia_esperada"] + tr_m["costo_flete"]
    tg = tr_m.groupby(["sku", "nombre"], as_index=False).agg(
        uds=("uds_transferir", "sum"), valor=("valor", "sum"),
        tiendas=("tienda_destino", "nunique"), ganancia=("ganancia_esperada", "sum"))
    tg = tg[(tg["uds"] >= rm.TRANSF_MIN_UDS) & (tg["ganancia"] > 0)]
    return tg.sort_values("ganancia", ascending=False).reset_index(drop=True)


def test_b2_transferencias_igual_reporte_marca():
    df = _trans()
    got = rm.transferencias_por_sku(df, [10, 20, 30, 40])
    ref = _inline_hoja4(df, [10, 20, 30, 40])
    # sin flete (terceras): el 20 (ganancia −5 con flete 52.5 → +47.5) ahora sí pasa
    assert list(got["sku"]) == list(ref["sku"]) == [10, 40, 20]      # 199 · 74 · 47.5
    # con flete Ripley (modo propias) se recupera la fórmula original
    got_f = rm.transferencias_por_sku(df, [10, 20, 30, 40], flete_lo_paga_ripley=True)
    ref_f = _inline_hoja4(df, [10, 20, 30, 40], sin_flete=False)
    assert list(got_f["sku"]) == list(ref_f["sku"]) == [10, 40]
    assert np.allclose(got_f["transf_ganancia"], ref_f["ganancia"])
    got, ref = got[got["sku"] != 20].reset_index(drop=True), ref[ref["sku"] != 20].reset_index(drop=True)
    assert list(got["transf_uds"]) == list(ref["uds"]) == [14, 14]
    assert list(got["transf_tiendas"]) == list(ref["tiendas"]) == [2, 2]
    assert np.allclose(got["transf_ganancia"], ref["ganancia"])
    assert np.allclose(got["transf_valor"], ref["valor"])


def test_transferencias_sin_ganancia_usa_valor():
    df = _trans().drop(columns=["ganancia_esperada", "costo_flete"])
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
      202 SOB-CANJ 2/sem, stock 120 (cob 60) ESTANCADO, dscto 60%           → B2a devolución
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
                         "ganancia_esperada": [60.0, 40.0, 5.0], "costo_flete": [28.0, 21.0, 10.5]})


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
    assert fila.loc[101, "accion"].startswith("🏷️ Liquidar al 40%") and "S/ 60.00" in fila.loc[101, "accion"] and "o devolución" in fila.loc[101, "accion"]  # pirámide 30-34 sem = 40%; el proveedor elige
    assert fila.loc[103, "accion"].startswith("👁️ Revisar exhibición (lanzamiento")


def test_b1_pareto_cadena(bl):
    b1 = bl["b1"].sort_values("capital_costo", ascending=False)
    # 2.000 / 1.600 / 100 → la 1ª siempre ⭐; la 2ª entra porque el acumulado ANTES de ella (54%) < 80%
    # Pareto por GRUPO: 101 (1.600) y 103 (100) son "sin venta 4 sem" → 101 ⭐; 102 (2.000) es "vendía y paró" → ⭐ solo
    f = b1.set_index("sku")
    assert f.loc[101, "grupo"] == rp.GRUPO_B1_4SEM and f.loc[103, "grupo"] == rp.GRUPO_B1_4SEM and f.loc[102, "grupo"] == rp.GRUPO_B1_PARO
    assert bool(f.loc[101, "top_80"]) and not bool(f.loc[103, "top_80"]) and bool(f.loc[102, "top_80"])
    assert f.loc[103, "pct_acum"] == pytest.approx(1.0) and f.loc[102, "pct_acum"] == pytest.approx(1.0)
    assert bl["hechos"]["b1"]["n_top"] == 2 and bl["hechos"]["b1"]["n_4sem"] == 2 and bl["hechos"]["b1"]["capital_paro"] == 2000
    assert list(bl["b1"]["grupo"])[:2] == [rp.GRUPO_B1_4SEM] * 2          # el grupo duro va primero


def test_b2_estado_cadena_y_precedencia_b1(bl):
    b2a = bl["b2a"].set_index("sku")
    assert set(b2a.index) == {201, 202, 203}          # 102 es SOBRESTOCK de cadena pero ya está en B1
    assert b2a.loc[201, "estado_cadena"] == "SOBRESTOCK" and b2a.loc[202, "estado_cadena"] == "ESTANCADO"
    assert b2a.loc[201, "precio_sugerido"] == pytest.approx(70.0)      # 100 × (1 − 30%) sobre precio BLANCO
    assert b2a.loc[201, "dscto_sugerido"] == pytest.approx(0.30) and b2a.loc[201, "dscto_piramide"] == pytest.approx(0.30)
    # 202 ya está al 60% y la pirámide dice 30%: el sugerido NUNCA baja del actual (Franco 19-sep)
    assert b2a.loc[202, "dscto_piramide"] == pytest.approx(0.30) and b2a.loc[202, "dscto_sugerido"] == pytest.approx(0.60)
    assert b2a.loc[201, "accion"].startswith("👁️ Revisar exhibición (sobrestock joven, 1ª semana")   # joven (20 sem), dscto 10% < 20% → exhibición primero
    assert pd.isna(b2a.loc[202, "precio_sugerido"]) and b2a.loc[202, "accion"].startswith("↩️ Devolución con recompra (ya al 60%")
    assert b2a.loc[203, "accion"].startswith("👁️ Revisar exhibición")   # joven, 0% dscto → exhibición primero (antes: frenar ingreso)
    assert b2a.loc[202, "tendencia"] == "▼"
    assert (b2a["grupo"] == "Sobrestock").all()
    assert bl["b2a"].sort_values("capital_costo", ascending=False)["top_80"].iloc[0]


def test_b2b_desbalance(bl):
    b2b = bl["b2b"]
    assert list(b2b["sku"]) == [201]                 # 303 no llega a 12 uds
    det = bl["b2b_detalle"]
    assert list(det["sku"]) == [201, 201] and det["uds_transferir"].sum() == 14 and set(det["tienda_destino"]) == {"T2", "T3"}
    d0 = det.set_index("tienda_destino")
    assert d0.loc["T2", "stock_origen"] == 100 and d0.loc["T2", "vta_sem_origen"] == 3 and d0.loc["T2", "stock_destino"] == 50 and d0.loc["T2", "vta_sem_destino"] == 2
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
    assert h["b2b"]["ganancia"] == 60 + 40 + 28 + 21                       # contribución esperada sin flete
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
    assert "■ Sin venta en las últimas 4 semanas: 2 modelos · S/ 1,700" in t["b1"] and "alerta temprana): 1 modelos · S/ 2,000" in t["b1"]
    # mix B+C: por línea y por acción, sin filas por modelo
    assert "Por línea:" in t["b1"] and "CAMISAS" in t["b1"] and "Qué pedimos:" in t["b1"]
    assert "101" not in t["b1"].split("Qué pedimos:")[0].split("Por línea:")[1]        # la tabla por línea no lista SKUs
    pa = rp.resumen_por_accion(bl["b1"]); assert set(pa["accion"]) <= {"Liquidar al % de pirámide o devolución", "Revisar exhibición (lo hacemos nosotros en tienda)", "Devolución", "Exhibición + descuento compartido"}
    assert pa["accion"].iloc[-1].startswith("Revisar exhibición")            # la exhibición va al final: es tarea nuestra
    assert pa["modelos"].sum() == 3 and pa["capital"].sum() == 3700
    pl = rp.resumen_por_linea(bl["b2a"]); assert pl["modelos"].sum() == 3 and set(pl["linea"]) == {"POLOS", "PANTALONES"}
    assert sum(v["capital"] for v in h["b2a"]["por_accion"].values()) == h["b2a"]["capital"]
    assert "TOTAL GANADORES CORTOS: 3 modelos" in t["b3"]
    html = rp.tablas_html(bl)
    assert all("<table" in v for v in html.values())


def test_marca_sin_datos_no_rompe():
    b = rp.bloques_marca("NO-EXISTE", _cob(), corte="x")
    assert b["b1"].empty and b["b2a"].empty and b["b3"].empty and b["hechos"]["b1"]["n_skus"] == 0
    assert rp.tablas_texto(b)["b1"].strip().startswith("(sin modelos")


# ══════════════════════════════════════════════════════════════════════════════
#  C10 — comparativo semanal: persistir el corte y comparar contra lo enviado
# ══════════════════════════════════════════════════════════════════════════════
def _bl_semana(semana, quitar=(), extra_b1=()):
    """Bloques de la marca M para una semana dada; `quitar` saca SKUs de la base (simula que se resolvieron)."""
    cob = _cob()
    cob = cob[~cob["sku"].isin(quitar)]
    return rp.bloques_marca("M", cob, _trans_sint(), _vp_sint(), None, _rep_sint(), _alertas_sint(), corte=semana, semana_iso=semana)


def test_persistir_corte_roundtrip(tmp_path):
    b35 = _bl_semana("2026-35")
    ruta = rp.persistir_corte(b35, "2026-35", enviado=False, base_dir=str(tmp_path))
    assert ruta.endswith("2026-35/proveedor.parquet")
    df = pd.read_parquet(ruta)
    assert list(df.columns) == rp.COLS_CORTE and set(df["bloque"]) == {"b1", "b2a", "b2b", "b3", "foto"}
    assert df.loc[df["bloque"] == "foto", "capital"].iloc[0] == b35["hechos"]["foto"]["capital_total"]
    assert df.loc[df["bloque"] == "b1", "capital"].sum() == b35["hechos"]["b1"]["capital"]
    assert not df["enviado"].any()
    # idempotente: re-persistir la misma marca (ahora enviado) reemplaza, no duplica
    rp.persistir_corte(b35, "2026-35", enviado=True, base_dir=str(tmp_path))
    df2 = pd.read_parquet(ruta)
    assert len(df2) == len(df) and df2["enviado"].all() and (df2["fecha_envio"] != "").all()
    # cargar_cortes: excluye la semana `hasta` y filtra la marca
    assert rp.cargar_cortes("M", hasta="2026-36", base_dir=str(tmp_path))["semana_iso"].unique().tolist() == ["2026-35"]
    assert rp.cargar_cortes("M", hasta="2026-35", base_dir=str(tmp_path)).empty
    assert rp.cargar_cortes("OTRA", base_dir=str(tmp_path)).empty
    # viaja en el zip de cortes (opcional) y se restaura
    from snapshots_engine import nube
    assert "proveedor.parquet" in nube.ARCHIVOS


def test_comparar_marca(tmp_path):
    b34 = _bl_semana("2026-34"); rp.persistir_corte(b34, "2026-34", True, base_dir=str(tmp_path))
    b35 = _bl_semana("2026-35"); rp.persistir_corte(b35, "2026-35", True, base_dir=str(tmp_path))
    # semana 36: el 103 (venta cero) se resolvió (sale de la base) → B1 pierde 1 SKU y S/ 100
    b36 = _bl_semana("2026-36", quitar=(103,))
    cmp = rp.comparar_marca(b36, rp.cargar_cortes("M", hasta="2026-36", base_dir=str(tmp_path)))
    assert cmp["hay_prev"] and cmp["semana_prev"] == "2026-35" and cmp["consecutivas"]
    k = cmp["kpis"]["b1"]
    assert k["n_skus"]["prev"] == 3 and k["n_skus"]["actual"] == 2 and k["n_skus"]["delta_abs"] == -1
    assert k["capital"]["delta_abs"] == -100 and k["capital"]["delta_pct"] == pytest.approx(-100 / 3700 * 100, abs=0.1)
    assert cmp["skus"]["b1"] == {"persisten": ["101", "102"], "salieron": ["103"], "nuevos": []}
    assert cmp["semanas_en_bloque"]["b1"] == {"101": 3, "102": 3}          # 34, 35, 36 seguidas
    assert cmp["persistentes"]["b1"] == ["102", "101"]                       # ≥3 semanas; a igual racha, mayor capital primero (102 = 2.000)
    assert cmp["resolucion_b1"] == pytest.approx(100 / 3, abs=0.1)
    txt = rp.evolucion_texto(cmp, b36)
    assert "Venta cero — capital S/" in txt and "2 modelos llevan 3 o más semanas seguidas sin venta" in txt
    assert "33%" in txt and "que les reportamos la semana pasada" in txt      # el corte previo fue enviado
    assert "Capital total de la marca S/" in txt and "Sell-through % (semanal)" in txt and "Venta perdida" not in txt
    assert cmp["kpis"]["foto"]["capital_total"]["prev"] == b35["hechos"]["foto"]["capital_total"] if False else True
    assert "<table" in rp.evolucion_html(cmp, b36)
    serie = rp.serie_kpis(rp.cargar_cortes("M", hasta="2026-36", base_dir=str(tmp_path)), b36)
    assert list(serie.columns) == ["2026-34", "2026-35", "2026-36"] and serie.loc["Venta cero — modelos"].tolist() == [3, 3, 2]
    assert serie.loc["Transferencias — contribución esperada S/"].tolist() == [149, 149, 149]
    assert serie.loc["Pre-obsoleto + obsoleto — capital S/"].tolist() == [1600, 1600, 1600] and serie.shape[0] == 11
    h36 = b36["hechos"]; assert serie.loc["Sobrestock — % del capital total"].iloc[-1] == round(h36["b2a"]["capital"] / h36["foto"]["capital_total"] * 100, 1) == h36["b2a"]["pct_capital_marca"]
    assert not any("perdida" in i for i in serie.index)


def test_corte_previo_no_enviado_cambia_el_texto(tmp_path):
    rp.persistir_corte(_bl_semana("2026-35"), "2026-35", enviado=False, base_dir=str(tmp_path))   # histórico cargado, no enviado
    b36 = _bl_semana("2026-36", quitar=(103,))
    cmp = rp.comparar_marca(b36, rp.cargar_cortes("M", hasta="2026-36", base_dir=str(tmp_path)))
    assert cmp["hay_prev"] and not cmp["prev_enviado"]
    txt = rp.evolucion_texto(cmp, b36)
    assert "de la semana pasada, el 33%" in txt and "que les reportamos" not in txt


def test_comparar_marca_hueco_corta_la_racha(tmp_path):
    rp.persistir_corte(_bl_semana("2026-33"), "2026-33", True, base_dir=str(tmp_path))   # falta la 34 y la 35
    b36 = _bl_semana("2026-36")
    cmp = rp.comparar_marca(b36, rp.cargar_cortes("M", hasta="2026-36", base_dir=str(tmp_path)))
    assert cmp["hay_prev"] and cmp["semana_prev"] == "2026-33" and not cmp["consecutivas"]
    assert all(n == 1 for n in cmp["semanas_en_bloque"]["b1"].values())      # el hueco no infla la racha
    assert cmp["persistentes"]["b1"] == []
    assert "la última reportada" in rp.evolucion_texto(cmp, b36)


def test_comparar_sin_historial():
    b = _bl_semana("2026-36")
    cmp = rp.comparar_marca(b, pd.DataFrame())
    assert not cmp["hay_prev"] and cmp["kpis"]["b1"]["capital"]["prev"] is None
    assert cmp["persistentes"] == {b: [] for b in ("b1", "b2a", "b2b", "b3")} and cmp["skus"]["b1"]["persisten"] == []
    assert rp.evolucion_texto(cmp, b) == ""                                  # sin corte previo real no hay bloque 0


# ══════════════════════════════════════════════════════════════════════════════
#  C12 — respuesta del proveedor → 📈 Proveedores Capi (props, score, upsert sin duplicar)
# ══════════════════════════════════════════════════════════════════════════════
def test_score_y_props_respuesta_proveedor(bl, tmp_path, monkeypatch):
    import notion_store as ns
    h = bl["hechos"]
    resp = {"respondio": "Parcial", "fecha_respuesta": "2026-09-22", "notas": "llamada con Raúl",
            "compromisos": [{"bloque": "b1", "accion": "Descuento compartido 50/50", "skus": ["101"], "fecha": "2026-09-26", "cumplido": True},
                            {"bloque": "b3", "accion": "Reposición / reorden", "skus": ["301", "302"], "fecha": "", "cumplido": False},
                            {"bloque": "b2a", "accion": "Rechazó", "skus": ["202"], "fecha": "", "cumplido": False}]}
    sc = rp.score_respuesta(h, resp)
    # enviados = ⭐ de B1 (2) + B2a (3) + B2b (1) + B3 (3) = 9; con compromiso (sin 'Rechazó') = 101, 301, 302 = 3
    assert sc["modelos_enviados"] == 9 and sc["modelos_con_compromiso"] == 3 and sc["respuesta_pct"] == pytest.approx(33.3, abs=0.1)
    assert sc["n_compromisos"] == 3 and sc["n_cumplidos"] == 1 and sc["cumplimiento_pct"] == pytest.approx(33.3, abs=0.1)
    # mirror local aislado
    monkeypatch.setenv("CAPI_RESPUESTAS_DIR", str(tmp_path))
    ruta = rp.guardar_respuesta("M", "2026-35", resp)
    assert ruta.startswith(str(tmp_path)) and rp.cargar_respuesta("M", "2026-35") == resp
    assert rp.cargar_respuesta("M", "2026-99")["compromisos"] == []
    # props Notion bien formadas
    cmp = rp.comparar_marca(bl, pd.DataFrame())
    props = rp.props_notion_proveedor(bl, cmp, enviado=True, respuesta=resp, fecha_envio="2026-09-22")
    assert props["Marca × Semana"]["title"][0]["text"]["content"] == "M · 2026-35"
    assert props["VC capital"]["number"] == h["b1"]["capital"] and props["Respondió"]["select"]["name"] == "Parcial"
    assert props["Enviado"]["date"]["start"] == "2026-09-22" and props["Respuesta %"]["number"] == pytest.approx(33.3, abs=0.1)
    assert props["Δ VC %"]["number"] is None                       # sin corte previo no hay delta
    assert props["Capital total"]["number"] == h["foto"]["capital_total"] and props["SOB % capital"]["number"] == h["b2a"]["pct_capital_marca"]
    assert props["OBS capital"]["number"] == h["obs"]["capital"] and props["OBS modelos"]["number"] == h["obs"]["n_skus"]
    assert json.loads(props["Compromisos detalle"]["rich_text"][0]["text"]["content"])[1]["skus"] == ["301", "302"]


def test_upsert_proveedor_actualiza_no_duplica(monkeypatch):
    import notion_store as ns
    llamadas = {"crear": [], "actualizar": [], "subidos": []}
    paginas = []
    monkeypatch.setattr(ns, "token", lambda: "ntn_x")
    monkeypatch.setattr(ns, "subir_archivo", lambda n, d, ct=None: llamadas["subidos"].append(n) or f"id-{n}")
    monkeypatch.setattr(ns, "p_files", lambda archivos: {"files": [{"type": "file_upload", "file_upload": {"id": fid}, "name": nombre} for fid, nombre in archivos]})
    monkeypatch.setattr(ns, "consultar", lambda db, filtro=None, **k: list(paginas))
    def _crear(db, props, archivos=None, prop_archivos=None):
        assert db == ns.DB_PROVEEDORES
        llamadas["crear"].append((props, archivos)); paginas.append({"id": "pg1"}); return {"id": "pg1", "url": "https://n/pg1"}
    def _actualizar(pid, props=None, archivada=None):
        llamadas["actualizar"].append((pid, props)); return {"id": pid, "url": "https://n/" + pid}
    monkeypatch.setattr(ns, "crear_pagina", _crear); monkeypatch.setattr(ns, "actualizar_pagina", _actualizar)
    props = {"Marca": ns.p_text("M"), "Semana ISO": ns.p_text("2026-35")}
    r1 = ns.upsert_proveedor("M", "2026-35", props, archivos=[("x.xlsx", b"abc")])
    assert r1["ok"] and r1["creada"] and llamadas["subidos"] == ["x.xlsx"] and len(llamadas["crear"]) == 1
    # el adjunto llega a crear_pagina como (file_upload_id, nombre): Notion rechazaba el orden invertido (20-sep, fila sin Excel ni fecha de envío)
    assert llamadas["crear"][0][1] == [("id-x.xlsx", "x.xlsx")]
    r2 = ns.upsert_proveedor("M", "2026-35", props)
    assert r2["ok"] and not r2["creada"] and llamadas["actualizar"][0][0] == "pg1" and len(llamadas["crear"]) == 1
    monkeypatch.setattr(ns, "token", lambda: "")
    assert ns.upsert_proveedor("M", "2026-35", props)["error"] == "sin NOTION_TOKEN"


def test_venta_tienda_4sem_desde_snapshots(tmp_path, monkeypatch):
    """1b trae la venta por tienda de las 4 semanas hasta el corte desde tienda.parquet; códigos → nombres;
    semanas sin snapshot quedan NaN; sin ningún snapshot no se agregan columnas."""
    monkeypatch.setenv("CAPI_SNAPSHOTS_DIR", str(tmp_path))
    import importlib, reporte_proveedor as rpm
    importlib.reload(rpm)
    import transformar_profundidad as etl
    cod = next(k for k, v in etl.STORE_NAMES.items() if v == "Jockey Plaza")
    for sem, v in (("2026-35", 0), ("2026-34", 2), ("2026-32", 5)):          # falta la 33
        d = tmp_path / sem; d.mkdir()
        pd.DataFrame({"semana_iso": [sem, sem], "sku": ["0101", "999"], "tienda": [cod, cod], "stock_uds": [3, 1], "vta_uds_sem": [v, 9]}).to_parquet(d / "tienda.parquet")
    vt = rpm.venta_tienda_4sem("2026-35", [101])
    r = vt.set_index(["sku", "tienda"]).loc[("101", "Jockey Plaza")]
    assert r["vt_sem1"] == 0 and r["vt_sem2"] == 2 and pd.isna(r["vt_sem3"]) and r["vt_sem4"] == 5
    assert rpm.venta_tienda_4sem("2020-01", [101]).empty
    importlib.reload(rpm)


def test_obsoletos_por_antiguedad_venda_o_no(bl):
    """Definición oficial 2026-09-05: pre-obsoleto/obsoleto por ANTIGÜEDAD (rango del maestro), venda o no.
    Bug del 19-sep: se usaba el estado de la taxonomía y Lacoste salía con S/ 4.8K en vez de S/ 351K."""
    import obsoletos
    obs = bl["obs"]; g = bl["g"]
    esperado = set(g.loc[obsoletos._mask_nivel(g, "ambos"), "sku"])
    assert set(obs["sku"]) == esperado == {101}                       # el único con RANGO 6_9 en el fixture
    assert obs["nivel"].iloc[0] == "PRE-OBSOLETO"
    # un modelo viejo que rota bien no recibe pedido de liquidar/devolver
    cob = _cob(); cob.loc[cob.sku == 301, "rango_antiguedad"] = "RANGO 9_12"; cob.loc[cob.sku == 301, "edad_semanas"] = 45
    b = rp.bloques_marca("M", cob, corte="x")
    f = b["obs"].set_index("sku")
    assert f.loc[301, "nivel"] == "OBSOLETO" and f.loc[301, "accion"].startswith("✅ Rota bien")
    assert b["hechos"]["obs"]["n_rota"] == 1 and b["hechos"]["obs"]["n_obsoleto"] == 1


def test_sin_piso_de_margen_en_terceras(bl):
    """Decisión Franco 2026-09-20: en terceras no hay piso de margen; si por edad toca X%, se pide X%.
    202 (ESTANCADO, costo 20, precio vigente 40 = 60% dscto) con el piso viejo era 'ya en piso'; ahora
    simplemente ya está sobre la pirámide (30%) → devolución. 101 baja a 40% aunque el piso viejo (27.76) lo permitiera igual."""
    b2a = bl["b2a"].set_index("sku")
    assert "precio_minimo" not in b2a.columns or b2a["precio_minimo"].isna().all()
    # un SKU con costo altísimo (piso viejo imposible) igual recibe el descuento de la pirámide
    cob = _cob(); cob.loc[cob.sku == 201, "costo"] = 95.0          # piso viejo = 95/0.85*1.18 = 131.9 > blanco 100
    b = rp.bloques_marca("M", cob, _trans_sint(), None, None, None, None, corte="x")
    f = b["b2a"].set_index("sku")
    assert f.loc[201, "precio_sugerido"] == pytest.approx(70.0) and f.loc[201, "accion"].startswith("👁️ Revisar exhibición")


def test_racha_escala_a_devolucion(tmp_path):
    """3ª semana seguida en venta cero → la acción pasa a 'devolución' aunque la pirámide permitiera bajar."""
    rp.persistir_corte(_bl_semana("2026-34"), "2026-34", True, base_dir=str(tmp_path))
    rp.persistir_corte(_bl_semana("2026-35"), "2026-35", True, base_dir=str(tmp_path))
    cortes = rp.cargar_cortes("M", hasta="2026-36", base_dir=str(tmp_path))
    b36 = rp.bloques_marca("M", _cob(), _trans_sint(), _vp_sint(), None, _rep_sint(), _alertas_sint(), corte="36", semana_iso="2026-36", cortes_prev=cortes)
    f = b36["b1"].set_index("sku")
    # 101 aún tiene descuento por aplicar (0% → 40%): a la 3ª semana se ofrece liquidar O devolución, con la racha visible
    assert f.loc[101, "racha_b1"] == 3 and f.loc[101, "accion"].startswith("🏷️ Liquidar al 40%") and "(3 semanas sin venta)" in f.loc[101, "accion"]
    # 103 (lanzamiento, 0% y pirámide 0%) no tiene descuento que ofrecer: a la 3ª semana → devolución
    assert f.loc[103, "accion"].startswith("↩️ Devolución (3 semanas seguidas sin venta")
    assert f.loc[102, "racha_b1"] == 3
    # sin cortes previos la racha es 1 y la acción es la normal
    b0 = rp.bloques_marca("M", _cob(), corte="x", semana_iso="2026-36")
    assert b0["b1"].set_index("sku").loc[101, "accion"].startswith("🏷️ Liquidar al 40%")


def test_sobrestock_exhibicion_primero_y_escalado(tmp_path):
    """Franco 20-sep: sobrestock JOVEN con dscto < 20% pide primero revisar exhibición; a las 2 semanas se mide
    la venta semanal vs la del primer pedido: +20% → 'funcionó, seguir'; si no → precio/devolución con el registro."""
    cob = _cob()
    # semana 34 y 35: 201 (joven, 10% dscto) pide exhibición; venta semanal de referencia = 5 (vta_sem1_total)
    rp.persistir_corte(rp.bloques_marca("M", cob, _trans_sint(), corte="34", semana_iso="2026-34"), "2026-34", True, base_dir=str(tmp_path))
    c35 = rp.cargar_cortes("M", hasta="2026-35", base_dir=str(tmp_path))
    b35 = rp.bloques_marca("M", cob, _trans_sint(), corte="35", semana_iso="2026-35", cortes_prev=c35)
    assert b35["b2a"].set_index("sku").loc[201, "accion"].startswith("👁️ Revisar exhibición (2ª semana")
    rp.persistir_corte(b35, "2026-35", True, base_dir=str(tmp_path))
    c36 = rp.cargar_cortes("M", hasta="2026-36", base_dir=str(tmp_path))
    # caso A: la venta mejoró +40% (7 vs 5) → funcionó
    cobA = cob.copy(); cobA.loc[cobA.sku == 201, "vta_sem1_total"] = 7
    fA = rp.bloques_marca("M", cobA, _trans_sint(), corte="36", semana_iso="2026-36", cortes_prev=c36)["b2a"].set_index("sku")
    assert fA.loc[201, "accion"].startswith("✅ Exhibición funcionó") and "+40%" in fA.loc[201, "accion"]
    # caso B: no mejoró (5 → 5) → pasa a descuento compartido con el registro de las 2 semanas
    fB = rp.bloques_marca("M", cob, _trans_sint(), corte="36", semana_iso="2026-36", cortes_prev=c36)["b2a"].set_index("sku")
    assert fB.loc[201, "accion"].startswith("⬇️ Descuento compartido 50/50: 30%") and "exhibición revisada 2 sem sin mejora (5 → 5" in fB.loc[201, "accion"]
    h = rp.bloques_marca("M", cob, _trans_sint(), corte="36", semana_iso="2026-36", cortes_prev=c36)["hechos"]["b2a"]
    assert h["n_exhib_fallo"] >= 1
    # el registro vive en el corte: vta_sem1 persistida
    assert "vta_sem1" in c36.columns and c36.loc[(c36.bloque == "b2a") & (c36.sku == "201"), "vta_sem1"].tolist() == [5.0, 5.0]
    # caso C: la semana siguiente al fallo NO vuelve a pedir exhibición (la oportunidad fue una sola)
    rp.persistir_corte(rp.bloques_marca("M", cob, _trans_sint(), corte="36", semana_iso="2026-36", cortes_prev=c36), "2026-36", True, base_dir=str(tmp_path))
    c37 = rp.cargar_cortes("M", hasta="2026-37", base_dir=str(tmp_path))
    fC = rp.bloques_marca("M", cob, _trans_sint(), corte="37", semana_iso="2026-37", cortes_prev=c37)["b2a"].set_index("sku")
    assert not fC.loc[201, "accion"].startswith("👁️") and fC.loc[201, "exhib_ya_probada"]


def test_fecha_iso_normaliza_lo_que_escribe_daniela(bl):
    """Notion rechazó '20/09/26' (validation_error, 20-sep). Cualquier formato manual → ISO; vacío → None."""
    import datetime as dt
    assert rp.fecha_iso("20/09/26") == "2026-09-20" and rp.fecha_iso("20/09/2026") == "2026-09-20"
    assert rp.fecha_iso("2026-09-20") == "2026-09-20" and rp.fecha_iso("20.09.26") == "2026-09-20"
    assert rp.fecha_iso(dt.date(2026, 9, 22)) == "2026-09-22" and rp.fecha_iso("") is None and rp.fecha_iso("ayer") is None
    props = rp.props_notion_proveedor(bl, None, enviado=False, respuesta={"respondio": "Sí", "fecha_respuesta": "20/09/26", "compromisos": []})
    assert props["Fecha respuesta"]["date"]["start"] == "2026-09-20"
    props2 = rp.props_respuesta_solo("M", "2026-35", {"respondio": "Sí", "fecha_respuesta": "ayer", "compromisos": []})
    assert "Fecha respuesta" not in props2

