"""
Obsoletos — S5 (2026-09-05, pedido Franco FR8).

Tres piezas sobre el df de cobertura (SKU×tienda) del motor:
1. ranking_por_tienda: capital obsoleto por tienda, de mayor a menor, con % del stock de la tienda.
2. por_entrar: mercadería que cruza a obsoleto en las próximas N semanas (alerta preventiva)
   con el descuento sugerido de la pirámide para atacarla ANTES de que se congele.
3. delta_marca: capital MUERTO por marca en la semana actual vs la anterior (snapshots).

Definiciones (conviven; Franco elige la oficial):
- "rango": rango_antiguedad ∈ {6_9, 9_12, 12_99} → más de 6 meses en tienda, venda o no
  (la que usa hoy la vista Gestión por Antigüedad).
- "taxonomia": estado MUERTO (>26 semanas sin venta) según taxonomia.classify_series.

Sesgo del campo Costo (memoria 2026-08-26): en terceras nacionales `costo` subestima el
margen ~11.7 pp. `capital_implicito` = stock × precio_vigente/1.18 × (1 − margen contable)
se muestra al lado para que el ranking no se lea con un costo que no es el real.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RANGOS_OBSOLETO = {"RANGO 6_9", "RANGO 9_12", "RANGO 12_99"}
UMBRAL_OBSOLETO_SEM = 26          # 6 meses
IGV = 1.18


def _mask_obsoleto(df: pd.DataFrame, definicion: str) -> pd.Series:
    if definicion == "taxonomia":
        return df.get("estado", pd.Series("", index=df.index)).astype(str).eq("MUERTO")
    return df.get("rango_antiguedad", pd.Series("", index=df.index)).isin(RANGOS_OBSOLETO)


def capital_implicito(df: pd.DataFrame, marcas_terceras: set | None = None) -> pd.Series:
    """Capital a costo implícito desde el margen contable (contribución/venta).
    Solo para marcas terceras (donde el campo costo está sesgado); el resto devuelve NaN."""
    if not {"precio_vigente", "margen_efectivo", "stock_total"} <= set(df.columns):
        return pd.Series(np.nan, index=df.index)
    m = pd.to_numeric(df["margen_efectivo"], errors="coerce")
    pv = pd.to_numeric(df["precio_vigente"], errors="coerce")
    stk = pd.to_numeric(df["stock_total"], errors="coerce").fillna(0)
    ci = stk * (pv / IGV) * (1 - m.clip(0, 0.95))
    if marcas_terceras is not None and "marca" in df.columns:
        es_t = df["marca"].astype(str).str.upper().str.strip().isin({x.upper() for x in marcas_terceras})
        ci = ci.where(es_t)
    return ci


def ranking_por_tienda(df_cob: pd.DataFrame, definicion: str = "rango",
                       marcas_terceras: set | None = None) -> pd.DataFrame:
    """Capital obsoleto por tienda, ordenado desc. Columnas:
    tienda, capital_obsoleto, uds_obsoletas, skus_obsoletos, capital_tienda, pct_stock_tienda,
    capital_implicito (terceras), marca_top (la que más pesa en el obsoleto de esa tienda)."""
    if df_cob is None or df_cob.empty or "tienda" not in df_cob.columns:
        return pd.DataFrame()
    d = df_cob.copy()
    d["capital"] = pd.to_numeric(d.get("stock_valor_costo"), errors="coerce").fillna(0)
    d["stock"] = pd.to_numeric(d.get("stock_total"), errors="coerce").fillna(0)
    d["_obs"] = _mask_obsoleto(d, definicion)
    d["_ci"] = capital_implicito(d, marcas_terceras)
    tot = d.groupby("tienda")["capital"].sum().rename("capital_tienda")
    o = d[d["_obs"]]
    if o.empty:
        return pd.DataFrame()
    g = o.groupby("tienda").agg(capital_obsoleto=("capital", "sum"), uds_obsoletas=("stock", "sum"),
                                skus_obsoletos=("sku", "nunique"), capital_implicito=("_ci", "sum"))
    if "marca" in o.columns:
        top = (o.groupby(["tienda", "marca"])["capital"].sum().reset_index()
                .sort_values("capital", ascending=False).drop_duplicates("tienda").set_index("tienda")["marca"])
        g["marca_top"] = top
    g = g.join(tot)
    g["pct_stock_tienda"] = (g["capital_obsoleto"] / g["capital_tienda"]).where(g["capital_tienda"] > 0)
    g["capital_implicito"] = g["capital_implicito"].replace(0, np.nan)
    return g.sort_values("capital_obsoleto", ascending=False).reset_index()


def por_entrar(df_cob: pd.DataFrame, semanas: int = 2, definicion: str = "rango",
               solo_sin_venta: bool | None = None, margen_min: float = 0.10) -> pd.DataFrame:
    """Mercadería que cruza el umbral de obsoleto (26 semanas) dentro de `semanas`.

    - definicion "rango": cualquier SKU×tienda con 26−N < edad ≤ 26 que aún no está en rango obsoleto.
    - definicion "taxonomia": lo mismo pero solo sin venta (prom_vta_uds == 0), que es lo que MUERTO exige.
    Devuelve filas con semanas_para_obsoleto, capital, descuento sugerido (pirámide) y precio sugerido.
    """
    from pricing import descuento_sugerido, precio_piso
    if df_cob is None or df_cob.empty or "edad_semanas" not in df_cob.columns:
        return pd.DataFrame()
    d = df_cob.copy()
    edad = pd.to_numeric(d["edad_semanas"], errors="coerce")
    d["capital"] = pd.to_numeric(d.get("stock_valor_costo"), errors="coerce").fillna(0)
    ya = _mask_obsoleto(d, definicion)
    m = (edad > UMBRAL_OBSOLETO_SEM - semanas) & (edad <= UMBRAL_OBSOLETO_SEM) & ~ya & (d["capital"] > 0)
    if solo_sin_venta is None:
        solo_sin_venta = definicion == "taxonomia"
    if solo_sin_venta and "prom_vta_uds" in d.columns:
        m &= pd.to_numeric(d["prom_vta_uds"], errors="coerce").fillna(0) == 0
    o = d[m].copy()
    if o.empty:
        return pd.DataFrame()
    o["semanas_para_obsoleto"] = (UMBRAL_OBSOLETO_SEM - edad[m]).clip(lower=0).round(0)
    pv = pd.to_numeric(o.get("precio_vigente"), errors="coerce")
    costo = pd.to_numeric(o.get("costo"), errors="coerce")
    dsc_act = pd.to_numeric(o.get("pct_descuento"), errors="coerce").fillna(0)
    sug = []
    for e, p, c, da in zip(edad[m], pv, costo, dsc_act):
        dsc, tipo = descuento_sugerido(float(e) if pd.notna(e) else 0)
        # el descuento preventivo es el de la pirámide AL CRUZAR (edad 26) si es mayor que el actual
        dsc_cruce, _ = descuento_sugerido(UMBRAL_OBSOLETO_SEM)
        dsc_obj = max(dsc, dsc_cruce)
        if pd.notna(p) and p > 0:
            piso = precio_piso(float(c), margen_min) if pd.notna(c) and c > 0 else 0.0
            precio_sug = max(round(p * (1 - dsc_obj), 2), piso)
            dsc_real = round(1 - precio_sug / p, 3)
        else:
            precio_sug, dsc_real = np.nan, np.nan
        sug.append((dsc_obj, precio_sug, dsc_real, "ya está" if da >= dsc_obj else "subir"))
    o["dscto_sugerido"] = [x[0] for x in sug]
    o["precio_sugerido"] = [x[1] for x in sug]
    o["dscto_real"] = [x[2] for x in sug]
    o["accion"] = [("✅ Descuento ya aplicado" if x[3] == "ya está" else "💰 Subir descuento antes de que cruce") for x in sug]
    cols = [c for c in ["tienda", "marca", "sku", "nombre", "categoria", "edad_semanas", "semanas_para_obsoleto",
                        "stock_total", "capital", "prom_vta_uds", "cobertura_sem", "precio_vigente", "pct_descuento",
                        "dscto_sugerido", "precio_sugerido", "dscto_real", "accion"] if c in o.columns]
    return o[cols].sort_values(["semanas_para_obsoleto", "capital"], ascending=[True, False]).reset_index(drop=True)


def resumen_por_entrar(df_pe: pd.DataFrame) -> pd.DataFrame:
    """Por marca: capital que cruza, uds, SKUs, y capital que aún no tiene el descuento sugerido."""
    if df_pe is None or df_pe.empty:
        return pd.DataFrame()
    g = df_pe.groupby("marca").agg(capital=("capital", "sum"), uds=("stock_total", "sum"),
                                   skus=("sku", "nunique"),
                                   capital_sin_dscto=("capital", lambda x: x[df_pe.loc[x.index, "accion"].str.startswith("💰")].sum()))
    return g.sort_values("capital", ascending=False).reset_index()


def delta_marca(sem_a: str, sem_b: str, estados=("MUERTO",)) -> pd.DataFrame:
    """Capital en `estados` por marca en sem_a vs sem_b (snapshots, nivel cadena)."""
    from analisis_estados import _capital_por_estado_marca
    a, b = _capital_por_estado_marca(sem_a), _capital_por_estado_marca(sem_b)
    if a.empty or b.empty:
        return pd.DataFrame()
    a = a[a["estado"].isin(estados)].groupby("marca")["stock_valor_costo"].sum().rename(sem_a)
    b = b[b["estado"].isin(estados)].groupby("marca")["stock_valor_costo"].sum().rename(sem_b)
    out = pd.concat([a, b], axis=1).fillna(0)
    out["delta"] = out[sem_b] - out[sem_a]
    out["delta_pct"] = (out["delta"] / out[sem_a].replace(0, np.nan)) * 100
    return out.sort_values("delta", ascending=False).reset_index()
