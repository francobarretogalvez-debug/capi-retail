"""
Render HTML del panel "Foto del inventario" como matriz de barras (Propuesta A, Franco 2026-09-06):
semanas como cabecera compartida (izquierda = más vieja, derecha = hoy), una fila por KPI con 5 barras
a escala propia, valor bajo cada barra, y a la derecha los chips Δ semanal y Δ mensual en una sola
fila alineada (verde = mejoró en la dirección que corresponde a ese KPI).
"""
from __future__ import annotations

import math

import pandas as pd

GRUPOS = [
    ("Dónde está el problema hoy", ["capital_inmovilizado", "pct_inmovilizado", "capital_preobsoleto", "capital_obsoleto",
                                     "skus_quiebre", "pct_venta_cero", "capital_venta_cero", "cobertura_sem"]),
    ("Qué viene y dónde está la plata", ["capital_por_entrar", "capital_por_pasar", "capital_nuevo_sin_venta",
                                         "pct_capital_cd", "capital_liquidacion", "on_order_uds"]),
]
NOMBRES = {"capital_inmovilizado": "Capital inmovilizado", "pct_inmovilizado": "% del capital inmovilizado",
           "capital_preobsoleto": "Pre-obsoleto (6–9 meses)", "capital_obsoleto": "Obsoleto (9 meses a más)",
           "skus_quiebre": "SKUs en quiebre", "pct_venta_cero": "% SKUs con stock sin venta",
           "capital_venta_cero": "Capital con venta cero la semana", "cobertura_sem": "Cobertura (semanas)",
           "capital_por_entrar": "Entra a pre-obsoleto en 4 sem", "capital_por_pasar": "Pasa a obsoleto en 4 sem",
           "capital_nuevo_sin_venta": "Lanzamientos sin venta", "pct_capital_cd": "% del capital en CD",
           "capital_liquidacion": "Capital en liquidación (≥40%)", "on_order_uds": "On order (uds)"}

CSS = """
<style>
.fi-wrap{font-family:inherit}
.fi-head,.fi-row{display:grid;grid-template-columns:230px 1fr 200px;column-gap:16px;align-items:end}
.fi-head{padding:4px 0 8px;border-bottom:2px solid var(--capi-border,#E4E2EC);margin-bottom:2px}
.fi-wks,.fi-bars{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}
.fi-wk{text-align:center;font-size:12px;color:var(--capi-text2,#5B5F73);line-height:1.25}
.fi-wk b{display:block;font-variant-numeric:tabular-nums;color:var(--capi-text,#1F2337)} .fi-wk.cur b{color:#6D3B8E}
.fi-wk span{display:block;font-size:11px;opacity:.8} .fi-wk em{display:block;font-style:normal;font-size:10.5px;color:#6D3B8E;font-weight:600;letter-spacing:.04em;text-transform:uppercase}
.fi-h{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--capi-text2,#8C90A3);padding-bottom:2px}
.fi-h.r{display:grid;grid-template-columns:1fr 1fr;text-align:center}
.fi-g{font-size:12.5px;color:var(--capi-text2,#5B5F73);font-weight:600;margin:14px 0 2px;letter-spacing:.02em}
.fi-row{padding:9px 0 7px;border-bottom:1px solid var(--capi-border,#E4E2EC)}
.fi-lab{font-size:13.5px;font-weight:500;line-height:1.3;padding-bottom:18px;color:var(--capi-text,#1F2337)}
.fi-lab .s{display:block;font-size:12px;color:var(--capi-text2,#5B5F73);font-variant-numeric:tabular-nums;margin-top:2px}
.fi-bars{height:78px;align-items:end}
.fi-bw{display:flex;flex-direction:column;justify-content:flex-end;align-items:center;height:100%;gap:3px}
.fi-b{width:100%;max-width:60px;background:#CFC3DD;border-radius:4px 4px 0 0;min-height:2px} .fi-b.cur{background:#6D3B8E}
.fi-v{font-size:11.5px;color:var(--capi-text2,#5B5F73);white-space:nowrap;font-variant-numeric:tabular-nums} .fi-v.cur{color:var(--capi-text,#1F2337);font-weight:600}
.fi-d{display:grid;grid-template-columns:1fr 1fr;gap:6px;padding-bottom:18px;align-items:center;justify-items:center}
.fi-chip{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11.5px;font-weight:600;background:#ECEBF1;color:#5B5F73;white-space:nowrap;font-variant-numeric:tabular-nums}
.fi-chip.good{background:#E3F4EC;color:#0F7A55} .fi-chip.bad{background:#FBE9E1;color:#B8461B}
@media (max-width:900px){.fi-head,.fi-row{grid-template-columns:150px 1fr 130px;column-gap:8px}}
</style>
"""


def _fmt(v, f):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    if f == "soles":
        return f"S/ {v/1e6:.2f}M" if abs(v) >= 1e6 else f"S/ {v/1e3:.0f}K"
    if f == "pct":
        return f"{v*100:.1f}%"
    if f == "num1":
        return f"{v:.1f}"
    return f"{v:,.0f}"


def _full(v, f):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "sin dato"
    if f == "soles":
        return f"S/ {v:,.0f}"
    if f == "pct":
        return f"{v*100:.2f}%"
    if f == "num1":
        return f"{v:.2f}"
    return f"{v:,.0f}"


def _delta(a, b, f):
    if a is None or b is None or (isinstance(a, float) and math.isnan(a)) or (isinstance(b, float) and math.isnan(b)):
        return None
    if f == "pct":
        return (b - a) * 100, "pp"
    return ((b - a) / a * 100 if a else float("nan")), "%"


def _chip(dv, better):
    if dv is None or dv[0] != dv[0]:
        return '<span class="fi-chip">—</span>'
    v, u = dv
    if v == 0 or better is None:
        cls, arrow = "", "•"
    else:
        cls = "good" if (v < 0) != bool(better) else "bad"
        arrow = "▲" if v > 0 else "▼"
    return f'<span class="fi-chip {cls}">{arrow} {v:+.1f} {u}</span>'


def render_matriz(kpis_def: list, kp: dict, sems: list, etiquetas: dict, sem_prev: str, sem_mes: str | None) -> str:
    """kpis_def = comparativo_semanal.KPIS; kp = {semana: dict}; sems ordenadas (vieja → hoy)."""
    meta = {k: (label, f, better) for k, label, f, better in kpis_def}
    actual = sems[-1]
    hdr = ""
    for i, w in enumerate(sems):
        tag = "hoy" if w == actual else ("hace 1 sem" if w == sem_prev else ("hace 1 mes" if w == sem_mes else ""))
        hdr += f'<div class="fi-wk {"cur" if w == actual else ""}"><b>{w}</b><span>{etiquetas.get(w, "")}</span><em>{tag}</em></div>'
    out = [CSS, '<div class="fi-wrap">',
           f'<div class="fi-head"><div class="fi-h">KPI · valor de hoy</div><div class="fi-wks">{hdr}</div>'
           f'<div class="fi-h r"><span>Δ vs hace 1 sem</span><span>Δ vs hace 1 mes</span></div></div>']
    for titulo, keys in GRUPOS:
        out.append(f'<div class="fi-g">{titulo}</div>')
        for k in keys:
            if k not in meta:
                continue
            _l, f, better = meta[k]
            vals = [kp.get(w, {}).get(k) for w in sems]
            vnum = [v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
            if not vnum:
                continue
            mx = max(vnum) or 1
            bars = ""
            for i, (w, v) in enumerate(zip(sems, vals)):
                cur = "cur" if w == actual else ""
                h = 0 if (v is None or (isinstance(v, float) and math.isnan(v))) else max(v, 0) / mx * 100
                bars += (f'<div class="fi-bw" title="{etiquetas.get(w, w)}: {_full(v, f)}"><div class="fi-b {cur}" style="height:{h:.1f}%"></div>'
                         f'<div class="fi-v {cur}">{_fmt(v, f)}</div></div>')
            ds = _delta(kp.get(sem_prev, {}).get(k), kp.get(actual, {}).get(k), f) if sem_prev else None
            dm = _delta(kp.get(sem_mes, {}).get(k), kp.get(actual, {}).get(k), f) if sem_mes else None
            out.append(f'<div class="fi-row"><div class="fi-lab">{NOMBRES.get(k, _l)}<span class="s">{_full(vals[-1], f)} hoy</span></div>'
                       f'<div class="fi-bars">{bars}</div><div class="fi-d">{_chip(ds, better)}{_chip(dm, better)}</div></div>')
    out.append('</div>')
    return "".join(out)
