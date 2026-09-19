"""AppTest de la página 🤝 Agente Terceras (reporte semanal al proveedor, C7): la página carga,
se genera el borrador + Excel sin excepción y el corte se persiste en un directorio aislado
(CAPI_SNAPSHOTS_DIR) — nunca en snapshots/ del repo. Skip limpio sin base local (CI)."""
import glob
import os

import pytest

from conftest import BASES_DIR, REPO


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


def test_generar_reporte_proveedor(resultados, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv("CAPI_SNAPSHOTS_DIR", str(tmp_path))       # aislar persistir_corte
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)          # sin IA → versión por reglas
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    base, res = resultados
    at = AppTest.from_file(os.path.join(REPO, "app_streamlit.py"), default_timeout=300)
    at.session_state["results"] = res
    at.session_state["_base_profundidad_path"] = base
    at.session_state["_snapshots_initialized"] = True
    at.session_state["nav_page"] = "🤝 Agente Terceras"
    at.run()
    assert not [str(e.value)[:300] for e in at.exception]
    assert "at_rep_bloques" in at.session_state
    bl = at.session_state["at_rep_bloques"][0]
    marca = at.session_state["at_rep_marca"]
    assert bl["marca"] == marca and bl["hechos"]["foto"]["capital_total"] > 0

    at.button(key="at_rep_gen").click().run()
    assert not [str(e.value)[:300] for e in at.exception]
    bor = at.session_state["at_borrador_rep"]
    assert bor["marca"] == marca and len(bor["xlsx"]) > 5000 and bor["sospechosos"] == []
    assert "versión por reglas" in bor["via"]
    assert at.session_state["at_rep_asunto"].startswith(marca)
    cuerpo = at.session_state["at_rep_cuerpo"]
    assert "1) VENTA CERO" in cuerpo and "3) GANADORES" in cuerpo and "TOTAL VENTA CERO" in cuerpo
    # el corte se guardó en el directorio aislado, no en snapshots/ del repo
    import reporte_proveedor as rp
    sem = at.session_state["at_rep_bloques"][0]["semana_iso"]
    assert os.path.exists(os.path.join(str(tmp_path), sem, rp.ARCHIVO_CORTE))
    assert not os.path.exists(os.path.join(REPO, "snapshots", sem, rp.ARCHIVO_CORTE))

    at.button(key="at_rep_descartar").click().run()
    assert not [str(e.value)[:300] for e in at.exception]
    assert "at_borrador_rep" not in at.session_state
