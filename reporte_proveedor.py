"""
reporte_proveedor.py — motor del "Reporte semanal al proveedor" (marcas terceras).

Decisión 2026-09-18 (Franco): un correo + un Excel por marca, tres frentes:
  B1  VENTA CERO        modelos sin venta en toda la cadena la última semana (ranking Pareto 80%)
  B2a SOBRESTOCK        modelos que venden pero cargan de más (estado de CADENA) → markdown 50/50,
                        canje/devolución o frenar ingreso
  B2b DESBALANCE        modelos con transferencias entre tiendas rentables (≥12 uds, ganancia > 0)
  B3  GANADORES CORTOS  buena rotación y poca cobertura → reponer desde CD o reorden al proveedor

Todo es pandas puro: sin Streamlit, sin Claude, sin I/O. El correo, el Excel y el dict
`hechos` son PROYECCIONES de los mismos DataFrames; nada recalcula un total.

Definiciones fijadas por el AUDIT con data real (Base al 30.08, ver plan §7d):
  - B1 a nivel cadena y última semana (`vta_sem1_total == 0`): a nivel tienda el 95% del
    catálogo tenía alguna tienda en cero (ilegible); la variante elegida da 61 SKUs en
    John Holden y se reproduce 1:1 desde tienda.parquet para el comparativo semanal.
  - Estado de B2 clasificado a nivel cadena (`taxonomia.classify_series` sobre cobertura de
    cadena): el 82% de los SKUs tiene 2+ estados según la tienda; el proveedor acciona por modelo.
  - Precedencia B1 > B2a > B3: un SKU aparece en un solo bloque.
  - Transferencias son desbalance entre tiendas, no sobrestock de cadena (0 de 41 SKUs de
    sobrestock de cadena las tenían) → sub-bloque propio 2b, cualquier estado.
  - Precio sugerido = `reportes_marcas._con_sugerencias` (pirámide sobre precio blanco, piso de
    margen, nunca subir), la misma función de la hoja "2. Activar", para no tener dos números.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import agente_terceras
import reportes_marcas
import taxonomia
import vistas_excel

# ── Parámetros (calibrables; medidos en el AUDIT 2026-09-19) ─────────────────
PARETO = 0.80
ESTADOS_B2 = ("SOBRESTOCK", "ESTANCADO", "PRE-OBSOLETO", "OBSOLETO")
ESTADOS_LIQUIDACION = ("PRE-OBSOLETO", "OBSOLETO")
ESTADOS_QUIEBRE = ("QUIEBRE", "PRE-QUIEBRE")
B3_PCT_VENTA = 0.75            # percentil de venta entre los SKUs que venden
B3_MIN_VTA = 2.0               # u/sem mínimas para ser "ganador"
B3_MIN_SELLERS = 4             # con menos SKUs vendiendo, el percentil no significa nada
B3_COB_MAX = 8.0               # semanas (límite de PRE-QUIEBRE)
B3_ALERTAS_ENTRADA = ("ACELERANDO", "RIESGO QUIEBRE")
EDAD_LIQUIDAR = 26             # semanas: venta cero con esta edad ya no es "exhibición", es liquidar
TOP_CUERPO_LINEA = 5           # modelos por línea en el cuerpo del correo (B1, B2a)
TOP_CUERPO_PLANO = 10          # modelos en el cuerpo (B2b, B3)

_TENDENCIA = {"ACELERANDO": "▲", "FRENANDO": "▼", "SE DETUVO": "▼", "RIESGO QUIEBRE": "⚡", "SIN TRACCIÓN": "🆕"}


# ── Utilitarios ───────────────────────────────────────────────────────────────
def sku_key(s) -> str:
    """Clave de SKU comparable entre df_cob (int), df_vp (str) y lotes (str sin ceros)."""
    return str(s).strip().lstrip("0")


def pareto_flag(capital: pd.Series, umbral: float = PARETO) -> tuple[pd.Series, pd.Series]:
    """(pct_acum, top_80) alineados al índice de `capital`. Misma regla que
    vistas_excel.venta_cero: acumulado ANTES de la fila < umbral → la primera fila
    siempre es TOP; round(6) evita el borde flotante."""
    if capital.empty:
        return capital.astype(float), capital.astype(bool)
    s = capital.fillna(0).astype(float)
    orden = s.sort_values(ascending=False, kind="mergesort")
    tot = orden.sum()
    if tot <= 0:
        z = pd.Series(0.0, index=s.index)
        return z, pd.Series(False, index=s.index)
    acum = orden.cumsum() / tot
    share = orden / tot
    top = (acum - share).round(6) < umbral
    return acum.reindex(s.index), top.reindex(s.index)


def slice_marca(df_cob: pd.DataFrame, marca: str) -> pd.DataFrame:
    m = str(marca).upper().strip()
    return df_cob[df_cob["marca"].astype(str).str.upper().str.strip() == m].copy()


def _col(df: pd.DataFrame, name: str, default=0.0) -> pd.Series:
    return df[name] if name in df.columns else pd.Series(default, index=df.index)


# ── Agregado por SKU (una fila por modelo, estado de cadena) ──────────────────
def por_sku(dfm: pd.DataFrame) -> pd.DataFrame:
    """Colapsa SKU×tienda → 1 fila por SKU con métricas de cadena.

    Parte de vistas_excel.agregar_por_sku (venta prom 4 sem y cobertura de cadena,
    la misma base de la hoja 2 del reporte por marca) y agrega:
      estado_cadena  taxonomia sobre cobertura_cadena + edad + rango (como el snapshot)
      vta_sem1       venta de la ÚLTIMA semana en toda la cadena (vta_sem1_total)
      n_tiendas_stock / n_tiendas_quiebre / stock_cd / tiendas_sin_venta
    """
    if dfm.empty:
        return pd.DataFrame()
    g = vistas_excel.agregar_por_sku(dfm)
    # agregar_por_sku agrupa por (estado, sku): colapsar a 1 fila por SKU
    agg = {"nombre": "first", "marca": "first", "stock_cadena": "sum", "n_tiendas": "sum",
           "capital_costo": "sum", "edad_semanas": "max", "vta_sem_prom4": "first"}
    for c in ("categoria", "temporada", "precio_blanco", "precio_vigente", "costo"):
        if c in g.columns:
            agg[c] = "first"
    if "pct_descuento" in g.columns:
        agg["pct_descuento"] = "max"
    g1 = g.groupby("sku", as_index=False).agg(agg)
    g1["cobertura_cadena"] = np.where(g1["vta_sem_prom4"] > 0,
                                      (g1["stock_cadena"] / g1["vta_sem_prom4"]).round(1), np.nan)
    por = dfm.groupby("sku")
    extra = pd.DataFrame({
        "vta_sem1": por[ "vta_sem1_total"].first() if "vta_sem1_total" in dfm.columns else por["prom_vta_uds"].sum(),
        "n_tiendas_stock": dfm[dfm["stock_total"] > 0].groupby("sku")["tienda"].nunique(),
        "n_tiendas_quiebre": dfm.assign(_q=dfm["estado"].isin(ESTADOS_QUIEBRE)).groupby("sku")["_q"].sum(),
        "stock_cd": por["stock_cd"].first() if "stock_cd" in dfm.columns else 0,
        "rango_antiguedad": por["rango_antiguedad"].first() if "rango_antiguedad" in dfm.columns else None,
    })
    g1 = g1.merge(extra, left_on="sku", right_index=True, how="left")
    g1["n_tiendas_stock"] = g1["n_tiendas_stock"].fillna(0).astype(int)
    g1["n_tiendas_quiebre"] = g1["n_tiendas_quiebre"].fillna(0).astype(int)
    g1["pct_tiendas_quiebre"] = np.where(g1["n_tiendas"] > 0, g1["n_tiendas_quiebre"] / g1["n_tiendas"], 0.0)
    g1["estado_cadena"] = taxonomia.classify_series(g1["cobertura_cadena"], g1["edad_semanas"], g1["rango_antiguedad"])
    g1["fuente_vta_sem1"] = "vta_sem1_total" if "vta_sem1_total" in dfm.columns else "prom_vta_uds"
    return g1


def _con_precio(g: pd.DataFrame, precio_min_map: dict | None) -> pd.DataFrame:
    """Pirámide + piso + nunca subir, con la MISMA función de la hoja '2. Activar'."""
    if g.empty:
        for c in ("dscto_sugerido", "precio_sugerido", "precio_minimo", "margen_resultante", "accion"):
            g[c] = pd.Series(dtype=float)
        return g
    out = reportes_marcas._con_sugerencias(g, precio_min_map or {})
    return out.rename(columns={"accion": "accion_precio"})


def _tendencia(g: pd.DataFrame, df_alertas: pd.DataFrame | None) -> pd.Series:
    if df_alertas is None or df_alertas.empty or "tipo_alerta" not in df_alertas.columns:
        return pd.Series("—", index=g.index)
    a = df_alertas.drop_duplicates("sku").set_index("sku")["tipo_alerta"].astype(str)
    t = g["sku"].map(a)
    def _map(v):
        if not isinstance(v, str):
            return "—"
        for k, sym in _TENDENCIA.items():
            if k in v:
                return sym
        return "—"
    return t.map(_map)


# ── Bloque 1: venta cero de cadena, última semana ─────────────────────────────
def bloque_venta_cero(g: pd.DataFrame, dfm: pd.DataFrame, precio_min_map: dict | None = None,
                      tipo_evento_map: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(b1, vc_tienda). b1 = SKUs con stock y SIN venta en toda la cadena la última semana,
    Pareto 80% del capital dentro de la marca. vc_tienda = detalle por tienda del módulo
    📲 (vistas_excel.venta_cero) para la ejecución en piso — se adjunta, no se rankea."""
    vc_tienda = vistas_excel.venta_cero(dfm, min_capital=0, tipo_evento_map=tipo_evento_map)
    if g.empty:
        return g, vc_tienda
    b1 = g[(g["vta_sem1"].fillna(0) <= 0) & (g["stock_cadena"] > 0)].copy()
    b1 = _con_precio(b1, precio_min_map)
    if b1.empty:
        return b1, vc_tienda
    b1["pct_acum"], b1["top_80"] = pareto_flag(b1["capital_costo"])
    b1["semanas_sin_venta"] = np.where(b1["vta_sem_prom4"] <= 0, "4+", "1")  # 4 sem sin venta vs solo la última
    def _acc(r):
        p = r.get("precio_sugerido")
        if r["edad_semanas"] >= EDAD_LIQUIDAR:
            return (f"🏷️ Liquidar: cofinanciar {r['dscto_sugerido']:.0%} → S/ {p:,.2f}" if pd.notna(p)
                    else "↩️ Canje / devolución (ya en piso de precio)")
        if r["estado_cadena"] == "NUEVO SIN VENTA":
            return "👁️ Revisar exhibición (lanzamiento sin arranque)"
        if pd.notna(p):
            return f"👁️ Exhibición + cofinanciar {r['dscto_sugerido']:.0%} → S/ {p:,.2f}"
        return "👁️ Revisar exhibición / comunicación de precio"
    b1["accion"] = b1.apply(_acc, axis=1)
    b1 = b1.sort_values(["top_80", "capital_costo"], ascending=[False, False]).reset_index(drop=True)
    return b1, vc_tienda


# ── Bloque 2a: sobrestock de cadena · 2b: desbalance entre tiendas ────────────
def bloque_sobrestock(g: pd.DataFrame, excluir: set, df_trans: pd.DataFrame | None = None,
                      precio_min_map: dict | None = None,
                      df_alertas: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(b2a, b2b).
    b2a: estado de CADENA ∈ ESTADOS_B2 con venta, menos los SKUs de B1. Acción primaria:
         markdown cofinanciado (si la pirámide deja bajar) → canje/devolución (viejo, estancado
         o ya en piso) → frenar ingreso (compra reciente sobredimensionada, dscto ya ≥ pirámide).
    b2b: transferencias rentables por modelo (reportes_marcas.transferencias_por_sku), cualquier
         estado. La ejecuta el proveedor (habilitadas para terceras, precisión Franco 18-sep)."""
    vacio = pd.DataFrame()
    if g.empty:
        return vacio, vacio
    b2 = g[g["estado_cadena"].isin(ESTADOS_B2) & (g["vta_sem_prom4"] > 0) & ~g["sku"].isin(excluir)].copy()
    b2 = _con_precio(b2, precio_min_map)
    if not b2.empty:
        b2["grupo"] = np.where(b2["estado_cadena"].isin(ESTADOS_LIQUIDACION), "Liquidación", "Sobrestock")
        b2["tendencia"] = _tendencia(b2, df_alertas)
        def _acc(r):
            p = r.get("precio_sugerido")
            viejo = (r["estado_cadena"] in ("ESTANCADO",) + ESTADOS_LIQUIDACION) or r["edad_semanas"] >= EDAD_LIQUIDAR
            en_piso = str(r.get("accion_precio", "")).startswith("✋")
            alts = []
            if pd.notna(p):
                acc = f"⬇️ Markdown cofinanciado 50/50: {r['dscto_sugerido']:.0%} → S/ {p:,.2f}"
                if viejo:
                    alts.append("canje / devolución con recompra")
            elif viejo or en_piso:
                acc = "↩️ Canje / devolución con recompra"
            else:
                acc = "⏸️ Frenar ingreso / no reponer (dscto ya en pirámide)"
                alts.append("canje si no rota en 4 semanas")
            return pd.Series({"accion": acc, "alternativas": " · ".join(alts)})
        b2 = pd.concat([b2, b2.apply(_acc, axis=1)], axis=1)
        b2["pct_acum"], b2["top_80"] = pareto_flag(b2["capital_costo"])
        b2 = b2.sort_values(["top_80", "capital_costo"], ascending=[False, False]).reset_index(drop=True)
    # 2b
    tr = reportes_marcas.transferencias_por_sku(df_trans, g["sku"]) if df_trans is not None else vacio
    if tr is None or tr.empty:
        b2b = vacio
    else:
        cols = ["sku", "categoria", "estado_cadena", "stock_cadena", "cobertura_cadena", "capital_costo", "n_tiendas_quiebre", "n_tiendas"]
        b2b = tr.merge(g[[c for c in cols if c in g.columns]], on="sku", how="left")
        b2b["accion"] = b2b.apply(lambda r: f"🔄 Mover {int(r['transf_uds'])} uds a {int(r['transf_tiendas'])} tienda(s)"
                                  + (f" · ganancia neta S/ {r['transf_ganancia']:,.0f}" if pd.notna(r.get('transf_ganancia')) else ""), axis=1)
        b2b = b2b.reset_index(drop=True)
    return b2, b2b


# ── Bloque 3: ganadores que se quedan cortos ──────────────────────────────────
def bloque_ganadores(g: pd.DataFrame, excluir: set, df_rep: pd.DataFrame | None = None,
                     df_vp: pd.DataFrame | None = None, df_alertas: pd.DataFrame | None = None,
                     marca: str = "") -> tuple[pd.DataFrame, float]:
    """(b3, umbral). Entra si vende ≥ umbral (max(B3_MIN_VTA, percentil 75 de los que venden))
    y (cobertura de cadena ≤ B3_COB_MAX o alerta ▲/⚡ del motor). Excluye B1 y B2a.
    Enriquece con la necesidad del motor de reposición (df_rep) y la venta perdida (df_vp)."""
    if g.empty:
        return pd.DataFrame(), B3_MIN_VTA
    sell = g[(g["vta_sem_prom4"] > 0) & ~g["sku"].isin(excluir)]
    umbral = float(max(B3_MIN_VTA, sell["vta_sem_prom4"].quantile(B3_PCT_VENTA))) if len(sell) >= B3_MIN_SELLERS else B3_MIN_VTA
    tend = _tendencia(g, df_alertas)
    por_alerta = tend.isin(["▲", "⚡"])
    crit = (g["vta_sem_prom4"] >= umbral) & ((g["cobertura_cadena"] <= B3_COB_MAX) | por_alerta) & ~g["sku"].isin(excluir)
    b3 = g[crit].copy()
    if b3.empty:
        return b3, umbral
    b3["tendencia"] = tend[b3.index]
    b3["entra_por"] = np.where(b3["cobertura_cadena"] <= B3_COB_MAX, "cobertura", "tendencia")
    # necesidad del motor de reposición
    for c in ("necesidad_uds", "desde_cd_uds", "pendiente_sin_cd_uds"):
        b3[c] = np.nan
    if df_rep is not None and not df_rep.empty and "a_reponer" in df_rep.columns:
        rp = df_rep[df_rep["sku"].isin(b3["sku"])]
        agg = {"necesidad_uds": ("a_reponer", "sum")}
        if "desde_cd" in rp.columns: agg["desde_cd_uds"] = ("desde_cd", "sum")
        if "pendiente" in rp.columns: agg["pendiente_sin_cd_uds"] = ("pendiente", "sum")
        r = rp.groupby("sku").agg(**agg)
        for c in r.columns:
            b3[c] = b3["sku"].map(r[c])
    # venta perdida de la semana (SKU×tienda → por SKU)
    for c in ("vp_neto_min", "vp_neto_max", "sem_en_quiebre_max", "on_order", "vp_evitable"):
        b3[c] = np.nan
    if df_vp is not None and not df_vp.empty and "sku" in df_vp.columns:
        v = df_vp.copy()
        if "marca" in v.columns and marca:
            v = v[v["marca"].astype(str).str.upper().str.strip() == marca.upper().strip()]
        v["_k"] = v["sku"].map(sku_key)
        agg = {}
        if "neto_min" in v: agg["vp_neto_min"] = ("neto_min", "sum")
        if "neto_max" in v: agg["vp_neto_max"] = ("neto_max", "sum")
        if "semanas_en_quiebre" in v: agg["sem_en_quiebre_max"] = ("semanas_en_quiebre", "max")
        if "on_order" in v: agg["on_order"] = ("on_order", "max")
        if "evitable" in v: agg["vp_evitable"] = ("evitable", "max")
        if agg:
            r = v.groupby("_k").agg(**agg)
            k = b3["sku"].map(sku_key)
            for c in r.columns:
                b3[c] = k.map(r[c]).values
    b3["requiere_proveedor"] = b3["stock_cd"].fillna(0) <= 0
    def _acc(r):
        if r["requiere_proveedor"]:
            base = "🏭 Reorden / compra al proveedor (sin stock en CD)"
        else:
            base = f"📦 Reponer desde CD ({int(r['stock_cd']):,} uds en CD)"
        if pd.notna(r.get("pendiente_sin_cd_uds")) and r["pendiente_sin_cd_uds"] > 0:
            base += f" · faltan {int(r['pendiente_sin_cd_uds']):,} uds sin CD"
        return base
    b3["accion"] = b3.apply(_acc, axis=1)
    b3 = b3.sort_values("vta_sem_prom4", ascending=False).reset_index(drop=True)
    return b3, umbral


# ── Foto de la marca (contexto del correo) ────────────────────────────────────
def foto_marca(dfm: pd.DataFrame, g: pd.DataFrame) -> dict:
    cap = float(dfm["stock_valor_costo"].sum())
    vta = float(dfm["prom_vta_uds"].fillna(0).sum()); stk = float(dfm["stock_total"].fillna(0).sum())
    st = round(vta / (vta + stk) * 100, 1) if (vta + stk) > 0 else 0.0
    mg = None
    if {"vta_soles_4sem", "contrib_soles_4sem"}.issubset(dfm.columns):
        s = dfm.drop_duplicates("sku")
        v = float(s["vta_soles_4sem"].fillna(0).sum()); c = float(s["contrib_soles_4sem"].fillna(0).sum())
        mg = round(c / v * 100, 1) if v > 0 else None
    return {"capital_total": round(cap), "skus": int(dfm["sku"].nunique()), "tiendas": int(dfm["tienda"].nunique()),
            "stock_uds": int(stk), "sell_through_pct": st, "margen_efectivo_pct": mg,
            "por_estado_cadena": g.groupby("estado_cadena")["capital_costo"].sum().round().to_dict() if not g.empty else {}}


# ── Hechos: todo número que el correo puede citar ─────────────────────────────
def _top(df: pd.DataFrame, cols: list, n: int, orden: str) -> list:
    if df.empty:
        return []
    d = df.sort_values(orden, ascending=False).head(n)
    out = []
    for _, r in d.iterrows():
        item = {}
        for c in cols:
            if c not in d.columns:
                continue
            v = r[c]
            if isinstance(v, (np.floating, float)):
                v = None if pd.isna(v) else (round(float(v), 1) if c.startswith("cob") or c.startswith("vta") else round(float(v)))
            elif isinstance(v, (np.integer,)):
                v = int(v)
            elif isinstance(v, (np.bool_, bool)):
                v = bool(v)
            item[c] = v
        out.append(item)
    return out


def hechos_marca(marca: str, foto: dict, b1, b2a, b2b, b3, umbral_b3: float, corte: str, semana_iso: str, top_n: int = 5) -> dict:
    def _s(df, c): return round(float(df[c].fillna(0).sum())) if (not df.empty and c in df.columns) else 0
    h = {"marca": marca, "corte": corte, "semana_iso": semana_iso, "foto": foto}
    h["b1"] = {"n_skus": int(len(b1)), "stock_uds": _s(b1, "stock_cadena"), "capital": _s(b1, "capital_costo"),
               "n_top": int(b1["top_80"].sum()) if not b1.empty else 0,
               "capital_top": round(float(b1.loc[b1["top_80"], "capital_costo"].sum())) if not b1.empty else 0,
               "n_liquidar": int((b1["edad_semanas"] >= EDAD_LIQUIDAR).sum()) if not b1.empty else 0,
               "n_4sem": int((b1["semanas_sin_venta"] == "4+").sum()) if not b1.empty else 0,
               "top": _top(b1, ["sku", "nombre", "categoria", "n_tiendas_stock", "stock_cadena", "capital_costo", "edad_semanas", "accion"], top_n, "capital_costo")}
    h["b1"]["pct_capital_marca"] = round(h["b1"]["capital"] / foto["capital_total"] * 100, 1) if foto.get("capital_total") else 0.0
    def _por_linea(df):
        if df.empty or "categoria" not in df.columns:
            return {}
        s = df.groupby(df["categoria"].fillna("Sin línea"))["capital_costo"].sum().round().sort_values(ascending=False)
        return {str(k): int(v) for k, v in s.items()}
    h["b1"]["por_linea"] = _por_linea(b1)
    h["b2a"] = {"n_skus": int(len(b2a)), "stock_uds": _s(b2a, "stock_cadena"), "capital": _s(b2a, "capital_costo"),
                "n_top": int(b2a["top_80"].sum()) if not b2a.empty else 0,
                "n_markdown": int(b2a["accion"].str.startswith("⬇️").sum()) if not b2a.empty else 0,
                "n_canje": int(b2a["accion"].str.startswith("↩️").sum()) if not b2a.empty else 0,
                "n_frenar": int(b2a["accion"].str.startswith("⏸️").sum()) if not b2a.empty else 0,
                "n_liquidacion": int((b2a["grupo"] == "Liquidación").sum()) if not b2a.empty else 0,
                "cobertura_prom": round(float(b2a["cobertura_cadena"].mean()), 1) if not b2a.empty else None,
                "por_linea": _por_linea(b2a),
                "top": _top(b2a, ["sku", "nombre", "categoria", "estado_cadena", "stock_cadena", "cobertura_cadena", "capital_costo", "accion"], top_n, "capital_costo")}
    h["b2b"] = {"n_skus": int(len(b2b)), "uds": _s(b2b, "transf_uds"), "ganancia": _s(b2b, "transf_ganancia"),
                "top": _top(b2b, ["sku", "nombre", "transf_uds", "transf_tiendas", "transf_ganancia"], top_n, "transf_uds")}
    h["b3"] = {"n_skus": int(len(b3)), "umbral_vta": round(umbral_b3, 1), "vta_sem_total": round(float(b3["vta_sem_prom4"].sum()), 1) if not b3.empty else 0.0,
               "n_sin_cd": int(b3["requiere_proveedor"].sum()) if not b3.empty else 0,
               "necesidad_uds": _s(b3, "necesidad_uds"), "pendiente_sin_cd_uds": _s(b3, "pendiente_sin_cd_uds"),
               "vp_neto_min": _s(b3, "vp_neto_min") if not b3.empty and b3["vp_neto_min"].notna().any() else None,
               "vp_neto_max": _s(b3, "vp_neto_max") if not b3.empty and b3["vp_neto_max"].notna().any() else None,
               "top": _top(b3, ["sku", "nombre", "categoria", "vta_sem_prom4", "cobertura_cadena", "n_tiendas_quiebre", "n_tiendas", "stock_cd", "accion"], top_n, "vta_sem_prom4")}
    return h


# ── Orquestador ───────────────────────────────────────────────────────────────
def bloques_marca(marca: str, df_cob: pd.DataFrame, df_trans: pd.DataFrame | None = None,
                  df_vp: pd.DataFrame | None = None, df_prec: pd.DataFrame | None = None,
                  df_rep: pd.DataFrame | None = None, df_alertas: pd.DataFrame | None = None,
                  corte: str | None = None, tipo_evento_map: dict | None = None,
                  semana_iso: str = "") -> dict:
    """Devuelve {marca, corte, semana_iso, g, b1, b2a, b2b, b3, vc_tienda, umbral_b3, hechos}."""
    from datetime import date
    corte = corte or f"{date.today():%d.%m.%Y}"
    dfm = slice_marca(df_cob, marca)
    g = por_sku(dfm)
    precio_min_map = {}
    if df_prec is not None and not df_prec.empty and "precio_minimo" in df_prec.columns:
        pm = df_prec[df_prec["sku"].isin(dfm["sku"])]
        precio_min_map = pm.drop_duplicates("sku").set_index("sku")["precio_minimo"].to_dict()
    b1, vc_tienda = bloque_venta_cero(g, dfm, precio_min_map, tipo_evento_map)
    ex = set(b1["sku"]) if not b1.empty else set()
    b2a, b2b = bloque_sobrestock(g, ex, df_trans, precio_min_map, df_alertas)
    ex2 = ex | (set(b2a["sku"]) if not b2a.empty else set())
    b3, umbral = bloque_ganadores(g, ex2, df_rep, df_vp, df_alertas, marca=marca)
    foto = foto_marca(dfm, g) if not dfm.empty else {"capital_total": 0, "skus": 0, "tiendas": 0, "stock_uds": 0,
                                                     "sell_through_pct": 0.0, "margen_efectivo_pct": None, "por_estado_cadena": {}}
    h = hechos_marca(marca, foto, b1, b2a, b2b, b3, umbral, corte, semana_iso)
    return {"marca": marca, "corte": corte, "semana_iso": semana_iso, "g": g, "dfm": dfm,
            "b1": b1, "b2a": b2a, "b2b": b2b, "b3": b3, "vc_tienda": vc_tienda, "umbral_b3": umbral, "hechos": h}


def detalle_lote(bloques: dict) -> pd.DataFrame:
    """Lo que se le manda al proveedor en venta cero, a grano SKU×tienda (todas las tiendas
    con stock de los modelos de B1), en el formato de acciones_log.COLS_DETALLE. Es el
    'pedido medible': kpi_venta_cero.activacion_lote mide la semana siguiente si vendió."""
    b1, dfm = bloques["b1"], bloques["dfm"]
    if b1.empty or dfm.empty:
        return pd.DataFrame(columns=["sku", "tienda", "uds", "marca", "nombre", "categoria"])
    d = dfm[dfm["sku"].isin(b1["sku"]) & (dfm["stock_total"] > 0)]
    out = d[["sku", "tienda", "stock_total", "marca", "nombre"] + (["categoria"] if "categoria" in d.columns else [])].copy()
    return out.rename(columns={"stock_total": "uds"}).reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
#  SALIDAS: tablas del correo (texto y HTML) y Excel — proyecciones de los bloques
# ══════════════════════════════════════════════════════════════════════════════
def _s(v, dec=0) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v:,.{dec}f}"


def _linea(v) -> str:
    return str(v) if isinstance(v, str) and v.strip() else "Sin línea"


def _filas_b1(b1: pd.DataFrame) -> list[dict]:
    return [{"SKU": r["sku"], "Producto": r["nombre"], "Línea": _linea(r.get("categoria")), "Tiendas": int(r["n_tiendas_stock"]),
             "Stock uds": int(r["stock_cadena"]), "Capital S/": float(r["capital_costo"]), "Edad sem": int(r["edad_semanas"]),
             "⭐": "⭐ TOP 80%" if r["top_80"] else "", "Acción": r["accion"]} for _, r in b1.iterrows()]


def _filas_b2a(b2a: pd.DataFrame) -> list[dict]:
    return [{"SKU": r["sku"], "Producto": r["nombre"], "Línea": _linea(r.get("categoria")), "Estado": r["estado_cadena"],
             "Stock uds": int(r["stock_cadena"]), "Cob sem": float(r["cobertura_cadena"]) if pd.notna(r["cobertura_cadena"]) else None,
             "Capital S/": float(r["capital_costo"]), "Dscto hoy": float(r.get("pct_descuento") or 0), "Tend": r.get("tendencia", "—"),
             "⭐": "⭐ TOP 80%" if r["top_80"] else "", "Acción": r["accion"]} for _, r in b2a.iterrows()]


def _filas_b2b(b2b: pd.DataFrame) -> list[dict]:
    return [{"SKU": r["sku"], "Producto": r["nombre"], "Uds a mover": int(r["transf_uds"]), "Tiendas destino": int(r["transf_tiendas"]),
             "Ganancia neta S/": float(r["transf_ganancia"]) if pd.notna(r.get("transf_ganancia")) else None} for _, r in b2b.iterrows()]


def _filas_b3(b3: pd.DataFrame) -> list[dict]:
    return [{"SKU": r["sku"], "Producto": r["nombre"], "Línea": _linea(r.get("categoria")), "Vta/sem": float(r["vta_sem_prom4"]),
             "Cob sem": float(r["cobertura_cadena"]) if pd.notna(r["cobertura_cadena"]) else None,
             "Tiendas en quiebre": f"{int(r['n_tiendas_quiebre'])} de {int(r['n_tiendas'])}", "Stock CD": int(r["stock_cd"] or 0),
             "Necesidad uds": int(r["necesidad_uds"]) if pd.notna(r.get("necesidad_uds")) else None,
             "Tend": r.get("tendencia", "—"), "Acción": r["accion"]} for _, r in b3.iterrows()]


def _grupos_por_linea(filas: list[dict], tope: int) -> list[tuple[str, float, int, list[dict]]]:
    """[(línea, capital_línea, n_total, filas_top)] ordenado por capital desc; ⭐ primero dentro de la línea."""
    df = pd.DataFrame(filas)
    if df.empty:
        return []
    out = []
    for lin, grp in df.groupby("Línea", sort=False):
        grp = grp.sort_values(["⭐", "Capital S/"], ascending=[False, False]) if "⭐" in grp else grp.sort_values("Capital S/", ascending=False)
        out.append((lin, float(grp["Capital S/"].sum()), len(grp), grp.head(tope).to_dict("records")))
    return sorted(out, key=lambda x: -x[1])


def _tabla_txt(rows: list[dict], cols: list[str], fmt: dict) -> str:
    if not rows:
        return "  (sin modelos)"
    cells = [[str(c) for c in cols]]
    for r in rows:
        cells.append([fmt.get(c, lambda v: "—" if v is None else str(v))(r.get(c)) for c in cols])
    w = [max(len(row[i]) for row in cells) for i in range(len(cols))]
    lines = ["  " + "  ".join(row[i].ljust(w[i]) for i in range(len(cols))) for row in cells]
    lines.insert(1, "  " + "  ".join("-" * w[i] for i in range(len(cols))))
    return "\n".join(lines)


_FMT_TXT = {"Capital S/": lambda v: _s(v), "Ganancia neta S/": lambda v: _s(v), "Cob sem": lambda v: _s(v, 1), "Vta/sem": lambda v: _s(v, 1),
            "Dscto hoy": lambda v: f"{v:.0%}" if v is not None else "—", "Stock uds": lambda v: _s(v), "Uds a mover": lambda v: _s(v),
            "Necesidad uds": lambda v: _s(v), "Stock CD": lambda v: _s(v)}
_COLS_TXT = {"b1": ["SKU", "Producto", "Tiendas", "Stock uds", "Capital S/", "Edad sem", "⭐", "Acción"],
             "b2a": ["SKU", "Producto", "Estado", "Stock uds", "Cob sem", "Capital S/", "Dscto hoy", "Tend", "⭐", "Acción"],
             "b2b": ["SKU", "Producto", "Uds a mover", "Tiendas destino", "Ganancia neta S/"],
             "b3": ["SKU", "Producto", "Vta/sem", "Cob sem", "Tiendas en quiebre", "Stock CD", "Necesidad uds", "Tend", "Acción"]}


def tablas_texto(bloques: dict, tope_linea: int = TOP_CUERPO_LINEA, tope_plano: int = TOP_CUERPO_PLANO) -> dict:
    """Bloques del cuerpo en texto plano (para el textarea / correo sin formato).
    Devuelve {"b1","b2a","b2b","b3"} → str. B1 y B2a agrupados por línea con subtotal;
    tope de modelos por línea; el resto se remite al Excel. Los TOTALes salen de `hechos`."""
    h = bloques["hechos"]; out = {}
    for key, filas in (("b1", _filas_b1(bloques["b1"])), ("b2a", _filas_b2a(bloques["b2a"]))):
        partes = []
        for lin, cap, n, top in _grupos_por_linea(filas, tope_linea):
            extra = f"  (+{n - len(top)} modelos más en el Excel)" if n > len(top) else ""
            partes.append(f"▸ {lin} — S/ {_s(cap)} en {n} modelo(s){extra}\n" + _tabla_txt(top, _COLS_TXT[key], _FMT_TXT))
        tot = h[key]
        partes.append(f"TOTAL {'VENTA CERO' if key == 'b1' else 'SOBRESTOCK'}: {tot['n_skus']} modelos · {_s(tot['stock_uds'])} uds · S/ {_s(tot['capital'])}")
        out[key] = "\n\n".join(partes) if filas else "  (sin modelos en este bloque)"
    f2b = _filas_b2b(bloques["b2b"])
    out["b2b"] = (_tabla_txt(f2b[:tope_plano], _COLS_TXT["b2b"], _FMT_TXT)
                  + (f"\n  (+{len(f2b) - tope_plano} modelos más en el Excel)" if len(f2b) > tope_plano else "")
                  + f"\nTOTAL DESBALANCE: {h['b2b']['n_skus']} modelos · {_s(h['b2b']['uds'])} uds a mover · ganancia neta S/ {_s(h['b2b']['ganancia'])}") if f2b else "  (sin transferencias rentables esta semana)"
    f3 = _filas_b3(bloques["b3"])
    out["b3"] = (_tabla_txt(f3[:tope_plano], _COLS_TXT["b3"], _FMT_TXT)
                 + (f"\n  (+{len(f3) - tope_plano} modelos más en el Excel)" if len(f3) > tope_plano else "")
                 + f"\nTOTAL GANADORES CORTOS: {h['b3']['n_skus']} modelos · {_s(h['b3']['vta_sem_total'], 1)} uds/sem · {h['b3']['n_sin_cd']} sin stock en CD"
                 + (f" · necesidad {_s(h['b3']['necesidad_uds'])} uds" if h['b3']['necesidad_uds'] else "")) if f3 else "  (sin ganadores cortos esta semana)"
    return out


_TD = "padding:3px 8px;border:1px solid #d9d9d9;font-family:Calibri,Arial,sans-serif;font-size:10.5pt;"
_TH = _TD + "background:#dce6f1;font-weight:bold;text-align:left;"


def _tabla_html(rows: list[dict], cols: list[str]) -> str:
    if not rows:
        return "<p style='font-family:Calibri,Arial;font-size:10.5pt;color:#666'>(sin modelos)</p>"
    num = {"Capital S/", "Ganancia neta S/", "Cob sem", "Vta/sem", "Stock uds", "Uds a mover", "Necesidad uds", "Stock CD", "Tiendas", "Tiendas destino", "Edad sem", "Dscto hoy"}
    th = "".join(f"<th style='{_TH}'>{c}</th>" for c in cols)
    trs = []
    for r in rows:
        bold = "font-weight:bold;" if str(r.get("⭐", "")).startswith("⭐") else ""
        tds = []
        for c in cols:
            v = _FMT_TXT.get(c, lambda x: "—" if x is None else str(x))(r.get(c))
            al = "text-align:right;" if c in num else ""
            tds.append(f"<td style='{_TD}{al}{bold}'>{v}</td>")
        trs.append("<tr>" + "".join(tds) + "</tr>")
    return f"<table style='border-collapse:collapse;margin:4px 0 8px 0'><thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>"


def tablas_html(bloques: dict, tope_linea: int = TOP_CUERPO_LINEA, tope_plano: int = TOP_CUERPO_PLANO) -> dict:
    """Mismo contenido que tablas_texto, como <table> con estilos inline (Outlook los conserva)."""
    h = bloques["hechos"]; out = {}
    P = "<p style='font-family:Calibri,Arial;font-size:10.5pt;margin:6px 0 2px 0'>"
    for key, filas in (("b1", _filas_b1(bloques["b1"])), ("b2a", _filas_b2a(bloques["b2a"]))):
        partes = []
        for lin, cap, n, top in _grupos_por_linea(filas, tope_linea):
            extra = f" <span style='color:#666'>(+{n - len(top)} modelos más en el Excel)</span>" if n > len(top) else ""
            partes.append(f"{P}<b>▸ {lin}</b> — S/ {_s(cap)} en {n} modelo(s){extra}</p>" + _tabla_html(top, _COLS_TXT[key]))
        tot = h[key]
        partes.append(f"{P}<b>TOTAL {'VENTA CERO' if key == 'b1' else 'SOBRESTOCK'}:</b> {tot['n_skus']} modelos · {_s(tot['stock_uds'])} uds · S/ {_s(tot['capital'])}</p>")
        out[key] = "".join(partes) if filas else f"{P}(sin modelos en este bloque)</p>"
    f2b = _filas_b2b(bloques["b2b"])
    out["b2b"] = (_tabla_html(f2b[:tope_plano], _COLS_TXT["b2b"]) + f"{P}<b>TOTAL DESBALANCE:</b> {h['b2b']['n_skus']} modelos · {_s(h['b2b']['uds'])} uds a mover · ganancia neta S/ {_s(h['b2b']['ganancia'])}</p>") if f2b else f"{P}(sin transferencias rentables esta semana)</p>"
    f3 = _filas_b3(bloques["b3"])
    out["b3"] = (_tabla_html(f3[:tope_plano], _COLS_TXT["b3"]) + f"{P}<b>TOTAL GANADORES CORTOS:</b> {h['b3']['n_skus']} modelos · {_s(h['b3']['vta_sem_total'], 1)} uds/sem · {h['b3']['n_sin_cd']} sin stock en CD</p>") if f3 else f"{P}(sin ganadores cortos esta semana)</p>"
    return out


# ── Excel del proveedor (delgado; mismos DataFrames que el correo) ────────────
_F = {"S": "#,##0", "P": "#,##0.00", "PCT": "0.0%", "C": "0.0"}


def _hoja_o_vacia(writer, hoja: str, titulo: str, df: pd.DataFrame, formatos: dict, chips_col: str | None = None):
    if df.empty:
        vistas_excel._tabla_con_titulo(writer, hoja, titulo, pd.DataFrame({"Aviso": ["Sin modelos en este bloque esta semana"]}), {})
        return
    if chips_col:
        reportes_marcas._escribir_tabla(writer, hoja, titulo, df, formatos, zebra=True, chips_col=chips_col)
    else:
        vistas_excel._tabla_con_titulo(writer, hoja, titulo, df, formatos, anchos={"Producto": 34, "Acción sugerida": 48, "Modelo": 34})


def excel_proveedor(bloques: dict) -> bytes:
    """Pestañas: Resumen · 1. Venta Cero (SKU) · 1b. Venta Cero x Tienda · 2a. Sobrestock ·
    2b. Desbalance tiendas · 3. Ganadores · Leyenda. Todas construidas de los mismos
    DataFrames del correo; los totales del Resumen salen de `hechos`."""
    import io
    h, marca, corte = bloques["hechos"], bloques["marca"], bloques["corte"]
    b1, b2a, b2b, b3 = bloques["b1"], bloques["b2a"], bloques["b2b"], bloques["b3"]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        foto = h["foto"]
        res = pd.DataFrame([
            {"Bloque": "1. Venta cero (sin venta la última semana en toda la cadena)", "Modelos": h["b1"]["n_skus"], "Stock (uds)": h["b1"]["stock_uds"], "Capital S/ (costo)": h["b1"]["capital"], "Qué pedimos": f"{h['b1']['n_top']} modelos concentran el 80% · {h['b1']['n_liquidar']} con más de 26 sem: liquidar / canje"},
            {"Bloque": "2a. Sobrestock de cadena (venden, pero cargan de más)", "Modelos": h["b2a"]["n_skus"], "Stock (uds)": h["b2a"]["stock_uds"], "Capital S/ (costo)": h["b2a"]["capital"], "Qué pedimos": f"markdown 50/50: {h['b2a']['n_markdown']} · canje/devolución: {h['b2a']['n_canje']} · frenar ingreso: {h['b2a']['n_frenar']}"},
            {"Bloque": "2b. Desbalance entre tiendas (transferencias rentables)", "Modelos": h["b2b"]["n_skus"], "Stock (uds)": h["b2b"]["uds"], "Capital S/ (costo)": None, "Qué pedimos": f"mover {h['b2b']['uds']:,} uds · ganancia neta S/ {h['b2b']['ganancia']:,}"},
            {"Bloque": "3. Ganadores que se quedan cortos", "Modelos": h["b3"]["n_skus"], "Stock (uds)": None, "Capital S/ (costo)": None, "Qué pedimos": f"{h['b3']['n_sin_cd']} sin stock en CD (reorden) · necesidad {h['b3']['necesidad_uds']:,} uds"},
        ])
        ws = vistas_excel._tabla_con_titulo(w, "Resumen", f"{marca} — Reporte semanal Ripley · corte {corte}", res,
                                            {"Stock (uds)": _F["S"], "Capital S/ (costo)": _F["S"]}, anchos={"Bloque": 58, "Qué pedimos": 70})
        fila = ws.max_row + 2
        ws.cell(row=fila, column=1, value=f"Foto de la marca en Ripley: S/ {foto['capital_total']:,} a costo · {foto['skus']} modelos · {foto['tiendas']} tiendas · "
                                          f"sell-through {foto['sell_through_pct']}% · margen efectivo {foto['margen_efectivo_pct'] if foto['margen_efectivo_pct'] is not None else '—'}%")
        ws.cell(row=fila + 1, column=1, value=f"Los bloques 1 y 2a suman S/ {h['b1']['capital'] + h['b2a']['capital']:,} = {round((h['b1']['capital'] + h['b2a']['capital']) / foto['capital_total'] * 100, 1) if foto['capital_total'] else 0}% del capital de la marca. Un modelo aparece en un solo bloque.")
        ws.cell(row=fila + 2, column=1, value=reportes_marcas._SUPUESTOS)
        ws.cell(row=fila + 3, column=1, value="Cifras al corte de la base (no a la fecha de envío). Generado por Capi.")
        # 1
        c1 = [("sku", "SKU"), ("nombre", "Producto"), ("categoria", "Línea"), ("temporada", "Temporada"), ("estado_cadena", "Estado"), ("n_tiendas_stock", "Tiendas con stock"),
              ("stock_cadena", "Stock (uds)"), ("capital_costo", "Capital S/ (costo)"), ("pct_acum", "% acum."), ("top_80", "Prioridad"), ("semanas_sin_venta", "Sem sin venta"),
              ("edad_semanas", "Edad (sem)"), ("pct_descuento", "Dscto actual"), ("dscto_sugerido", "Dscto sugerido"), ("precio_vigente", "P. Vigente"),
              ("precio_sugerido", "P. Sugerido"), ("precio_minimo", "P. Mínimo (piso)"), ("accion", "Acción sugerida")]
        d1 = b1[[a for a, _ in c1 if a in b1.columns]].rename(columns=dict(c1)).copy() if not b1.empty else pd.DataFrame()
        if not d1.empty:
            d1["Prioridad"] = np.where(d1["Prioridad"], "⭐ TOP 80%", "")
        _hoja_o_vacia(w, "1. Venta Cero (SKU)", f"{marca} — Modelos con stock y SIN venta la última semana en toda la cadena · ⭐ = concentran el 80% del capital · corte {corte}",
                      d1, {"Stock (uds)": _F["S"], "Capital S/ (costo)": _F["S"], "% acum.": _F["PCT"], "Edad (sem)": "0", "Dscto actual": _F["PCT"], "Dscto sugerido": _F["PCT"],
                           "P. Vigente": _F["P"], "P. Sugerido": _F["P"], "P. Mínimo (piso)": _F["P"]}, chips_col="Estado")
        # 1b
        vc = bloques["vc_tienda"]
        if vc is not None and not vc.empty:
            vistas_excel.hoja_venta_cero(w, vc, hoja="1b. Venta Cero x Tienda",
                                         titulo=f"{marca} — Detalle por tienda: SKU con stock y sin venta en esa tienda (⭐ TOP 80% del capital sin venta de la tienda) · corte {corte}")
        else:
            _hoja_o_vacia(w, "1b. Venta Cero x Tienda", f"{marca} — sin combos SKU×tienda sin venta · corte {corte}", pd.DataFrame(), {})
        # 2a
        c2 = [("sku", "SKU"), ("nombre", "Producto"), ("categoria", "Línea"), ("temporada", "Temporada"), ("grupo", "Grupo"), ("estado_cadena", "Estado"), ("tendencia", "Tendencia"),
              ("stock_cadena", "Stock (uds)"), ("vta_sem_prom4", "Vta sem (prom 4)"), ("cobertura_cadena", "Cobertura (sem)"), ("capital_costo", "Capital S/ (costo)"),
              ("pct_acum", "% acum."), ("top_80", "Prioridad"), ("edad_semanas", "Edad (sem)"), ("costo", "Costo unit."), ("pct_descuento", "Dscto actual"), ("dscto_sugerido", "Dscto sugerido"),
              ("precio_blanco", "P. Blanco"), ("precio_vigente", "P. Vigente"), ("precio_sugerido", "P. Sugerido"), ("margen_resultante", "Margen result."), ("precio_minimo", "P. Mínimo (piso)"),
              ("accion", "Acción sugerida"), ("alternativas", "Alternativas")]
        d2 = b2a[[a for a, _ in c2 if a in b2a.columns]].rename(columns=dict(c2)).copy() if not b2a.empty else pd.DataFrame()
        if not d2.empty:
            d2["Prioridad"] = np.where(d2["Prioridad"], "⭐ TOP 80%", "")
        _hoja_o_vacia(w, "2a. Sobrestock", f"{marca} — Sobrestock y liquidación a nivel cadena (venden, pero cargan de más) · corte {corte}",
                      d2, {**reportes_marcas._FMTS_PRECIO, "% acum.": _F["PCT"]}, chips_col="Estado")
        # 2b
        c3 = [("sku", "SKU"), ("nombre", "Producto"), ("categoria", "Línea"), ("estado_cadena", "Estado"), ("transf_uds", "Uds a mover"), ("transf_tiendas", "Tiendas destino"),
              ("transf_valor", "Valor S/ (venta)"), ("transf_ganancia", "Ganancia neta S/"), ("stock_cadena", "Stock (uds)"), ("cobertura_cadena", "Cobertura (sem)"), ("accion", "Acción sugerida")]
        d3 = b2b[[a for a, _ in c3 if a in b2b.columns]].rename(columns=dict(c3)) if not b2b.empty else pd.DataFrame()
        _hoja_o_vacia(w, "2b. Desbalance tiendas", f"{marca} — Transferencias entre tiendas con ganancia neta (≥{reportes_marcas.TRANSF_MIN_UDS} uds por modelo) · corte {corte}",
                      d3, {"Uds a mover": _F["S"], "Valor S/ (venta)": _F["S"], "Ganancia neta S/": _F["S"], "Stock (uds)": _F["S"], "Cobertura (sem)": _F["C"]}, chips_col="Estado")
        # 3
        c4 = [("sku", "SKU"), ("nombre", "Producto"), ("categoria", "Línea"), ("temporada", "Temporada"), ("tendencia", "Tendencia"), ("entra_por", "Entra por"),
              ("vta_sem_prom4", "Vta sem (prom 4)"), ("stock_cadena", "Stock (uds)"), ("cobertura_cadena", "Cobertura (sem)"), ("n_tiendas_quiebre", "Tiendas en quiebre"),
              ("n_tiendas", "Tiendas"), ("stock_cd", "Stock CD"), ("on_order", "On order"), ("necesidad_uds", "Necesidad (uds)"), ("desde_cd_uds", "A girar hoy (uds)"),
              ("pendiente_sin_cd_uds", "Pendiente sin CD (uds)"), ("sem_en_quiebre_max", "Sem en quiebre (máx)"), ("vp_neto_min", "Venta perdida S/ (mín)"),
              ("vp_neto_max", "Venta perdida S/ (máx)"), ("accion", "Acción sugerida")]
        d4 = b3[[a for a, _ in c4 if a in b3.columns]].rename(columns=dict(c4)) if not b3.empty else pd.DataFrame()
        _hoja_o_vacia(w, "3. Ganadores", f"{marca} — Modelos con buena rotación que se están quedando cortos (venta ≥ {h['b3']['umbral_vta']} u/sem y cobertura ≤ {B3_COB_MAX:.0f} sem o tendencia ▲) · corte {corte}",
                      d4, {"Vta sem (prom 4)": _F["C"], "Stock (uds)": _F["S"], "Cobertura (sem)": _F["C"], "Stock CD": _F["S"], "On order": _F["S"], "Necesidad (uds)": _F["S"],
                           "A girar hoy (uds)": _F["S"], "Pendiente sin CD (uds)": _F["S"], "Venta perdida S/ (mín)": _F["S"], "Venta perdida S/ (máx)": _F["S"]})
        reportes_marcas._hoja_leyenda(w)
    buf.seek(0)
    return buf.read()


# ══════════════════════════════════════════════════════════════════════════════
#  COMPARATIVO SEMANAL (C10): persistir lo que se envió y comparar contra eso
#  Principio: el pedido medible es el corte registrado; no se recalculan motores
#  sobre bases viejas. `proveedor.parquet` viaja dentro del zip de cortes que
#  snapshots_engine.nube sube/restaura de Notion (opcional: los cortes viejos no lo traen).
# ══════════════════════════════════════════════════════════════════════════════
import os as _os
from datetime import date as _date, datetime as _datetime, timedelta as _timedelta

try:
    from snapshots_engine.config import SNAPSHOTS_DIR as _SNAPSHOTS_DIR
except Exception:  # pragma: no cover
    _SNAPSHOTS_DIR = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "snapshots")

ARCHIVO_CORTE = "proveedor.parquet"
COLS_CORTE = ["marca", "semana_iso", "corte", "bloque", "sku", "nombre", "categoria", "estado", "capital", "uds",
              "cobertura", "vta_sem", "n_tiendas", "n_tiendas_quiebre", "stock_cd", "accion", "top_80",
              "enviado", "fecha_envio", "generado"]
PERSISTENCIA_ALERTA = 3   # semanas consecutivas en el mismo bloque → presión explícita en el correo


def _ruta_corte(semana_iso: str, base_dir: str | None = None) -> str:
    return _os.path.join(base_dir or _SNAPSHOTS_DIR, semana_iso, ARCHIVO_CORTE)


def _semana_anterior(semana_iso: str) -> str:
    y, w = (int(x) for x in semana_iso.split("-"))
    d = _date.fromisocalendar(y, w, 1) - _timedelta(days=7)
    iy, iw, _ = d.isocalendar()
    return f"{iy}-{iw:02d}"


def filas_corte(bloques: dict, semana_iso: str, enviado: bool = False) -> pd.DataFrame:
    """Formato largo (una fila por marca × semana × bloque × SKU) con lo que dice el correo."""
    marca, corte = bloques["marca"], bloques["corte"]
    ahora = _datetime.now().isoformat(timespec="seconds")
    partes = []
    def _mk(df, bloque, capital, uds, cob, vta, estado, accion, top):
        if df.empty:
            return
        d = pd.DataFrame({
            "marca": marca, "semana_iso": semana_iso, "corte": corte, "bloque": bloque,
            "sku": df["sku"].map(sku_key).values, "nombre": df["nombre"].astype(str).values,
            "categoria": df["categoria"].astype(str).values if "categoria" in df.columns else "",
            "estado": df[estado].astype(str).values if estado and estado in df.columns else "",
            "capital": df[capital].fillna(0).astype(float).round(0).values if capital in df.columns else 0.0,
            "uds": df[uds].fillna(0).astype(float).round(0).values if uds in df.columns else 0.0,
            "cobertura": df[cob].astype(float).values if cob and cob in df.columns else np.nan,
            "vta_sem": df[vta].astype(float).values if vta and vta in df.columns else np.nan,
            "n_tiendas": df["n_tiendas"].fillna(0).astype(int).values if "n_tiendas" in df.columns else 0,
            "n_tiendas_quiebre": df["n_tiendas_quiebre"].fillna(0).astype(int).values if "n_tiendas_quiebre" in df.columns else 0,
            "stock_cd": df["stock_cd"].fillna(0).astype(float).values if "stock_cd" in df.columns else 0.0,
            "accion": df[accion].astype(str).values if accion in df.columns else "",
            "top_80": df[top].astype(bool).values if top and top in df.columns else False,
            "enviado": bool(enviado), "fecha_envio": ahora if enviado else "", "generado": ahora,
        })
        partes.append(d)
    _mk(bloques["b1"], "b1", "capital_costo", "stock_cadena", "cobertura_cadena", "vta_sem_prom4", "estado_cadena", "accion", "top_80")
    _mk(bloques["b2a"], "b2a", "capital_costo", "stock_cadena", "cobertura_cadena", "vta_sem_prom4", "estado_cadena", "accion", "top_80")
    _mk(bloques["b2b"], "b2b", "capital_costo", "transf_uds", "cobertura_cadena", None, "estado_cadena", "accion", None)
    _mk(bloques["b3"], "b3", "capital_costo", "stock_cadena", "cobertura_cadena", "vta_sem_prom4", "estado_cadena", "accion", None)
    if not partes:
        return pd.DataFrame(columns=COLS_CORTE)
    return pd.concat(partes, ignore_index=True)[COLS_CORTE]


def persistir_corte(bloques: dict, semana_iso: str, enviado: bool = False, base_dir: str | None = None) -> str:
    """Escribe/actualiza las filas de la marca en snapshots/<semana>/proveedor.parquet.
    Idempotente por (marca, semana): reemplaza las filas previas de esa marca. Devuelve la ruta."""
    ruta = _ruta_corte(semana_iso, base_dir)
    _os.makedirs(_os.path.dirname(ruta), exist_ok=True)
    nuevo = filas_corte(bloques, semana_iso, enviado)
    if _os.path.exists(ruta):
        prev = pd.read_parquet(ruta)
        prev = prev[prev["marca"].astype(str).str.upper() != str(bloques["marca"]).upper()]
        nuevo = pd.concat([prev, nuevo], ignore_index=True)
    nuevo.to_parquet(ruta, index=False)
    return ruta


def cargar_cortes(marca: str, hasta: str | None = None, n: int = 8, base_dir: str | None = None) -> pd.DataFrame:
    """Filas persistidas de la marca en las últimas `n` semanas anteriores a `hasta` (excluida)."""
    base = base_dir or _SNAPSHOTS_DIR
    if not _os.path.isdir(base):
        return pd.DataFrame(columns=COLS_CORTE)
    semanas = sorted(d for d in _os.listdir(base) if _os.path.exists(_ruta_corte(d, base)))
    if hasta:
        semanas = [s for s in semanas if s < hasta]
    partes = []
    for s in semanas[-n:]:
        try:
            df = pd.read_parquet(_ruta_corte(s, base))
        except Exception:
            continue
        df = df[df["marca"].astype(str).str.upper() == str(marca).upper()]
        if not df.empty:
            partes.append(df)
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame(columns=COLS_CORTE)


def _kpis_de_filas(df: pd.DataFrame) -> dict:
    def _b(bl):
        d = df[df["bloque"] == bl]
        return d
    b1, b2a, b2b, b3 = _b("b1"), _b("b2a"), _b("b2b"), _b("b3")
    return {"b1": {"n_skus": len(b1), "capital": float(b1["capital"].sum()), "stock_uds": float(b1["uds"].sum())},
            "b2a": {"n_skus": len(b2a), "capital": float(b2a["capital"].sum())},
            "b2b": {"n_skus": len(b2b), "uds": float(b2b["uds"].sum())},
            "b3": {"n_skus": len(b3), "vta_sem_total": float(b3["vta_sem"].fillna(0).sum())}}


def _kpis_de_hechos(h: dict) -> dict:
    return {"b1": {"n_skus": h["b1"]["n_skus"], "capital": float(h["b1"]["capital"]), "stock_uds": float(h["b1"]["stock_uds"])},
            "b2a": {"n_skus": h["b2a"]["n_skus"], "capital": float(h["b2a"]["capital"])},
            "b2b": {"n_skus": h["b2b"]["n_skus"], "uds": float(h["b2b"]["uds"])},
            "b3": {"n_skus": h["b3"]["n_skus"], "vta_sem_total": float(h["b3"]["vta_sem_total"])}}


def comparar_marca(bloques: dict, cortes_prev: pd.DataFrame) -> dict:
    """Compara los bloques de la semana contra el corte persistido más reciente de la marca.

    Devuelve {semana, semana_prev, consecutivas, hay_prev, kpis{bloque{kpi{actual,prev,delta_abs,delta_pct}}},
              skus{bloque{persisten,salieron,nuevos}}, semanas_en_bloque{bloque{sku:n}}, persistentes{bloque:[skus]},
              resolucion_b1 (% de los SKUs de venta cero de la semana anterior que ya no están sin venta)}.
    La racha `semanas_en_bloque` cuenta hacia atrás sobre cortes CONSECUTIVOS; un hueco la corta."""
    semana = bloques.get("semana_iso") or ""
    h = bloques["hechos"]
    actual = filas_corte(bloques, semana)
    out = {"semana": semana, "hay_prev": False, "semana_prev": None, "consecutivas": False,
           "kpis": {}, "skus": {}, "semanas_en_bloque": {}, "persistentes": {}, "resolucion_b1": None}
    k_act = _kpis_de_hechos(h)
    if cortes_prev is None or cortes_prev.empty:
        out["kpis"] = {b: {k: {"actual": v, "prev": None, "delta_abs": None, "delta_pct": None} for k, v in d.items()} for b, d in k_act.items()}
        out["semanas_en_bloque"] = {b: {s: 1 for s in actual.loc[actual["bloque"] == b, "sku"]} for b in ("b1", "b2a", "b2b", "b3")}
        return out
    semanas = sorted(cortes_prev["semana_iso"].unique())
    prev_w = semanas[-1]
    prev = cortes_prev[cortes_prev["semana_iso"] == prev_w]
    out.update(hay_prev=True, semana_prev=prev_w, consecutivas=(semana and _semana_anterior(semana) == prev_w))
    k_prev = _kpis_de_filas(prev)
    for b, d in k_act.items():
        out["kpis"][b] = {}
        for k, v in d.items():
            p = k_prev[b].get(k)
            da = (v - p) if p is not None else None
            dp = (round(da / p * 100, 1) if p else None) if da is not None else None
            out["kpis"][b][k] = {"actual": v, "prev": p, "delta_abs": da, "delta_pct": dp}
    for b in ("b1", "b2a", "b2b", "b3"):
        s_act = set(actual.loc[actual["bloque"] == b, "sku"]); s_prev = set(prev.loc[prev["bloque"] == b, "sku"])
        out["skus"][b] = {"persisten": sorted(s_act & s_prev), "salieron": sorted(s_prev - s_act), "nuevos": sorted(s_act - s_prev)}
        # racha consecutiva hacia atrás
        racha = {}
        por_sem = {w: set(cortes_prev.loc[(cortes_prev["semana_iso"] == w) & (cortes_prev["bloque"] == b), "sku"]) for w in semanas}
        for s in s_act:
            n, w = 1, semana
            while True:
                w = _semana_anterior(w)
                if w not in por_sem or s not in por_sem[w]:
                    break
                n += 1
            racha[s] = n
        out["semanas_en_bloque"][b] = racha
        out["persistentes"][b] = sorted([s for s, n in racha.items() if n >= PERSISTENCIA_ALERTA], key=lambda s: (-racha[s], s))
    p1 = set(prev.loc[prev["bloque"] == "b1", "sku"])
    if p1:
        out["resolucion_b1"] = round(len(p1 - set(actual.loc[actual["bloque"] == "b1", "sku"])) / len(p1) * 100, 1)
    return out


_KPI_LABELS = [("b1", "capital", "Venta cero — capital S/"), ("b1", "n_skus", "Venta cero — modelos"),
               ("b2a", "capital", "Sobrestock — capital S/"), ("b2a", "n_skus", "Sobrestock — modelos"),
               ("b2b", "uds", "Desbalance — uds a mover"), ("b3", "n_skus", "Ganadores cortos — modelos")]


def evolucion_texto(cmp: dict, bloques: dict) -> str:
    """Bloque 0 del correo (solo si hay corte previo REAL). Texto plano."""
    if not cmp.get("hay_prev"):
        return ""
    filas = []
    for b, k, lab in _KPI_LABELS:
        d = cmp["kpis"][b][k]
        flecha = "" if d["delta_abs"] is None else ("▲" if d["delta_abs"] > 0 else ("▼" if d["delta_abs"] < 0 else "="))
        dpct = "" if d["delta_pct"] is None else f" ({d['delta_pct']:+.0f}%)"
        filas.append({"Indicador": lab, f"Sem {cmp['semana_prev']}": _s(d["prev"]), f"Sem {cmp['semana']}": _s(d["actual"]), "Δ": f"{flecha} {_s(d['delta_abs'])}{dpct}".strip()})
    cols = list(filas[0].keys())
    txt = _tabla_txt(filas, cols, {})
    extra = []
    if cmp.get("resolucion_b1") is not None:
        extra.append(f"De los modelos sin venta que les reportamos la semana pasada, el {cmp['resolucion_b1']:.0f}% ya volvió a vender o salió de la lista.")
    pers = cmp["persistentes"].get("b1", [])
    if pers:
        nombres = bloques["b1"].assign(_k=bloques["b1"]["sku"].map(sku_key)).set_index("_k")["nombre"].to_dict()
        rachas = cmp["semanas_en_bloque"]["b1"]
        top = ", ".join(f"{s} {nombres.get(s, '')} ({rachas[s]} sem)" for s in pers[:5])
        extra.append(f"{len(pers)} modelos llevan {PERSISTENCIA_ALERTA} o más semanas seguidas sin venta: {top}{'…' if len(pers) > 5 else ''}.")
    if not cmp.get("consecutivas"):
        extra.append(f"(La comparación es contra la semana {cmp['semana_prev']}, la última reportada.)")
    return txt + ("\n" + "\n".join(extra) if extra else "")


def evolucion_html(cmp: dict, bloques: dict) -> str:
    if not cmp.get("hay_prev"):
        return ""
    filas = []
    for b, k, lab in _KPI_LABELS:
        d = cmp["kpis"][b][k]
        flecha = "" if d["delta_abs"] is None else ("▲" if d["delta_abs"] > 0 else ("▼" if d["delta_abs"] < 0 else "="))
        dpct = "" if d["delta_pct"] is None else f" ({d['delta_pct']:+.0f}%)"
        filas.append({"Indicador": lab, f"Sem {cmp['semana_prev']}": _s(d["prev"]), f"Sem {cmp['semana']}": _s(d["actual"]), "Δ": f"{flecha} {_s(d['delta_abs'])}{dpct}".strip()})
    cols = list(filas[0].keys())
    html = _tabla_html(filas, cols)
    P = "<p style='font-family:Calibri,Arial;font-size:10.5pt;margin:4px 0'>"
    if cmp.get("resolucion_b1") is not None:
        html += f"{P}De los modelos sin venta que les reportamos la semana pasada, el <b>{cmp['resolucion_b1']:.0f}%</b> ya volvió a vender o salió de la lista.</p>"
    pers = cmp["persistentes"].get("b1", [])
    if pers:
        nombres = bloques["b1"].assign(_k=bloques["b1"]["sku"].map(sku_key)).set_index("_k")["nombre"].to_dict()
        rachas = cmp["semanas_en_bloque"]["b1"]
        top = ", ".join(f"{s} {nombres.get(s, '')} ({rachas[s]} sem)" for s in pers[:5])
        html += f"{P}<b>{len(pers)} modelos llevan {PERSISTENCIA_ALERTA} o más semanas seguidas sin venta:</b> {top}{'…' if len(pers) > 5 else ''}</p>"
    return html


def serie_kpis(cortes: pd.DataFrame, bloques: dict | None = None) -> pd.DataFrame:
    """KPI × semana (para la hoja '0. Evolución' y el historial en la app)."""
    filas = {}
    if cortes is not None and not cortes.empty:
        for w, d in cortes.groupby("semana_iso"):
            k = _kpis_de_filas(d)
            filas[w] = {lab: k[b][kk] for b, kk, lab in _KPI_LABELS}
    if bloques is not None:
        k = _kpis_de_hechos(bloques["hechos"])
        filas[bloques.get("semana_iso") or "actual"] = {lab: k[b][kk] for b, kk, lab in _KPI_LABELS}
    if not filas:
        return pd.DataFrame()
    return pd.DataFrame(filas).reindex(columns=sorted(filas))
