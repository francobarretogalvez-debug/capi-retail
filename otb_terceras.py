"""
Reparto del OTB mensual entre marcas terceras — S10 / NF1 (2026-09-05).

Hoy Franco recibe un monto total y lo reparte a mano por venta a costo y cobertura.
Frame que dio (2026-09-05): el reparto debe (1) maximizar venta, (2) bajar cobertura,
(3) bajar obsoleto, (4) servir de argumento de negociación con la marca. Regla clave:
cobertura alta castiga, SALVO que la marca esté destallada (cobertura alta con curva rota
= le falta mercadería de las tallas que venden, no menos OTB).

Método (transparente, sin optimizador):
  base_i      = venta a costo 4 sem de la marca / Σ (lo que Franco hace hoy)
  factor_i    = f_cobertura × f_obsoleto × f_margen × f_quiebre  (cada uno acotado, pesos editables)
  score_i     = base_i × factor_i;  reparto_i = OTB × score_i / Σ score
  Se muestra "reparto por venta" vs "reparto Capi" y por qué cambió cada marca.
Outputs en bandas cuando el input es estimación (regla Loop-Auditor): el reparto sale con ±10%.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RANGOS_OBS = {"RANGO 6_9", "RANGO 9_12", "RANGO 12_99"}
PESOS_DEFAULT = dict(cobertura=1.0, obsoleto=1.0, margen=0.5, quiebre=0.5)
COB_OBJETIVO_SEM = 13.0     # 3 meses (target de Franco)


def metricas_marca(df_cob: pd.DataFrame, marcas: set, destallado: dict | None = None) -> pd.DataFrame:
    """Una fila por marca con: venta a costo 4 sem, stock a costo, cobertura (sem), margen contable,
    % capital obsoleto (>6 m), % SKUs en quiebre, % opciones con curva rota (si se pasa `destallado`)."""
    d = df_cob[df_cob["marca"].astype(str).str.upper().str.strip().isin({m.upper() for m in marcas})].copy()
    if d.empty:
        return pd.DataFrame()
    d["marca"] = d["marca"].astype(str).str.upper().str.strip()
    d["capital"] = pd.to_numeric(d.get("stock_valor_costo"), errors="coerce").fillna(0)
    # venta y contribución 4 sem están a nivel SKU (repetidas por tienda) → dedup por SKU
    sku = d.drop_duplicates("sku")[["sku", "marca", "vta_soles_4sem", "contrib_soles_4sem"]].copy()
    sku["vta"] = pd.to_numeric(sku["vta_soles_4sem"], errors="coerce").fillna(0)
    sku["contr"] = pd.to_numeric(sku["contrib_soles_4sem"], errors="coerce").fillna(0)
    sku["vta_costo"] = (sku["vta"] - sku["contr"]).clip(lower=0)      # costo de lo vendido = venta − contribución
    v = sku.groupby("marca").agg(venta_4sem=("vta", "sum"), contribucion_4sem=("contr", "sum"), venta_costo_4sem=("vta_costo", "sum"))
    s = d.groupby("marca").agg(stock_costo=("capital", "sum"), n_skus=("sku", "nunique"))
    obs = d[d.get("rango_antiguedad", pd.Series("", index=d.index)).isin(RANGOS_OBS)].groupby("marca")["capital"].sum().rename("capital_obsoleto")
    q = d.groupby("marca")["estado"].apply(lambda x: float((x == "QUIEBRE").mean())).rename("pct_skus_quiebre") if "estado" in d.columns else None
    m = v.join(s, how="outer").join(obs, how="left")
    if q is not None:
        m = m.join(q, how="left")
    m = m.fillna({"capital_obsoleto": 0.0, "pct_skus_quiebre": 0.0})
    m["margen_pct"] = np.where(m["venta_4sem"] > 0, m["contribucion_4sem"] / m["venta_4sem"], np.nan)
    m["cobertura_sem"] = np.where(m["venta_costo_4sem"] > 0, m["stock_costo"] / (m["venta_costo_4sem"] / 4), np.inf)
    m["pct_obsoleto"] = np.where(m["stock_costo"] > 0, m["capital_obsoleto"] / m["stock_costo"], 0.0)
    m["pct_curva_rota"] = pd.Series(destallado or {}).reindex(m.index).astype(float) if destallado else np.nan
    return m.reset_index().sort_values("venta_costo_4sem", ascending=False).reset_index(drop=True)


def _f_cobertura(cob, cob_obj, curva_rota, peso):
    """>objetivo castiga hasta 0.4×; <objetivo premia hasta 1.3×. Si está destallada (curva rota ≥30%),
    la cobertura alta no castiga: el problema es de tallas, no de exceso."""
    if not np.isfinite(cob):
        return 0.4
    ratio = cob / cob_obj
    f = float(np.clip(1.0 - 0.6 * (ratio - 1.0), 0.4, 1.3)) if ratio >= 1 else float(np.clip(1.0 + 0.3 * (1.0 - ratio), 1.0, 1.3))
    if pd.notna(curva_rota) and curva_rota >= 0.30 and ratio > 1:
        f = max(f, 1.0)
    return 1.0 + peso * (f - 1.0)


def _f_lineal(x, pivote, pendiente, lo, hi, peso, invertir=False):
    if pd.isna(x):
        return 1.0
    f = float(np.clip(1.0 + pendiente * ((pivote - x) if invertir else (x - pivote)), lo, hi))
    return 1.0 + peso * (f - 1.0)


def repartir_otb(metricas: pd.DataFrame, otb_total: float, pesos: dict | None = None,
                 cob_objetivo: float = COB_OBJETIVO_SEM, margen_ref: float | None = None,
                 max_delta_pct: float | None = 40.0) -> pd.DataFrame:
    """Reparto por venta (hoy) vs reparto Capi (con factores). Devuelve una fila por marca con ambos,
    los factores, el delta y una banda ±10% para el reparto Capi."""
    if metricas is None or metricas.empty or not otb_total:
        return pd.DataFrame()
    w = {**PESOS_DEFAULT, **(pesos or {})}
    m = metricas.copy()
    tot_v = m["venta_costo_4sem"].sum()
    m["base_venta"] = m["venta_costo_4sem"] / tot_v if tot_v else 1.0 / len(m)
    mref = margen_ref if margen_ref is not None else float(np.nanmedian(m["margen_pct"]))
    m["f_cobertura"] = [_f_cobertura(c, cob_objetivo, r, w["cobertura"]) for c, r in zip(m["cobertura_sem"], m["pct_curva_rota"])]
    m["f_obsoleto"] = [_f_lineal(x, 0.10, -1.5, 0.5, 1.0, w["obsoleto"]) for x in m["pct_obsoleto"]]          # >10% obsoleto castiga
    m["f_margen"] = [_f_lineal(x, mref, 2.0, 0.8, 1.2, w["margen"]) for x in m["margen_pct"]]                    # ±10 pp de margen → ±20%
    m["f_quiebre"] = [_f_lineal(x, 0.05, 2.0, 1.0, 1.3, w["quiebre"]) for x in m["pct_skus_quiebre"]]          # quiebres → necesita más
    m["factor"] = m["f_cobertura"] * m["f_obsoleto"] * m["f_margen"] * m["f_quiebre"]
    m["score"] = m["base_venta"] * m["factor"]
    m["reparto_venta"] = (m["base_venta"] * otb_total).round(0)
    capi = m["score"] / m["score"].sum() * otb_total
    if max_delta_pct:
        # Tope por marca (±max_delta_pct vs reparto por venta): evita que una sola marca absorba lo que
        # pierden las demás. Lo que sobra/falta tras el tope se reparte entre las marcas no topadas.
        lo, hi = m["reparto_venta"] * (1 - max_delta_pct / 100), m["reparto_venta"] * (1 + max_delta_pct / 100)
        capi = capi.clip(lo, hi)
        for _ in range(20):
            resto = otb_total - capi.sum()
            if abs(resto) < 1:
                break
            # sobra → a las que aún no tocan el techo; falta → de las que aún no tocan el piso
            libres = (capi < hi - 1e-9) if resto > 0 else (capi > lo + 1e-9)
            if not libres.any():
                break
            capi[libres] = capi[libres] + resto * (capi[libres] / capi[libres].sum())
            capi = capi.clip(lo, hi)
    m["reparto_capi"] = capi.round(0)
    m["reparto_capi_min"] = (m["reparto_capi"] * 0.9).round(0)
    m["reparto_capi_max"] = (m["reparto_capi"] * 1.1).round(0)
    m["delta"] = m["reparto_capi"] - m["reparto_venta"]
    m["delta_pct"] = np.where(m["reparto_venta"] > 0, m["delta"] / m["reparto_venta"] * 100, np.nan)
    m["por_que"] = [_explicar(r, cob_objetivo) for r in m.itertuples(index=False)]
    m["argumento_negociacion"] = [_argumento(r) for r in m.itertuples(index=False)]
    return m.sort_values("reparto_capi", ascending=False).reset_index(drop=True)


def _explicar(r, cob_obj) -> str:
    partes = []
    if r.f_cobertura < 0.97:
        partes.append(f"cobertura {r.cobertura_sem:.0f} sem sobre el objetivo de {cob_obj:.0f}" +
                      (" (pero destallada)" if pd.notna(r.pct_curva_rota) and r.pct_curva_rota >= 0.30 else ""))
    elif r.f_cobertura > 1.03:
        partes.append(f"cobertura {r.cobertura_sem:.0f} sem, bajo el objetivo")
    if r.f_obsoleto < 0.97:
        partes.append(f"{r.pct_obsoleto*100:.0f}% del stock con más de 6 meses")
    if r.f_margen > 1.03:
        partes.append(f"margen {r.margen_pct*100:.0f}% sobre la mediana")
    elif r.f_margen < 0.97:
        partes.append(f"margen {r.margen_pct*100:.0f}% bajo la mediana")
    if r.f_quiebre > 1.03:
        partes.append(f"{r.pct_skus_quiebre*100:.0f}% de SKUs en quiebre")
    return "; ".join(partes) if partes else "en línea con su venta"


def _argumento(r) -> str:
    if r.capital_obsoleto >= 20000 and r.delta < 0:
        return (f"Tiene S/ {r.capital_obsoleto:,.0f} a costo con más de 6 meses. Propuesta: que retire/canjee ese obsoleto y "
                f"el OTB sube de S/ {r.reparto_capi:,.0f} hacia S/ {r.reparto_venta:,.0f}.")
    if pd.notna(r.pct_curva_rota) and r.pct_curva_rota >= 0.30:
        return f"{r.pct_curva_rota*100:.0f}% de sus opciones con curva rota: pedir reposición de tallas antes que estilos nuevos."
    if r.f_quiebre > 1.03:
        return f"{r.pct_skus_quiebre*100:.0f}% de SKUs en quiebre: hay demanda sin atender, priorizar entrega."
    if r.delta > 0:
        return "Gana OTB por rotación y margen: mantener condiciones."
    return "Sin palanca especial este mes."


def ficha_marca(reparto: pd.DataFrame, marca: str) -> dict:
    r = reparto[reparto["marca"].str.upper() == marca.upper()]
    return r.iloc[0].to_dict() if len(r) else {}
