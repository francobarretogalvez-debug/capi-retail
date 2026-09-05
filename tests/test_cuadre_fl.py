"""
test_cuadre_fl.py — Cuadre del MOTOR contra la hoja FL de Planificación.

Es la verificación que habilita todas las demás: si el motor no reproduce el
Excel partiendo de los mismos datos, cualquier diferencia posterior es un error
nuestro y no una mejora.

REGLA (2026-09-02, pedido de Franco tras encontrar el fallo): todo test que se
llame "cuadre" DEBE invocar el motor y comparar su salida contra la referencia
externa. La primera versión de este archivo verificaba que los números del
Excel fueran consistentes entre sí — cierto pero inútil: no probaba una sola
línea de `flujo_engine`. Un cuadre que no llama al motor no es un cuadre.

La hoja viene filtrada a OSCAR DE LA RENTA / POLOS M/C, así que el cuadre se
hace sobre esa combinación, que es la que el archivo trae calculada.

    python3 tests/test_cuadre_fl.py
"""

import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import flujo_engine as fe  # noqa: E402
import flujo_ingesta as fi  # noqa: E402

XLSM = os.path.expanduser("~/Downloads/Flujo por Línea OI26 EFA Gus v3.xlsm")
MARCA, LINEA = "OSCAR DE LA RENTA", "POLOS M/C"

# Valores que el Excel MUESTRA para ese corte (hoja FL, filas 17-28, bloque
# 2026). Son la referencia externa contra la que se compara la salida del motor.
# periodo -> (und, vta, con, vta_cto, stk_uu, stk_soles, mv)
FL_2026 = {
    "P202511": (2396, 239916.6, 83956.0, 155960.6, 8027, 533390.3, 7.0),
    "P202512": (1097, 105042.1, 33636.1,  71406.0, 6930, 461984.3, 6.3),
    "P202601": (1155, 116052.3, 39475.8,  76576.5, 7489, 505407.8, 5.5),
    "P202602": (1236, 120539.5, 38588.7,  81950.8, 6253, 423457.0, 5.2),
    "P202603": ( 924,  89548.0, 26855.9,  62692.2, 8614, 590764.8, 7.3),
    "P202604": (1934, 193029.0, 61788.7, 131240.3, 6680, 459524.6, 7.8),
    "P202605": ( 765,  69251.3, 17341.7,  51909.6, 5915, 407615.0, 5.8),
    "P202606": ( 892,  87512.3, 26262.8,  61249.6, 7594, 526365.4, 6.9),
    "P202607": ( 928,  88484.9, 24758.8,  63726.1, 6666, 462639.3, 4.1),
    "P202608": (1234, 119357.9, 34618.7,  84739.3, 5432, 377900.1, 2.8),
    "P202609": (1148, 120345.8, 40891.8,  79454.0, 6855, 478446.0, 3.6),
    "P202610": (2491, 266563.8, 93294.8, 173269.0, 8364, 585177.1, 5.8),
}

# El Excel usa `10 × (1 + Factor)` como piso de venta cuando un sub-flujo del
# corte no tiene historia LY. Eso inyecta hasta S/10 sintéticos por periodo:
# aparece en 6 de los 12 meses y suma S/59,9 sobre S/1,6M = 0,0037% del año.
# Es un artefacto del Excel, no del motor; se tolera y se documenta.
# Tolerancias FIJADAS SOBRE EL DESVÍO MEDIDO, no elegidas para que pase el
# test. Máximos observados en los 12 periodos de 2026 (ODLR / POLOS M/C):
#   unidades de venta  0.0    <- cuadra EXACTO en los 12 periodos
#   venta S/          10.0    <- exactamente S/10 en 6 periodos, 0 en los otros 6
#   contribución      18.2
#   venta a costo     21.0    <- vta_cto = vta - contrib: los errores se SUMAN
#   stock unidades     1.7    <- el Excel aplica INT()
#   stock S/          66.6    <- la identidad lo acumula periodo a periodo
# El desvío total de venta en el año es S/60 sobre S/1,6M = 0,0037%, y coincide
# con el término `10 × (1 + Factor)` que el Excel usa como piso cuando un
# sub-flujo del corte no tiene historia LY. Es un artefacto del Excel.
TOL_UNIDADES_VENTA = 1.0
TOL_SOLES = 15.0
TOL_CONTRIB = 25.0
TOL_VTA_CTO = 30.0
TOL_STOCK_UU = 2.0
TOL_STOCK_SOLES = 90.0
TOL_MV = 0.06
# Guarda de materialidad: por encima de esto el desvío deja de ser redondeo.
TOL_DESVIO_RELATIVO_ANUAL = 0.01  # %


def _serie_del_motor():
    df = fi.cargar_bd_fl(XLSM)
    s = fe.serie(df, marca=MARCA, linea=LINEA)
    return s[s["anio"] == 2026].set_index("periodo")


def test_motor_reproduce_venta_y_contribucion():
    """La salida del motor contra los números que muestra el Excel."""
    s = _serie_del_motor()
    faltan = [p for p in FL_2026 if p not in s.index]
    assert not faltan, f"el motor no produjo los periodos {faltan}"

    peor_v = peor_c = peor_u = peor_vc = 0.0
    for periodo, (und, vta, con, vta_cto, *_) in FL_2026.items():
        r = s.loc[periodo]
        peor_u = max(peor_u, abs(r["vta_uu"] - und))
        peor_v = max(peor_v, abs(r["vta_soles"] - vta))
        peor_c = max(peor_c, abs(r["contrib"] - con))
        peor_vc = max(peor_vc, abs(r["vta_costo_calc"] - vta_cto))
        assert abs(r["vta_uu"] - und) < TOL_UNIDADES_VENTA, f"{periodo}: unidades"
        assert abs(r["vta_soles"] - vta) < TOL_SOLES, f"{periodo}: venta"
        assert abs(r["contrib"] - con) < TOL_CONTRIB, f"{periodo}: contribución"
        assert abs(r["vta_costo_calc"] - vta_cto) < TOL_VTA_CTO, f"{periodo}: venta a costo"
    print(f"OK venta: 12 periodos | desvío máx {peor_u:.0f} uds, S/{peor_v:.1f} venta, "
          f"S/{peor_c:.1f} contribución, S/{peor_vc:.1f} venta a costo")


def test_motor_reproduce_el_stock():
    """El stock que arrastra el motor contra el cierre que muestra el Excel."""
    s = _serie_del_motor()
    peor_u = peor_s = 0.0
    for periodo, (*_, stk_uu, stk_soles, _) in FL_2026.items():
        r = s.loc[periodo]
        peor_u = max(peor_u, abs(r["stock_uu"] - stk_uu))
        peor_s = max(peor_s, abs(r["stock_soles"] - stk_soles))
        assert abs(r["stock_uu"] - stk_uu) < TOL_STOCK_UU, f"{periodo}: stock uds"
        assert abs(r["stock_soles"] - stk_soles) < TOL_STOCK_SOLES, f"{periodo}: stock S/"
    print(f"OK stock: desvío máx {peor_u:.1f} uds y S/{peor_s:.1f}")


def test_motor_verifica_identidad_sobre_el_corte():
    """`verificar_identidad` del motor, corriendo sobre los datos reales."""
    df = fi.cargar_bd_fl(XLSM)
    v = fe.verificar_identidad(fe.serie(df, marca=MARCA, linea=LINEA))
    v = v[v["periodo"] >= "P202505"]
    assert len(v) >= 12, f"solo {len(v)} periodos evaluables"
    peor = v["dif_pct"].abs().max()
    assert peor < 2.0, f"identidad fuera de tolerancia: {peor:.2f}%"
    print(f"OK identidad del motor: {len(v)} periodos, desvío máx {peor:.3f}%")


def test_motor_reproduce_el_mv_de_ripley():
    """El MV del Excel es stock ÷ promedio de 3 meses forward de venta a costo.

    Se reproduce con la salida del motor —no con los números del Excel— para
    probar que la serie del motor genera el mismo indicador.
    """
    s = _serie_del_motor()
    periodos = list(FL_2026)
    peor = 0.0
    for i in range(len(periodos) - 3):
        p = periodos[i]
        fwd = [s.loc[periodos[j], "vta_costo_calc"] for j in (i + 1, i + 2, i + 3)]
        mv_motor = s.loc[p, "stock_soles"] / np.mean(fwd)
        peor = max(peor, abs(mv_motor - FL_2026[p][6]))
        assert abs(mv_motor - FL_2026[p][6]) < TOL_MV, (
            f"{p}: MV motor {mv_motor:.2f} vs Excel {FL_2026[p][6]}")
    print(f"OK MV: desvío máx {peor:.3f}")


def test_desvio_anual_es_inmaterial():
    """Guarda de materialidad: el desvío acumulado no puede dejar de ser redondeo.

    Es más robusto que las tolerancias absolutas — si el motor se desalinea del
    Excel de verdad, esto lo detecta aunque cada periodo quede bajo su umbral.
    """
    s = _serie_del_motor()
    total_excel = sum(v[1] for v in FL_2026.values())
    total_motor = s.loc[list(FL_2026)]["vta_soles"].sum()
    desvio = abs(total_motor - total_excel) / total_excel * 100
    assert desvio < TOL_DESVIO_RELATIVO_ANUAL, (
        f"desvío anual {desvio:.4f}% supera {TOL_DESVIO_RELATIVO_ANUAL}%")
    print(f"OK materialidad: desvío anual {desvio:.4f}% "
          f"(S/{abs(total_motor - total_excel):.1f} sobre S/{total_excel:,.0f})")


def test_cobertura_por_consumo_vs_mv(verbose=True):
    """Mide el efecto de la corrección. No es cuadre: es la diferencia buscada."""
    s = _serie_del_motor()
    periodos = list(FL_2026)
    filas = []
    for i in range(len(periodos) - 4):
        p = periodos[i]
        fwd = [s.loc[q, "vta_costo_calc"] for q in periodos[i + 1:]]
        propio = fe.cobertura_consumo(s.loc[p, "stock_soles"], fwd)
        ripley = FL_2026[p][6]
        dif = (ripley / propio - 1) * 100 if np.isfinite(propio) and propio > 0 else np.nan
        filas.append((p, ripley, propio, dif))

    if verbose:
        print("  periodo     MV Ripley   consumo    dif")
        for p, r, c, d in filas:
            c_txt = f"{c:7.2f}" if np.isfinite(c) else "    inf"
            d_txt = f"{d:+6.1f}%" if np.isfinite(d) else "      —"
            print(f"  {p:<11s} {r:8.2f} {c_txt} {d_txt}")
    difs = [abs(d) for *_, d in filas if np.isfinite(d)]
    assert difs, "no se pudo comparar ningún periodo"
    print(f"OK comparación: dif mediana {np.median(difs):.1f}%, máx {max(difs):.1f}%")


def main():
    if not os.path.exists(XLSM):
        print(f"SKIP: no se encuentra {XLSM}")
        return 0
    for fn in (test_motor_reproduce_venta_y_contribucion,
               test_motor_reproduce_el_stock,
               test_motor_verifica_identidad_sobre_el_corte,
               test_motor_reproduce_el_mv_de_ripley,
               test_desvio_anual_es_inmaterial,
               test_cobertura_por_consumo_vs_mv):
        print(f"\n— {fn.__name__} —")
        fn()
    print("\nEl MOTOR reproduce la hoja FL. Las diferencias posteriores son "
          "correcciones, no errores de réplica.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
