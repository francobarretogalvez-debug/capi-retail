"""
test_flujo.py — Verificación del motor de flujo contra el archivo real de
Planificación. Ejecutable como script, igual que el resto de tests del repo:

    python3 tests/test_flujo.py

Cubre las verificaciones #2 y #4 del plan del módulo de planificación:
identidad contable, curva estacional y sesgo del promedio vs consumo acumulado.
"""

import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flujo_engine as fe  # noqa: E402
import flujo_ingesta as fi  # noqa: E402

XLSM = os.path.expanduser(
    "~/Downloads/Flujo por Línea OI26 EFA Gus v3.xlsm"
)

# La identidad es exacta desde mayo 2025 (P202505). Antes hay ruido de arranque
# de la serie: los desvíos de ene-abr 2025 se compensan entre sí (+86K netos
# sobre S/2,5M), lo que apunta a timing de imputación, no a error sistemático.
PERIODO_LIMPIO_DESDE = "P202505"


def test_identidad():
    df = fi.cargar_bd_fl(XLSM)
    fallos = []
    for marca in ["CACHAREL", "MARQUIS", "NAVIGATA", "US POLO", "OSCAR DE LA RENTA"]:
        v = fe.verificar_identidad(fe.serie(df, marca=marca))
        v = v[v["periodo"] >= PERIODO_LIMPIO_DESDE]
        peor = v["dif_pct"].abs().max()
        assert len(v) >= 12, f"{marca}: solo {len(v)} periodos limpios"
        if peor >= 2.0:
            fallos.append(f"{marca}: desvío máximo {peor:.2f}%")
        print(f"  {marca:20s} {len(v):>3} periodos | desvío máx {peor:.3f}%")
    assert not fallos, "identidad fuera de tolerancia: " + "; ".join(fallos)
    print("OK identidad: stock(t-1) + compra - venta_a_costo = stock(t)")


def test_curva_estacional():
    df = fi.cargar_bd_fl(XLSM)
    c = fe.curva_estacional(df, marca="CACHAREL")
    assert len(c) == 12, f"la curva debe tener 12 periodos, tiene {len(c)}"
    assert abs(c["indice_p50"].sum() - 12.0) < 0.01, "los índices deben sumar 12"
    assert (c["anios"] >= 6).all(), "cada periodo necesita al menos 6 años de base"
    assert (c["indice_p25"] <= c["indice_p50"] + 1e-9).all(), "p25 debe ser <= p50"
    assert (c["indice_p75"] >= c["indice_p50"] - 1e-9).all(), "p75 debe ser >= p50"

    # Diciembre (periodo 10) es el pico de la categoría; octubre (8) el valle.
    dic = c.loc[c["periodo_num"] == 10, "indice_p50"].iloc[0]
    oct_ = c.loc[c["periodo_num"] == 8, "indice_p50"].iloc[0]
    assert dic > 1.4, f"diciembre debería ser pico, dio {dic:.2f}"
    assert oct_ < 0.85, f"octubre debería ser valle, dio {oct_:.2f}"
    print(f"OK curva: dic={dic:.2f} oct={oct_:.2f}, suma={c['indice_p50'].sum():.2f}")


def test_cobertura_consumo_vs_promedio():
    # Con demanda plana los dos métodos coinciden: es la condición de validez
    # de la fórmula que usa hoy Planificación.
    plano = [100, 100, 100, 100]
    assert abs(fe.cobertura_consumo(300, plano) - 3.0) < 1e-9
    assert abs(fe.stock_objetivo(plano, 3.0) - 300) < 1e-9

    # Con un pico adelante, el promedio SOBREESTIMA la cobertura: hace creer
    # que alcanza justo cuando entra el evento.
    pico = [250, 60, 50, 50]
    real = fe.cobertura_consumo(300, pico)
    prom = 300 / np.mean(pico[:3])
    assert prom > real, "el promedio debería sobreestimar con un pico adelante"
    assert abs(real - 1.833) < 0.01, f"cobertura real esperada ~1.83, dio {real:.3f}"
    print(f"OK cobertura: con pico adelante promedio={prom:.2f} vs real={real:.2f} "
          f"({(prom/real-1)*100:+.0f}%)")

    # Objetivo = ventana: coinciden por aritmética (lo verificado con Cacharel).
    assert abs(fe.stock_objetivo(pico, 3.0) - np.mean(pico[:3]) * 3) < 1e-6


def test_compra_requerida():
    f = [1000, 800, 600, 500]
    r = fe.compra_requerida(stock_inicial=500, venta_periodo=1200,
                            forecast_forward=f, cobertura_objetivo=2.0)
    # cierre objetivo = 1000 + 800 = 1800; compra = 1200 + 1800 - 500
    assert abs(r["cierre_objetivo"] - 1800) < 1e-9
    assert abs(r["compra_requerida"] - 2500) < 1e-9
    # El déficit no se trunca: vender 1200 con 500 de stock deja -700.
    assert abs(r["deficit"] + 700) < 1e-9, "el déficit debe quedar visible"
    assert r["cierre_sin_compra_truncado"] == 0.0, "la serie truncada replica a Ripley"

    # OTB negativo no se trunca: su magnitud es lo que hay que cancelar.
    o = fe.otb(500, 1200, f, 2.0, oc_comprometida=3000)
    assert o["otb"] < 0, "con OC de sobra el OTB debe salir negativo"
    print(f"OK compra requerida: déficit={r['deficit']:.0f}, "
          f"OTB sobrecomprometido={o['otb']:.0f}")


def test_regresion_auditoria():
    """Bugs encontrados en la auditoría del 2026-09-02. No deben volver."""
    # B1a — al agotarse el stock EXACTO al final del horizonte devolvía inf,
    # o sea reportaba sobrestock máximo en algo perfectamente calibrado. Es el
    # error que dispara markdown donde no corresponde.
    assert fe.cobertura_consumo(300, [100, 100, 100]) == 3.0
    assert fe.cobertura_consumo(250, [100, 100, 100]) == 2.5
    assert fe.cobertura_consumo(400, [100, 100, 100]) == np.inf  # sí sobrevive

    # B1b — un mes con forecast cero también está cubierto: transcurre aunque
    # no consuma stock. Saltarlo subestimaba la cobertura.
    assert fe.cobertura_consumo(300, [0, 100, 100, 100]) == 4.0
    assert fe.cobertura_consumo(100, [0, 0, 100]) == 3.0

    # B2 — con un hueco en la serie, la apertura viene de un periodo que no es
    # t-1; evaluar la identidad ahí da un falso desvío.
    d = pd.DataFrame({
        "division": ["H"] * 3, "departamento": ["D"] * 3, "marca": ["X"] * 3,
        "linea": ["L"] * 3, "programa": ["---"] * 3, "anio": [2026] * 3,
        "periodo": ["P202601", "P202603", "P202604"],
        "mes_num": [3, 5, 6], "periodo_num": [1, 3, 4],
        "vta_uu": [100] * 3, "vta_soles": [1000] * 3, "contrib": [500] * 3,
        "vta_costo_calc": [500] * 3, "compra_uu": [0] * 3, "compra_soles": [0] * 3,
        "stock_uu": [900, 700, 600], "stock_soles": [9000, 7000, 6000],
    })
    s = fe.serie(d, marca="X")
    assert list(s["periodo_contiguo"]) == [False, False, True]
    assert fe.verificar_identidad(s)["periodo"].tolist() == ["P202604"]

    # B4 — con año base parcial se dividía por 12 igual, subestimando el nivel.
    curva = pd.DataFrame({"periodo_num": range(1, 13), "indice_p50": [1.0] * 12,
                          "indice_p25": [1.0] * 12, "indice_p75": [1.0] * 12})
    hist = pd.DataFrame({"anio": [2026] * 3, "periodo_num": [1, 2, 3],
                         "vta_uu": [1000, 1000, 1000]})
    pr = fe.proyectar(hist, curva, [4], factor=0.0, base_anio=2026)
    assert abs(pr["proy_p50"].iloc[0] - 1000) < 1e-6, "año base parcial mal anualizado"

    # B5 — el exceso de stock sobre el objetivo no se reportaba: la compra se
    # truncaba a cero y el sobrestock quedaba invisible.
    r = fe.compra_requerida(100_000, 1000, [1000, 1000, 1000], 2.0)
    assert r["compra_requerida"] == 0
    assert abs(r["exceso_sobre_objetivo"] - 97_000) < 1e-6
    print("OK regresión: B1a, B1b, B2, B4 y B5 cubiertos")


def test_pace_y_banda():
    """El pace mide ritmo contra la curva, y su banda debe ESTRECHARSE."""
    df = fi.cargar_bd_fl(XLSM, validar=False)
    serie = fe.serie(df, marca="CACHAREL")
    curva = fe.curva_estacional(df, marca="CACHAREL")

    # Con el año completo, la proyección anual debe ser la venta real del año.
    p = fe.pace(serie, curva, 2026)
    real_2026 = serie[serie["anio"] == 2026]["vta_uu"].sum()
    assert abs(p["proyeccion_anual"] - real_2026) < 1, (
        f"proyección {p['proyeccion_anual']:.0f} vs real {real_2026:.0f}")
    assert abs(p["fraccion_de_anio_esperada"] - 1.0) < 1e-9, (
        "con el año completo la fracción esperada debe ser 1.0")

    # La banda se estrecha: al cerrar el año no queda incertidumbre. Sumar los
    # percentiles de cada mes daba lo contrario — se ensanchaba — porque
    # suponía que los 12 meses caían a la vez en su percentil bajo.
    ancho_medio = (fe.pace(serie, curva, 2026, hasta_periodo_num=5)["pace_banda_alta"]
                   - fe.pace(serie, curva, 2026, hasta_periodo_num=5)["pace_banda_baja"])
    ancho_final = (fe.pace(serie, curva, 2026, hasta_periodo_num=12)["pace_banda_alta"]
                   - fe.pace(serie, curva, 2026, hasta_periodo_num=12)["pace_banda_baja"])
    assert ancho_final < ancho_medio, (
        f"la banda debe estrecharse: mes 5 {ancho_medio:.3f} vs mes 12 {ancho_final:.3f}")
    assert ancho_final < 0.01, "al cerrar el año la banda debe ser ~0"
    print(f"OK pace: {p['pace']:.3f} | banda mes 5 {ancho_medio:.3f} → mes 12 {ancho_final:.3f}")


def test_otb_que_se_abre():
    """El OTB incremental es la diferencia entre el ritmo real y el plan."""
    df = fi.cargar_bd_fl(XLSM, validar=False)
    serie = fe.serie(df, marca="CACHAREL")
    curva = fe.curva_estacional(df, marca="CACHAREL")

    r = fe.otb_que_se_abre(serie, curva, 2026, factor_plan=0.15, hasta_periodo_num=6)
    assert r["sobrecumplimiento"] == r["factor_real"] - r["factor_plan"]
    # Cacharel 2026 viene por debajo de 2025, así que el OTB se CIERRA: el
    # signo negativo es información de gestión (cancelar o diferir), no un error.
    assert r["otb_que_se_abre"] < 0, "Cacharel 2026 va bajo plan: el OTB debe cerrarse"

    # Un plan más exigente cierra más OTB; uno más laxo, menos. Monótono.
    r_bajo = fe.otb_que_se_abre(serie, curva, 2026, factor_plan=0.0, hasta_periodo_num=6)
    assert r_bajo["otb_que_se_abre"] > r["otb_que_se_abre"]
    print(f"OK OTB incremental: factor real {r['factor_real']:+.1%} vs plan "
          f"+15.0% → {r['otb_que_se_abre']:+,.0f} uds")


def main():
    if not os.path.exists(XLSM):
        print(f"SKIP: no se encuentra {XLSM}")
        return 0
    for fn in (test_identidad, test_curva_estacional,
               test_cobertura_consumo_vs_promedio, test_compra_requerida,
               test_regresion_auditoria, test_pace_y_banda,
               test_otb_que_se_abre):
        print(f"\n— {fn.__name__} —")
        fn()
    print("\nTodos los tests de flujo pasaron.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
