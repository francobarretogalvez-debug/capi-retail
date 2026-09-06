"""
Comparativo semanal y mensual — S4 (2026-09-05, pedido Franco FR6 + tracking de KPIs).

Decisión de producto (Franco, 2026-09-05 noche): lo que importa es la FOTO ACTUAL del
inventario y si las acciones la mueven. Por eso el panel compara la foto de hoy contra
la de hace una semana (Δ semanal) y la de hace ~un mes (Δ mensual), sobre KPIs de stock.
No se compara contra el año pasado (sin calendario de eventos de precio, el YoY engaña y
además no hay snapshots de stock de 2025). La venta queda como contexto, sin flechas.

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

ESTADOS_OBSOLETO = ["MUERTO"]   # (taxonomía) — el panel usa la definición por antigüedad, ver abajo

# (clave, etiqueta, formato, "mayor es mejor")
KPIS = [
    ("venta_soles",        "Venta S/ (semana)",            "soles", True),
    ("contribucion_soles", "Contribución S/ (semana)",     "soles", True),
    ("margen_pct",         "Margen %",                     "pct",   True),
    ("precio_realizado",   "Precio promedio realizado S/", "soles2", None),
    ("capital_total",      "Capital total S/ (costo)",     "soles", None),
    ("capital_inmovilizado", "Capital inmovilizado S/",    "soles", False),
    ("pct_inmovilizado",   "% capital inmovilizado",       "pct",   False),
    ("capital_preobsoleto", "Pre-obsoleto S/ (6–9 meses)",  "soles", False),
    ("capital_obsoleto",   "Obsoleto S/ (9 meses a más)",  "soles", False),
    ("cobertura_sem",      "Cobertura (semanas)",          "num1",  False),
    ("skus_quiebre",       "SKUs en quiebre",              "int",   False),
    ("pct_venta_cero",     "% SKUs con stock sin venta",   "pct",   False),
    # ── Segunda fila (revisión Franco 2026-09-05): ¿qué viene y dónde está la plata? ──
    ("capital_por_entrar", "Por entrar a pre-obsoleto en 4 sem S/", "soles", False),  # indicador adelantado
    ("capital_por_pasar",  "Por pasar a obsoleto en 4 sem S/", "soles", False),
    ("capital_nuevo_sin_venta", "Lanzamientos sin venta S/ (NUEVO SIN VENTA)", "soles", False),
    ("pct_capital_cd",     "% del capital en CD (no en piso)", "pct",   False),
    ("capital_liquidacion", "Capital con dscto ≥40% S/ (liquidación)", "soles", False),
    ("on_order_uds",       "On order (uds en tránsito + colocadas)", "int", None),
]
SEM_PREOBSOLETO, SEM_OBSOLETO = 26, 39     # 6 y 9 meses (definición Franco 2026-09-05)
UMBRAL_OBSOLETO_SEM = SEM_PREOBSOLETO


def _col(d: pd.DataFrame, name: str) -> pd.Series:
    """Columna numérica o ceros si el snapshot (viejo) no la trae."""
    if name in d.columns:
        return pd.to_numeric(d[name], errors="coerce").fillna(0)
    return pd.Series(0.0, index=d.index)


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
    edad = pd.to_numeric(d["edad_semanas"], errors="coerce") if "edad_semanas" in d.columns else pd.Series(np.nan, index=d.index)
    vta4 = sum(_col(d, f"vta_u_sem_{i}ant") for i in (1, 2, 3, 4))
    # Definición Franco 2026-09-05: pre-obsoleto 6–9 meses, obsoleto ≥9 meses, por antigüedad (rango del
    # maestro si existe; edad en semanas como respaldo). Misma regla que obsoletos.py y la vista Antigüedad.
    if "rango_antiguedad" in d.columns and d["rango_antiguedad"].notna().any():
        _r = d["rango_antiguedad"].astype(str)
        pre_edad, obsoleto_edad = _r.eq("RANGO 6_9") & con_stock, _r.isin(["RANGO 9_12", "RANGO 12_99"]) & con_stock
    else:
        pre_edad = (edad > SEM_PREOBSOLETO) & (edad <= SEM_OBSOLETO) & con_stock
        obsoleto_edad = (edad > SEM_OBSOLETO) & con_stock
    por_entrar = (edad > SEM_PREOBSOLETO - 4) & (edad <= SEM_PREOBSOLETO) & con_stock
    por_pasar = (edad > SEM_OBSOLETO - 4) & (edad <= SEM_OBSOLETO) & con_stock
    dsc = _col(d, "pct_descuento")
    dsc = dsc / 100 if dsc.max() > 1.5 else dsc
    cd_uds = _col(d, "stock_cd")
    costo_u = (capital / stock.replace(0, np.nan)).fillna(0)
    oo = _col(d, "on_order_cd_tiendas") + _col(d, "on_order_ordenes")
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
        "capital_preobsoleto": float(capital[pre_edad].sum()),
        "capital_obsoleto": float(capital[obsoleto_edad].sum()),
        "capital_muerto": float(capital[estado.isin(ESTADOS_OBSOLETO)].sum()),
        "cobertura_sem": (float(stock.sum() / venta_total_sem) if venta_total_sem else np.nan),
        "skus_quiebre": int((estado == "QUIEBRE").sum()),
        "pct_venta_cero": (float(((uds_sem == 0) & con_stock).sum() / con_stock.sum()) if con_stock.sum() else np.nan),
        "capital_por_entrar": float(capital[por_entrar].sum()),
        "capital_por_pasar": float(capital[por_pasar].sum()),
        "capital_nuevo_sin_venta": float(capital[estado == "NUEVO SIN VENTA"].sum()),
        "pct_capital_cd": (float((cd_uds * costo_u).sum() / capital.sum()) if capital.sum() else np.nan),
        "capital_liquidacion": float(capital[dsc >= 0.40].sum()),
        "on_order_uds": (float(oo.sum()) if oo.sum() > 0 else np.nan),   # NaN en snapshots viejos sin on-order
        "n_skus": int(d["sku"].nunique()),
    }


def _num(w: str) -> int:
    y, ww = w.split("-")
    return int(y) * 100 + int(ww)


def semana_mensual(sems: list[str], actual: str, semanas_atras: int = 4) -> str | None:
    """El snapshot más cercano a `semanas_atras` semanas antes de `actual` (mínimo 3 atrás)."""
    cands = [w for w in sems if w < actual and (_num(actual) - _num(w)) >= 3]
    if not cands:
        return None
    return min(cands, key=lambda w: abs((_num(actual) - _num(w)) - semanas_atras))


def foto_actual(hasta: str | None = None) -> dict:
    """KPIs de la última semana + Δ semanal (vs snapshot anterior) + Δ mensual (vs ~4 sem atrás).
    Devuelve {'actual', 'semana_prev', 'semana_mes', 'kpis': {sem: dict}, 'delta_sem': {kpi: (abs, pct)}, 'delta_mes': {...}}."""
    weeks = list_available_weeks()
    if hasta:
        weeks = [w for w in weeks if w <= hasta]
    if not weeks:
        return {}
    actual = weeks[-1]
    prev = weeks[-2] if len(weeks) >= 2 else None
    mes = semana_mensual(weeks, actual)
    kp = {w: kpis_semana(w) for w in {actual, prev, mes} if w}

    def _d(a, b):
        out = {}
        for key, _l, fmt, _b in KPIS:
            va, vb = (kp.get(a) or {}).get(key, np.nan), (kp.get(b) or {}).get(key, np.nan)
            if pd.isna(va) or pd.isna(vb):
                out[key] = (np.nan, np.nan)
            elif fmt == "pct":
                out[key] = (vb - va, (vb - va) * 100)          # abs en fracción, "pct" en puntos
            else:
                out[key] = (vb - va, ((vb - va) / va * 100) if va else np.nan)
        return out
    return {"actual": actual, "semana_prev": prev, "semana_mes": mes, "kpis": kp,
            "delta_sem": _d(prev, actual) if prev else {}, "delta_mes": _d(mes, actual) if mes else {}}


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
