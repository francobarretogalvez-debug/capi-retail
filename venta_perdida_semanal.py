"""
Venta perdida de la ÚLTIMA SEMANA, por SKU × tienda — feedback Franco 2026-09-06.

El cálculo histórico (`snapshots_engine.api.estimate_lost_sales`) trabaja a nivel cadena: solo
cuenta quiebre cuando el SKU tiene stock 0 en TODA la cadena. Un SKU con stock en el CD o en
otra tienda pero en 0 en la tienda que lo vende no entra ahí, y ese es el quiebre más común.
Con el snapshot por tienda (`snapshots_engine/tienda.py`) se puede hacer bien:

Para la semana t y cada SKU × tienda:
  quiebre  = la tienda cerró la semana con stock 0 en ese SKU (fila ausente en el snapshot de
             tienda, o stock_uds == 0), habiendo vendido ese SKU en esa tienda en semanas previas.
  velocidad = promedio de la venta semanal del SKU en ESA tienda en las últimas N semanas con stock
             (mín. 2 observaciones); banda = [mín(prom simple, prom reciente), máx(...)].
  perdida_uds = max(0, velocidad − venta de la semana t)   (si vendió algo antes de quebrar, se descuenta)
  precio realizado sin IGV y margen contable del SKU: del snapshot de cadena de la semana t
  (venta_soles / unidades_vendidas; contribucion / venta), igual que el cálculo histórico.
  neto = bruto × (1 − recaptura 30%); margen perdido = neto × margen contable.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from snapshots_engine import tienda as _t
from snapshots_engine.storage import load_snapshot

IGV = 1.18
COB_QUIEBRE_SEM = 4.0            # regla Franco 2026-09-06: quiebre = cobertura ≤ 4 semanas en la tienda
DSCTO_LIQUIDACION = 0.40         # regla Majo: ≥40% = liquidación
RANGOS_OBSOLETO = {"RANGO 6_9", "RANGO 9_12", "RANGO 12_99"}
# Liquidación (temporada en liquidación o dscto ≥40%) SOLO cuenta como quiebre si el CD todavía tiene
# stock relevante del SKU (regla Franco 2026-09-06): umbral anti-ripios = % del stock total (CD+tiendas)
# y un mínimo de unidades. Para esos SKUs la venta perdida se topa con el stock del CD.
UMBRAL_CD_PCT = 0.10
MIN_CD_UDS = 6


def temporada_en_liquidacion(semana: str | None = None, snapshot: pd.DataFrame | None = None) -> str:
    """Temporada (OI/PV) que se está liquidando en el corte. Se infiere de la data: la temporada
    con mayor proporción de SKUs con descuento ≥40% es la que se liquida (sep-2026: OI, ejemplo
    de Franco). Respaldo por calendario si no hay data: mar–ago liquida PV, sep–feb liquida OI."""
    if snapshot is not None and "temporada" in snapshot.columns and "pct_descuento" in snapshot.columns:
        t = snapshot["temporada"].astype(str).str.upper().str.strip()
        d = pd.to_numeric(snapshot["pct_descuento"], errors="coerce").fillna(0)
        d = d / 100 if d.max() > 1.5 else d
        share = {tp: float((d[t == tp] >= DSCTO_LIQUIDACION).mean()) for tp in ("OI", "PV") if (t == tp).sum() >= 50}
        if len(share) == 2 and abs(share["OI"] - share["PV"]) > 0.05:
            return max(share, key=share.get)
    import datetime as _dt
    if semana:
        y, w = semana.split("-")
        mes = _dt.date.fromisocalendar(int(y), int(w), 7).month
    else:
        mes = _dt.date.today().month
    return "PV" if 3 <= mes <= 8 else "OI"


def exclusiones_quiebre(semana: str, umbral_cd_pct: float = UMBRAL_CD_PCT, min_cd_uds: int = MIN_CD_UDS) -> tuple[set, dict]:
    """SKUs que NO cuentan para quiebre (regla Franco 2026-09-06):
      - mercadería con más de 6 meses (pre-obsoleto + obsoleto): siempre fuera;
      - liquidación (temporada en liquidación o dscto ≥40%): fuera SOLO si el CD ya no tiene stock
        relevante del SKU (CD < umbral % del total CD+tiendas, o < mínimo de uds). Si el CD sí tiene,
        el quiebre en tienda es evitable y cuenta.
    Devuelve (set de skus excluidos, conteos). Además `liquidacion_con_cd` = dict sku → stock CD
    para topar la venta perdida de esos SKUs."""
    s = load_snapshot(semana)
    sku = s["sku"].astype(str).str.strip()
    dsc = pd.to_numeric(s.get("pct_descuento"), errors="coerce").fillna(0)
    dsc = dsc / 100 if dsc.max() > 1.5 else dsc
    obs = s.get("rango_antiguedad", pd.Series("", index=s.index)).astype(str).isin(RANGOS_OBSOLETO)
    temp_liq = temporada_en_liquidacion(semana, snapshot=s)
    temp = s.get("temporada", pd.Series("", index=s.index)).astype(str).str.upper().str.strip().eq(temp_liq)
    liq = (dsc >= DSCTO_LIQUIDACION) | temp
    cd = pd.to_numeric(s.get("stock_cd"), errors="coerce").fillna(0)
    tot = pd.to_numeric(s.get("stock_total"), errors="coerce").fillna(0)
    cd_share = (cd / tot.replace(0, np.nan)).fillna(0)
    cd_ok = (cd >= min_cd_uds) & (cd_share >= umbral_cd_pct)
    liq_sin_cd = liq & ~cd_ok & ~obs
    liq_con_cd = liq & cd_ok & ~obs
    excl = set(sku[obs | liq_sin_cd])
    conteo = {"obsoleto_6m": int(obs.sum()), "liquidacion_sin_cd": int(liq_sin_cd.sum()),
              "liquidacion_con_cd_cuenta": int(liq_con_cd.sum()), "temporada_liq": temp_liq,
              "umbral_cd_pct": umbral_cd_pct, "min_cd_uds": min_cd_uds, "total": len(excl)}
    liquidacion_con_cd = dict(zip(sku[liq_con_cd], cd[liq_con_cd]))
    return excl, {**conteo, "_liq_cd": liquidacion_con_cd}


def _precio_margen(semana: str) -> pd.DataFrame:
    """Precio realizado sin IGV y margen contable por SKU (snapshot de cadena de la semana)."""
    s = load_snapshot(semana)
    uds = pd.to_numeric(s.get("unidades_vendidas"), errors="coerce")
    sol = pd.to_numeric(s.get("venta_soles"), errors="coerce")
    con = pd.to_numeric(s.get("contribucion_soles"), errors="coerce")
    pv = pd.to_numeric(s.get("precio_vigente"), errors="coerce")
    precio = (sol / uds).where(uds > 0)
    precio = precio.fillna(pv / IGV)
    margen = (con / sol).where(sol > 0).clip(0, 0.9)
    out = pd.DataFrame({"sku": s["sku"].astype(str).str.strip(), "precio": precio, "margen": margen,
                        "marca": s.get("marca"), "descripcion": s.get("descripcion"), "linea": s.get("linea"),
                        "stock_cd": pd.to_numeric(s.get("stock_cd"), errors="coerce").fillna(0)})
    return out.drop_duplicates("sku")


def venta_perdida_semana(semana: str | None = None, n_prev: int = 4, min_obs: int = 2,
                         tasa_recaptura: float = 0.30) -> dict:
    """Venta perdida de una semana por SKU × tienda. Devuelve dict con totales (bandas) y detalle."""
    weeks = _t.list_tienda_weeks()
    if not weeks:
        return {}
    semana = semana or weeks[-1]
    prev = [w for w in weeks if w < semana][-n_prev:]
    if len(prev) < min_obs:
        return {"semana": semana, "insuficiente": True}
    cur = _t.load_tienda(semana)
    cur["sku"] = cur["sku"].astype(str)
    hist = pd.concat([_t.load_tienda(w).assign(_w=w) for w in prev], ignore_index=True)
    hist["sku"] = hist["sku"].astype(str)
    excl, excl_conteo = exclusiones_quiebre(semana)
    liq_cd = excl_conteo.pop("_liq_cd", {})
    cur = cur[~cur["sku"].isin(excl)]
    hist = hist[~hist["sku"].isin(excl)]
    # velocidad por SKU×tienda: solo semanas donde la tienda tenía stock (venta observable)
    h = hist[hist["stock_uds"] > 0].copy()
    h["_ord"] = h["_w"].map({w: i for i, w in enumerate(prev)})
    g = h.groupby(["sku", "tienda"])
    vel = g["vta_uds_sem"].agg(n_obs="size", vel_simple="mean").reset_index()
    # promedio ponderado reciente (peso = orden 1..n)
    h["_wgt"] = h["_ord"] + 1
    h["_wv"] = h["vta_uds_sem"] * h["_wgt"]
    wv = h.groupby(["sku", "tienda"]).agg(_wv=("_wv", "sum"), _wg=("_wgt", "sum")).reset_index()
    wv["vel_reciente"] = wv["_wv"] / wv["_wg"]
    vel = vel.merge(wv[["sku", "tienda", "vel_reciente"]], on=["sku", "tienda"], how="left")
    vel = vel[(vel["n_obs"] >= min_obs) & (vel[["vel_simple", "vel_reciente"]].max(axis=1) > 0)]
    # estado de la semana t: stock al cierre y venta de la semana (fila ausente = stock 0, venta 0)
    c = cur[["sku", "tienda", "stock_uds", "vta_uds_sem", "on_order"]]
    m = vel.merge(c, on=["sku", "tienda"], how="left")
    m["stock_uds"] = m["stock_uds"].fillna(0)
    m["vta_uds_sem"] = m["vta_uds_sem"].fillna(0)
    m["on_order"] = m["on_order"].fillna(0)
    # ── Quiebre por cobertura (regla Franco): cob ≤ 4 sem en la tienda; contador de semanas ──
    m["vel_ref"] = m[["vel_simple", "vel_reciente"]].max(axis=1)
    m["cobertura_sem"] = np.where(m["vel_ref"] > 0, m["stock_uds"] / m["vel_ref"], np.inf)
    en_q = m[m["cobertura_sem"] <= COB_QUIEBRE_SEM].copy()
    # semanas consecutivas en quiebre: mirar hacia atrás mientras stock/vel_ref ≤ 4
    stock_prev = {w: (_t.load_tienda(w).assign(sku=lambda d: d["sku"].astype(str))
                       .groupby(["sku", "tienda"])["stock_uds"].sum().to_dict()) for w in prev}   # índice único → dict
    def _sem_en_quiebre(row):
        n = 1
        for w in reversed(prev):
            st_w = float(stock_prev[w].get((row.sku, row.tienda), 0))
            if row.vel_ref > 0 and st_w / row.vel_ref <= COB_QUIEBRE_SEM:
                n += 1
            else:
                break
        return n
    en_q["semanas_en_quiebre"] = [_sem_en_quiebre(r) for r in en_q.itertuples(index=False)] if len(en_q) else []
    quiebre_resumen = {
        "n_combos_cob4": int(len(en_q)), "n_skus_cob4": int(en_q["sku"].nunique()) if len(en_q) else 0,
        "n_tiendas_cob4": int(en_q["tienda"].nunique()) if len(en_q) else 0,
        "semanas_promedio": float(en_q["semanas_en_quiebre"].mean()) if len(en_q) else 0.0,
        "dist_semanas": (en_q["semanas_en_quiebre"].clip(upper=4).value_counts().sort_index().to_dict() if len(en_q) else {}),
        "exclusiones": excl_conteo,
    }
    # Regla Franco 2026-09-06 (segunda vuelta): TODO lo que está en quiebre (cob ≤ 4 sem en la tienda)
    # entra al cálculo, no solo lo que cerró en 0. La pérdida es lo que la tienda dejó de vender
    # respecto a su velocidad: max(0, velocidad − venta real de la semana). Si aun con poco stock
    # vendió su velocidad, esa semana no pierde plata (pero sigue contando como semana en quiebre).
    q = en_q.copy()
    q["cerro_en_cero"] = q["stock_uds"] <= 0
    if q.empty:
        return {"semana": semana, "prev": prev, "n_combos": 0, "detalle": pd.DataFrame(), "por_tienda": pd.DataFrame(),
                "bruto_min": 0.0, "bruto_max": 0.0, "neto_min": 0.0, "neto_max": 0.0, "margen_min": 0.0, "margen_max": 0.0,
                "quiebre": quiebre_resumen, "en_quiebre": en_q}
    q["vel_min"] = q[["vel_simple", "vel_reciente"]].min(axis=1)
    q["vel_max"] = q[["vel_simple", "vel_reciente"]].max(axis=1)
    _vta_pos = q["vta_uds_sem"].clip(lower=0)     # devoluciones (venta negativa) no inflan la pérdida
    q["uds_min"] = (q["vel_min"] - _vta_pos).clip(lower=0)
    q["uds_max"] = (q["vel_max"] - _vta_pos).clip(lower=0)
    # SKUs en liquidación: la venta perdida total del SKU (todas las tiendas) se topa con el stock del CD,
    # repartido entre tiendas en proporción a su velocidad (no hay reorden: solo se pierde lo que se pudo mandar)
    q["liquidacion"] = q["sku"].isin(set(liq_cd))
    if q["liquidacion"].any():
        for col in ("uds_min", "uds_max"):
            tot_sku = q.groupby("sku")[col].transform("sum")
            cap = q["sku"].map(liq_cd).astype(float)
            factor = np.where((q["liquidacion"]) & (tot_sku > cap), cap / tot_sku.replace(0, np.nan), 1.0)
            q[col] = q[col] * pd.Series(factor, index=q.index).fillna(1.0)
        q["uds_min"] = np.minimum(q["uds_min"], q["uds_max"])   # el tope por CD no puede invertir la banda
    pm = _precio_margen(semana)
    q = q.merge(pm, on="sku", how="left")
    q["precio"] = q["precio"].fillna(0)
    q["margen"] = q["margen"].fillna(0)
    q["bruto_min"] = q["uds_min"] * q["precio"]
    q["bruto_max"] = q["uds_max"] * q["precio"]
    r = 1 - tasa_recaptura
    q["neto_min"], q["neto_max"] = q["bruto_min"] * r, q["bruto_max"] * r
    q["margen_min"], q["margen_max"] = q["neto_min"] * q["margen"], q["neto_max"] * q["margen"]
    # evitable = hay stock en el CD para despachar o ya viene en camino a esa tienda
    q["evitable"] = (q["on_order"] > 0) | (q["stock_cd"].fillna(0) > 0)
    n_en_quiebre = int(len(q))
    q = q[q["uds_max"] > 0]
    por_tienda = (q.groupby("tienda").agg(combos=("sku", "size"), en_cero=("cerro_en_cero", "sum"), sem_quiebre_prom=("semanas_en_quiebre", "mean"),
                                          uds_min=("uds_min", "sum"), uds_max=("uds_max", "sum"),
                                          neto_min=("neto_min", "sum"), neto_max=("neto_max", "sum"),
                                          margen_min=("margen_min", "sum"), margen_max=("margen_max", "sum"),
                                          evitables=("evitable", "sum"), neto_evitable=("neto_max", lambda x: x[q.loc[x.index, "evitable"]].sum()))
                    .reset_index().sort_values("neto_max", ascending=False))
    cols = ["tienda", "sku", "descripcion", "marca", "linea", "semanas_en_quiebre", "cobertura_sem", "stock_uds", "cerro_en_cero", "vel_min", "vel_max", "vta_uds_sem", "uds_min", "uds_max",
            "precio", "margen", "neto_min", "neto_max", "margen_min", "margen_max", "stock_cd", "on_order", "evitable", "liquidacion"]
    det = q[[c for c in cols if c in q.columns]].sort_values("neto_max", ascending=False).reset_index(drop=True)
    return {"semana": semana, "prev": prev, "n_combos": int(len(det)), "n_en_quiebre": n_en_quiebre, "n_skus": int(det["sku"].nunique()),
            "n_tiendas": int(det["tienda"].nunique()), "detalle": det, "por_tienda": por_tienda,
            "bruto_min": float(q["bruto_min"].sum()), "bruto_max": float(q["bruto_max"].sum()),
            "neto_min": float(q["neto_min"].sum()), "neto_max": float(q["neto_max"].sum()),
            "margen_min": float(q["margen_min"].sum()), "margen_max": float(q["margen_max"].sum()),
            "tasa_recaptura": tasa_recaptura, "quiebre": quiebre_resumen,
            "en_quiebre": en_q.sort_values(["semanas_en_quiebre", "vel_ref"], ascending=[False, False]).reset_index(drop=True)}


def serie_semanas(n: int = 5) -> pd.DataFrame:
    """Venta perdida neta por semana para las últimas n semanas con snapshot de tienda."""
    weeks = _t.list_tienda_weeks()
    rows = []
    for w in weeks[-n:]:
        r = venta_perdida_semana(w)
        if r and not r.get("insuficiente"):
            rows.append({"semana": w, "combos": r["n_combos"], "neto_min": r["neto_min"], "neto_max": r["neto_max"],
                         "margen_min": r["margen_min"], "margen_max": r["margen_max"]})
    return pd.DataFrame(rows)
