"""Agente proveedor (decisión 2026-09-18): redacción acotada + parser compartido.

C2: `agente_reporte.partir_asunto` reemplaza a los dos parsers duplicados. El bug que
motivó el cambio: el texto del modelo traía `---` como regla horizontal más abajo (no
como separador), el parser viejo partía ahí y el saludo quedaba dentro del asunto.
"""
import agente_reporte
import agente_terceras


def test_partir_asunto_robusto():
    # 1) el caso del bug: sin separador tras el asunto, `---` más abajo
    txt = "ASUNTO: Sobrestock crítico\n\nEstimado Raúl, buenos días.\n\n---\n\n**Detalle por línea**\n| Línea | S/ |"
    p = agente_reporte.partir_asunto(txt)
    assert p["asunto"] == "Sobrestock crítico"
    assert p["cuerpo"].startswith("Estimado Raúl")
    assert "---" in p["cuerpo"]                       # la regla horizontal se conserva en el cuerpo
    # 2) formato canónico ASUNTO / --- / cuerpo
    p = agente_reporte.partir_asunto("ASUNTO: X\n---\nCuerpo con --- adentro\nfin")
    assert p == {"asunto": "X", "cuerpo": "Cuerpo con --- adentro\nfin"}
    # 3) asunto en negrita markdown
    assert agente_reporte.partir_asunto("**ASUNTO:** Y\n\nCuerpo")["asunto"] == "Y"
    # 4) sin ASUNTO: todo es cuerpo
    assert agente_reporte.partir_asunto("sin asunto\n---\nalgo") == {"asunto": "", "cuerpo": "sin asunto\n---\nalgo"}
    # 5) un solo parser para los tres agentes
    assert agente_terceras._parse_correo is agente_reporte.partir_asunto
    assert agente_reporte._partir is agente_reporte.partir_asunto


# ══════════════════════════════════════════════════════════════════════════════
#  C6 — redacción acotada: reglas, Claude con cliente falso, ensamblado y guard
# ══════════════════════════════════════════════════════════════════════════════
import json
import types

import pytest

import agente_proveedor as ap
import reporte_proveedor as rp
from test_reporte_proveedor import _alertas_sint, _cob, _rep_sint, _trans_sint, _vp_sint


@pytest.fixture(scope="module")
def bl():
    return rp.bloques_marca("M", _cob(), _trans_sint(), _vp_sint(), None, _rep_sint(), _alertas_sint(), corte="30.08.2026", semana_iso="2026-35")


def test_redactar_reglas_solo_cifras_de_hechos(bl):
    h = bl["hechos"]
    prosa = ap.redactar_reglas(h)
    assert set(prosa) == set(ap.CLAVES) and all(prosa[k] for k in ap.CLAVES)
    assert agente_reporte.verificar(" ".join(prosa.values()), h) == []
    assert "M —" in prosa["asunto"] and "30.08.2026" in prosa["asunto"]


def test_ensamblar_estructura(bl):
    h = bl["hechos"]
    out = ap.ensamblar(h, ap.redactar_reglas(h), rp.tablas_texto(bl), rp.tablas_html(bl), firma="Daniela · Ripley")
    t = out["cuerpo_texto"]
    i1, i2, i3, i4 = (t.index(s) for s in ("1) VENTA CERO", "2a) SOBRESTOCK", "2b) TRANSFERENCIAS", "3) GANADORES"))
    assert i1 < i2 < i3 < i4
    assert "TOTAL VENTA CERO" in t and "TOTAL SOBRESTOCK" in t and t.rstrip().endswith("Daniela · Ripley")
    # sección fija de criterio (Franco 20-sep): después del cierre, antes de la firma; sin mencionar sistemas ni IA
    assert t.index("Cómo priorizamos este reporte") > t.index(ap.redactar_reglas(h)["cierre"][:30]) and t.index("Cómo priorizamos") < t.index("Daniela · Ripley")
    assert "Cómo priorizamos este reporte" in out["cuerpo_html"]
    assert not any(w in ap.COMO_PRIORIZAMOS.lower() for w in ("sistema", "herramienta", "inteligencia", "capi", "algoritmo"))
    assert out["cuerpo_html"].count("<table") >= 4
    assert out["sospechosos"] == []
    # sin asunto en la prosa → asunto por defecto
    prosa = ap.redactar_reglas(h); prosa["asunto"] = ""
    assert ap.ensamblar(h, prosa, rp.tablas_texto(bl), rp.tablas_html(bl))["asunto"].startswith("M — Reporte semanal Ripley al 30.08.2026")


class _FakeClient:
    def __init__(self, texto):
        self._t = texto
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kw):
        assert kw["system"] == ap.SYSTEM and kw["model"] == ap.MODEL
        return types.SimpleNamespace(content=[types.SimpleNamespace(text=self._t)])


def test_redactar_con_cliente_falso(bl, monkeypatch):
    h = bl["hechos"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    # 1) JSON completo dentro de fences → se usa tal cual
    ok = {k: f"texto {k}" for k in ap.CLAVES}
    monkeypatch.setattr(ap, "Anthropic", lambda **kw: _FakeClient("```json\n" + json.dumps(ok) + "\n```"))
    assert ap.redactar(h) == ok
    # 2) JSON incompleto → las claves que faltan salen de las reglas
    parcial = {"asunto": "A", "intro": "I"}
    monkeypatch.setattr(ap, "Anthropic", lambda **kw: _FakeClient(json.dumps(parcial)))
    r = ap.redactar(h)
    assert r["asunto"] == "A" and r["lead_b3"] == ap.redactar_reglas(h)["lead_b3"]
    # 3) basura → todo por reglas, sin excepción
    monkeypatch.setattr(ap, "Anthropic", lambda **kw: _FakeClient("no soy json"))
    assert ap.redactar(h) == ap.redactar_reglas(h)
    # 4) cifra inventada en la prosa → el guard la marca
    inv = dict(ok); inv["intro"] = "La marca tiene S/ 999,999 parados."
    monkeypatch.setattr(ap, "Anthropic", lambda **kw: _FakeClient(json.dumps(inv)))
    out = ap.ensamblar(h, ap.redactar(h), rp.tablas_texto(bl), rp.tablas_html(bl))
    assert out["sospechosos"] == ["S/ 999,999"]
    # 5) sin API key → ValueError (la UI cae a reglas)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError):
        ap.redactar(h)
