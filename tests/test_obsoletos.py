"""S5 (2026-09-05): ranking de obsoletos por tienda, alerta 'por entrar a obsoleto' y costo implícito."""
import pandas as pd

import obsoletos


def _cob():
    return pd.DataFrame([
        # tienda A: obsoleto por rango 300 de 500 → 60%
        dict(tienda="A", marca="MARQUIS", sku="1", nombre="p1", categoria="L", stock_total=10, stock_valor_costo=300, rango_antiguedad="RANGO 9_12", edad_semanas=40, prom_vta_uds=0, precio_vigente=59, costo=30, pct_descuento=0.3, margen_efectivo=0.4, estado="OBSOLETO"),
        dict(tienda="A", marca="DOCKERS", sku="2", nombre="p2", categoria="L", stock_total=10, stock_valor_costo=200, rango_antiguedad="RANGO 3_6", edad_semanas=25, prom_vta_uds=0, precio_vigente=118, costo=20, pct_descuento=0.0, margen_efectivo=0.5, estado="DORMIDO"),
        # tienda B: nada obsoleto hoy, uno cruza en 3 semanas (edad 23) pero vende
        dict(tienda="B", marca="MARQUIS", sku="3", nombre="p3", categoria="L", stock_total=5, stock_valor_costo=100, rango_antiguedad="RANGO 3_6", edad_semanas=23, prom_vta_uds=2, precio_vigente=59, costo=30, pct_descuento=0.3, margen_efectivo=0.4, estado="OPTIMO"),
    ])


def test_ranking_por_tienda_dos_niveles():
    r = obsoletos.ranking_por_tienda(_cob(), definicion="rango", marcas_terceras={"DOCKERS"})
    assert r.iloc[0]["tienda"] == "A" and r.iloc[0]["capital_obsoleto"] == 300
    assert r.iloc[0]["capital_obsoleto_9m_mas"] == 300 and r.iloc[0]["capital_preobsoleto_6_9m"] == 0   # RANGO 9_12 → obsoleto
    assert abs(r.iloc[0]["pct_stock_tienda"] - 0.6) < 1e-9 and r.iloc[0]["marca_top"] == "MARQUIS"
    assert "B" not in set(r["tienda"])
    t = obsoletos.ranking_por_tienda(_cob(), definicion="taxonomia")
    assert t.iloc[0]["capital_obsoleto"] == 300


def test_por_entrar_dos_semanas_y_descuento():
    pe = obsoletos.por_entrar(_cob(), semanas=2, definicion="rango")
    assert pe["sku"].tolist() == ["2"]           # edad 25 cruza 26 en ≤2 sem; el de 23 no
    assert pe.iloc[0]["semanas_para_obsoleto"] == 1
    assert pe.iloc[0]["dscto_sugerido"] >= 0.30 and pe.iloc[0]["accion"].startswith("💰")
    pe3 = obsoletos.por_entrar(_cob(), semanas=4, definicion="rango")
    assert set(pe3["sku"]) == {"2", "3"}
    pe3t = obsoletos.por_entrar(_cob(), semanas=4, definicion="taxonomia")
    assert set(pe3t["sku"]) == {"2"}             # taxonomía exige sin venta
    assert obsoletos.por_entrar(_cob(), semanas=4, hacia="obsoleto").empty   # nadie está entre 35 y 39 sem
    res = obsoletos.resumen_por_entrar(pe3)
    assert res.iloc[0]["marca"] in {"DOCKERS", "MARQUIS"} and res["capital"].sum() == 300


def test_capital_implicito_solo_terceras():
    ci = obsoletos.capital_implicito(_cob(), marcas_terceras={"DOCKERS"})
    assert pd.isna(ci.iloc[0]) and abs(ci.iloc[1] - 10 * (118 / 1.18) * 0.5) < 1e-6
