"""
Liquidación con stock en CD → outlet (regla Franco 2026-09-06).

La mercadería en liquidación (temporada que se liquida, o dscto ≥40%) que todavía tiene stock
en el CD NO se repone a tienda regular: se manda a los outlets (OPLN, OSI). El motor de
reposición ya la excluye (mercadería no activa); acá se arma el plan de despacho a outlet.

Reparto entre outlets: proporcional a lo que cada outlet vendió de esa marca × línea en las
últimas 4 semanas (snapshot por tienda); si ninguno tiene historia, mitades.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from snapshots_engine import tienda as _t
from snapshots_engine.storage import load_snapshot, list_available_weeks

OUTLETS = ["OPLN", "OSI"]
OUTLET_NOMBRES = {"OPLN": "Outlet Plaza Norte", "OSI": "Outlet San Isidro"}


def velocidad_outlets(semana: str, n_prev: int = 4) -> pd.DataFrame:
    """Venta semanal promedio por outlet × sku (últimas n semanas con snapshot por tienda)."""
    weeks = [w for w in _t.list_tienda_weeks() if w <= semana][-n_prev:]
    if not weeks:
        return pd.DataFrame(columns=["sku", "tienda", "vel"])
    h = pd.concat([_t.load_tienda(w) for w in weeks], ignore_index=True)
    h = h[h["tienda"].isin(OUTLETS)]
    h["sku"] = h["sku"].astype(str)
    return h.groupby(["sku", "tienda"])["vta_uds_sem"].sum().div(len(weeks)).rename("vel").reset_index()


def plan_outlet(semana: str | None = None) -> dict:
    """SKUs en liquidación con stock en CD → unidades a enviar a cada outlet."""
    from venta_perdida_semanal import exclusiones_quiebre, temporada_en_liquidacion
    semana = semana or list_available_weeks()[-1]
    s = load_snapshot(semana)
    s["sku"] = s["sku"].astype(str).str.strip()
    _, info = exclusiones_quiebre(semana)
    liq_cd = info.get("_liq_cd", {})
    if not liq_cd:
        return {"semana": semana, "plan": pd.DataFrame(), "por_marca": pd.DataFrame(), "temporada_liq": info.get("temporada_liq")}
    d = s[s["sku"].isin(set(liq_cd))].drop_duplicates("sku").copy()
    d["stock_cd"] = pd.to_numeric(d["stock_cd"], errors="coerce").fillna(0)
    d = d[d["stock_cd"] > 0]
    costo = pd.to_numeric(d.get("costo_unitario"), errors="coerce").fillna(0)
    d["capital_cd"] = d["stock_cd"] * costo
    dsc = pd.to_numeric(d.get("pct_descuento"), errors="coerce").fillna(0)
    d["pct_descuento"] = dsc / 100 if dsc.max() > 1.5 else dsc
    # velocidad por outlet: primero por sku; si no hay, por marca×línea; si no, mitades
    vel = velocidad_outlets(semana)
    vel_sku = vel.pivot(index="sku", columns="tienda", values="vel").reindex(columns=OUTLETS).fillna(0)
    ml = s.drop_duplicates("sku").set_index("sku")[["marca", "linea"]]
    vel_ml = (vel.join(ml, on="sku").groupby(["marca", "linea", "tienda"])["vel"].sum().unstack("tienda").reindex(columns=OUTLETS).fillna(0))
    def _split(row):
        v = vel_sku.loc[row.sku].values if row.sku in vel_sku.index else np.zeros(len(OUTLETS))
        base = "velocidad del SKU en el outlet"
        if v.sum() <= 0:
            key = (row.marca, row.linea)
            v = vel_ml.loc[key].values if key in vel_ml.index else np.zeros(len(OUTLETS)); base = "velocidad de la marca×línea en el outlet"
        if v.sum() <= 0:
            v = np.ones(len(OUTLETS)); base = "mitades (sin historia en outlets)"
        share = v / v.sum()
        uds = np.floor(share * row.stock_cd)
        uds[np.argmax(share)] += row.stock_cd - uds.sum()          # el resto al de mayor share
        return pd.Series({**{f"uds_{o}": int(u) for o, u in zip(OUTLETS, uds)}, "base_reparto": base})
    sp = d.apply(_split, axis=1)
    d = pd.concat([d, sp], axis=1)
    cols = [c for c in ["sku", "descripcion", "marca", "linea", "temporada", "edad_semanas", "pct_descuento", "precio_vigente",
                        "stock_cd", "stock_tiendas", "capital_cd"] + [f"uds_{o}" for o in OUTLETS] + ["base_reparto"] if c in d.columns]
    plan = d[cols].sort_values("capital_cd", ascending=False).reset_index(drop=True)
    pm = plan.groupby("marca").agg(skus=("sku", "nunique"), uds_cd=("stock_cd", "sum"), capital_cd=("capital_cd", "sum"),
                                   **{f"uds_{o}": (f"uds_{o}", "sum") for o in OUTLETS}).reset_index().sort_values("capital_cd", ascending=False)
    return {"semana": semana, "plan": plan, "por_marca": pm, "temporada_liq": info.get("temporada_liq"),
            "n_skus": int(len(plan)), "uds": int(plan["stock_cd"].sum()), "capital": float(plan["capital_cd"].sum())}
