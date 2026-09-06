"""Regresión del motor de conclusiones (analisis_estados) y del calendario Ripley sobre snapshots reales."""
import os

import pytest

from conftest import SNAPSHOTS_DIR

import analisis_estados
import calendario_ripley

pytestmark = pytest.mark.skipif(
    not os.path.exists(os.path.join(SNAPSHOTS_DIR, "2026-33")),
    reason="faltan snapshots de referencia 2026-31..33")


def test_conclusiones_32_33():
    c = analisis_estados.conclusiones("2026-32", "2026-33")
    titulos = " | ".join(x["titulo"] for x in c)
    assert any("ESTANCADO" in t["titulo"] for t in c), titulos
    assert any("Capital en exceso" in t["titulo"] for t in c), titulos
    assert all(t["nivel"] in ("positivo", "atencion", "critico", "info") for t in c)


def test_conclusiones_31_32_y_sin_datos():
    c2 = analisis_estados.conclusiones("2026-31", "2026-32")
    # con inmovilizado = DORMIDO+ESTANCADO+LIQUIDAR+MUERTO (06-sep) la mejora 31→32 la explica el sobrestock
    assert any(t["nivel"] == "positivo" and ("exceso" in t["titulo"].lower() or "sobrestock" in t["titulo"].lower()) for t in c2)
    c3 = analisis_estados.conclusiones("2026-99", "2026-98")
    assert c3 and c3[0]["titulo"] == "Sin datos suficientes"


def test_migraciones():
    m = analisis_estados.matriz_migraciones("2026-32", "2026-33")
    assert not m.empty and set(m["clase"]) <= {"mejora", "deterioro", "lateral", "relanzamiento"}
    rel = m[(m["estado_a"] == "DORMIDO") & (m["estado_b"] == "NUEVO SIN VENTA")]
    assert rel.empty or (rel["clase"] == "relanzamiento").all(), "edad reseteada no puede ser mejora"
    assert (m["capital"] >= 0).all()
    s = analisis_estados.serie_migraciones()
    assert len(s) >= 9 and {"capital_mejora", "capital_deterioro", "neto"} <= set(s.columns)
    d = analisis_estados.detalle_migracion("2026-32", "2026-33",
                                           m.iloc[0]["estado_a"], m.iloc[0]["estado_b"])
    assert not d.empty and "stock_valor_costo" in d.columns


def test_calendario_ripley():
    i = calendario_ripley.info_fecha("2026-08-23")
    assert i and i["semact"] == "W202627" and int(i["sem_num"]) == 27, i
    assert calendario_ripley.info_fecha("2026-08-16")["semact"] == "W202626"
    assert calendario_ripley.info_fecha("2015-01-01") == {}
    et = analisis_estados.etiqueta_semana("2026-34")
    assert "Sem 27 Ripley" in et and "23.08" in et and "Vent. C" in et, et
