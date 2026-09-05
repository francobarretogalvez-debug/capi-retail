"""
Comparativo semanal — S4 (2026-09-05, pedido Franco FR6 + tracking de KPIs).

Panel "semana actual vs W−1 / W−2 / W−3 / W−4" construido SOLO desde los snapshots
semanales (nivel SKU agregado cadena). Reusa `compare_weeks` / `capital_por_estado`
de snapshots_engine.api, que estaban construidas y sin consumidor.

Reglas:
- Venta semanal = vta_u_sem_1ant (la venta de la semana), NUNCA `unidades_vendidas`
  (acumulado de temporada). Fix B1 auditoría 2026-08-23.
- Soles semanales = uds semanales × precio realizado del SKU (venta_soles/uds acumulados).
- Capital inmovilizado = estados DORMIDO+ESTANCADO+SOBRESTOCK+LIQUIDAR+MUERTO (ESTADOS_EXCESO).
- Obsoleto (definición provisional hasta que Franco elija): estado MUERTO por taxonomía.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from snapshots_engine import api as _api
from snapshots_engine.api import ESTADOS_EXCESO
from snapshots_engine.storage import load_snapshot, list_available_weeks

ESTADOS_OBSOLETO = ["MUERTO"]

# (clave, etiqueta, formato, "mayor es mejor")
KPIS = [
    ("venta_soles",        "Venta S/ (semana)",            "soles", True),
    ("contribucion_soles", "Contribución S/ (semana)",     "soles", True),
    ("margen_pct",         "Margen %",                     "pct",   True),
    ("precio_realizado",   "Precio promedio realizado S/", "soles2", None),
    ("capital_total",      "Capital total S/ (costo)",     "soles", None),
    ("capital_inmovilizado", "Capital inmovilizado S/",    "soles", False),
    ("pct_inmovilizado",   "% capital inmovilizado",       "pct",   False),
    ("capital_obsoleto",   "Capital obsoleto S/ (MUERTO)", "soles", False),
    ("cobertura_sem",      "Cobertura (semanas)",          "num1",  False),
    ("skus_quiebre",       "SKUs en quiebre",              "int",   False),
    ("pct_venta_cero",     "% SKUs con stock sin venta",   "pct",   False),
]


def _estado_series(df: pd.DataFrame) -> pd.Series:
    from taxonomia import classify_series
    return classify_series(df["cobertura_sem"], edad=df.get("edad_semanas"), rango=df.get("rango_antiguedad"))


def kpis_semana(semana: str) -> dict:
    """KPIs de una semana desde su snapshot. Devuelve {} si no existe."""
    try:
        df = load_snapshot(semana)
    except FileNotFoundError:
        return {}
    if df is None or df.empty:
        return {}
    d = df.copy()
    uds_sem = pd.to_numeric(d.get("vta_u_sem_1ant"), errors="coerce").fillna(0)
    uds_acum = pd.to_numeric(d.get("unidades_vendidas"), errors="coerce")
    soles_acum = pd.to_numeric(d.get("venta_soles"), errors="coerce")
    contr_acum = pd.to_numeric(d.get("contribucion_soles"), errors="coerce")
    precio_real = (soles_acum / uds_acum).where(uds_acum > 0)
    margen_sku = (contr_acum / soles_acum).where(soles_acum > 0).clip(-1, 1)
    soles_sem = uds_sem * precio_real.fillna(0)
    contr_sem = soles_sem * margen_sku.fillna(0)
    stock = pd.to_numeric(d.get("stock_total"), errors="coerce").fillna(0)
    capital = pd.to_numeric(d.get("stock_valor_costo"), errors="coerce").fillna(0)
    estado = _estado_series(d) if "cobertura_sem" in d.columns else pd.Series("", index=d.index)
    con_stock = stock > 0
    venta_total_sem = float(uds_sem.sum())
    return {
        "semana": semana,
        "venta_uds": venta_total_sem,
        "venta_soles": float(soles_sem.sum()),
        "contribucion_soles": float(contr_sem.sum()),
        "margen_pct": (float(contr_sem.sum() / soles_sem.sum()) if soles_sem.sum() else np.nan),
        "precio_realizado": (float(soles_sem.sum() / venta_total_sem) if venta_total_sem else np.nan),
        "capital_total": float(capital.sum()),
        "capital_inmovilizado": float(capital[estado.isin(ESTADOS_EXCESO)].sum()),
        "pct_inmovilizado": (float(capital[estado.isin(ESTADOS_EXCESO)].sum() / capital.sum()) if capital.sum() else np.nan),
        "capital_obsoleto": float(capital[estado.isin(ESTADOS_OBSOLETO)].sum()),
        "cobertura_sem": (float(stock.sum() / venta_total_sem) if venta_total_sem else np.nan),
        "skus_quiebre": int((estado == "QUIEBRE").sum()),
        "pct_venta_cero": (float(((uds_sem == 0) & con_stock).sum() / con_stock.sum()) if con_stock.sum() else np.nan),
        "n_skus": int(d["sku"].nunique()),
    }


def semanas_panel(hasta: str | None = None, n: int = 5) -> list[str]:
    """Las últimas n semanas con snapshot (no necesariamente consecutivas)."""
    weeks = list_available_weeks()
    if hasta:
        weeks = [w for w in weeks if w <= hasta]
    return weeks[-n:]


def panel_4_semanas(hasta: str | None = None) -> dict:
    """Tabla KPI × semana (W, W−1 … W−4) + deltas de W vs cada anterior.

    Devuelve {'semanas': [...], 'tabla': DataFrame(index=etiqueta KPI, cols=semanas),
              'deltas': DataFrame(index=etiqueta, cols=['vs W−1', 'vs W−2', ...]) en %,
              'kpis': {semana: dict}, 'consecutivas': bool}.
    """
    sems = semanas_panel(hasta)
    if len(sems) < 2:
        return {"semanas": sems, "tabla": pd.DataFrame(), "deltas": pd.DataFrame(), "kpis": {}, "consecutivas": False}
    kp = {s: kpis_semana(s) for s in sems}
    kp = {s: v for s, v in kp.items() if v}
    sems = [s for s in sems if s in kp]
    filas = {}
    for key, label, _fmt, _better in KPIS:
        filas[label] = [kp[s].get(key, np.nan) for s in sems]
    tabla = pd.DataFrame(filas, index=sems).T  # KPI × semana
    actual = sems[-1]
    deltas = {}
    for i, s in enumerate(reversed(sems[:-1]), start=1):
        col = f"vs W−{i} ({s})"
        vals = []
        for key, label, fmt, _b in KPIS:
            a, b = kp[s].get(key, np.nan), kp[actual].get(key, np.nan)
            if fmt == "pct":
                vals.append((b - a) * 100 if pd.notna(a) and pd.notna(b) else np.nan)  # puntos porcentuales
            else:
                vals.append(((b - a) / a * 100) if (pd.notna(a) and a not in (0, np.nan) and pd.notna(b)) else np.nan)
        deltas[col] = vals
    deltas = pd.DataFrame(deltas, index=[l for _, l, _, _ in KPIS])

    def _num(w):
        y, ww = w.split("-")
        return int(y) * 100 + int(ww)
    consecutivas = all(_num(sems[i + 1]) - _num(sems[i]) == 1 for i in range(len(sems) - 1))
    return {"semanas": sems, "tabla": tabla, "deltas": deltas, "kpis": kp, "consecutivas": consecutivas}


def pareto_inmovilizado(semana: str, umbral: float = 0.80) -> pd.DataFrame:
    """SKUs en estados de exceso ordenados por capital, con % acumulado y marca TOP 80%.
    Nivel cadena (el snapshot no tiene tienda)."""
    try:
        df = load_snapshot(semana)
    except FileNotFoundError:
        return pd.DataFrame()
    if df is None or df.empty or "stock_valor_costo" not in df.columns:
        return pd.DataFrame()
    d = df.copy()
    d["estado"] = _estado_series(d)
    d = d[d["estado"].isin(ESTADOS_EXCESO)].copy()
    d["capital"] = pd.to_numeric(d["stock_valor_costo"], errors="coerce").fillna(0)
    d = d.sort_values("capital", ascending=False, kind="mergesort")
    tot = d["capital"].sum()
    if not tot:
        return pd.DataFrame()
    d["share"] = d["capital"] / tot
    d["pct_acum"] = d["share"].cumsum()
    d["top_80"] = np.where((d["pct_acum"] - d["share"]).round(6) < umbral, "⭐ TOP 80%", "")
    cols = [c for c in ["sku", "descripcion", "nombre", "marca", "categoria", "estado", "stock_total",
                        "capital", "share", "pct_acum", "top_80", "edad_semanas", "cobertura_sem"] if c in d.columns]
    return d[cols].reset_index(drop=True)


def resumen_pareto(semana: str) -> dict:
    p = pareto_inmovilizado(semana)
    if p.empty:
        return {}
    top = p[p["top_80"] != ""]
    return {"semana": semana, "n_skus_exceso": int(len(p)), "n_skus_top80": int(len(top)),
            "capital_exceso": float(p["capital"].sum()), "capital_top80": float(top["capital"].sum()),
            "pct_skus_top80": float(len(top) / len(p))}
