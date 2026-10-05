"""Historial de ETA (append-only) y maestro de OC del módulo 🚢 PV en Tránsito.

Todo en tmp_path vía CAPI_SNAPSHOTS_DIR y sin NOTION_TOKEN: nada toca Notion ni el repo."""
import os
from datetime import date

import pandas as pd
import pytest

import eta_store as es
import pv_transito as pt

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
DETALLE = os.path.join(FIX, "detalle_comex_mini.xlsx")
LLEGADAS = os.path.join(FIX, "llegadas_franco_mini.xlsx")


@pytest.fixture(autouse=True)
def _aislar(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPI_SNAPSHOTS_DIR", str(tmp_path))
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    yield


def test_rutas_bajo_snapshots_dir(tmp_path):
    assert es.ruta_historial().startswith(str(tmp_path)) and es.ruta_historial().endswith("pv_transito/eta_historial.parquet")
    assert es.cargar_historial().empty and list(es.cargar_historial().columns) == es.COLS_HIST
    assert es.vigente(es.cargar_historial()).empty


def test_registrar_es_append_y_vigente_es_la_ultima():
    es.registrar("1003", date(2026, 9, 20), "comex", registrado_en=date(2026, 9, 10), marca="MARQUIS", modelo="PANT TRES")
    es.registrar("1003", date(2026, 10, 20), "manual", comentario="proveedor confirma 20/10", registrado_en=date(2026, 10, 4), usuario="franco")
    es.registrar("1002", date(2026, 10, 15), "comex", registrado_en=date(2026, 9, 10))
    h = es.cargar_historial()
    assert len(h) == 3 and (h["notion_url"] == "").all()                    # sin token: nada se subió
    assert h["semana_ripley"].iloc[0] == "W202630"                          # 10/09/2026 → semana Ripley 30
    v = es.vigente(h).set_index("oc")
    assert v.loc["1003", "eta"] == pd.Timestamp("2026-10-20") and v.loc["1003", "fuente"] == "manual"
    assert v.loc["1003", "n_cambios"] == 2 and v.loc["1003", "dias_corrimiento"] == 30
    assert v.loc["1003", "comentario"] == "proveedor confirma 20/10"
    assert v.loc["1002", "n_cambios"] == 1 and v.loc["1002", "dias_corrimiento"] == 0


def test_fuente_invalida():
    with pytest.raises(ValueError):
        es.registrar("1", date(2026, 1, 1), "excel")


def test_registrar_lote_solo_si_cambia():
    es.registrar("1002", date(2026, 10, 15), "comex", registrado_en=date(2026, 9, 10))
    cambios = pd.DataFrame({"oc": ["1002", "1004"], "eta": [pd.Timestamp("2026-10-15"), pd.Timestamp("2026-10-22")]})
    assert es.registrar_lote(cambios, "comex", registrado_en=date(2026, 9, 17)) == 1      # 1002 no cambió
    cambios2 = pd.DataFrame({"oc": ["1002", "1004"], "eta": [pd.Timestamp("2026-10-18"), None]})
    assert es.registrar_lote(cambios2, "proveedor", registrado_en=date(2026, 9, 24)) == 2  # 1002 corre, 1004 pierde ETA
    v = es.vigente(es.cargar_historial()).set_index("oc")
    assert v.loc["1002", "eta"] == pd.Timestamp("2026-10-18") and pd.isna(v.loc["1004", "eta"])
    assert es.registrar_lote(cambios2, "proveedor", registrado_en=date(2026, 10, 1)) == 0  # ya vigentes


def test_sincronizar_desde_reporte_arma_el_historial_solo():
    oc = pt.leer_detalle(DETALLE, fecha_reporte=date(2026, 9, 24))
    n = es.sincronizar_desde_reporte(oc, fecha_reporte=date(2026, 9, 24))
    assert n == 10                                              # 10 OC distintas (12 filas), incluida 1006 sin ETA
    h = es.cargar_historial()
    assert (h["fuente"] == "comex").all() and (h["registrado_en"] == pd.Timestamp("2026-09-24")).all()
    assert h.set_index("oc").loc["1001", "marca"] == "MARQUIS"
    assert es.sincronizar_desde_reporte(oc, fecha_reporte=date(2026, 10, 1)) == 0           # mismo corte: nada nuevo
    oc2 = oc.copy()
    oc2.loc[oc2["oc"] == "1002", "eta_cd"] = pd.Timestamp("2026-10-29")                    # comex corrió la OC
    assert es.sincronizar_desde_reporte(oc2, fecha_reporte=date(2026, 10, 1)) == 1
    v = es.vigente(es.cargar_historial()).set_index("oc")
    assert v.loc["1002", "n_cambios"] == 2 and v.loc["1002", "dias_corrimiento"] == 14
    # la ETA vigente manda sobre la del reporte viejo
    e = pt.aplicar_eta_vigente(oc, v.reset_index()).drop_duplicates("oc").set_index("oc")
    assert e.loc["1002", "eta"] == pd.Timestamp("2026-10-29") and e.loc["1002", "eta_fuente"] == "comex"


def test_maestro_infiere_recibidas_cuando_la_oc_desaparece():
    oc = pt.leer_llegadas(LLEGADAS, nombre="llegadas 10.09.xlsx")
    m = es.actualizar_maestro(oc, date(2026, 9, 10), "llegadas")
    assert len(m) == 5 and not m["recibida_inferida"].any()
    oc2 = oc[oc["oc"] != "2002"]                                 # 2002 ya no viene: la recibieron
    m2 = es.actualizar_maestro(oc2, date(2026, 9, 17), "llegadas").set_index("oc")
    assert m2.loc["2002", "recibida_inferida"] and m2.loc["2002", "semana_recibida"] == "W202631"
    assert not m2.loc["2001", "recibida_inferida"] and m2.loc["2001", "ultima_vez_vista"] == pd.Timestamp("2026-09-17")
    assert m2.loc["2002", "ultima_vez_vista"] == pd.Timestamp("2026-09-10")
    # si vuelve a aparecer, deja de estar inferida
    m3 = es.actualizar_maestro(oc, date(2026, 9, 24), "llegadas").set_index("oc")
    assert not m3.loc["2002", "recibida_inferida"]
    # y el motor la cuenta como recibida al cruzar el maestro
    oc3 = oc2.merge(m2.reset_index()[["oc", "recibida_inferida"]], on="oc", how="left")
    oc3 = pt.marcar_recibida(pd.concat([oc3, oc[oc["oc"] == "2002"].assign(recibida_inferida=True)]))
    assert oc3.set_index("oc").loc["2002", "recibida"]


def test_cargar_masivo_excel(tmp_path):
    p = tmp_path / "etas_proveedor.xlsx"
    pd.DataFrame({"N° OC": [1002, 1003, None, 1004], "Nueva ETA": ["29/10/2026", 46305, "x", None],
                  "Comentario": ["naviera", "", "", ""]}).to_excel(p, index=False)
    df = es.cargar_masivo_excel(str(p))
    assert list(df["oc"]) == ["1002", "1003"] and df.attrs["descartadas"] == 1     # la fila sin OC se bota antes; la sin ETA se cuenta
    assert df["eta"].tolist() == [pd.Timestamp("2026-10-29"), pd.Timestamp("2026-10-10")]
    assert df["comentario"].tolist() == ["naviera", ""]
    assert es.registrar_lote(df, "proveedor", registrado_en=date(2026, 10, 4)) == 2


def test_restaurar_sin_token_no_hace_nada():
    assert es.restaurar_desde_notion() == 0
    import notion_store
    assert notion_store.registrar_eta("1", "2026-10-10", "manual")["ok"] is False
    assert notion_store.listar_eta() == []
