"""AppTest de la vista 🚢 PV en Tránsito (2026-10-04): corre el motor sobre base_mini (fixture
anonimizado), inyecta resultados y renderiza la vista (a) sin archivo y (b) con el DETALLE mini
ficticio tomado de session_state['pvt_archivo_path']. Todo aislado: CAPI_SNAPSHOTS_DIR y
CAPI_INPUTS_DIR en tmp, sin NOTION_TOKEN. Corre en CI (no necesita base real)."""
import os

import pandas as pd
import pytest

from conftest import REPO

FIX = os.path.join(REPO, "tests", "fixtures")
VISTA = "🚢 PV en Tránsito"


@pytest.fixture(scope="module")
def res():
    import motor_v2
    return motor_v2.run_analysis(os.path.join(FIX, "base_mini.xlsx"))


@pytest.fixture
def aislado(tmp_path, monkeypatch):
    monkeypatch.setenv("CAPI_SNAPSHOTS_DIR", str(tmp_path / "snap"))
    monkeypatch.setenv("CAPI_INPUTS_DIR", str(tmp_path / "inputs"))
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return tmp_path


def _app(res, archivo=None):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(os.path.join(REPO, "app_streamlit.py"), default_timeout=300)
    at.session_state["results"] = res
    at.session_state["_base_profundidad_path"] = os.path.join(FIX, "base_mini.xlsx")
    at.session_state["_snapshots_initialized"] = True
    at.session_state["_cortes_restaurados"] = True
    at.session_state["nav_page"] = VISTA
    if archivo:
        at.session_state["pvt_archivo_path"] = archivo
    return at


def test_nav_registrada():
    src = open(os.path.join(REPO, "app_streamlit.py"), encoding="utf-8").read()
    assert '("🚢", "PV en Tránsito")' in src and 'elif nav_page == "🚢 PV en Tránsito"' in src
    assert "import vista_pv_transito" in src


def test_vista_sin_archivo_no_rompe(res, aislado):
    at = _app(res)
    at.run()
    assert not [str(e.value)[:300] for e in at.exception]
    assert any("Sube el DETALLE" in str(i.value) for i in at.info)


def test_vista_con_detalle_mini(res, aislado):
    import eta_store as es
    at = _app(res, os.path.join(FIX, "detalle_comex_mini.xlsx"))
    at.run()
    errores = [str(e.value)[:300] for e in at.exception]
    assert not errores, errores
    # el corte sincronizó el historial solo: 10 OC distintas con su ETA de comex
    h = es.cargar_historial()
    assert h["oc"].nunique() == 10 and (h["fuente"] == "comex").all()
    # hay botón de descarga del export con el nombre contractual y el editor de OC
    assert any(d.label.endswith(".xlsx") and "pv_transito_" in d.label for d in at.get("download_button"))
    assert at.session_state["pvt_res"]["formato"] == "detalle"
    resumen = at.session_state["pvt_res"]["resumen"]
    assert set(resumen["marca"]) == {"Marquis", "Navigata"}            # US POLO no es marca foco en la config real
    # guardar sin cambios no registra nada
    at.button(key="pvt_guardar").click().run()
    assert not [str(e.value)[:300] for e in at.exception]
    assert es.cargar_historial()["oc"].nunique() == 10


def test_vista_con_llegadas_franco(res, aislado):
    import eta_store as es
    at = _app(res, os.path.join(FIX, "llegadas_franco_mini.xlsx"))
    at.run()
    assert not [str(e.value)[:300] for e in at.exception]
    assert at.session_state["pvt_res"]["formato"] == "llegadas"
    assert any("solo trae lo **pendiente**" in str(w.value) for w in at.warning)
    assert not es.cargar_maestro().empty                                # el fallback alimenta el maestro de OC
