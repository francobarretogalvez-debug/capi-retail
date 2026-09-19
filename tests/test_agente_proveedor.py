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
