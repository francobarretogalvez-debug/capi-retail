"""
agente_reporte.py — Redacta el correo semanal de gerencia con los datos ya calculados.

Regla dura del diseño: **el agente redacta, no calcula.** Recibe un diccionario de
cifras que ya salieron del motor y verificadas, y solo las pone en prosa. No ve la
base, no hace aritmética, y el prompt le prohíbe inventar o derivar números nuevos.

Si un modelo se pone a calcular márgenes de cabeza, tarde o temprano manda un
número equivocado a la gerenta — y el costo de eso es mucho mayor que el de una
redacción algo más rígida. Por eso el fallback sin IA (`reporte_semanal.correo`)
es un ciudadano de primera, no un plan B degradado.

El resultado es SIEMPRE un borrador. Nada se envía solo.
"""

from __future__ import annotations

import json
import os

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

MODEL = "claude-sonnet-5"
MAX_TOKENS = 2000
TEMPERATURE = 0.3

SYSTEM = """Eres el analista de una buyer senior de moda hombre en Ripley Perú. Redactas
el correo semanal que ella le manda a su jefa (gerenta de la categoría) con el
rendimiento de una marca por tienda.

REGLAS INVIOLABLES
1. Usa EXCLUSIVAMENTE las cifras del JSON que recibes. No inventes, no estimes, no
   derives números nuevos, no hagas aritmética. Si un dato no está, no lo menciones.
2. No redondees distinto a como viene. Si el JSON dice 32.6%, escribe 32.6%.
3. Si una cifra te parece rara, NO la corrijas ni la omitas: menciónala como viene.

TONO
- Peruano profesional, directo, de analista a jefa. Nada de relleno corporativo.
- Usa "tú". Evita "estimada", "quedo atento", "no dudes en".
- Frases cortas. La conclusión primero, la evidencia después.
- Markdown simple: **negrita** para cifras clave. Sin encabezados con #.

ESTRUCTURA
- Una línea de apertura y el resumen de la ventana.
- Una sección por cada punto que la gerenta preguntó, en el orden del JSON.
- Cierra con lo que falta y una línea de disponibilidad.

Devuelve:
ASUNTO: <asunto>
---
<cuerpo>"""


def hechos(d, mockup_notas=True) -> dict:
    """Empaqueta lo verificado del análisis en un dict para el redactor.

    Todo lo que entra acá ya pasó por el motor. El agente no recibe nada crudo.
    """
    act, con = d["act"], d["conm2"]
    vta, contrib = float(act.venta_soles.sum()), float(act.contribucion.sum())
    sin = act[act.m2.isna() & (act.unidades > 0)]

    def fila(r, cols):
        return {k: (None if r[k] != r[k] else round(float(r[k]), 2)) if k != "tienda" else r[k]
                for k in cols}

    top = con.nlargest(5, "contrib_x_m2")
    peor = con[con.contribucion > 0].nlargest(1, "m2")
    tend = act[act.cobertura_sem.notna() & act.cobertura_1sem.notna() & (act.cobertura_sem < 100)].copy()
    tend["delta"] = tend.cobertura_1sem - tend.cobertura_sem

    h = {
        "marca": d["marca"],
        "ventana": {"semanas": d["nsem"], "cortes": d["cortes"]},
        "totales_marca_completa": {
            "venta_neta_soles": round(vta), "unidades": int(act.unidades.sum()),
            "contribucion_soles": round(contrib),
            "margen_pct": round(contrib / vta * 100, 1) if vta else None,
            "pct_venta_en_liquidacion": round(float(act.venta_liq.sum()) / vta * 100) if vta else None,
        },
        "alcance": {
            "tiendas_en_los_cuadros": int(len(con)),
            "criterio": "solo tiendas con m2 de corner asignado",
            "excluidas": list(sin.tienda),
            "pct_venta_excluida": round(float(sin.venta_soles.sum()) / vta * 100) if vta else 0,
            "contribucion_excluida_soles": round(float(sin.contribucion.sum())),
            "margen_solo_cuadros_pct": round(float(con.contribucion.sum()) / float(con.venta_soles.sum()) * 100, 1)
                                       if con.venta_soles.sum() else None,
        },
        "contribucion_por_m2": {
            "cadena_soles_por_m2": round(float(con.contribucion.sum()) / float(con.m2.sum()))
                                   if con.m2.sum() else None,
            "m2_totales": round(float(con.m2.sum())),
            "top": [fila(r, ["tienda", "m2", "contribucion", "contrib_x_m2", "margen"])
                    for _, r in top.iterrows()],
            "mas_metros_bajo_rendimiento": (fila(peor.iloc[0], ["tienda", "m2", "contrib_x_m2"])
                                            if len(peor) else None),
        },
        "cobertura": {
            "objetivo_semanas": 12,
            "cadena_ventana": round(float(act.stock_uds.sum()) / (float(act.unidades.sum()) / d["nsem"]), 1)
                              if act.unidades.sum() else None,
            "cadena_ultima_semana": round(float(act.stock_uds.sum()) / float(act.und_ult_sem.sum()), 1)
                                    if act.und_ult_sem.sum() else None,
            "mayor_frenada": (fila(tend.nlargest(1, "delta").iloc[0],
                                   ["tienda", "cobertura_sem", "cobertura_1sem", "und_ult_sem"])
                              if len(tend) else None),
            "mayor_aceleracion": (fila(tend.nsmallest(1, "delta").iloc[0],
                                       ["tienda", "cobertura_sem", "cobertura_1sem"])
                                  if len(tend) else None),
        },
    }

    if d.get("vs") and len(d["comp"]):
        import rendimiento_tienda as rt
        a, b = rt._norm(d["marca"]), rt._norm(d["vs"])
        c = d["comp"]
        if a in c.columns and b in c.columns:
            cc = c[(c[a] > 0) & c.tienda.isin(con.tienda)].copy()
            cc["ratio"] = cc[a] / cc[b].replace(0, None)
            cc = cc.sort_values("ratio", ascending=False)
            h["vs_marca"] = {
                "marca_comparada": d["vs"],
                "por_tienda": [{"tienda": r.tienda, "ratio_pct": round(float(r.ratio) * 100)}
                               for _, r in cc.iterrows() if r.ratio == r.ratio],
                "nota_totales": f"{d['vs']} vende en más tiendas que {d['marca']}, "
                                f"así que el total de cadena no es comparable.",
            }
    if mockup_notas:
        h["pendientes"] = [
            "La serie mes a mes necesita acumular más cortes semanales; el micro trae "
            "venta por tienda de una sola semana.",
            "Los corners con metraje pero sin venta suficiente se reportan aparte para no "
            "ensuciar el promedio con un cero que no significa mal desempeño.",
        ]
    return h


def redactar(h: dict, preguntas: str = "", api_key: str | None = None) -> dict:
    """Devuelve {'asunto', 'cuerpo'}. Lanza ValueError si no se puede."""
    key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError("Falta ANTHROPIC_API_KEY.")
    if Anthropic is None:
        raise ImportError("Falta el SDK: pip install anthropic")

    partes = [f"Cifras verificadas del análisis:\n\n{json.dumps(h, ensure_ascii=False, indent=2)}"]
    if preguntas.strip():
        partes.append("La gerenta preguntó esto — responde punto por punto, en este orden:\n"
                      + preguntas.strip())
    partes.append("Redacta el correo. Recuerda: solo las cifras del JSON, sin aritmética propia.")

    client = Anthropic(api_key=key, timeout=45.0, max_retries=1)
    try:
        r = client.messages.create(model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
                                   system=SYSTEM, messages=[{"role": "user", "content": "\n\n".join(partes)}])
    except Exception as e:
        n = type(e).__name__
        if "RateLimit" in n or "Overloaded" in str(e):
            raise ValueError("El asistente está saturado. Reintenta en unos segundos.")
        if "Timeout" in n or "Connection" in n:
            raise ValueError("El asistente tardó demasiado. Reintenta.")
        if "NotFound" in n or "model" in str(e).lower():
            raise ValueError(f"El modelo {MODEL} no está disponible con esta API key.")
        raise
    return _partir(r.content[0].text)


def _partir(texto: str) -> dict:
    asunto, cuerpo = "", texto.strip()
    if "ASUNTO:" in texto:
        resto = texto.split("ASUNTO:", 1)[1]
        if "---" in resto:
            asunto, cuerpo = resto.split("---", 1)
        else:
            p = resto.split("\n", 1)
            asunto, cuerpo = p[0], (p[1] if len(p) > 1 else "")
    return {"asunto": asunto.strip(), "cuerpo": cuerpo.strip()}


def verificar(cuerpo: str, h: dict) -> list[str]:
    """Cifras con formato de soles en el texto que NO están en los hechos.

    No prueba que el correo sea correcto —un número puede estar bien escrito y mal
    usado— pero atrapa el caso peligroso: que el modelo se haya inventado una cifra.
    """
    import re
    permitidos = set()

    def recoger(o):
        if isinstance(o, dict):
            for v in o.values():
                recoger(v)
        elif isinstance(o, list):
            for v in o:
                recoger(v)
        elif isinstance(o, (int, float)) and o is not None:
            permitidos.add(round(abs(float(o))))
            permitidos.add(round(abs(float(o)), 1))

    recoger(h)
    sosp = []
    for m in re.finditer(r"S/\s?([\d.,]+)", cuerpo):
        try:
            v = round(abs(float(m.group(1).replace(",", ""))))
        except ValueError:
            continue
        if v not in permitidos and v > 1:
            sosp.append(f"S/ {m.group(1)}")
    return sorted(set(sosp))
