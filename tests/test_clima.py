"""Regresión de los filtros de empuje: clima (GRUESO no va a calor), margen destino ≥25%,
descuento destino <40% (regla Majo) y mal match → destino final. 2026-08-25."""
import pytest

from conftest import base_local, requiere_archivo

from afinidad_engine import _load_calorico, _load_tiendas_calor


def test_configs_clima():
    hot = _load_tiendas_calor()
    assert {"IQT", "PUCALPA I", "PIU2", "CHIC", "CHII"} <= hot, hot
    cal = _load_calorico()
    assert all(cal.get(l) == "GRUESO" for l in
               ["CASACAS", "CHOMPAS", "POLERONES", "BLAZERS", "CHAQUETAS"]), cal


@pytest.fixture(scope="module")
def afinidad_23_08():
    base = requiere_archivo(base_local("Base al 23.08.xlsx"), "Base al 23.08.xlsx")
    from afinidad_engine import build_afinidad
    return base, build_afinidad(base)


def test_grueso_no_va_a_calor(afinidad_23_08):
    _, r = afinidad_23_08
    hot, cal = _load_tiendas_calor(), _load_calorico()
    for nombre, df, col in [("empujes", r["empujes_df"], "tienda"),
                            ("redistribución", r["redistribucion_df"], "tienda_destino")]:
        if df.empty or "linea" not in df.columns:
            continue
        viol = df[df["linea"].map(lambda l: cal.get(l, "NEUTRO") == "GRUESO") & df[col].isin(hot)]
        assert viol.empty, f"{nombre}: {len(viol)} GRUESO→calor"


def test_margen_y_dscto_destino(afinidad_23_08):
    _, r = afinidad_23_08
    emp = r["empujes_df"]
    if "margen_destino_pct" in emp.columns:
        _con = emp[emp["margen_destino_pct"].notna()]
        assert (_con["margen_destino_pct"] >= 25).all(), _con["margen_destino_pct"].min()
    if "dscto_destino_pct" in emp.columns:
        _cd = emp[emp["dscto_destino_pct"].notna()]
        assert (_cd["dscto_destino_pct"] < 40).all(), _cd["dscto_destino_pct"].max()


def test_mal_match_destino_final(afinidad_23_08, tmp_path):
    base, r = afinidad_23_08
    import motor_v2 as _mv
    import transformar_profundidad as _etl
    from afinidad_engine import mal_match_destino
    pl = str(tmp_path / "pl_tc.xlsx")
    _etl.transform(base, output_path=pl)
    _rm = _mv.run_analysis(pl)
    _res = mal_match_destino(r["anomalias_df"], _rm["transferencias"], _rm["cobertura"])
    _cu, _hu = _res["cubiertos"], _res["huerfanos"]
    assert not _hu.empty and _hu["capital_parado"].sum() > 1_000_000
    assert set(_cu["sku"]).isdisjoint(set(_hu["sku"]))
    _jov = _hu[_hu["edad_semanas"] < 8]
    assert (_jov["accion"].str.contains("Revisar")).all()
    _vie = _hu[_hu["edad_semanas"] >= 8]
    assert (_vie["accion"].str.contains("Liquidar")).all()
