"""Motor TT (S9): réplica de las fórmulas del Excel Pima de Franco + reglas seleccionables."""
import pandas as pd

import motor_tt


def _df():
    # Pima Atocongo (bloque real del Excel): cubicaje 300, peso BLANC 0.12, curva S1 M3 L3 XL2, divisor 6
    rows = [("BLANC", "S", 3, 5), ("BLANC", "M", 8, 10), ("BLANC", "L", 6, 12), ("BLANC", "XL", 11, 1)]
    return pd.DataFrame([dict(cod_modelo="1", modelo="POLO PIQUE PIMA CAH", cod_variacion=f"v{t}", variacion=f"POLO PIQUE PIMA CAH BLANC {t}",
                              color=c, talla=t, tienda="Atocongo", stock_uds=oh, vta_uds_3s=uu, on_order=0, cd_disp=44)
                         for c, t, oh, uu in rows])


def test_even_excel():
    assert motor_tt._even(5.0) == 6 and motor_tt._even(6.0) == 6 and motor_tt._even(0.4) == 2 and motor_tt._even(0) == 0


def test_replica_excel_pima_atocongo():
    cub = {"Atocongo": {"cubicaje": 300, "cob_objetivo_sem": 4}}
    rep = motor_tt.reposicion_tt(_df(), cub, peso_color={"BLANC": 0.12}, curva_talla={"S": 1, "M": 3, "L": 3, "XL": 2},
                                 divisor=6, regla="cubicaje")
    r = rep.set_index("talla")
    # SI = EVEN(ROUND(300 × 0.12 × curva/6)): S=EVEN(6)=6 · M=EVEN(18)=18 · L=18 · XL=EVEN(12)=12
    assert r.loc["S", "stock_ideal_cubicaje"] == 6 and r.loc["M", "stock_ideal_cubicaje"] == 18 and r.loc["XL", "stock_ideal_cubicaje"] == 12
    # Repo 2 = max(0, SI − OH): S=6−3=3 · M=18−8=10 · L=18−6=12 · XL=12−11=1
    assert r["repo_cubicaje"].to_dict() == {"S": 3, "M": 10, "L": 12, "XL": 1}
    # Repo 1 (velocidad): M: vel=round(10/3)=3 → ideal 12, adic 6 → 12−8+6=10 ; XL: vel=0 → 0−11+0 → 0
    assert r.loc["M", "repo_velocidad"] == 10 and r.loc["XL", "repo_velocidad"] == 0
    assert r["repo_final"].to_dict() == r["repo_cubicaje"].to_dict()
    rep_max = motor_tt.reposicion_tt(_df(), cub, peso_color={"BLANC": 0.12}, curva_talla={"S": 1, "M": 3, "L": 3, "XL": 2}, divisor=6, regla="max")
    assert rep_max.set_index("talla").loc["L", "repo_final"] == max(12, rep.set_index("talla").loc["L", "repo_velocidad"])


def test_pesos_y_mix():
    pc, ct = motor_tt.pesos_desde_venta(_df())
    assert pc == {"BLANC": 1.0} and ct["M"] == 3 and ct["S"] == 1 or ct["S"] >= 1
    mix = motor_tt.mix_ideal(_df(), "talla").set_index("talla")
    assert mix.loc["XL", "ajuste_uds"] < 0 and mix.loc["L", "ajuste_uds"] > 0   # XL: 11 en stock y vende 1; L: 6 en stock y vende 12
    cd = motor_tt.cobertura_cd(motor_tt.reposicion_tt(_df(), {"Atocongo": {"cubicaje": 300, "cob_objetivo_sem": 4}},
                                                      peso_color={"BLANC": 0.12}, curva_talla={"S": 1, "M": 3, "L": 3, "XL": 2}, divisor=6))
    assert (cd["faltante_compra"] == 0).all()   # CD tiene 44 por variación, alcanza
