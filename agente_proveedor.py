"""
agente_proveedor.py — redacción del "Reporte semanal al proveedor" (decisión 2026-09-18).

Regla de diseño (heredada de agente_reporte): **el agente redacta, no calcula**. Las tablas
del correo las arma reporte_proveedor en Python; Claude solo escribe la intro, una frase de
entrada por bloque y el cierre, a partir del dict `hechos` (todo número que puede citar).
Salida del modelo: JSON con claves fijas. Si no hay API key, el SDK falta, el modelo se cae o
el JSON no parsea, se usa `redactar_reglas` (frases fijas desde `hechos`): Daniela nunca se
queda sin correo. Guard anti-alucinación: agente_reporte.verificar sobre la prosa.
El envío lo hace una persona desde Outlook. Este módulo no manda nada.
"""
from __future__ import annotations

import json
import os
import re

import agente_reporte

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover
    Anthropic = None

MODEL = agente_reporte.MODEL
MAX_TOKENS = 900
TEMPERATURE = 0.3

CLAVES = ("asunto", "intro", "lead_b1", "lead_b2a", "lead_b2b", "lead_b3", "cierre")

SYSTEM = """Eres el asistente de un equipo de compras de Ripley (retail de moda, Perú).
Redactas el REPORTE SEMANAL que el buyer manda al representante de una marca tercera.
Las TABLAS del correo ya están hechas; tú escribes solo la prosa alrededor.

REGLAS INVIOLABLES
1. Usa ÚNICAMENTE cifras que aparezcan en el JSON de hechos. Nada de aritmética propia, ni
   promedios, ni "aproximadamente". Si un dato no está, no lo menciones.
2. No repitas las tablas ni listes modelos uno por uno: eso ya va en el cuerpo.
3. Tono: español peruano de negocios, tú, colaborativo y directo. Es una relación de largo plazo.
   Sin adjetivos vacíos ni relleno. Cada campo: máximo 2 oraciones.
4. Devuelve SOLO un JSON válido, sin texto antes ni después, sin fences, con exactamente estas claves:
   {"asunto": "...", "intro": "...", "lead_b1": "...", "lead_b2a": "...", "lead_b2b": "...", "lead_b3": "...", "cierre": "..."}
   - asunto: "<MARCA> — Reporte semanal Ripley al <corte>: ..." (una línea).
   - intro: saludo + para qué es el reporte + la foto de la marca (capital total y % en los frentes).
   - lead_b1: qué pedimos sobre venta cero (descuento compartido / liquidar lo viejo / devolución). La exhibición la revisa Ripley en tienda, no es pedido al proveedor.
   - lead_b2a: qué pedimos sobre sobrestock (descuento compartido 50/50, devolución con recompra o frenar ingreso) y, si hay, cuántos modelos ya son pre-obsoletos u obsoletos (clave "obs": pestaña 4 del Excel).
   - lead_b2b: qué pedimos sobre las transferencias entre tiendas (las ejecuta la marca).
   - lead_b3: qué pedimos sobre los ganadores cortos (reponer desde CD / reorden).
   - cierre: pedido de respuesta con fecha o reunión + despedida cordial.
   Si un bloque viene con n_skus = 0, su lead dice en una frase que esta semana no hay casos."""


# ── Redacción por reglas (sin IA) ────────────────────────────────────────────
def _sn(v):
    return f"{v:,}" if isinstance(v, (int, float)) and v is not None else "—"


def redactar_reglas(h: dict) -> dict:
    """Frases fijas desde `hechos`. Ciudadano de primera: es lo que sale sin API key."""
    m = str(h.get("marca", "")).title(); corte = h.get("corte", ""); f = h.get("foto", {})
    b1, b2a, b2b, b3 = h.get("b1", {}), h.get("b2a", {}), h.get("b2b", {}), h.get("b3", {})
    pct = b1.get("pct_capital_marca", 0)
    intro = (f"Hola, te comparto el reporte semanal de {m} en Ripley al {corte}. La marca tiene S/ {_sn(f.get('capital_total'))} a costo en "
             f"{_sn(f.get('skus'))} modelos y {_sn(f.get('tiendas'))} tiendas. Abajo van los tres frentes de la semana con el detalle por modelo; "
             f"el Excel adjunto trae todo el desglose.")
    lead_b1 = (f"Venta cero: {b1.get('n_skus', 0)} modelos con stock no vendieron ni una unidad la última semana (S/ {_sn(b1.get('capital'))}, "
               f"{pct}% del capital de la marca); {b1.get('n_top', 0)} concentran el 80%. La exhibición la revisamos nosotros en tienda; a ustedes les pedimos el descuento compartido donde aplica y, para los "
               f"{b1.get('n_liquidar', 0)} con más de 26 semanas, liquidar al descuento que marca la pirámide o devolver; ustedes eligen, pero cada semana que pasa la lista solo crece."
               if b1.get("n_skus") else "Venta cero: esta semana no hay modelos sin venta en toda la cadena.")
    obs = h.get("obs", {})
    obs_txt = (f" Además, {obs.get('n_skus', 0)} modelos de la marca ya tienen más de 6 meses en tienda (S/ {_sn(obs.get('capital'))}, {obs.get('pct_capital_marca', 0)}% del capital): "
               f"{obs.get('n_rota', 0)} todavía rotan bien y se agotan solos; para el resto, la pestaña 4 del Excel dice qué liquidar ({obs.get('n_liquidar', 0)}) y qué recoger o devolver ({obs.get('n_recoger', 0)})."
               if obs.get("n_skus") else "")
    lead_b2a = (f"Sobrestock: {b2a.get('n_skus', 0)} modelos venden pero cargan de más (S/ {_sn(b2a.get('capital'))}). Propuesta por modelo: "
                f"descuento compartido 50/50 en {b2a.get('n_markdown', 0)}, devolución con recompra en {b2a.get('n_canje', 0)} y frenar el ingreso en "
                f"{b2a.get('n_frenar', 0)}." + obs_txt
                if b2a.get("n_skus") else "Sobrestock: esta semana no hay modelos con sobrestock a nivel cadena." + obs_txt)
    lead_b2b = (f"Transferencias entre tiendas: {b2b.get('n_skus', 0)} modelos tienen stock donde no rota y faltan donde sí. Son {_sn(b2b.get('uds'))} unidades "
                f"a mover, con S/ {_sn(b2b.get('ganancia'))} de contribución esperada en destino; te pedimos programar las transferencias (el traslado corre por la marca); el detalle origen → destino va en el Excel."
                if b2b.get("n_skus") else "Transferencias: esta semana no hay movimientos entre tiendas con demanda suficiente en destino.")
    lead_b3 = (f"Ganadores que se quedan cortos: {b3.get('n_skus', 0)} modelos con buena rotación y poca cobertura; {b3.get('n_sin_cd', 0)} sin stock en CD. "
               + (f"La necesidad calculada es de {_sn(b3.get('necesidad_uds'))} unidades. " if b3.get("necesidad_uds") else "")
               + "Te pedimos confirmar reposición desde CD y, donde no hay CD, disponibilidad y plazo de reorden."
               if b3.get("n_skus") else "Ganadores: esta semana no hay modelos de alta rotación en riesgo de quiebre.")
    cierre = ("¿Nos confirmas por este medio, antes del viernes, qué acciones toman por bloque? Si prefieres, coordinamos una llamada de 20 minutos "
              "para revisarlo modelo por modelo. Gracias por el apoyo.")
    return {"asunto": f"{h.get('marca', '')} — Reporte semanal Ripley al {corte}: venta cero, sobrestock y reposición",
            "intro": intro, "lead_b1": lead_b1, "lead_b2a": lead_b2a, "lead_b2b": lead_b2b, "lead_b3": lead_b3, "cierre": cierre}


# ── Redacción con Claude (JSON acotado) ──────────────────────────────────────
def _parsear_json(texto: str) -> dict | None:
    t = texto.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t); t = re.sub(r"\s*```$", "", t)
    ini, fin = t.find("{"), t.rfind("}")
    if ini < 0 or fin < 0:
        return None
    try:
        d = json.loads(t[ini:fin + 1])
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) else None


def redactar(h: dict, api_key: str | None = None) -> dict:
    """Devuelve dict con CLAVES. Lanza ValueError/ImportError si no se puede llamar al modelo
    (la UI cae a redactar_reglas). Si el modelo responde pero el JSON está incompleto, completa
    con las reglas clave por clave."""
    key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError("Falta ANTHROPIC_API_KEY.")
    if Anthropic is None:
        raise ImportError("Falta el SDK: pip install anthropic")
    prompt = ("Hechos verificados de la semana (JSON):\n\n" + json.dumps(h, ensure_ascii=False, indent=2)
              + "\n\nEscribe la prosa del reporte. Recuerda: solo cifras del JSON, solo el JSON de salida.")
    client = Anthropic(api_key=key, timeout=45.0, max_retries=1)
    try:
        r = client.messages.create(model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
                                   system=SYSTEM, messages=[{"role": "user", "content": prompt}])
    except Exception as e:
        n = type(e).__name__
        if "RateLimit" in n or "Overloaded" in str(e):
            raise ValueError("El asistente está saturado. Reintenta en unos segundos.")
        if "Timeout" in n or "Connection" in n:
            raise ValueError("El asistente tardó demasiado. Reintenta.")
        if "NotFound" in n or "model" in str(e).lower():
            raise ValueError(f"El modelo {MODEL} no está disponible con esta API key.")
        raise
    d = _parsear_json(r.content[0].text) or {}
    reglas = redactar_reglas(h)
    return {k: (str(d[k]).strip() if d.get(k) else reglas[k]) for k in CLAVES}


# ── Ensamblado del correo ────────────────────────────────────────────────────
_TITULOS = {"b1": "1) VENTA CERO", "b2a": "2a) SOBRESTOCK DE CADENA", "b2b": "2b) TRANSFERENCIAS ENTRE TIENDAS", "b3": "3) GANADORES QUE SE QUEDAN CORTOS"}


def _subtitulo(h: dict, k: str) -> str:
    b = h.get(k, {})
    if k == "b1":
        return f"S/ {_sn(b.get('capital'))} sin venta la última semana · {b.get('n_skus', 0)} modelos · {_sn(b.get('stock_uds'))} uds"
    if k == "b2a":
        return f"S/ {_sn(b.get('capital'))} en {b.get('n_skus', 0)} modelos que venden pero cargan de más"
    if k == "b2b":
        return f"{b.get('n_skus', 0)} modelos · {_sn(b.get('uds'))} uds a mover · contribución esperada S/ {_sn(b.get('ganancia'))}"
    return f"{b.get('n_skus', 0)} modelos con buena rotación y poca cobertura · {b.get('n_sin_cd', 0)} sin stock en CD"


_CRITERIOS_B2 = ("Criterios: descuento compartido 50/50 según acuerdo vigente, sobre precio regular y según la antigüedad del modelo (pirámide: 20% a 80%); "
                 "transferencias desde 12 unidades por modelo y con demanda en la tienda destino (el traslado lo ejecuta y lo asume la marca, sin flete de Ripley); "
                 "devolución para lo que no rota ni con precio.")


def ensamblar(h: dict, prosa: dict, tablas_texto: dict, tablas_html: dict, firma: str = "", evolucion_texto: str = "",
              evolucion_html: str = "") -> dict:
    """{asunto, cuerpo_texto, cuerpo_html, sospechosos}. La prosa se verifica contra `hechos`
    (agente_reporte.verificar); las tablas son nuestras y no se verifican."""
    lead = {"b1": prosa.get("lead_b1", ""), "b2a": prosa.get("lead_b2a", ""), "b2b": prosa.get("lead_b2b", ""), "b3": prosa.get("lead_b3", "")}
    partes = [prosa.get("intro", "")]
    if evolucion_texto:
        partes.append("0) EVOLUCIÓN VS SEMANA ANTERIOR\n" + evolucion_texto)
    for k in ("b1", "b2a", "b2b", "b3"):
        bloque = f"{_TITULOS[k]} — {_subtitulo(h, k)}\n{lead[k]}\n\n{tablas_texto.get(k, '')}"
        if k == "b1":
            bloque += "\n(El Excel adjunto trae el detalle por tienda con la acción de piso: etiquetar, cartel o exhibición.)"
        if k == "b2b":
            bloque += "\n" + _CRITERIOS_B2
        partes.append(bloque)
    partes.append(prosa.get("cierre", ""))
    if firma:
        partes.append(firma)
    cuerpo_texto = "\n\n".join(p for p in partes if p)

    P = "<p style='font-family:Calibri,Arial;font-size:11pt;margin:8px 0'>"
    H = "<p style='font-family:Calibri,Arial;font-size:11.5pt;font-weight:bold;margin:14px 0 2px 0;color:#1f3864'>"
    hp = [P + prosa.get("intro", "").replace("\n", "<br>") + "</p>"]
    if evolucion_html:
        hp.append(H + "0) EVOLUCIÓN VS SEMANA ANTERIOR</p>" + evolucion_html)
    for k in ("b1", "b2a", "b2b", "b3"):
        hp.append(H + f"{_TITULOS[k]} — <span style='font-weight:normal'>{_subtitulo(h, k)}</span></p>" + P + lead[k] + "</p>" + tablas_html.get(k, ""))
        if k == "b1":
            hp.append(f"<p style='font-family:Calibri,Arial;font-size:10pt;color:#555;margin:2px 0'>El Excel adjunto trae el detalle por tienda con la acción de piso: etiquetar, cartel o exhibición.</p>")
        if k == "b2b":
            hp.append(f"<p style='font-family:Calibri,Arial;font-size:10pt;color:#555;margin:2px 0'>{_CRITERIOS_B2}</p>")
    hp.append(P + prosa.get("cierre", "") + "</p>")
    if firma:
        hp.append(P + firma.replace("\n", "<br>") + "</p>")
    cuerpo_html = "<div style='max-width:1100px'>" + "".join(hp) + "</div>"

    prosa_txt = " ".join(str(prosa.get(k, "")) for k in CLAVES)
    sospechosos = agente_reporte.verificar(prosa_txt, h)
    asunto = prosa.get("asunto") or f"{h.get('marca', '')} — Reporte semanal Ripley al {h.get('corte', '')}: venta cero, sobrestock y reposición"
    return {"asunto": asunto, "cuerpo_texto": cuerpo_texto, "cuerpo_html": cuerpo_html, "sospechosos": sospechosos}
