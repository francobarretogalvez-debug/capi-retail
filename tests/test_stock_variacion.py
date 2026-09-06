"""S9 ingesta (2026-09-05): pivot de stock por variación × tienda → tabla larga + diagnóstico de mix."""
import pandas as pd

import stock_variacion as sv


def _pivot():
    # fila 8 = tiendas, fila 9 = métricas (formato del reporte Ripley), datos desde la 10
    mets = list(sv.METRICAS.keys())
    hdr_t = [None] * 7 + ["Jockey Plaza"] * 10 + ["San Miguel"] * 10
    hdr_m = ["CODMOD", "MODELO", "CODVAR", "VARIACION", "CODPROV", "PROVEEDOR", "RANGO"] + mets + mets
    def fila(cm, mod, cv, var, jp, sm):
        return [cm, mod, cv, var, 1, "PROV", "RANGO 0_3"] + jp + sm
    z = [0] * 10
    rows = [
        [None] * 27, [None] * 27, [None] * 27, [None] * 27, [None] * 27, [None] * 27,
        [None] * 27, hdr_t, hdr_m,
        # Venta S/, Venta Unid, Stock S/, Stock Unid, OH disp, OO, CD disp, CD OO, costo, uds asig
        fila("100", "POLO PIMA", "101", "POLO PIMA NAVY S.", [100, 4, 200, 10, 10, 0, 50, 0, 0, 0], z),
        fila("100", "POLO PIMA", "102", "POLO PIMA NAVY M.", [200, 8, 100, 5, 5, 2, 50, 0, 0, 0], [50, 2, 0, 0, 0, 0, 0, 0, 0, 0]),
        fila("100", "POLO PIMA", "103", "POLO PIMA NAVY L.", [0, 0, 400, 20, 20, 0, 50, 0, 0, 0], z),
        fila("100", "POLO PIMA", "104", "POLO PIMA NAVY XL.", [300, 12, 0, 0, 0, 0, 50, 0, 0, 0], z),   # quiebre XL
        fila("200", "CHOMPA X", "201", "CHOMPA X ROJO M.", z, z),                                       # sin actividad → fuera
    ]
    return pd.DataFrame(rows)


def test_leer_pivot_y_split(tmp_path):
    p = tmp_path / "piv.xlsx"
    _pivot().to_excel(p, header=False, index=False)
    df = sv.leer_xlsb(str(p))
    assert set(df["tienda"]) == {"Jockey Plaza", "San Miguel"}
    assert len(df) == 5 and "201" not in set(df["cod_variacion"])   # 4 en JP + 1 en SM con venta
    assert set(df["talla"]) == {"S", "M", "L", "XL"} and set(df["color"]) == {"NAVY"}
    xl = df[(df.talla == "XL") & (df.tienda == "Jockey Plaza")].iloc[0]
    assert xl.vta_uds_3s == 12 and xl.stock_uds == 0 and xl.cd_disp == 50


def test_mix_y_diagnostico(tmp_path):
    p = tmp_path / "piv.xlsx"
    _pivot().to_excel(p, header=False, index=False)
    df = sv.leer_xlsb(str(p))
    jp = df[df.tienda == "Jockey Plaza"]
    mix = sv.mix_por_eje(jp, "talla")
    d = dict(zip(mix["talla"], mix["diagnostico"]))
    assert d["XL"].startswith("🔴") and d["L"].startswith("🟡") and mix["talla"].tolist() == ["S", "M", "L", "XL"]
    cr = sv.curva_rota_por_tienda(jp)
    assert bool(cr.iloc[0]["curva_rota"]) and cr.iloc[0]["tallas_faltantes"] == 1
    dt = sv.diagnostico_tiendas(df)
    assert dt.set_index("tienda").loc["Jockey Plaza", "diagnostico"].startswith("📏")
    assert dt.set_index("tienda").loc["San Miguel", "diagnostico"].startswith("👁️") or dt.set_index("tienda").loc["San Miguel", "vta_uds_3s"] == 2
