"""
Auditoría de predistribución — S15 (Franco, 2026-09-06).

La venta perdida deja de ser un reporte: se le asigna causa raíz por SKU × tienda y se cruza
con lo que sobró en esa misma tienda × línea, para poner a prueba la predistribución.

Capas:
 1. Qué perdimos: tienda × línea, marca, tipo de producto, procedencia, temporada, SKUs recurrentes.
 2. Por qué (causa raíz, en orden de precedencia):
      1 CD tenía stock (no bajó a la tienda)         → dueño: reposición / bajada
      2 Viene en camino (llegó tarde)                 → dueño: tránsito
      3 Otras tiendas tienen stock (mal distribuido)  → dueño: matriz de predistribución
      4 Cadena sin stock · NACIONAL (reorden posible) → dueño: compra / proveedor
      5 Cadena sin stock · IMPORTADO (próxima compra) → dueño: próxima ventana
 3. Índice de acierto por tienda × línea (preliminar, sin curva plan): faltó (venta perdida con
    CD u otras tiendas) vs sobró (capital en sobrestock + venta cero) → diagnóstico.
    Con el archivo de curvas por tienda y los clusters (pendientes de Franco) se compara la
    curva plan vs la curva observada y se propone la curva Capi.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from snapshots_engine import tienda as _t
from snapshots_engine.storage import load_snapshot

CAUSAS = {
    "1_cd": ("1 · El CD tenía stock (no bajó a la tienda)", "Reposición / bajada a tienda"),
    "1b_liq_outlet": ("1b · Liquidación con stock en CD (va a outlet, no a tienda regular)", "Despacho a outlet (OPLN / OSI)"),
    "2_transito": ("2 · Viene en camino (llegó tarde)", "Tránsito CD → tienda"),
    "3_otras": ("3 · Otras tiendas tienen stock (mal distribuido)", "Matriz de predistribución / transferencia"),
    "4_nac": ("4 · Cadena sin stock · nacional (reorden posible)", "Compra / proveedor"),
    "5_imp": ("5 · Cadena sin stock · importado (solo próxima compra)", "Próxima ventana de compra"),
}
SEM_RECURRENTE = 3


def enriquecer(detalle: pd.DataFrame, semana: str) -> pd.DataFrame:
    """Agrega atributos del SKU (procedencia, tipo, temporada, proveedor), stock en otras tiendas
    y la causa raíz a cada SKU × tienda con pérdida."""
    if detalle is None or detalle.empty:
        return pd.DataFrame()
    d = detalle.copy()
    d["sku"] = d["sku"].astype(str)
    s = load_snapshot(semana)
    s["sku"] = s["sku"].astype(str).str.strip()
    cols = [c for c in ["procedencia", "tipo_producto", "temporada", "proveedor", "categoria"] if c in s.columns]
    d = d.join(s.drop_duplicates("sku").set_index("sku")[cols], on="sku")
    t = _t.load_tienda(semana)
    t["sku"] = t["sku"].astype(str)
    otras = t[t["stock_uds"] > 0].groupby("sku")["stock_uds"].sum()
    d["stock_otras_tiendas"] = (d["sku"].map(otras).fillna(0) - d["stock_uds"].clip(lower=0)).clip(lower=0)

    def _causa(x):
        if float(x.get("stock_cd", 0) or 0) > 0:
            return "1b_liq_outlet" if bool(x.get("liquidacion", False)) else "1_cd"
        if float(x.get("on_order", 0) or 0) > 0:
            return "2_transito"
        if float(x.get("stock_otras_tiendas", 0) or 0) > 0:
            return "3_otras"
        if str(x.get("procedencia", "")).upper().startswith("NAC"):
            return "4_nac"
        return "5_imp"
    d["causa_key"] = d.apply(_causa, axis=1)
    d["causa"] = d["causa_key"].map(lambda k: CAUSAS[k][0])
    d["dueno"] = d["causa_key"].map(lambda k: CAUSAS[k][1])
    d["recurrente"] = pd.to_numeric(d.get("semanas_en_quiebre"), errors="coerce").fillna(1) >= SEM_RECURRENTE
    return d


def por_causa(d: pd.DataFrame) -> pd.DataFrame:
    if d is None or d.empty:
        return pd.DataFrame()
    g = d.groupby(["causa_key", "causa", "dueno"]).agg(combos=("sku", "size"), skus=("sku", "nunique"), tiendas=("tienda", "nunique"),
                                                        neto_min=("neto_min", "sum"), neto_max=("neto_max", "sum"),
                                                        recurrentes=("recurrente", "sum")).reset_index().sort_values("causa_key")
    g["pct"] = g["neto_max"] / g["neto_max"].sum()
    return g


def por_dimension(d: pd.DataFrame, dim: str) -> pd.DataFrame:
    if d is None or d.empty or dim not in d.columns:
        return pd.DataFrame()
    g = d.groupby(dim).agg(combos=("sku", "size"), neto_min=("neto_min", "sum"), neto_max=("neto_max", "sum"),
                           recurrentes=("recurrente", "sum")).reset_index().sort_values("neto_max", ascending=False)
    g["pct"] = g["neto_max"] / g["neto_max"].sum()
    return g


def tienda_linea(d: pd.DataFrame) -> pd.DataFrame:
    """Venta perdida por tienda × línea con causa dominante y semanas en quiebre promedio."""
    if d is None or d.empty:
        return pd.DataFrame()
    g = d.groupby(["tienda", "linea"]).agg(combos=("sku", "size"), neto_min=("neto_min", "sum"), neto_max=("neto_max", "sum"),
                                           sem_quiebre_prom=("semanas_en_quiebre", "mean"), recurrentes=("recurrente", "sum"),
                                           con_cd=("causa_key", lambda x: int((x == "1_cd").sum()))).reset_index()
    dom = (d.groupby(["tienda", "linea", "causa"])["neto_max"].sum().reset_index()
             .sort_values("neto_max", ascending=False).drop_duplicates(["tienda", "linea"])[["tienda", "linea", "causa"]]
             .rename(columns={"causa": "causa_dominante"}))
    return g.merge(dom, on=["tienda", "linea"], how="left").sort_values("neto_max", ascending=False).reset_index(drop=True)


def skus_recurrentes(d: pd.DataFrame, n: int = 50) -> pd.DataFrame:
    if d is None or d.empty:
        return pd.DataFrame()
    r = d[d["recurrente"]]
    g = r.groupby(["sku", "descripcion", "marca", "linea"]).agg(tiendas=("tienda", "nunique"), sem_prom=("semanas_en_quiebre", "mean"),
                                                                 neto_max=("neto_max", "sum"), stock_cd=("stock_cd", "max"),
                                                                 causa=("causa", lambda x: x.mode().iloc[0] if len(x) else "")).reset_index()
    return g.sort_values("neto_max", ascending=False).head(n).reset_index(drop=True)


def indice_acierto(d: pd.DataFrame, df_cob: pd.DataFrame) -> pd.DataFrame:
    """Por tienda × línea: FALTÓ (venta perdida neta con CD u otras tiendas, es decir evitable por
    predistribución) vs SOBRÓ (capital a costo en SOBRESTOCK + venta cero de esa tienda × línea en la
    base cargada). Diagnóstico preliminar hasta tener la curva plan y los clusters."""
    if d is None or d.empty or df_cob is None or df_cob.empty:
        return pd.DataFrame()
    # la liquidación con CD (1b) no es "faltó" de predistribución: su acción es outlet
    falto = (d[d["causa_key"].isin(["1_cd", "3_otras"])].groupby(["tienda", "linea"])
               .agg(falto_neto=("neto_max", "sum"), combos_falto=("sku", "size"), recurrentes=("recurrente", "sum")).reset_index())
    c = df_cob.copy()
    c["linea"] = c["categoria"] if "categoria" in c.columns else c.get("linea")
    c["capital"] = pd.to_numeric(c["stock_valor_costo"], errors="coerce").fillna(0)
    c["sobra"] = c["estado"].isin(["SOBRESTOCK", "ESTANCADO"]) | ((pd.to_numeric(c.get("prom_vta_uds"), errors="coerce").fillna(0) == 0) & (c["capital"] > 0))
    tot = c.groupby(["tienda", "linea"]).agg(capital=("capital", "sum"), skus=("sku", "nunique")).reset_index()
    sob = c[c["sobra"]].groupby(["tienda", "linea"]).agg(sobro_capital=("capital", "sum"), combos_sobro=("sku", "size")).reset_index()
    # los códigos de tienda del snapshot (JP, SM…) vs nombres largos en df_cob: normalizar por STORE_NAMES
    try:
        from transformar_profundidad import STORE_NAMES
        inv = {v: k for k, v in STORE_NAMES.items()}
        tot["tienda"] = tot["tienda"].map(lambda x: inv.get(x, x)); sob["tienda"] = sob["tienda"].map(lambda x: inv.get(x, x))
    except Exception:
        pass
    m = tot.merge(sob, on=["tienda", "linea"], how="left").merge(falto, on=["tienda", "linea"], how="left").fillna(
        {"sobro_capital": 0, "combos_sobro": 0, "falto_neto": 0, "combos_falto": 0, "recurrentes": 0})
    m["pct_sobro"] = np.where(m["capital"] > 0, m["sobro_capital"] / m["capital"], 0)
    # diagnóstico: faltó relevante = >S/500/sem o ≥3 combos recurrentes; sobró relevante = >25% del capital
    f_rel = (m["falto_neto"] >= 500) | (m["recurrentes"] >= 3)
    s_rel = m["pct_sobro"] >= 0.25
    m["diagnostico"] = np.select([f_rel & s_rel, f_rel, s_rel],
                                 ["🔀 Mal repartido (falta y sobra a la vez)", "📉 Le mandaron poco", "📈 Le mandaron de más"], "🟢 Acierta")
    m["costo_total"] = m["falto_neto"] + m["sobro_capital"] * 0.05   # sobró: costo de oportunidad semanal aprox. 5% del capital parado
    return m.sort_values("costo_total", ascending=False).reset_index(drop=True)
