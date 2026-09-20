"""
K1 — Activación de venta cero (aprobado por Franco 2026-09-12).

Pregunta: de los SKU×tienda sin venta que se mandaron a los jefes de tienda (lote `vc-…` del
log de acciones), ¿qué % vendió al menos 1 unidad en la(s) semana(s) siguiente(s)? Se compara
contra los combos de venta cero de la misma semana que NO se mandaron (la cola fuera del Pareto):
es el control natural, misma tienda, misma semana.

Tasa base medida (snapshots 30→35, sin ritual): 29–35 % en el Pareto 80 %, 11–12 % en la cola.
El KPI tiene que superar esa base para atribuirle algo al Excel.
"""
from __future__ import annotations

import pandas as pd


def _venta_cero(t: pd.DataFrame) -> pd.DataFrame:
    d = t.copy()
    d["sku"] = d["sku"].astype(str).str.strip().str.lstrip("0")
    return d[(pd.to_numeric(d["stock_uds"], errors="coerce").fillna(0) > 0)
             & (pd.to_numeric(d["vta_uds_sem"], errors="coerce").fillna(0) <= 0)][["sku", "tienda", "stock_uds", "stock_costo"]]


def activacion_df(detalle: pd.DataFrame, cur: pd.DataFrame, nxt: pd.DataFrame) -> dict:
    """detalle: lote enviado (sku, tienda_cod). cur / nxt: tienda.parquet de la semana del envío y de la
    siguiente. Devuelve tasas de activación de enviados vs no enviados y el detalle por combo."""
    vc = _venta_cero(cur)
    n = nxt.copy()
    n["sku"] = n["sku"].astype(str).str.strip().str.lstrip("0")
    m = vc.merge(n[["sku", "tienda", "vta_uds_sem"]], on=["sku", "tienda"], how="left")
    m["vta_uds_sem"] = pd.to_numeric(m["vta_uds_sem"], errors="coerce").fillna(0)
    env = set(zip(detalle["sku"].astype(str).str.lstrip("0"), detalle["tienda_cod"].astype(str)))
    m["enviado"] = [(s, t) in env for s, t in zip(m["sku"], m["tienda"])]
    m["activo"] = m["vta_uds_sem"] > 0
    e, c = m[m["enviado"]], m[~m["enviado"]]
    act_e = float(e["activo"].mean()) if len(e) else float("nan")
    act_c = float(c["activo"].mean()) if len(c) else float("nan")
    por_tienda = (m.groupby(["tienda", "enviado"])["activo"].agg(["mean", "size"]).reset_index()
                    .pivot(index="tienda", columns="enviado", values=["mean", "size"]))
    return {"n_enviados": int(len(e)), "n_no_enviados": int(len(c)),
            "activacion_enviados": act_e, "activacion_no_enviados": act_c,
            "lift_pp": (act_e - act_c) * 100 if len(e) and len(c) else float("nan"),
            "enviados_no_encontrados": int(len(env) - len(e)), "detalle": m, "por_tienda": por_tienda}


def activacion_lote(lote: str, semanas_despues: int = 1) -> dict:
    """Wrapper con I/O: lote del log (semana_iso = semana de la base con la que se generó) y los
    snapshots por tienda de esa semana y de `semanas_despues` cortes después."""
    import acciones_log
    from snapshots_engine import tienda as _t
    filas = acciones_log.lotes()
    fila = filas[filas["lote"] == lote]
    if fila.empty:
        return {"error": f"lote {lote} no está en el log"}
    sem = str(fila.iloc[0]["semana_iso"])
    weeks = _t.list_tienda_weeks()
    if sem not in weeks:
        return {"error": f"no hay snapshot por tienda de {sem}"}
    after = [w for w in weeks if w > sem]
    if len(after) < semanas_despues:
        return {"error": f"todavía no hay {semanas_despues} corte(s) después de {sem}", "semana": sem}
    det = acciones_log.detalle_lote(lote)
    r = activacion_df(det, _t.load_tienda(sem), _t.load_tienda(after[semanas_despues - 1]))
    r.update({"lote": lote, "semana": sem, "semana_medida": after[semanas_despues - 1]})
    return r
