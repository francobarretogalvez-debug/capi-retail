"""Motor del módulo 🚢 PV en Tránsito (2026-10-04) sobre fixtures FICTICIOS.

Cada OC del fixture prueba una regla (ver tests/fixtures/make_detalle_mini.py). hoy = 2026-10-04 fijo.
Números esperados (calculados a mano):
  MARQUIS PV hombre: OC 1001 (200 u recibidas, FOB 1000) · 1002 (200 pend, 15/10) · 1003 (100 pend,
  vencida 20/09) · 1010 (120 pend, FOB 0, 20/11) → compra 620 · recibido 200 · pend 420 · atrasadas 100.
  NAVIGATA: 1004 (300+50 línea partida, 22/10) · 1005 (150 recibida En CD) → compra 500 · rec 150 · pend 350.
  US POLO (alias 'U.S. Polo Assn.'): 1006 80 u sin ETA → compra 80 · pend 80 · atrasadas 80.
  Fuera: 1007 sin marca · 1008 OI 27 · 1009 MUJER."""
import os
from datetime import date

import numpy as np
import pandas as pd
import pytest

import pv_transito as pt

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
DETALLE = os.path.join(FIX, "detalle_comex_mini.xlsx")
LLEGADAS = os.path.join(FIX, "llegadas_franco_mini.xlsx")
HOY = date(2026, 10, 4)
CFG = {**pt.cargar_config(), "marcas_foco": ["MARQUIS", "NAVIGATA", "US POLO"]}


@pytest.fixture(scope="module")
def oc_todo():
    return pt.leer_detalle(DETALLE, fecha_reporte=date(2026, 9, 24), cfg=CFG)


@pytest.fixture(scope="module")
def res(oc_todo):
    return pt.procesar(DETALLE, nombre="DETALLE (29).xlsx", hoy=HOY, temporada="PV", fecha_reporte=date(2026, 9, 24), cfg=CFG)


# ── Lectura ──────────────────────────────────────────────────────────────────
def test_detecta_formato_y_hoja():
    assert pt.detectar_formato(pt._leer_hojas(DETALLE)) == "detalle"
    assert pt.detectar_formato(pt._leer_hojas(LLEGADAS)) == "llegadas"
    _, fmt = pt.leer_reporte(DETALLE, cfg=CFG)
    assert fmt == "detalle"


def test_mapea_columnas_por_nombre_no_por_posicion():
    m = pt.mapear_columnas(["  monto_fob ", "Und", "proyectado ingreso cd3", "OC", "Línea", "ETA"])
    assert m == {"oc": "OC", "linea": "Línea", "uds": "Und", "fob_usd": "  monto_fob ",
                 "eta_puerto": "ETA", "eta_cd": "proyectado ingreso cd3"}
    # las dos formas (DETALLE y hojas de Franco) mapean a las mismas columnas estándar
    assert pt.mapear_columnas(["UNIDADES", "ETD1", "ETA CALLAO"]) == {"uds": "UNIDADES", "etd": "ETD1", "eta_puerto": "ETA CALLAO"}


def test_faltan_obligatorias_levanta_error():
    with pytest.raises(ValueError, match="obligatorias"):
        pt._estandarizar(pd.DataFrame({"OC": ["1"], "MARCA": ["X"]}), "detalle", HOY, CFG, pt.cargar_alias())


def test_estandar_tipos_y_recibida(oc_todo):
    assert len(oc_todo) == 12 and oc_todo["oc"].dtype == object
    assert set(pt.COLUMN_MAP) <= set(oc_todo.columns)
    assert pd.api.types.is_datetime64_any_dtype(oc_todo["eta_cd"])
    rec = oc_todo.groupby("oc")["recibida"].all()
    assert rec["1001"] and rec["1005"] and not rec["1002"] and not rec["1003"]
    assert oc_todo.drop_duplicates("oc").set_index("oc")["temporada"]["1008"] == "OI"
    assert (oc_todo[oc_todo["oc"] != "1008"]["temporada"] == "PV").all()


def test_alias_de_marca(oc_todo):
    m = oc_todo.drop_duplicates("oc").set_index("oc")
    assert m.loc["1006", "marca_norm"] == "US POLO" and m.loc["1006", "marca_display"] == "U.S. Polo Assn."
    assert m.loc["1001", "marca_norm"] == "MARQUIS" and m.loc["1001", "marca_display"] == "Marquis"
    norm, disp = pt.normalizar_marca(pd.Series(["dockers2", "Jack & Jones", "  marquis "]))
    assert list(norm) == ["DOCKERS", "JACK & JONES", "MARQUIS"] and list(disp) == ["Dockers", "Jack & Jones", "Marquis"]


def test_fechas_serial_y_fuera_de_rango():
    s = pd.Series([46223, 0, None, 46285.0, "24/09/2026"])
    f = pt._a_fecha(s)
    assert f.iloc[0] == pd.Timestamp("2026-07-20") and pd.isna(f.iloc[1]) and pd.isna(f.iloc[2])   # 46223 = 20/07/2026
    assert f.iloc[3] == pd.Timestamp("2026-09-20") and f.iloc[4] == pd.Timestamp("2026-09-24")     # 46285 = 20/09/2026


def test_fecha_reporte_desde_nombre():
    h = date(2026, 10, 4)
    assert pt.fecha_reporte_de("LLEGADAS RETRASOS COMEX 24.09.xlsb", hoy=h) == date(2026, 9, 24)
    assert pt.fecha_reporte_de("DETALLE_2026-09-24.xlsx", hoy=h) == date(2026, 9, 24)
    assert pt.fecha_reporte_de("Base al 20.09.xlsx", hoy=h) == date(2026, 9, 20)
    assert pt.fecha_reporte_de("DETALLE (29).xlsx", fallback=date(2026, 9, 30), hoy=h) == date(2026, 9, 30)
    assert pt.fecha_reporte_de("corte 15.12.xlsx", hoy=h) == date(2025, 12, 15)     # futura → año anterior


# ── Filtros, líneas partidas, validación ─────────────────────────────────────
def test_filtro_marcas_division_temporada(oc_todo):
    f = pt.filtrar(oc_todo, CFG["marcas_foco"], "HOMBRE", "PV")
    assert set(f["oc"]) == {"1001", "1002", "1003", "1004", "1005", "1006", "1010"}
    assert "1009" not in set(pt.filtrar(oc_todo, None, "HOMBRE", None)["oc"])
    assert set(pt.filtrar(oc_todo, None, None, "OI")["oc"]) == {"1008"}


def test_lineas_partidas_se_suman(oc_todo):
    f = pt.filtrar(oc_todo, CFG["marcas_foco"], "HOMBRE", "PV")
    s, n = pt.sumar_lineas_partidas(f)
    assert n == 1 and len(s) == len(f) - 1
    fila = s[s["oc"] == "1004"].iloc[0]
    assert fila["uds"] == 350 and fila["fob_usd"] == 1050


def test_validar_hallazgos(oc_todo):
    h = pt.validar(oc_todo)
    por = h.groupby("tipo")["oc"].apply(set).to_dict()
    assert por["SIN_MARCA"] == {"1007"}
    assert por["SIN_ETA"] == {"1006"}
    assert por["COSTO_CERO"] == {"1010"}
    assert por["LINEA_PARTIDA"] == {"1004"}
    assert "UDS_NO_POSITIVAS" not in por and "SIN_TEMPORADA" not in por


# ── Costo ────────────────────────────────────────────────────────────────────
def test_costo_fob_por_factor_y_base(oc_todo):
    f = pt.filtrar(oc_todo, ["MARQUIS"], "HOMBRE", "PV")
    e = pt.enriquecer_con_base(f, None, CFG)
    r = e.set_index("oc")
    assert r.loc["1002", "costo_unit_sol"] == pytest.approx(800 / 200 * 4.04)
    assert r.loc["1002", "costo_fuente"] == "fob_x_factor" and r.loc["1010", "costo_fuente"] == ""
    assert np.isnan(r.loc["1010", "costo_unit_sol"])
    base = pd.DataFrame({"cod_prod": ["M2", "M10"], "costo": [20.0, 30.0], "stock_tiendas": [120, 0], "stock_cd": [10, 0]})
    e2 = pt.enriquecer_con_base(f, base, CFG).set_index("oc")
    assert e2.loc["1002", "costo_unit_sol"] == 20.0 and e2.loc["1002", "costo_fuente"] == "base"
    assert e2.loc["1010", "costo_unit_sol"] == 30.0 and e2.loc["1010", "costo_total_sol"] == 3600
    assert e2.loc["1002", "stock_tiendas"] == 120 and np.isnan(e2.loc["1003", "stock_tiendas"])


def test_base_desde_df_cob():
    df_cob = pd.DataFrame({"sku": ["M2", "M2", "M3"], "tienda": ["A", "B", "A"], "costo": [20, 20, 5],
                           "stock_uds": [100, 20, 7], "stock_cd": [10, 10, 0]})
    b = pt.base_desde_df_cob(df_cob).set_index("cod_prod")
    assert b.loc["M2", "stock_tiendas"] == 120 and b.loc["M2", "stock_cd"] == 10 and b.loc["M3", "costo"] == 5


# ── Resumen, semanas, atrasos ────────────────────────────────────────────────
def test_resumen_por_marca(res):
    r = res["resumen"].set_index("marca")
    assert list(r.index) == ["Marquis", "Navigata", "U.S. Polo Assn."]
    mq = r.loc["Marquis"]
    assert mq["und_compra_pv"] == 620 and mq["und_recibida_cd"] == 200 and mq["und_pendiente"] == 420
    assert mq["pct_recibido_und"] == pytest.approx(200 / 620)
    assert mq["pct_recibido_costo"] == pytest.approx(1000 / 2400)       # el factor no altera el %
    assert mq["costo_compra_pv"] == pytest.approx(2400 * 4.04)
    assert mq["und_atrasadas"] == 100 and mq["oc_atrasadas"] == 1 and mq["proxima_eta"] == date(2026, 10, 15)
    assert mq["semaforo"] == "rojo"                                      # 100/420 ≥ 10 %
    nv = r.loc["Navigata"]
    assert nv["und_compra_pv"] == 500 and nv["und_recibida_cd"] == 150 and nv["und_pendiente"] == 350
    assert nv["und_atrasadas"] == 0 and nv["semaforo"] == "verde" and nv["proxima_eta"] == date(2026, 10, 22)
    up = r.loc["U.S. Polo Assn."]
    assert up["und_pendiente"] == 80 and up["und_atrasadas"] == 80 and up["proxima_eta"] is None
    assert up["pct_recibido_und"] == 0 and np.isnan(up["und_en_tienda"])
    assert res["n_lineas_partidas"] == 1


def test_detalle_llegadas_texto(res):
    r = res["resumen"].set_index("marca")
    assert r.loc["Marquis", "detalle_llegadas"] == "Polos M/C 200 u 15/10; Camisas M/C 120 u 20/11; atrasado 100 u"
    assert r.loc["Navigata", "detalle_llegadas"] == "Camisas M/L 350 u 22/10"
    assert r.loc["U.S. Polo Assn.", "detalle_llegadas"] == "atrasado 80 u"


def test_resumen_por_linea_cuadra_con_marca(res):
    rl = res["resumen_linea"]
    assert set(rl.columns) >= {"marca", "linea", "und_compra_pv"}
    assert rl.groupby("marca")["und_compra_pv"].sum().to_dict() == {"Marquis": 620, "Navigata": 500, "U.S. Polo Assn.": 80}
    agg = pt.resumen_desde_lineas(rl).set_index("marca")
    assert agg.loc["Marquis", "pct_recibido_und"] == pytest.approx(200 / 620)


def test_pendiente_por_semana_ripley(res):
    ps = res["por_semana"]
    assert ps["uds"].sum() == 420 + 350 + 80
    d = ps.set_index(["marca", "semana"])["uds"].to_dict()
    assert d[("Marquis", "VENCIDA")] == 100 and d[("U.S. Polo Assn.", "SIN ETA")] == 80
    assert d[("Marquis", "W202635")] == 200          # 15/10 → semana Ripley que cierra 18/10
    assert d[("Navigata", "W202636")] == 350         # 22/10 → cierra 25/10
    assert d[("Marquis", "W202640")] == 120          # 20/11 → cierra 22/11
    assert list(ps["semana"][:2]) == ["VENCIDA", "SIN ETA"]
    assert ps.loc[ps["semana"] == "W202635", "cierre"].iloc[0] == pd.Timestamp("2026-10-18")


def test_semana_ripley_vectorizada_es_la_del_calendario():
    import calendario_ripley as cr
    f = pd.Series(pd.to_datetime(["2026-09-20", "2026-10-15", "2030-01-01", None]))
    s = pt.semana_ripley(f)
    assert s.iloc[0] == "W202631" == cr.info_fecha(date(2026, 9, 20))["semact"]
    assert s.iloc[1] == cr.info_fecha(date(2026, 10, 15))["semact"]
    assert s.iloc[2] == "" and s.iloc[3] == ""


def test_atrasos(res):
    a = res["atrasos"]
    assert set(a["oc"]) == {"1003", "1006"}
    assert a.set_index("oc").loc["1003", "dias_atraso"] == 14 and pd.isna(a.set_index("oc").loc["1006", "dias_atraso"])


def test_eta_vigente_manda_sobre_la_del_reporte(oc_todo):
    f = pt.filtrar(oc_todo, ["MARQUIS"], "HOMBRE", "PV")
    vig = pd.DataFrame({"oc": ["1003"], "eta": [pd.Timestamp("2026-10-20")], "fuente": ["manual"]})
    e = pt.aplicar_eta_vigente(f, vig)
    m = e.drop_duplicates("oc").set_index("oc")
    assert m.loc["1003", "eta"] == pd.Timestamp("2026-10-20") and m.loc["1003", "eta_fuente"] == "manual"
    assert m.loc["1002", "eta"] == pd.Timestamp("2026-10-15") and m.loc["1002", "eta_fuente"] == "comex"
    r = pt.resumen_marca(e, HOY, cfg=CFG).iloc[0]
    assert r["und_atrasadas"] == 0 and r["semaforo"] == "verde"       # con la ETA corregida ya no está atrasada


# ── Export (contrato fijo) ───────────────────────────────────────────────────
def test_export_esquema_exacto(res, tmp_path):
    ex = pt.export_formato(res["resumen"])
    assert list(ex.columns) == ["marca", "und_compra_pv", "und_recibida_cd", "und_en_tienda", "costo_compra_pv",
                                "costo_recibido_cd", "pct_recibido_und", "pct_recibido_costo", "und_pendiente",
                                "costo_pendiente", "proxima_eta", "und_atrasadas", "detalle_llegadas"]
    assert len(ex) == 3 and ex["pct_recibido_und"].between(0, 1).all()
    ruta = pt.escribir_export(res["resumen"], str(tmp_path), HOY, notas=res["notas"])
    assert os.path.basename(ruta) == "pv_transito_2026-10-04.xlsx"
    leido = pd.read_excel(ruta, sheet_name="pv_transito")
    assert list(leido.columns) == pt.EXPORT_COLS and leido["und_compra_pv"].sum() == 1200
    assert "notas" in pd.ExcelFile(ruta).sheet_names
    b = pt.export_bytes(res["resumen"])
    import io
    assert list(pd.read_excel(io.BytesIO(b), sheet_name="pv_transito").columns) == pt.EXPORT_COLS


def test_export_desde_resumen_por_linea_agrega(res):
    ex = pt.export_formato(res["resumen_linea"])
    assert list(ex.columns) == pt.EXPORT_COLS and len(ex) == 3
    assert ex.set_index("marca").loc["Marquis", "und_compra_pv"] == 620


# ── Fallback: hojas de Franco ────────────────────────────────────────────────
def test_leer_llegadas_franco():
    oc = pt.leer_llegadas(LLEGADAS, nombre="LLEGADAS RETRASOS COMEX 24.09.xlsx", cfg=CFG)
    assert set(oc["oc"]) == {"2001", "2002", "2003", "2004", "2005"}       # sin '(en blanco)' ni 'Total general'
    m = oc.set_index("oc")
    assert m.loc["2001", "ventana"] == "E" and m.loc["2004", "ventana"] == "F" and m.loc["2005", "ventana"] == "A"
    assert m.loc["2001", "temporada"] == "PV" and m.loc["2005", "temporada"] == "OI"
    assert m.loc["2002", "recibida"] and not m.loc["2003", "recibida"]
    assert pd.isna(m.loc["2003", "etd"]) and pd.isna(m.loc["2003", "eta_puerto"])     # los 0 → nulo
    assert m.loc["2003", "eta_cd"] == pd.Timestamp("2026-09-28")
    assert m.loc["2001", "uds"] == 800 and (oc["fuente"] == "llegadas").all()
    assert oc["fecha_reporte"].iloc[0] == pd.Timestamp("2026-09-24")
    assert oc["sku"].eq("").all() and oc["fob_usd"].isna().all()


def test_procesar_llegadas_sin_costo():
    r = pt.procesar(LLEGADAS, nombre="llegadas 24.09.xlsx", hoy=HOY, temporada="PV",
                    cfg={**CFG, "marcas_foco": ["SPAVALDI", "MARQUIS", "CACHAREL", "NAVIGATA"]})
    assert r["formato"] == "llegadas"
    res = r["resumen"].set_index("marca")
    assert res.loc["Spavaldi", "und_compra_pv"] == 2800 and res.loc["Spavaldi", "und_recibida_cd"] == 2000
    assert res.loc["Marquis", "und_atrasadas"] == 1100 and np.isnan(res.loc["Marquis", "costo_compra_pv"])
    assert "Navigata" not in res.index                                   # 2005 es OI
    assert any("% recibido no calculable" in n for n in r["notas"])
    ex = pt.export_formato(r["resumen"])
    assert list(ex.columns) == pt.EXPORT_COLS and ex["costo_compra_pv"].isna().all()
