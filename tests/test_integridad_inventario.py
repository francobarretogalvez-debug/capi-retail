"""
test_integridad_inventario.py — La revisión crítica que debe correr siempre.

Franco, 2026-09-02: "si algún inventario de cualquier mes no cuadra, todo en
adelante tendría error".

Es correcto, con un matiz que define el diseño:

  HISTÓRICO   cada periodo trae su stock MEDIDO, así que un mes roto es un
              error local — señala captura mal imputada ese mes (típicamente
              compras fuera de fecha), y no invalida un mes posterior que sí
              cuadra.
  PROYECCIÓN  el stock se calcula encadenado, así que el error SÍ se arrastra
              a todo el horizonte. Y ahí vive el OTB.

Por eso la guarda no bloquea "para siempre" hacia adelante: mira una ventana
móvil para decidir si el problema de captura sigue vigente, y bloquea la
proyección desde cualquier ancla que no cuadre.

Este test corre sobre TODAS las series, no sobre la marca que uno mira.

    python3 tests/test_integridad_inventario.py
"""

import os

import pytest
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

import flujo_engine as fe  # noqa: E402
import flujo_ingesta as fi  # noqa: E402

XLSM = os.path.expanduser("~/Downloads/Flujo por Línea OI26 EFA Gus v3.xlsm")
if not os.path.exists(XLSM):
    XLSM = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data2", "_flujo_planificacion.xlsm")
pytestmark = pytest.mark.skipif(not os.path.exists(XLSM),
                                reason="sin el Excel de Planificación (data local de Ripley, no va al repo)")

# El año vigente es el que alimenta las decisiones de compra. Los años viejos
# tienen captura degradada conocida (2023: 41% de periodos rotos, 2024: 35%,
# 2025: 25%) y no se usan como ancla, solo para la forma de la curva.
ANIO_VIGENTE = 2026
MAX_PCT_ROTOS_ANIO_VIGENTE = 2.0
MAX_SERIES_NO_APTAS_PCT = 10.0


def _df():
    return fi.cargar_bd_fl(XLSM)


def test_anio_vigente_cuadra():
    """El año que alimenta las decisiones no puede tener inventario descuadrado."""
    df = _df()
    total = rotos = 0
    detalle = []
    for clave, g in df.groupby(["marca", "linea", "programa"]):
        m = fe.marcar_confiabilidad(fe.serie(g))
        m = m[(m["anio"] == ANIO_VIGENTE) & m["dif_identidad_pct"].notna()]
        if m.empty:
            continue
        total += len(m)
        malos = m[~m["identidad_ok"]]
        rotos += len(malos)
        for _, r in malos.iterrows():
            detalle.append(f"{clave[0]}/{clave[1]} {r['periodo']} "
                           f"({r['dif_identidad_pct']:.1f}%)")

    pct = rotos / total * 100 if total else 0
    print(f"  {ANIO_VIGENTE}: {total} periodos evaluados, {rotos} rotos ({pct:.2f}%)")
    for d in detalle[:6]:
        print(f"    → {d}")
    assert pct <= MAX_PCT_ROTOS_ANIO_VIGENTE, (
        f"{pct:.1f}% de los periodos de {ANIO_VIGENTE} no cuadran "
        f"(máximo {MAX_PCT_ROTOS_ANIO_VIGENTE}%). Revisar la imputación de "
        f"compras antes de usar cualquier OTB: " + "; ".join(detalle[:5]))
    print(f"OK año vigente: {pct:.2f}% de periodos rotos")


def test_la_mayoria_de_series_es_apta_para_proyectar():
    """Si casi ninguna serie sirve de ancla, la guarda o la fuente están mal."""
    a = fe.auditar_integridad(_df())
    assert len(a) > 0, "la auditoría no devolvió series"
    no_aptas = a[~a["apta_para_proyectar"]]
    pct = len(no_aptas) / len(a) * 100
    print(f"  series: {len(a)} | aptas: {a['apta_para_proyectar'].sum()} | "
          f"no aptas: {len(no_aptas)} ({pct:.1f}%)")
    for _, r in no_aptas.head(5).iterrows():
        print(f"    → {r['marca']}/{r['linea']}: {r['periodos_rotos']} rotos "
              f"({r['pct_rotos']}%), S/{r['soles_desviados']:,.0f}")
    assert pct <= MAX_SERIES_NO_APTAS_PCT, (
        f"{pct:.1f}% de las series no sirven como ancla. O la fuente se "
        f"degradó, o la guarda quedó demasiado estricta y nadie la va a usar.")
    print(f"OK anclas: {100 - pct:.1f}% de las series son aptas")


def test_la_guarda_bloquea_anclas_rotas():
    """La guarda tiene que fallar ruidosamente, no devolver un número dudoso."""
    df = _df()
    s = fe.serie(df, marca="CACHAREL")
    m = fe.marcar_confiabilidad(s)

    roto = m[~m["identidad_ok"] & m["dif_identidad_pct"].notna()]
    assert len(roto), "no hay periodo roto en Cacharel para probar la guarda"
    periodo_roto = roto.iloc[0]["periodo"]
    try:
        fe.validar_ancla(s, periodo_roto)
        raise AssertionError(f"la guarda NO bloqueó el periodo roto {periodo_roto}")
    except fe.SerieNoConfiable:
        pass

    bueno = m[m["ancla_ok"] & m["dif_identidad_pct"].notna()]
    assert len(bueno), "no hay ningún ancla válida en Cacharel"
    fe.validar_ancla(s, bueno.iloc[-1]["periodo"])  # no debe levantar
    print(f"OK guarda: bloquea {periodo_roto}, acepta {bueno.iloc[-1]['periodo']}")


def test_proyectar_rechaza_anio_base_roto():
    """`proyectar` valida por defecto: la guarda no puede ser opt-in."""
    df = _df()
    s = fe.serie(df, marca="CACHAREL")
    c = fe.curva_estacional(df, marca="CACHAREL")

    try:
        fe.proyectar(s, c, [11], base_anio=2023)
        raise AssertionError("proyectar aceptó un año base con la identidad rota")
    except fe.SerieNoConfiable:
        pass

    pr = fe.proyectar(s, c, [11], base_anio=ANIO_VIGENTE)
    assert len(pr) == 1 and pr["proy_p50"].iloc[0] > 0
    # El escape existe pero es explícito y queda en el código de quien lo pide.
    assert len(fe.proyectar(s, c, [11], base_anio=2023, validar=False)) == 1
    print("OK proyectar: rechaza año base roto, acepta el vigente")


def test_la_propagacion_solo_aplica_hacia_adelante():
    """Un error viejo no invalida un ancla actual que cuadra al 0,00%.

    Es el bug que tuvo la primera versión de la guarda: propagaba la
    contaminación sin límite y bloqueaba 99 de 102 series, incluidos periodos
    con desvío exactamente cero. Una guarda que bloquea todo se apaga.
    """
    df = _df()
    m = fe.marcar_confiabilidad(fe.serie(df, marca="CACHAREL"))
    ultimo = m.iloc[-1]
    assert ultimo["identidad_ok"], "el último periodo de Cacharel debería cuadrar"
    assert ultimo["ancla_ok"], (
        "el último periodo cuadra pero la guarda lo marca contaminado: "
        "la propagación volvió a ser ilimitada")
    print(f"OK propagación acotada: {ultimo['periodo']} es ancla válida "
          f"pese a errores en 2025")


def main():
    if not os.path.exists(XLSM):
        print(f"SKIP: no se encuentra {XLSM}")
        return 0
    for fn in (test_anio_vigente_cuadra,
               test_la_mayoria_de_series_es_apta_para_proyectar,
               test_la_guarda_bloquea_anclas_rotas,
               test_proyectar_rechaza_anio_base_roto,
               test_la_propagacion_solo_aplica_hacia_adelante):
        print(f"\n— {fn.__name__} —")
        fn()
    print("\nIntegridad del inventario verificada sobre todas las series.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
