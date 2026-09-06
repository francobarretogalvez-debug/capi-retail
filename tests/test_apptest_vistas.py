"""Smoke test de vistas con AppTest (S1, 2026-09-05): corre el motor sobre la base local
más reciente, inyecta los resultados en session_state y recorre las vistas clave
verificando que ninguna lance excepción. Skip limpio si no hay base local (CI)."""
import glob
import os

import pytest

from conftest import BASES_DIR, REPO

VISTAS = ["🏠 Dashboard", "📲 Productos Venta Cero", "🔄 Transferencias", "📊 Gestión por Antigüedad",
          "🏆 Caso de Éxito", "🎯 Match Producto-Plaza", "📐 Rendimiento de Marca", "📦 Reposición",
          "💰 Gestión de Precios", "🤝 Agente Terceras", "🧵 Talla y Color", "📊 Planificación"]


def _base_mas_reciente():
    bases = sorted(glob.glob(os.path.join(BASES_DIR, "Base al *.xlsx")), key=os.path.getmtime)
    return bases[-1] if bases else None


@pytest.fixture(scope="module")
def resultados(tmp_path_factory):
    base = _base_mas_reciente()
    if not base:
        pytest.skip("sin base local en data2/bases antiguas/")
    import motor_v2
    import transformar_profundidad as etl
    pl = str(tmp_path_factory.mktemp("pl") / "plantilla.xlsx")
    etl.transform(base, output_path=pl, fecha_corte=etl.fecha_corte_desde_nombre(os.path.basename(base)))
    return base, motor_v2.run_analysis(pl)


@pytest.mark.parametrize("vista", VISTAS)
def test_vista_sin_excepcion(resultados, vista):
    from streamlit.testing.v1 import AppTest
    base, res = resultados
    at = AppTest.from_file(os.path.join(REPO, "app_streamlit.py"), default_timeout=300)
    at.session_state["results"] = res
    at.session_state["_base_profundidad_path"] = base
    at.session_state["_snapshots_initialized"] = True
    at.session_state["nav_page"] = vista
    at.run()
    errores = [str(e.value)[:300] for e in at.exception]
    assert not errores, f"{vista}: {errores}"
