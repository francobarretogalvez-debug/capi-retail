"""
reporte_proveedor.py — motor del "Reporte semanal al proveedor" (marcas terceras).

Decisión 2026-09-18 (Franco): un correo + un Excel por marca, tres frentes:
  B1  VENTA CERO        modelos sin venta en toda la cadena la última semana (ranking Pareto 80%)
  B2a SOBRESTOCK        modelos que venden pero cargan de más (estado de CADENA) → markdown 50/50,
                        devolución o frenar ingreso
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
GRUPO_B1_4SEM = "Sin venta en las últimas 4 semanas"
GRUPO_B1_PARO = "Vendía y no vendió la última semana"
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
        "vta_sem2": por["vta_sem2_total"].first() if "vta_sem2_total" in dfm.columns else np.nan,
        "vta_sem3": por["vta_sem3_total"].first() if "vta_sem3_total" in dfm.columns else np.nan,
        "vta_sem4": por["vta_sem4_total"].first() if "vta_sem4_total" in dfm.columns else np.nan,
        "n_tiendas_stock": dfm[dfm["stock_total"] > 0].groupby("sku")["tienda"].nunique(),
        "n_tiendas_venta4": dfm[dfm["prom_vta_uds"].fillna(0) > 0].groupby("sku")["tienda"].nunique(),
        "n_tiendas_quiebre": dfm.assign(_q=dfm["estado"].isin(ESTADOS_QUIEBRE)).groupby("sku")["_q"].sum(),
        "stock_cd": por["stock_cd"].first() if "stock_cd" in dfm.columns else 0,
        "rango_antiguedad": por["rango_antiguedad"].first() if "rango_antiguedad" in dfm.columns else None,
    })
    g1 = g1.merge(extra, left_on="sku", right_index=True, how="left")
    g1["n_tiendas_stock"] = g1["n_tiendas_stock"].fillna(0).astype(int)
    g1["n_tiendas_venta4"] = g1["n_tiendas_venta4"].fillna(0).astype(int)
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
COLS_DETALLE_TIENDA = ["tienda", "sku", "nombre", "categoria", "grupo", "stock_total", "stock_valor_costo", "pct_acum_tienda", "top_80",
                       "vta_sem1", "vta_sem2", "vta_sem3", "vta_sem4", "vt_sem1", "vt_sem2", "vt_sem3", "vt_sem4",
                       "precio_vigente", "pct_descuento", "tipo_evento", "edad_semanas", "accion"]


def venta_tienda_4sem(semana_iso: str, skus, base_dir: str | None = None) -> pd.DataFrame:
    """Venta semanal POR TIENDA de las 4 semanas hasta `semana_iso` (incluida), leída de
    snapshots/<sem>/tienda.parquet (pedido Franco 2026-09-19). Columnas vt_sem1 (la última) .. vt_sem4.
    Tiendas en código (JP, AQP…) se traducen al nombre del df_cob con STORE_NAMES. Semanas sin
    snapshot quedan en NaN; si no hay ninguna, devuelve vacío y la pestaña 1b sale sin esas columnas."""
    if not semana_iso or "-" not in str(semana_iso):
        return pd.DataFrame()
    try:
        from transformar_profundidad import STORE_NAMES
    except Exception:
        STORE_NAMES = {}
    base = base_dir or _SNAPSHOTS_DIR
    keys = {sku_key(s) for s in skus}
    semanas, w = [], str(semana_iso)
    for _ in range(4):
        semanas.append(w); w = _semana_anterior(w)
    partes = []
    for i, sem in enumerate(semanas, start=1):
        ruta = _os.path.join(base, sem, "tienda.parquet")
        if not _os.path.exists(ruta):
            continue
        try:
            d = pd.read_parquet(ruta, columns=["sku", "tienda", "vta_uds_sem"])
        except Exception:
            continue
        d["sku"] = d["sku"].map(sku_key); d = d[d["sku"].isin(keys)]
        d["tienda"] = d["tienda"].map(lambda x: STORE_NAMES.get(x, x))
        partes.append(d.groupby(["sku", "tienda"])["vta_uds_sem"].sum().rename(f"vt_sem{i}"))
    if not partes:
        return pd.DataFrame()
    out = pd.concat(partes, axis=1).reset_index()
    for i in (1, 2, 3, 4):
        if f"vt_sem{i}" not in out.columns:
            out[f"vt_sem{i}"] = np.nan
    return out[["sku", "tienda", "vt_sem1", "vt_sem2", "vt_sem3", "vt_sem4"]]


def detalle_tienda_b1(dfm: pd.DataFrame, b1: pd.DataFrame, tipo_evento_map: dict | None = None, semana_iso: str = "") -> pd.DataFrame:
    """Pestaña 1b: una fila por modelo de B1 × tienda con stock. Por definición cada fila vendió 0 en
    esa tienda la última semana (el modelo vendió 0 en toda la cadena). Pareto 80% del capital POR
    TIENDA (lo que la tienda debe atacar primero), acción de piso del módulo 📲 y la venta semanal
    del modelo a nivel cadena (sem -1..-4) para ver cómo venía vendiendo. Es el mismo universo que
    `detalle_lote` (lo que se mide como activación la semana siguiente)."""
    if b1.empty or dfm.empty:
        return pd.DataFrame(columns=COLS_DETALLE_TIENDA)
    d = dfm[dfm["sku"].isin(b1["sku"]) & (dfm["stock_total"].fillna(0) > 0)].copy()
    if d.empty:
        return pd.DataFrame(columns=COLS_DETALLE_TIENDA)
    info = b1.set_index("sku")
    d["grupo"] = d["sku"].map(info["grupo"])
    for i in (1, 2, 3, 4):
        d[f"vta_sem{i}"] = d["sku"].map(info[f"vta_sem{i}"]) if f"vta_sem{i}" in info.columns else np.nan
    d["tipo_evento"] = d["sku"].map(tipo_evento_map or {}).fillna("") if tipo_evento_map else ""
    d["accion"] = d.apply(vistas_excel._accion_venta_cero, axis=1)
    vt = venta_tienda_4sem(semana_iso, d["sku"]) if semana_iso else pd.DataFrame()
    if not vt.empty:
        d["_k"] = d["sku"].map(sku_key)
        d = d.merge(vt, left_on=["_k", "tienda"], right_on=["sku", "tienda"], how="left", suffixes=("", "_vt")).drop(columns=[c for c in ("sku_vt", "_k") if c in d.columns] or [])
    d = d.sort_values(["tienda", "stock_valor_costo"], ascending=[True, False], kind="mergesort")
    tot = d.groupby("tienda")["stock_valor_costo"].transform("sum")
    acum = d.groupby("tienda")["stock_valor_costo"].cumsum() / tot.replace(0, np.nan)
    share = d["stock_valor_costo"] / tot.replace(0, np.nan)
    d["pct_acum_tienda"] = acum.fillna(0)
    d["top_80"] = np.where((acum - share).fillna(0).round(6) < PARETO, "⭐ TOP 80%", "")
    for c in COLS_DETALLE_TIENDA:
        if c not in d.columns:
            d[c] = np.nan
    return d[COLS_DETALLE_TIENDA].reset_index(drop=True)


def bloque_venta_cero(g: pd.DataFrame, dfm: pd.DataFrame, precio_min_map: dict | None = None,
                      tipo_evento_map: dict | None = None, semana_iso: str = "") -> tuple[pd.DataFrame, pd.DataFrame]:
    """(b1, vc_tienda). b1 = SKUs con stock y SIN venta en toda la cadena la última semana, en dos
    grupos (sin venta 4 semanas · vendía y paró), Pareto 80% por grupo. vc_tienda = detalle por
    tienda de esos mismos modelos (detalle_tienda_b1), que va a la pestaña 1b y al lote de K1."""
    if g.empty:
        return g, pd.DataFrame(columns=COLS_DETALLE_TIENDA)
    b1 = g[(g["vta_sem1"].fillna(0) <= 0) & (g["stock_cadena"] > 0)].copy()
    b1 = _con_precio(b1, precio_min_map)
    if b1.empty:
        return b1, pd.DataFrame(columns=COLS_DETALLE_TIENDA)
    # Dos grupos (Franco 2026-09-19): sin venta en las 4 últimas semanas vs vendía y paró la última.
    # Pareto 80% DENTRO de cada grupo: cada lista tiene su propio TOP.
    # "4+" = cero en la cadena las 4 semanas Y ninguna tienda con venta positiva en 4 semanas (una
    # tienda puede tener +0.25 con devoluciones en otra que netean a 0: eso es "vendía y paró").
    b1["semanas_sin_venta"] = np.where((b1["vta_sem_prom4"] <= 0) & (b1["n_tiendas_venta4"] <= 0), "4+", "1")
    b1["grupo"] = np.where(b1["semanas_sin_venta"] == "4+", GRUPO_B1_4SEM, GRUPO_B1_PARO)
    b1["pct_acum"] = 0.0; b1["top_80"] = False
    for gname, idx in b1.groupby("grupo").groups.items():
        pa, tp = pareto_flag(b1.loc[idx, "capital_costo"])
        b1.loc[idx, "pct_acum"] = pa; b1.loc[idx, "top_80"] = tp
    def _acc(r):
        p = r.get("precio_sugerido")
        if r["edad_semanas"] >= EDAD_LIQUIDAR:
            return (f"🏷️ Liquidar: descuento compartido {r['dscto_sugerido']:.0%} → S/ {p:,.2f}" if pd.notna(p)
                    else "↩️ Devolución (ya en piso de precio)")
        if r["estado_cadena"] == "NUEVO SIN VENTA":
            return "👁️ Revisar exhibición (lanzamiento sin arranque)"
        if pd.notna(p):
            return f"👁️ Exhibición + descuento compartido {r['dscto_sugerido']:.0%} → S/ {p:,.2f}"
        return "👁️ Revisar exhibición / comunicación de precio"
    b1["accion"] = b1.apply(_acc, axis=1)
    b1["_g"] = (b1["grupo"] != GRUPO_B1_4SEM).astype(int)
    b1 = b1.sort_values(["_g", "top_80", "capital_costo"], ascending=[True, False, False]).drop(columns="_g").reset_index(drop=True)
    return b1, detalle_tienda_b1(dfm, b1, tipo_evento_map, semana_iso)


# ── Bloque 2a: sobrestock de cadena · 2b: desbalance entre tiendas ────────────
def bloque_sobrestock(g: pd.DataFrame, excluir: set, df_trans: pd.DataFrame | None = None,
                      precio_min_map: dict | None = None,
                      df_alertas: pd.DataFrame | None = None, dfm_ref: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(b2a, b2b).
    b2a: estado de CADENA ∈ ESTADOS_B2 con venta, menos los SKUs de B1. Acción primaria:
         descuento compartido (si la pirámide deja bajar) → devolución (viejo, estancado
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
                acc = f"⬇️ Descuento compartido 50/50: {r['dscto_sugerido']:.0%} → S/ {p:,.2f}"
                if viejo:
                    alts.append("devolución con recompra")
            elif viejo or en_piso:
                acc = "↩️ Devolución con recompra"
            else:
                acc = "⏸️ Frenar ingreso / no reponer (dscto ya en pirámide)"
                alts.append("devolución si no rota en 4 semanas")
            return pd.Series({"accion": acc, "alternativas": " · ".join(alts)})
        b2 = pd.concat([b2, b2.apply(_acc, axis=1)], axis=1)
        b2["pct_acum"], b2["top_80"] = pareto_flag(b2["capital_costo"])
        b2 = b2.sort_values(["top_80", "capital_costo"], ascending=[False, False]).reset_index(drop=True)
    # 2b
    tr = reportes_marcas.transferencias_por_sku(df_trans, g["sku"]) if df_trans is not None else vacio
    b2b_det = vacio
    if tr is None or tr.empty:
        b2b = vacio
    else:
        # Detalle origen → destino de los modelos que pasan el umbral (el proveedor ejecuta las
        # transferencias en terceras, precisión Franco 18-sep; pedido del detalle 19-sep).
        _cols_det = [c for c in ("sku", "nombre", "categoria", "tienda_origen", "tienda_destino", "uds_transferir", "ganancia_esperada", "veredicto",
                                 "cob_origen_pre", "cob_origen_post", "cob_destino_pre", "cob_destino_post", "precio_vigente", "motivo") if c in df_trans.columns]
        _cols_det = _cols_det + [c for c in ("costo_flete", "uds_vendibles_horizonte") if c in df_trans.columns]
        b2b_det = df_trans[df_trans["sku"].isin(tr["sku"])][_cols_det].copy()
        # Terceras: el traslado lo asume la marca → contribución esperada sin flete y veredicto por demanda
        if "ganancia_esperada" in b2b_det.columns:
            b2b_det["contrib_esperada"] = b2b_det["ganancia_esperada"] + (b2b_det["costo_flete"].fillna(0) if "costo_flete" in b2b_det.columns else 0)
            b2b_det["veredicto"] = np.where(b2b_det["contrib_esperada"] > 0, "✅ Con demanda en destino", "⚠️ Sin demanda proyectada en destino")
        if "categoria" not in b2b_det.columns and "categoria" in g.columns:
            b2b_det["categoria"] = b2b_det["sku"].map(g.set_index("sku")["categoria"])
        # Stock y venta promedio (4 sem) de la tienda origen y destino, para leer las coberturas
        # (Franco 19-sep). Cantidad del motor: min(exceso origen, déficit destino) con cobertura
        # objetivo de 12 semanas: exceso = stock − 12 × venta; déficit = 12 × venta − stock.
        if dfm_ref is not None and not dfm_ref.empty:
            _st = dfm_ref.set_index(["sku", "tienda"])[["stock_total", "prom_vta_uds"]]
            for lado in ("origen", "destino"):
                idx = pd.MultiIndex.from_arrays([b2b_det["sku"], b2b_det[f"tienda_{lado}"]])
                b2b_det[f"stock_{lado}"] = _st["stock_total"].reindex(idx).values
                b2b_det[f"vta_sem_{lado}"] = _st["prom_vta_uds"].reindex(idx).round(2).values
        if dfm_ref is not None and not dfm_ref.empty and "costo" in dfm_ref.columns:
            _costo = dfm_ref.drop_duplicates("sku").set_index("sku")["costo"]
            b2b_det["costo_total"] = (b2b_det["uds_transferir"] * b2b_det["sku"].map(_costo)).round(2)
        if "precio_vigente" in b2b_det.columns:
            b2b_det["valor_venta"] = (b2b_det["uds_transferir"] * b2b_det["precio_vigente"]).round(2)
        b2b_det = b2b_det.sort_values(["sku", "uds_transferir"], ascending=[True, False]).reset_index(drop=True)
        cols = ["sku", "categoria", "estado_cadena", "stock_cadena", "cobertura_cadena", "capital_costo", "n_tiendas_quiebre", "n_tiendas"]
        b2b = tr.merge(g[[c for c in cols if c in g.columns]], on="sku", how="left")
        b2b["accion"] = b2b.apply(lambda r: f"🔄 Mover {int(r['transf_uds'])} uds a {int(r['transf_tiendas'])} tienda(s)"
                                  + (f" · contribución esperada S/ {r['transf_ganancia']:,.0f}" if pd.notna(r.get('transf_ganancia')) else ""), axis=1)
        b2b = b2b.reset_index(drop=True)
    b2b.attrs["detalle"] = b2b_det
    b2b.attrs["rutas"] = rutas_tienda_a_tienda(b2b_det)
    return b2, b2b


def rutas_tienda_a_tienda(det: pd.DataFrame) -> pd.DataFrame:
    """Cuadro origen → destino para que el proveedor mapee cuánto mueve de tienda a tienda
    (pedido Franco 19-sep): modelos, unidades, costo total (uds × costo) y valor venta."""
    if det is None or det.empty:
        return pd.DataFrame(columns=["tienda_origen", "tienda_destino", "modelos", "uds", "costo_total", "valor_venta"])
    agg = {"modelos": ("sku", "nunique"), "uds": ("uds_transferir", "sum")}
    if "costo_total" in det.columns: agg["costo_total"] = ("costo_total", "sum")
    if "valor_venta" in det.columns: agg["valor_venta"] = ("valor_venta", "sum")
    r = det.groupby(["tienda_origen", "tienda_destino"], as_index=False).agg(**agg)
    return r.sort_values("uds", ascending=False).reset_index(drop=True)


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


# ── Bloque 4: pre-obsoleto y obsoleto (vista transversal, Franco 20-sep) ──────
def bloque_obsoletos(g: pd.DataFrame, b1: pd.DataFrame, b2a: pd.DataFrame, precio_min_map: dict | None = None) -> pd.DataFrame:
    """Todos los modelos con estado de CADENA PRE-OBSOLETO u OBSOLETO, vendan o no. No es un bloque
    excluyente: cada fila dice en qué bloque del correo ya aparece (venta cero / sobrestock) para que el
    proveedor vea junta la mercadería que hay que liquidar o recoger."""
    if g.empty:
        return pd.DataFrame()
    ob = g[g["estado_cadena"].isin(ESTADOS_LIQUIDACION)].copy()
    if ob.empty:
        return ob
    ob = _con_precio(ob, precio_min_map)
    en_b1, en_b2a = set(b1["sku"]) if not b1.empty else set(), set(b2a["sku"]) if not b2a.empty else set()
    ob["en_bloque"] = np.where(ob["sku"].isin(en_b1), "1) Venta cero", np.where(ob["sku"].isin(en_b2a), "2a) Sobrestock", "—"))
    def _acc(r):
        p = r.get("precio_sugerido")
        if pd.notna(p):
            return f"🏷️ Liquidar: descuento compartido {r['dscto_sugerido']:.0%} → S/ {p:,.2f}"
        return "↩️ Recoger / devolución (ya en piso de precio)" if r["estado_cadena"] == "OBSOLETO" else "↩️ Devolución (ya en piso de precio)"
    ob["accion"] = ob.apply(_acc, axis=1)
    ob["pct_acum"], ob["top_80"] = pareto_flag(ob["capital_costo"])
    ob["_o"] = (ob["estado_cadena"] != "OBSOLETO").astype(int)
    return ob.sort_values(["_o", "capital_costo"], ascending=[True, False]).drop(columns="_o").reset_index(drop=True)


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
               "capital_4sem": round(float(b1.loc[b1["semanas_sin_venta"] == "4+", "capital_costo"].sum())) if not b1.empty else 0,
               "n_paro": int((b1["semanas_sin_venta"] == "1").sum()) if not b1.empty else 0,
               "capital_paro": round(float(b1.loc[b1["semanas_sin_venta"] == "1", "capital_costo"].sum())) if not b1.empty else 0,
               "top": _top(b1, ["sku", "nombre", "categoria", "n_tiendas_stock", "stock_cadena", "capital_costo", "edad_semanas", "accion"], top_n, "capital_costo")}
    h["b1"]["por_accion"] = {r.accion: {"modelos": int(r.modelos), "capital": round(float(r.capital))} for r in resumen_por_accion(b1).itertuples()} if not b1.empty else {}
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
    h["b2a"]["pct_capital_marca"] = round(h["b2a"]["capital"] / foto["capital_total"] * 100, 1) if foto.get("capital_total") else 0.0
    h["b2a"]["por_accion"] = {r.accion: {"modelos": int(r.modelos), "capital": round(float(r.capital))} for r in resumen_por_accion(b2a).itertuples()} if not b2a.empty else {}
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
    b1, vc_tienda = bloque_venta_cero(g, dfm, precio_min_map, tipo_evento_map, semana_iso)
    ex = set(b1["sku"]) if not b1.empty else set()
    b2a, b2b = bloque_sobrestock(g, ex, df_trans, precio_min_map, df_alertas, dfm_ref=dfm)
    b2b_det = b2b.attrs.get("detalle", pd.DataFrame()) if hasattr(b2b, "attrs") else pd.DataFrame()
    b2b_rutas = b2b.attrs.get("rutas", pd.DataFrame()) if hasattr(b2b, "attrs") else pd.DataFrame()
    ex2 = ex | (set(b2a["sku"]) if not b2a.empty else set())
    b3, umbral = bloque_ganadores(g, ex2, df_rep, df_vp, df_alertas, marca=marca)
    vp_marca = pd.DataFrame()
    if df_vp is not None and not df_vp.empty and "marca" in df_vp.columns:
        vp_marca = df_vp[df_vp["marca"].astype(str).str.upper().str.strip() == str(marca).upper().strip()].copy()
        if "neto_max" in vp_marca.columns:
            vp_marca = vp_marca.sort_values("neto_max", ascending=False).reset_index(drop=True)
    obs = bloque_obsoletos(g, b1, b2a, precio_min_map) if not g.empty else pd.DataFrame()
    foto = foto_marca(dfm, g) if not dfm.empty else {"capital_total": 0, "skus": 0, "tiendas": 0, "stock_uds": 0,
                                                     "sell_through_pct": 0.0, "margen_efectivo_pct": None, "por_estado_cadena": {}}
    h = hechos_marca(marca, foto, b1, b2a, b2b, b3, umbral, corte, semana_iso)
    h["obs"] = {"n_skus": int(len(obs)), "capital": round(float(obs["capital_costo"].sum())) if not obs.empty else 0,
                "stock_uds": int(obs["stock_cadena"].sum()) if not obs.empty else 0,
                "n_obsoleto": int((obs["estado_cadena"] == "OBSOLETO").sum()) if not obs.empty else 0,
                "capital_obsoleto": round(float(obs.loc[obs["estado_cadena"] == "OBSOLETO", "capital_costo"].sum())) if not obs.empty else 0,
                "n_preobsoleto": int((obs["estado_cadena"] == "PRE-OBSOLETO").sum()) if not obs.empty else 0,
                "n_liquidar": int(obs["accion"].str.startswith("🏷️").sum()) if not obs.empty else 0,
                "n_recoger": int(obs["accion"].str.startswith("↩️").sum()) if not obs.empty else 0,
                "pct_capital_marca": round(float(obs["capital_costo"].sum()) / foto["capital_total"] * 100, 1) if (not obs.empty and foto.get("capital_total")) else 0.0,
                "top": _top(obs, ["sku", "nombre", "categoria", "estado_cadena", "edad_semanas", "stock_cadena", "capital_costo", "en_bloque", "accion"], 5, "capital_costo")}
    h["vp"] = {"combos": int(len(vp_marca)), "skus": int(vp_marca["sku"].nunique()) if not vp_marca.empty and "sku" in vp_marca.columns else 0,
               "neto_min": round(float(vp_marca["neto_min"].sum())) if not vp_marca.empty and "neto_min" in vp_marca.columns else None,
               "neto_max": round(float(vp_marca["neto_max"].sum())) if not vp_marca.empty and "neto_max" in vp_marca.columns else None,
               "evitables": int(vp_marca["evitable"].sum()) if not vp_marca.empty and "evitable" in vp_marca.columns else 0,
               "disponible": bool(df_vp is not None and not df_vp.empty)}
    return {"marca": marca, "corte": corte, "semana_iso": semana_iso, "g": g, "dfm": dfm,
            "b1": b1, "b2a": b2a, "b2b": b2b, "b2b_detalle": b2b_det, "b2b_rutas": b2b_rutas, "b3": b3, "obs": obs, "vp_marca": vp_marca, "vc_tienda": vc_tienda, "umbral_b3": umbral, "hechos": h}


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
    return [{"SKU": r["sku"], "Producto": r["nombre"], "Línea": _linea(r.get("categoria")), "Grupo": r["grupo"], "Tiendas": int(r["n_tiendas_stock"]),
             "Stock uds": int(r["stock_cadena"]), "Capital S/": float(r["capital_costo"]), "Edad sem": int(r["edad_semanas"]),
             "⭐": "⭐ TOP 80%" if r["top_80"] else "", "Acción": r["accion"]} for _, r in b1.iterrows()]


def _filas_b2a(b2a: pd.DataFrame) -> list[dict]:
    return [{"SKU": r["sku"], "Producto": r["nombre"], "Línea": _linea(r.get("categoria")), "Estado": r["estado_cadena"],
             "Stock uds": int(r["stock_cadena"]), "Cob sem": float(r["cobertura_cadena"]) if pd.notna(r["cobertura_cadena"]) else None,
             "Capital S/": float(r["capital_costo"]), "Dscto hoy": float(r.get("pct_descuento") or 0), "Tend": r.get("tendencia", "—"),
             "⭐": "⭐ TOP 80%" if r["top_80"] else "", "Acción": r["accion"]} for _, r in b2a.iterrows()]


def _filas_b2b(b2b: pd.DataFrame) -> list[dict]:
    return [{"SKU": r["sku"], "Producto": r["nombre"], "Uds a mover": int(r["transf_uds"]), "Tiendas destino": int(r["transf_tiendas"]),
             "Contribución esperada S/": float(r["transf_ganancia"]) if pd.notna(r.get("transf_ganancia")) else None} for _, r in b2b.iterrows()]


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


_FMT_TXT = {"Capital S/": lambda v: _s(v), "Modelos": lambda v: _s(v), "Ganancia neta S/": lambda v: _s(v), "Contribución esperada S/": lambda v: _s(v), "Cob sem": lambda v: _s(v, 1), "Vta/sem": lambda v: _s(v, 1),
            "Dscto hoy": lambda v: f"{v:.0%}" if v is not None else "—", "Stock uds": lambda v: _s(v), "Uds a mover": lambda v: _s(v),
            "Necesidad uds": lambda v: _s(v), "Stock CD": lambda v: _s(v)}
_COLS_TXT = {"b1": ["SKU", "Producto", "Tiendas", "Stock uds", "Capital S/", "Edad sem", "⭐", "Acción"],
             "b2a": ["SKU", "Producto", "Estado", "Stock uds", "Cob sem", "Capital S/", "Dscto hoy", "Tend", "⭐", "Acción"],
             "b2b": ["SKU", "Producto", "Uds a mover", "Tiendas destino", "Contribución esperada S/"],
             "b3": ["SKU", "Producto", "Vta/sem", "Cob sem", "Tiendas en quiebre", "Stock CD", "Necesidad uds", "Tend", "Acción"]}


_CATEGORIAS_ACCION = [("🏷️", "Liquidar con descuento compartido"), ("⬇️", "Descuento compartido 50/50"), ("↩️", "Devolución"),
                      ("⏸️", "Frenar ingreso"), ("👁️ Exhibición +", "Exhibición + descuento compartido"), ("👁️", "Revisar exhibición")]


def categoria_accion(accion: str) -> str:
    """Normaliza la acción por modelo a su familia (sin % ni precio) para agrupar en el correo."""
    a = str(accion or "")
    for pref, cat in _CATEGORIAS_ACCION:
        if a.startswith(pref):
            return cat
    return a.split(":")[0].strip() or "—"


def resumen_por_linea(df: pd.DataFrame) -> pd.DataFrame:
    """Mix B: una fila por línea con modelos, uds, capital y el pedido dominante (conteo por acción)."""
    if df.empty:
        return pd.DataFrame(columns=["linea", "modelos", "uds", "capital", "pedido"])
    d = df.assign(lin_=df["categoria"].map(_linea) if "categoria" in df.columns else "Sin línea", cat_=df["accion"].map(categoria_accion))
    out = d.groupby("lin_").agg(modelos=("sku", "count"), uds=("stock_cadena", "sum"), capital=("capital_costo", "sum")).reset_index().rename(columns={"lin_": "linea"})
    ped = d.groupby(["lin_", "cat_"]).size().reset_index(name="n").sort_values(["lin_", "n"], ascending=[True, False])
    def _pedido(l):
        rs = list(ped[ped.lin_ == l].itertuples())
        txt = " · ".join(f"{r.n} {r.cat_.lower()}" for r in rs[:2])
        return txt + (f" · +{len(rs) - 2} otras" if len(rs) > 2 else "")
    out["pedido"] = out["linea"].map(_pedido)
    return out.sort_values("capital", ascending=False).reset_index(drop=True)


def resumen_por_accion(df: pd.DataFrame, top_n: int = 3) -> pd.DataFrame:
    """Mix C: una fila por acción pedida con modelos, capital y los N modelos de mayor capital."""
    if df.empty:
        return pd.DataFrame(columns=["accion", "modelos", "uds", "capital", "top"])
    d = df.assign(cat_=df["accion"].map(categoria_accion)).sort_values("capital_costo", ascending=False)
    out = d.groupby("cat_").agg(modelos=("sku", "count"), uds=("stock_cadena", "sum"), capital=("capital_costo", "sum")).reset_index().rename(columns={"cat_": "accion"})
    out["top"] = out["accion"].map(lambda c: " · ".join(f"{r.sku} {str(r.nombre)[:26]}" for r in d[d.cat_ == c].head(top_n).itertuples()))
    # Lo que le toca al proveedor primero (por capital); la exhibición la revisamos nosotros en tienda → al final y etiquetada
    out["_n"] = (out["accion"] == "Revisar exhibición").astype(int)
    out = out.sort_values(["_n", "capital"], ascending=[True, False]).drop(columns="_n")
    out["accion"] = out["accion"].replace({"Revisar exhibición": "Revisar exhibición (lo hacemos nosotros en tienda)"})
    return out.reset_index(drop=True)


def _seccion_resumen_txt(df: pd.DataFrame, nombre: str) -> str:
    pl = resumen_por_linea(df); pa = resumen_por_accion(df)
    filas_l = [{"Línea": r.linea, "Modelos": r.modelos, "Stock uds": int(r.uds), "Capital S/": r.capital, "Pedido": r.pedido} for r in pl.itertuples()]
    filas_a = [{"Acción pedida": r.accion, "Modelos": r.modelos, "Capital S/": r.capital, "Los 3 de mayor capital (resto en el Excel)": r.top} for r in pa.itertuples()]
    return ("Por línea:\n" + _tabla_txt(filas_l, ["Línea", "Modelos", "Stock uds", "Capital S/", "Pedido"], _FMT_TXT)
            + "\n\nQué pedimos:\n" + _tabla_txt(filas_a, ["Acción pedida", "Modelos", "Capital S/", "Los 3 de mayor capital (resto en el Excel)"], _FMT_TXT))


def tablas_texto(bloques: dict, tope_linea: int = TOP_CUERPO_LINEA, tope_plano: int = TOP_CUERPO_PLANO) -> dict:
    """Bloques del cuerpo en texto plano (para el textarea / correo sin formato).
    Devuelve {"b1","b2a","b2b","b3"} → str. B1 y B2a agrupados por línea con subtotal;
    tope de modelos por línea; el resto se remite al Excel. Los TOTALes salen de `hechos`."""
    h = bloques["hechos"]; out = {}
    # Mix B+C (Franco 20-sep): resumen por línea + resumen por acción; los modelos completos viven en el Excel.
    for key, df in (("b1", bloques["b1"]), ("b2a", bloques["b2a"])):
        if df.empty:
            out[key] = "  (sin modelos en este bloque)"; continue
        partes = []
        if key == "b1":
            hb = h["b1"]
            partes.append(f"■ Sin venta en las últimas 4 semanas: {hb['n_4sem']} modelos · S/ {_s(hb['capital_4sem'])}   ■ Vendían y no vendieron la última semana (alerta temprana): {hb['n_paro']} modelos · S/ {_s(hb['capital_paro'])}")
        partes.append(_seccion_resumen_txt(df, key))
        tot = h[key]
        partes.append(f"TOTAL {'VENTA CERO' if key == 'b1' else 'SOBRESTOCK'}: {tot['n_skus']} modelos · {_s(tot['stock_uds'])} uds · S/ {_s(tot['capital'])} · detalle por modelo en la pestaña {'1' if key == 'b1' else '2a'} del Excel")
        out[key] = "\n\n".join(partes)
    f2b = _filas_b2b(bloques["b2b"])
    out["b2b"] = (_tabla_txt(f2b[:tope_plano], _COLS_TXT["b2b"], _FMT_TXT)
                  + (f"\n  (+{len(f2b) - tope_plano} modelos más en el Excel)" if len(f2b) > tope_plano else "")
                  + f"\nTOTAL TRANSFERENCIAS: {h['b2b']['n_skus']} modelos · {_s(h['b2b']['uds'])} uds a mover · contribución esperada S/ {_s(h['b2b']['ganancia'])}\n  (El detalle de qué unidades salen de qué tienda y a cuál llegan va en la pestaña '2b. Detalle transferencias' del Excel.)") if f2b else "  (sin transferencias rentables esta semana)"
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
    num = {"Capital S/", "Ganancia neta S/", "Contribución esperada S/", "Cob sem", "Vta/sem", "Stock uds", "Uds a mover", "Necesidad uds", "Stock CD", "Tiendas", "Tiendas destino", "Edad sem", "Dscto hoy", "Modelos"}
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
    for key, df in (("b1", bloques["b1"]), ("b2a", bloques["b2a"])):
        if df.empty:
            out[key] = f"{P}(sin modelos en este bloque)</p>"; continue
        partes = []
        if key == "b1":
            hb = h["b1"]
            partes.append(f"{P}<b>■ Sin venta en las últimas 4 semanas:</b> {hb['n_4sem']} modelos · S/ {_s(hb['capital_4sem'])} &nbsp;&nbsp; <b>■ Vendían y no vendieron la última semana</b> (alerta temprana): {hb['n_paro']} modelos · S/ {_s(hb['capital_paro'])}</p>")
        pl = resumen_por_linea(df); pa = resumen_por_accion(df)
        filas_l = [{"Línea": r.linea, "Modelos": r.modelos, "Stock uds": int(r.uds), "Capital S/": r.capital, "Pedido": r.pedido} for r in pl.itertuples()]
        filas_a = [{"Acción pedida": r.accion, "Modelos": r.modelos, "Capital S/": r.capital, "Los 3 de mayor capital (resto en el Excel)": r.top} for r in pa.itertuples()]
        partes.append(f"{P}<b>Por línea</b></p>" + _tabla_html(filas_l, ["Línea", "Modelos", "Stock uds", "Capital S/", "Pedido"]))
        partes.append(f"{P}<b>Qué pedimos</b></p>" + _tabla_html(filas_a, ["Acción pedida", "Modelos", "Capital S/", "Los 3 de mayor capital (resto en el Excel)"]))
        tot = h[key]
        partes.append(f"{P}<b>TOTAL {'VENTA CERO' if key == 'b1' else 'SOBRESTOCK'}:</b> {tot['n_skus']} modelos · {_s(tot['stock_uds'])} uds · S/ {_s(tot['capital'])} · detalle por modelo en la pestaña {'1' if key == 'b1' else '2a'} del Excel</p>")
        out[key] = "".join(partes)
    f2b = _filas_b2b(bloques["b2b"])
    out["b2b"] = (_tabla_html(f2b[:tope_plano], _COLS_TXT["b2b"]) + f"{P}<b>TOTAL TRANSFERENCIAS:</b> {h['b2b']['n_skus']} modelos · {_s(h['b2b']['uds'])} uds a mover · contribución esperada S/ {_s(h['b2b']['ganancia'])}</p>{P}<span style='color:#555'>El detalle de qué unidades salen de qué tienda y a cuál llegan va en la pestaña '2b. Detalle transferencias' del Excel.</span></p>") if f2b else f"{P}(sin transferencias rentables esta semana)</p>"
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


def excel_proveedor(bloques: dict, cortes: pd.DataFrame | None = None, cmp: dict | None = None) -> bytes:
    """Pestañas: Resumen · [0. Evolución] · 1. Venta Cero (SKU) · 1b. Venta Cero x Tienda · 2a. Sobrestock ·
    2b. Rutas tienda a tienda · 2b. Detalle transferencias · 3. Ganadores · 4. Pre-obsoleto y obsoleto · Leyenda. Todas construidas de los mismos DataFrames del
    correo; los totales del Resumen salen de `hechos`. Con `cortes`/`cmp` (comparar_marca) agrega la
    hoja 0 (serie KPI × semana) y la columna "Semanas en el bloque" en 1, 2a y 3."""
    import io
    racha = (cmp or {}).get("semanas_en_bloque", {})
    def _racha(df, bloque):
        if df.empty or not racha.get(bloque):
            return None
        return df["sku"].map(sku_key).map(racha[bloque]).fillna(1).astype(int)
    h, marca, corte = bloques["hechos"], bloques["marca"], bloques["corte"]
    b1, b2a, b2b, b3 = bloques["b1"], bloques["b2a"], bloques["b2b"], bloques["b3"]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        foto = h["foto"]
        res = pd.DataFrame([
            {"Bloque": f"1. Venta cero — {GRUPO_B1_4SEM.lower()}", "Modelos": h["b1"]["n_4sem"], "Stock (uds)": None, "Capital S/ (costo)": h["b1"]["capital_4sem"], "Qué pedimos": f"liquidar / devolución lo de más de 26 sem ({h['b1']['n_liquidar']} en todo el bloque) · exhibición y precio en el resto"},
            {"Bloque": f"1. Venta cero — {GRUPO_B1_PARO.lower()} (alerta temprana)", "Modelos": h["b1"]["n_paro"], "Stock (uds)": None, "Capital S/ (costo)": h["b1"]["capital_paro"], "Qué pedimos": "revisar exhibición y precio esta semana; si repite, pasa al grupo anterior"},
            {"Bloque": "2a. Sobrestock de cadena (venden, pero cargan de más)", "Modelos": h["b2a"]["n_skus"], "Stock (uds)": h["b2a"]["stock_uds"], "Capital S/ (costo)": h["b2a"]["capital"], "Qué pedimos": f"descuento compartido 50/50: {h['b2a']['n_markdown']} · devolución: {h['b2a']['n_canje']} · frenar ingreso: {h['b2a']['n_frenar']}"},
            {"Bloque": "2b. Transferencias entre tiendas (las ejecuta la marca)", "Modelos": h["b2b"]["n_skus"], "Stock (uds)": h["b2b"]["uds"], "Capital S/ (costo)": None, "Qué pedimos": f"mover {h['b2b']['uds']:,} uds · contribución esperada S/ {h['b2b']['ganancia']:,} · detalle origen → destino en la pestaña 2b. Detalle"},
            {"Bloque": "3. Ganadores que se quedan cortos", "Modelos": h["b3"]["n_skus"], "Stock (uds)": None, "Capital S/ (costo)": None, "Qué pedimos": f"{h['b3']['n_sin_cd']} sin stock en CD (reorden) · necesidad {h['b3']['necesidad_uds']:,} uds"},
            {"Bloque": "4. Pre-obsoleto y obsoleto (transversal: vendan o no)", "Modelos": h.get("obs", {}).get("n_skus", 0), "Stock (uds)": h.get("obs", {}).get("stock_uds", 0), "Capital S/ (costo)": h.get("obs", {}).get("capital", 0),
             "Qué pedimos": f"{h.get('obs', {}).get('n_obsoleto', 0)} obsoletos (S/ {_s(h.get('obs', {}).get('capital_obsoleto'))}) + {h.get('obs', {}).get('n_preobsoleto', 0)} pre-obsoletos · liquidar {h.get('obs', {}).get('n_liquidar', 0)} · recoger/devolución {h.get('obs', {}).get('n_recoger', 0)}"},
        ])
        ws = vistas_excel._tabla_con_titulo(w, "Resumen", f"{marca} — Reporte semanal Ripley · corte {corte}", res,
                                            {"Stock (uds)": _F["S"], "Capital S/ (costo)": _F["S"]}, anchos={"Bloque": 58, "Qué pedimos": 70})
        fila = ws.max_row + 2
        ws.cell(row=fila, column=1, value=f"Foto de la marca en Ripley: S/ {foto['capital_total']:,} a costo · {foto['skus']} modelos · {foto['tiendas']} tiendas · "
                                          f"sell-through {foto['sell_through_pct']}% · margen efectivo {foto['margen_efectivo_pct'] if foto['margen_efectivo_pct'] is not None else '—'}%")
        ws.cell(row=fila + 1, column=1, value=f"Los bloques 1 y 2a suman S/ {h['b1']['capital'] + h['b2a']['capital']:,} = {round((h['b1']['capital'] + h['b2a']['capital']) / foto['capital_total'] * 100, 1) if foto['capital_total'] else 0}% del capital de la marca. Un modelo aparece en un solo bloque (1, 2a o 3); la pestaña 4 es transversal y se solapa con ellos a propósito.")
        ws.cell(row=fila + 2, column=1, value=reportes_marcas._SUPUESTOS)
        ws.cell(row=fila + 3, column=1, value="Cifras al corte de la base (no a la fecha de envío). Generado por Capi.")
        fila += 5
        ws.cell(row=fila, column=1, value="Qué contiene cada pestaña").font = vistas_excel.F_TITULO
        guia = [
            ("0. Evolución", "Serie semana a semana de los indicadores de cada frente (solo con reportes anteriores enviados con Capi)."),
            ("1. Venta Cero (SKU)", "Modelos con stock que NO vendieron ni una unidad en toda la cadena la última semana, en dos grupos: sin venta en las últimas 4 semanas y los que vendían y pararon. ⭐ = concentran el 80% del capital de su grupo. Con la venta del modelo de las 4 últimas semanas y el descuento sugerido (nunca menor al actual)."),
            ("1b. Venta Cero x Tienda", "Los mismos modelos de la pestaña 1, tienda por tienda: dónde está el stock, qué prioridad tiene en esa tienda (⭐ = 80% del capital sin venta de la tienda), la venta de esa tienda en las 4 últimas semanas (de los snapshots; si no hay, la del modelo en cadena), y la acción de piso (etiquetar, cartel o revisar exhibición)."),
            ("2a. Sobrestock", "Modelos que venden pero cargan de más a nivel cadena (cobertura ≥ 26 semanas) o entran en liquidación: acción sugerida por modelo (markdown compartido, devolución, frenar ingreso)."),
            ("2b. Rutas tienda a tienda", "Transferencias entre tiendas que ejecuta la marca (modelos con ≥12 uds a mover y demanda en destino; sin flete Ripley): cuánto se mueve de cada tienda origen a cada tienda destino, con modelos, unidades, costo total y valor venta, y fila TOTAL."),
            ("2b. Detalle transferencias", "El detalle de esas transferencias: cuántas unidades de cada modelo salen de qué tienda y llegan a cuál, con stock, venta semanal y cobertura antes/después en ambas tiendas. La cantidad busca dejar ambas en 12 semanas de cobertura."),
            ("3. Ganadores", "Modelos con buena rotación y poca cobertura (≤ 8 semanas) o acelerando: necesidad calculada, stock en CD y acción (reponer desde CD / reorden)."),
            ("4. Pre-obsoleto y obsoleto", "Vista transversal: todos los modelos con más de 6 meses sin rotación a nivel cadena (pre-obsoleto 6-9 meses, obsoleto 9 o más), vendan o no, con el bloque del correo donde ya aparecen, el descuento sugerido y si toca liquidar o recoger."),
            ("Leyenda", "Cómo se calculan los estados, la pirámide de descuentos por antigüedad, el piso de margen y las reglas de transferencia."),
        ]
        for i, (hoja_n, desc) in enumerate(guia, start=1):
            ws.cell(row=fila + i, column=1, value=hoja_n).font = vistas_excel.F_HEADER
            ws.cell(row=fila + i, column=2, value=desc)
        # 0. Evolución (solo si hay cortes previos reales)
        if cortes is not None and not cortes.empty:
            serie = serie_kpis(cortes, bloques)
            if not serie.empty and serie.shape[1] >= 2:
                ev = serie.reset_index().rename(columns={"index": "Indicador"})
                ws0 = vistas_excel._tabla_con_titulo(w, "0. Evolución", f"{marca} — Evolución semanal de los frentes (cortes generados con Capi; 'enviado' según registro) · corte {corte}",
                                                     ev, {c: _F["S"] for c in ev.columns if c != "Indicador"}, anchos={"Indicador": 40})
                # filas en % con 1 decimal (el formato por columna las dejaba como enteros)
                from openpyxl.utils import get_column_letter as _gcl
                for r_ in range(3, ws0.max_row + 1):
                    if "%" in str(ws0.cell(row=r_, column=1).value or ""):
                        for c_ in range(2, ws0.max_column + 1):
                            ws0.cell(row=r_, column=c_).number_format = "0.0"
                if cmp and cmp.get("hay_prev"):
                    fila0 = ws0.max_row + 2
                    if cmp.get("resolucion_b1") is not None:
                        quien = "reportados" if cmp.get("prev_enviado") else "detectados (corte no enviado)"
                        ws0.cell(row=fila0, column=1, value=f"De los modelos sin venta {quien} la semana {cmp['semana_prev']}, el {cmp['resolucion_b1']:.0f}% ya volvió a vender o salió de la lista.")
                        fila0 += 1
                    pers = cmp.get("persistentes", {}).get("b1", [])
                    if pers:
                        ws0.cell(row=fila0, column=1, value=f"{len(pers)} modelos llevan {PERSISTENCIA_ALERTA} o más semanas seguidas sin venta (ver columna 'Semanas en el bloque' en la hoja 1).")
        # 1
        c1 = [("sku", "SKU"), ("nombre", "Producto"), ("categoria", "Línea"), ("grupo", "Grupo"), ("temporada", "Temporada"), ("estado_cadena", "Estado"), ("n_tiendas_stock", "Tiendas con stock"),
              ("stock_cadena", "Stock (uds)"), ("capital_costo", "Capital S/ (costo)"), ("pct_acum", "% acum."), ("top_80", "Prioridad"), ("semanas_sin_venta", "Sem sin venta"),
              ("vta_sem1", "Vta sem -1 (cadena)"), ("vta_sem2", "Vta sem -2"), ("vta_sem3", "Vta sem -3"), ("vta_sem4", "Vta sem -4"),
              ("edad_semanas", "Edad (sem)"), ("pct_descuento", "Dscto actual"), ("dscto_piramide", "Dscto pirámide"), ("dscto_sugerido", "Dscto sugerido"), ("precio_vigente", "P. Vigente"),
              ("precio_sugerido", "P. Sugerido"), ("precio_minimo", "P. Mínimo (piso)"), ("accion", "Acción sugerida")]
        d1 = b1[[a for a, _ in c1 if a in b1.columns]].rename(columns=dict(c1)).copy() if not b1.empty else pd.DataFrame()
        if not d1.empty:
            d1["Prioridad"] = np.where(d1["Prioridad"], "⭐ TOP 80%", "")
            r1 = _racha(b1, "b1")
            if r1 is not None:
                d1.insert(min(11, len(d1.columns)), "Semanas en el bloque", r1.values)
        _hoja_o_vacia(w, "1. Venta Cero (SKU)", f"{marca} — Modelos con stock y SIN venta la última semana en toda la cadena · ⭐ = concentran el 80% del capital · corte {corte}",
                      d1, {"Stock (uds)": _F["S"], "Capital S/ (costo)": _F["S"], "% acum.": _F["PCT"], "Edad (sem)": "0", "Dscto actual": _F["PCT"], "Dscto pirámide": _F["PCT"], "Dscto sugerido": _F["PCT"],
                           "Vta sem -1 (cadena)": _F["S"], "Vta sem -2": _F["S"], "Vta sem -3": _F["S"], "Vta sem -4": _F["S"],
                           "P. Vigente": _F["P"], "P. Sugerido": _F["P"], "P. Mínimo (piso)": _F["P"]}, chips_col="Estado")
        # 1b: detalle por tienda de los modelos del bloque 1
        vc = bloques["vc_tienda"]
        ren1b = {"tienda": "Tienda", "sku": "SKU", "nombre": "Producto", "categoria": "Línea", "grupo": "Grupo", "stock_total": "Stock (uds)",
                 "stock_valor_costo": "Capital S/", "pct_acum_tienda": "% acum. en tienda", "top_80": "Prioridad en tienda",
                 "vta_sem1": "Vta sem -1 (cadena)", "vta_sem2": "Vta sem -2", "vta_sem3": "Vta sem -3", "vta_sem4": "Vta sem -4",
                 "vt_sem1": "Vta tienda sem -1", "vt_sem2": "Vta tienda sem -2", "vt_sem3": "Vta tienda sem -3", "vt_sem4": "Vta tienda sem -4",
                 "precio_vigente": "Precio", "pct_descuento": "Dscto", "tipo_evento": "Tipo evento", "edad_semanas": "Edad (sem)", "accion": "Acción de piso"}
        d1b = vc.rename(columns=ren1b) if vc is not None and not vc.empty else pd.DataFrame()
        if not d1b.empty:
            # En 1b solo la venta de ESA tienda (Franco 19-sep). La de cadena ya está en la hoja 1;
            # se usa como respaldo únicamente si no hay snapshots por tienda para ninguna semana.
            _vt_cols = [c for c in d1b.columns if c.startswith("Vta tienda")]
            _vc_cols = [c for c in ("Vta sem -1 (cadena)", "Vta sem -2", "Vta sem -3", "Vta sem -4") if c in d1b.columns]
            if _vt_cols and not d1b[_vt_cols].isna().all().all():
                d1b = d1b.drop(columns=_vc_cols)
            else:
                d1b = d1b.drop(columns=_vt_cols)
        _hoja_o_vacia(w, "1b. Venta Cero x Tienda",
                      f"{marca} — Los modelos del bloque 1, tienda por tienda: dónde está el stock que no vendió la última semana (⭐ = concentra el 80% del capital sin venta de esa tienda) · corte {corte}",
                      d1b, {"Stock (uds)": _F["S"], "Capital S/": _F["S"], "% acum. en tienda": "0%", "Vta sem -1 (cadena)": _F["S"], "Vta sem -2": _F["S"],
                            "Vta sem -3": _F["S"], "Vta sem -4": _F["S"], "Vta tienda sem -1": _F["S"], "Vta tienda sem -2": _F["S"], "Vta tienda sem -3": _F["S"],
                            "Vta tienda sem -4": _F["S"], "Precio": _F["P"], "Dscto": "0%", "Edad (sem)": "0"})
        # 2a
        c2 = [("sku", "SKU"), ("nombre", "Producto"), ("categoria", "Línea"), ("temporada", "Temporada"), ("grupo", "Grupo"), ("estado_cadena", "Estado"), ("tendencia", "Tendencia"),
              ("stock_cadena", "Stock (uds)"), ("vta_sem_prom4", "Vta sem (prom 4)"), ("cobertura_cadena", "Cobertura (sem)"), ("capital_costo", "Capital S/ (costo)"),
              ("pct_acum", "% acum."), ("top_80", "Prioridad"), ("edad_semanas", "Edad (sem)"), ("costo", "Costo unit."), ("pct_descuento", "Dscto actual"), ("dscto_piramide", "Dscto pirámide"), ("dscto_sugerido", "Dscto sugerido"),
              ("precio_blanco", "P. Blanco"), ("precio_vigente", "P. Vigente"), ("precio_sugerido", "P. Sugerido"), ("margen_resultante", "Margen result."), ("precio_minimo", "P. Mínimo (piso)"),
              ("accion", "Acción sugerida"), ("alternativas", "Alternativas")]
        d2 = b2a[[a for a, _ in c2 if a in b2a.columns]].rename(columns=dict(c2)).copy() if not b2a.empty else pd.DataFrame()
        if not d2.empty:
            d2["Prioridad"] = np.where(d2["Prioridad"], "⭐ TOP 80%", "")
            r2 = _racha(b2a, "b2a")
            if r2 is not None:
                d2.insert(min(13, len(d2.columns)), "Semanas en el bloque", r2.values)
        _hoja_o_vacia(w, "2a. Sobrestock", f"{marca} — Sobrestock y liquidación a nivel cadena (venden, pero cargan de más) · corte {corte}",
                      d2, {**reportes_marcas._FMTS_PRECIO, "% acum.": _F["PCT"]}, chips_col="Estado")
        # 2b: el consolidado por modelo se retiró del Excel (Franco 20-sep: todo se ve en el detalle); queda en el correo y en la app
        # 2b rutas: cuadro tienda origen → tienda destino
        rutas = bloques.get("b2b_rutas", pd.DataFrame())
        d3r = rutas.rename(columns={"tienda_origen": "Tienda origen", "tienda_destino": "Tienda destino", "modelos": "Modelos", "uds": "Uds a mover",
                                    "costo_total": "Costo total S/", "valor_venta": "Valor venta S/"}) if rutas is not None and not rutas.empty else pd.DataFrame()
        if not d3r.empty:
            tot = {"Tienda origen": "TOTAL", "Tienda destino": "", "Modelos": int(bloques["b2b"]["sku"].nunique()), "Uds a mover": int(d3r["Uds a mover"].sum())}
            for c in ("Costo total S/", "Valor venta S/"):
                if c in d3r.columns: tot[c] = float(d3r[c].sum())
            d3r = pd.concat([d3r, pd.DataFrame([tot])], ignore_index=True)
        _hoja_o_vacia(w, "2b. Rutas tienda a tienda", f"{marca} — Cuánto se mueve de cada tienda a cada tienda: modelos, unidades, costo total (uds × costo) y valor venta · corte {corte}",
                      d3r, {"Modelos": _F["S"], "Uds a mover": _F["S"], "Costo total S/": _F["S"], "Valor venta S/": _F["S"]})
        # 2b detalle: qué unidades salen de qué tienda y a cuál llegan
        det = bloques.get("b2b_detalle", pd.DataFrame())
        orden_det = [("sku", "SKU"), ("nombre", "Producto"), ("categoria", "Línea"),
                     ("tienda_origen", "Tienda origen"), ("stock_origen", "Stock origen"), ("vta_sem_origen", "Vta/sem origen"), ("cob_origen_pre", "Cob origen antes (sem)"), ("cob_origen_post", "Cob origen después (sem)"),
                     ("tienda_destino", "Tienda destino"), ("stock_destino", "Stock destino"), ("vta_sem_destino", "Vta/sem destino"), ("cob_destino_pre", "Cob destino antes (sem)"), ("cob_destino_post", "Cob destino después (sem)"),
                     ("uds_transferir", "Uds a mover"), ("costo_total", "Costo total S/"), ("valor_venta", "Valor venta S/"), ("uds_vendibles_horizonte", "Uds vendibles 8 sem"),
                     ("contrib_esperada", "Contribución esperada S/ (sin flete)"), ("veredicto", "Veredicto"), ("precio_vigente", "Precio"), ("motivo", "Motivo")]
        d3d = det[[a for a, _ in orden_det if a in det.columns]].rename(columns=dict(orden_det)) if det is not None and not det.empty else pd.DataFrame()
        _hoja_o_vacia(w, "2b. Detalle transferencias",
                      f"{marca} — Detalle de las transferencias, tienda origen → tienda destino. Cantidad = min(exceso origen, déficit destino) con cobertura objetivo de 12 semanas "
                      f"(exceso = stock − 12 × venta/sem; déficit = 12 × venta/sem − stock). Suma por modelo = 'Uds a mover' del bloque 2b del correo · corte {corte}",
                      d3d, {"Uds a mover": _F["S"], "Costo total S/": _F["S"], "Valor venta S/": _F["S"], "Uds vendibles 8 sem": _F["C"], "Contribución esperada S/ (sin flete)": _F["S"], "Stock origen": _F["S"], "Stock destino": _F["S"], "Vta/sem origen": "0.00", "Vta/sem destino": "0.00",
                            "Cob origen antes (sem)": _F["C"], "Cob origen después (sem)": _F["C"], "Cob destino antes (sem)": _F["C"], "Cob destino después (sem)": _F["C"], "Precio": _F["P"]})
        # 3
        c4 = [("sku", "SKU"), ("nombre", "Producto"), ("categoria", "Línea"), ("temporada", "Temporada"), ("tendencia", "Tendencia"), ("entra_por", "Entra por"),
              ("vta_sem_prom4", "Vta sem (prom 4)"), ("stock_cadena", "Stock (uds)"), ("cobertura_cadena", "Cobertura (sem)"), ("n_tiendas_quiebre", "Tiendas en quiebre"),
              ("n_tiendas", "Tiendas"), ("stock_cd", "Stock CD"), ("on_order", "On order"), ("necesidad_uds", "Necesidad (uds)"), ("desde_cd_uds", "A girar hoy (uds)"),
              ("pendiente_sin_cd_uds", "Pendiente sin CD (uds)"), ("sem_en_quiebre_max", "Sem en quiebre (máx)"), ("vp_neto_min", "Venta perdida S/ (mín)"),
              ("vp_neto_max", "Venta perdida S/ (máx)"), ("accion", "Acción sugerida")]
        d4 = b3[[a for a, _ in c4 if a in b3.columns]].rename(columns=dict(c4)).copy() if not b3.empty else pd.DataFrame()
        if not d4.empty:
            r4 = _racha(b3, "b3")
            if r4 is not None:
                d4.insert(min(6, len(d4.columns)), "Semanas en el bloque", r4.values)
        _hoja_o_vacia(w, "3. Ganadores", f"{marca} — Modelos con buena rotación que se están quedando cortos (venta ≥ {h['b3']['umbral_vta']} u/sem y cobertura ≤ {B3_COB_MAX:.0f} sem o tendencia ▲) · corte {corte}",
                      d4, {"Vta sem (prom 4)": _F["C"], "Stock (uds)": _F["S"], "Cobertura (sem)": _F["C"], "Stock CD": _F["S"], "On order": _F["S"], "Necesidad (uds)": _F["S"],
                           "A girar hoy (uds)": _F["S"], "Pendiente sin CD (uds)": _F["S"], "Venta perdida S/ (mín)": _F["S"], "Venta perdida S/ (máx)": _F["S"]})
        # 4. Pre-obsoleto y obsoleto (transversal)
        obs = bloques.get("obs", pd.DataFrame()); ho = h.get("obs", {})
        c5 = [("sku", "SKU"), ("nombre", "Producto"), ("categoria", "Línea"), ("temporada", "Temporada"), ("estado_cadena", "Estado"), ("en_bloque", "Aparece en"),
              ("edad_semanas", "Edad (sem)"), ("n_tiendas_stock", "Tiendas con stock"), ("stock_cadena", "Stock (uds)"), ("capital_costo", "Capital S/ (costo)"), ("pct_acum", "% acum."), ("top_80", "Prioridad"),
              ("vta_sem_prom4", "Vta sem (prom 4)"), ("cobertura_cadena", "Cobertura (sem)"), ("costo", "Costo unit."), ("pct_descuento", "Dscto actual"), ("dscto_piramide", "Dscto pirámide"),
              ("dscto_sugerido", "Dscto sugerido"), ("precio_blanco", "P. Blanco"), ("precio_vigente", "P. Vigente"), ("precio_sugerido", "P. Sugerido"), ("margen_resultante", "Margen result."),
              ("precio_minimo", "P. Mínimo (piso)"), ("accion", "Acción sugerida")]
        d5 = obs[[a for a, _ in c5 if a in obs.columns]].rename(columns=dict(c5)).copy() if obs is not None and not obs.empty else pd.DataFrame()
        if not d5.empty:
            d5["Prioridad"] = np.where(d5["Prioridad"], "⭐ TOP 80%", "")
        _hoja_o_vacia(w, "4. Pre-obsoleto y obsoleto",
                      f"{marca} — Mercadería pre-obsoleta (6-9 meses) y obsoleta (9 meses a más) a nivel cadena, venda o no: {ho.get('n_skus', 0)} modelos · S/ {_s(ho.get('capital'))} ({ho.get('pct_capital_marca', 0)}% del capital) · "
                      f"liquidar {ho.get('n_liquidar', 0)} · recoger/devolución {ho.get('n_recoger', 0)} · corte {corte}",
                      d5, {**reportes_marcas._FMTS_PRECIO, "% acum.": _F["PCT"], "Tiendas con stock": _F["S"]}, chips_col="Estado")
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
# CAPI_SNAPSHOTS_DIR permite aislar la persistencia (tests de UI, corridas de prueba) sin tocar snapshots/.
_SNAPSHOTS_DIR = _os.environ.get("CAPI_SNAPSHOTS_DIR") or _SNAPSHOTS_DIR

ARCHIVO_CORTE = "proveedor.parquet"
COLS_CORTE = ["marca", "semana_iso", "corte", "bloque", "sku", "nombre", "categoria", "estado", "capital", "uds",
              "cobertura", "vta_sem", "n_tiendas", "n_tiendas_quiebre", "stock_cd", "accion", "top_80",
              "ganancia", "vp_neto_max", "capital_obsoleto", "enviado", "fecha_envio", "generado"]
# bloque "foto": una fila por marca con capital total (capital), stock (uds), sell-through % (vta_sem), margen % (cobertura)
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
    def _mk(df, bloque, capital, uds, cob, vta, estado, accion, top, ganancia=None, vp=None):
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
            "ganancia": df[ganancia].astype(float).values if ganancia and ganancia in df.columns else np.nan,
            "vp_neto_max": df[vp].astype(float).values if vp and vp in df.columns else np.nan,
            "capital_obsoleto": np.nan,
            "enviado": bool(enviado), "fecha_envio": ahora if enviado else "", "generado": ahora,
        })
        partes.append(d)
    _mk(bloques["b1"], "b1", "capital_costo", "stock_cadena", "cobertura_cadena", "vta_sem_prom4", "estado_cadena", "accion", "top_80")
    _mk(bloques["b2a"], "b2a", "capital_costo", "stock_cadena", "cobertura_cadena", "vta_sem_prom4", "estado_cadena", "accion", "top_80")
    _mk(bloques["b2b"], "b2b", "capital_costo", "transf_uds", "cobertura_cadena", None, "estado_cadena", "accion", None, ganancia="transf_ganancia")
    _mk(bloques["b3"], "b3", "capital_costo", "stock_cadena", "cobertura_cadena", "vta_sem_prom4", "estado_cadena", "accion", None, vp="vp_neto_max")
    f = bloques["hechos"].get("foto", {})
    foto = pd.DataFrame([{"marca": marca, "semana_iso": semana_iso, "corte": corte, "bloque": "foto", "sku": "", "nombre": "FOTO DE LA MARCA", "categoria": "",
                          "estado": "", "capital": float(f.get("capital_total") or 0), "uds": float(f.get("stock_uds") or 0), "cobertura": float(f["margen_efectivo_pct"]) if f.get("margen_efectivo_pct") is not None else np.nan,
                          "vta_sem": float(f.get("sell_through_pct") or 0), "n_tiendas": int(f.get("tiendas") or 0), "n_tiendas_quiebre": 0, "stock_cd": 0.0, "accion": "", "top_80": False,
                          "ganancia": np.nan, "vp_neto_max": float(bloques["hechos"].get("vp", {}).get("neto_max") or 0) if bloques["hechos"].get("vp", {}).get("neto_max") is not None else np.nan,
                          "capital_obsoleto": float(bloques["hechos"].get("obs", {}).get("capital") or 0),
                          "enviado": bool(enviado), "fecha_envio": ahora if enviado else "", "generado": ahora}])
    partes.append(foto)
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
    b1, b2a, b2b, b3, foto = _b("b1"), _b("b2a"), _b("b2b"), _b("b3"), _b("foto")
    g_col = b2b["ganancia"] if "ganancia" in b2b.columns else pd.Series(dtype=float)
    vp_col = b3["vp_neto_max"] if "vp_neto_max" in b3.columns else pd.Series(dtype=float)
    return {"b1": {"n_skus": len(b1), "capital": float(b1["capital"].sum()), "stock_uds": float(b1["uds"].sum())},
            "b2a": {"n_skus": len(b2a), "capital": float(b2a["capital"].sum()),
                    "capital_pct": (round(float(b2a["capital"].sum()) / float(foto["capital"].sum()) * 100, 1) if len(foto) and float(foto["capital"].sum()) > 0 else None)},
            "b2b": {"n_skus": len(b2b), "uds": float(b2b["uds"].sum()), "ganancia": float(g_col.fillna(0).sum())},
            "b3": {"n_skus": len(b3), "vta_sem_total": float(b3["vta_sem"].fillna(0).sum()), "vp_neto_max": float(vp_col.fillna(0).sum())},
            "foto": {"capital_total": float(foto["capital"].sum()) if len(foto) else None,
                     "sell_through_pct": float(foto["vta_sem"].iloc[0]) if len(foto) else None,
                     "vp_marca_max": float(foto["vp_neto_max"].iloc[0]) if len(foto) and "vp_neto_max" in foto.columns and pd.notna(foto["vp_neto_max"].iloc[0]) else None,
                     "capital_obsoleto": float(foto["capital_obsoleto"].iloc[0]) if len(foto) and "capital_obsoleto" in foto.columns and pd.notna(foto["capital_obsoleto"].iloc[0]) else None}}


def _kpis_de_hechos(h: dict) -> dict:
    f = h.get("foto", {})
    return {"b1": {"n_skus": h["b1"]["n_skus"], "capital": float(h["b1"]["capital"]), "stock_uds": float(h["b1"]["stock_uds"])},
            "b2a": {"n_skus": h["b2a"]["n_skus"], "capital": float(h["b2a"]["capital"]),
                    "capital_pct": (round(float(h["b2a"]["capital"]) / float(f["capital_total"]) * 100, 1) if f.get("capital_total") else None)},
            "b2b": {"n_skus": h["b2b"]["n_skus"], "uds": float(h["b2b"]["uds"]), "ganancia": float(h["b2b"].get("ganancia") or 0)},
            "b3": {"n_skus": h["b3"]["n_skus"], "vta_sem_total": float(h["b3"]["vta_sem_total"]), "vp_neto_max": float(h["b3"].get("vp_neto_max") or 0)},
            "foto": {"capital_total": float(f.get("capital_total") or 0), "sell_through_pct": float(f.get("sell_through_pct") or 0),
                     "vp_marca_max": (float(h["vp"]["neto_max"]) if h.get("vp", {}).get("neto_max") is not None else None),
                     "capital_obsoleto": float(h.get("obs", {}).get("capital") or 0)}}


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
        out["skus"] = {b: {"persisten": [], "salieron": [], "nuevos": sorted(actual.loc[actual["bloque"] == b, "sku"])} for b in ("b1", "b2a", "b2b", "b3")}
        out["persistentes"] = {b: [] for b in ("b1", "b2a", "b2b", "b3")}
        return out
    semanas = sorted(cortes_prev["semana_iso"].unique())
    prev_w = semanas[-1]
    prev = cortes_prev[cortes_prev["semana_iso"] == prev_w]
    out.update(hay_prev=True, semana_prev=prev_w, consecutivas=(semana and _semana_anterior(semana) == prev_w))
    k_prev = _kpis_de_filas(prev)
    for b, d in k_act.items():
        out["kpis"][b] = {}
        for k, v in d.items():
            p = k_prev.get(b, {}).get(k)
            da = (v - p) if (p is not None and v is not None) else None
            if k.endswith("_pct"):
                dp = round(da, 1) if da is not None else None          # KPI en %: delta en puntos
            else:
                dp = (round(da / p * 100, 1) if p else None) if da is not None else None
            out["kpis"][b][k] = {"actual": v, "prev": p, "delta_abs": da, "delta_pct": dp}
    out["prev_enviado"] = bool(prev["enviado"].any()) if "enviado" in prev.columns else False
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
        # orden: más semanas primero y, a igual racha, más capital (que el correo cite lo que pesa, no billeteras sueltas)
        cap_b = actual.loc[actual["bloque"] == b].set_index("sku")["capital"].to_dict()
        out["persistentes"][b] = sorted([s for s, n in racha.items() if n >= PERSISTENCIA_ALERTA], key=lambda s: (-racha[s], -cap_b.get(s, 0), s))
    p1 = set(prev.loc[prev["bloque"] == "b1", "sku"])
    if p1:
        out["resolucion_b1"] = round(len(p1 - set(actual.loc[actual["bloque"] == "b1", "sku"])) / len(p1) * 100, 1)
    return out


_KPI_LABELS = [("foto", "capital_total", "Capital total de la marca S/"), ("foto", "sell_through_pct", "Sell-through % (semanal)"),
               ("b1", "capital", "Venta cero — capital S/"), ("b1", "n_skus", "Venta cero — modelos"),
               ("b2a", "capital", "Sobrestock — capital S/"), ("b2a", "capital_pct", "Sobrestock — % del capital total"), ("b2a", "n_skus", "Sobrestock — modelos"),
               ("b2b", "uds", "Transferencias — uds a mover"), ("b2b", "ganancia", "Transferencias — contribución esperada S/"),
               ("foto", "capital_obsoleto", "Pre-obsoleto + obsoleto — capital S/"),
               ("b3", "n_skus", "Ganadores cortos — modelos")]


def evolucion_texto(cmp: dict, bloques: dict) -> str:
    """Bloque 0 del correo (solo si hay corte previo REAL). Texto plano."""
    if not cmp.get("hay_prev"):
        return ""
    filas = []
    for b, k, lab in _KPI_LABELS:
        d = cmp["kpis"][b][k]
        flecha = "" if d["delta_abs"] is None else ("▲" if d["delta_abs"] > 0 else ("▼" if d["delta_abs"] < 0 else "="))
        es_pct = k.endswith("_pct")
        dpct = "" if d["delta_pct"] is None else (f" ({d['delta_pct']:+.1f} pp)" if es_pct else f" ({d['delta_pct']:+.0f}%)")
        fmt = (lambda v: "—" if v is None else f"{v:.1f}%") if es_pct else _s
        filas.append({"Indicador": lab, f"Sem {cmp['semana_prev']}": fmt(d["prev"]), f"Sem {cmp['semana']}": fmt(d["actual"]), "Δ": f"{flecha} {(lambda v: '—' if v is None else f'{v:+.1f}') (d['delta_abs']) if es_pct else _s(d['delta_abs'])}{dpct}".strip()})
    cols = list(filas[0].keys())
    txt = _tabla_txt(filas, cols, {})
    extra = []
    if cmp.get("resolucion_b1") is not None:
        quien = "que les reportamos la semana pasada" if cmp.get("prev_enviado") else "de la semana pasada"
        extra.append(f"De los modelos sin venta {quien}, el {cmp['resolucion_b1']:.0f}% ya volvió a vender o salió de la lista.")
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
        es_pct = k.endswith("_pct")
        dpct = "" if d["delta_pct"] is None else (f" ({d['delta_pct']:+.1f} pp)" if es_pct else f" ({d['delta_pct']:+.0f}%)")
        fmt = (lambda v: "—" if v is None else f"{v:.1f}%") if es_pct else _s
        filas.append({"Indicador": lab, f"Sem {cmp['semana_prev']}": fmt(d["prev"]), f"Sem {cmp['semana']}": fmt(d["actual"]), "Δ": f"{flecha} {(lambda v: '—' if v is None else f'{v:+.1f}') (d['delta_abs']) if es_pct else _s(d['delta_abs'])}{dpct}".strip()})
    cols = list(filas[0].keys())
    html = _tabla_html(filas, cols)
    P = "<p style='font-family:Calibri,Arial;font-size:10.5pt;margin:4px 0'>"
    if cmp.get("resolucion_b1") is not None:
        quien = "que les reportamos la semana pasada" if cmp.get("prev_enviado") else "de la semana pasada"
        html += f"{P}De los modelos sin venta {quien}, el <b>{cmp['resolucion_b1']:.0f}%</b> ya volvió a vender o salió de la lista.</p>"
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


# ══════════════════════════════════════════════════════════════════════════════
#  RESPUESTA DEL PROVEEDOR (C12): lo que el proveedor confirma por correo, cargado por
#  Daniela, viaja a Notion (📈 Proveedores Capi + 📋 Acciones Capi). Score = solo confirmado.
# ══════════════════════════════════════════════════════════════════════════════
import json as _json

ACCIONES_PROVEEDOR = ["Descuento compartido 50/50", "Transferencia entre tiendas", "Devolución con recompra",
                      "Reposición / reorden", "Exhibición en tienda", "Rechazó", "Otro"]
BLOQUES_LABEL = {"b1": "1) Venta cero", "b2a": "2a) Sobrestock", "b2b": "2b) Transferencias", "b3": "3) Ganadores"}
RESPONDIO = ["Sin respuesta aún", "Sí", "Parcial", "No"]
_DIR_RESPUESTAS = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "acciones", "proveedor")


def ruta_respuesta(marca: str, semana_iso: str, base_dir: str | None = None) -> str:
    d = _os.path.join(base_dir or _os.environ.get("CAPI_RESPUESTAS_DIR") or _DIR_RESPUESTAS, str(marca).upper().replace("/", "-"))
    return _os.path.join(d, f"{semana_iso}.json")


def cargar_respuesta(marca: str, semana_iso: str, base_dir: str | None = None) -> dict:
    r = ruta_respuesta(marca, semana_iso, base_dir)
    if _os.path.exists(r):
        try:
            return _json.load(open(r, encoding="utf-8"))
        except Exception:
            pass
    return {"respondio": RESPONDIO[0], "fecha_respuesta": "", "notas": "", "compromisos": []}


def guardar_respuesta(marca: str, semana_iso: str, respuesta: dict, base_dir: str | None = None) -> str:
    r = ruta_respuesta(marca, semana_iso, base_dir)
    _os.makedirs(_os.path.dirname(r), exist_ok=True)
    _json.dump(respuesta, open(r, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return r


def score_respuesta(hechos: dict, respuesta: dict) -> dict:
    """respuesta_pct = modelos con compromiso / modelos enviados (TOP 80% de B1 + B2a + B2b + B3);
    cumplimiento_pct = compromisos marcados cumplidos / compromisos."""
    comps = respuesta.get("compromisos") or []
    enviados = int(hechos["b1"].get("n_top", 0)) + int(hechos["b2a"].get("n_skus", 0)) + int(hechos["b2b"].get("n_skus", 0)) + int(hechos["b3"].get("n_skus", 0))
    con_comp = set()
    for c in comps:
        if str(c.get("accion", "")).startswith("Rechazó"):
            continue
        for s in c.get("skus") or []:
            con_comp.add(sku_key(s))
    n_cump = sum(1 for c in comps if c.get("cumplido"))
    return {"n_compromisos": len(comps), "n_cumplidos": n_cump, "modelos_enviados": enviados, "modelos_con_compromiso": len(con_comp),
            "respuesta_pct": round(min(len(con_comp), enviados) / enviados * 100, 1) if enviados else None,
            "cumplimiento_pct": round(n_cump / len(comps) * 100, 1) if comps else None}


def props_notion_proveedor(bloques: dict, cmp: dict | None = None, enviado: bool = False, respuesta: dict | None = None,
                           fecha_envio: str | None = None) -> dict:
    """Propiedades de la fila marca × semana en 📈 Proveedores Capi (formato Notion vía notion_store.p_*)."""
    import notion_store as ns
    h = bloques["hechos"]; marca = str(bloques["marca"]).upper().strip(); sem = bloques.get("semana_iso") or ""
    cmp = cmp or {}; resp = respuesta or {}
    def _d(b, k, campo="delta_pct"):
        try:
            return cmp["kpis"][b][k][campo]
        except Exception:
            return None
    sc = score_respuesta(h, resp) if resp else {}
    props = {
        "Marca × Semana": ns.p_title(f"{marca} · {sem}"), "Marca": ns.p_text(marca), "Semana ISO": ns.p_text(sem), "Corte": ns.p_text(str(bloques.get("corte", ""))),
        "VC capital": ns.p_number(h["b1"]["capital"]), "VC modelos": ns.p_number(h["b1"]["n_skus"]), "VC combos": ns.p_number(len(bloques["vc_tienda"]) if bloques.get("vc_tienda") is not None else 0),
        "SOB capital": ns.p_number(h["b2a"]["capital"]), "SOB modelos": ns.p_number(h["b2a"]["n_skus"]), "DESB uds": ns.p_number(h["b2b"]["uds"]),
        "GAN modelos": ns.p_number(h["b3"]["n_skus"]), "GAN vta sem": ns.p_number(h["b3"]["vta_sem_total"]),
        "Δ VC %": ns.p_number(_d("b1", "capital")), "Δ SOB %": ns.p_number(_d("b2a", "capital")), "Δ GAN": ns.p_number(_d("b3", "n_skus", "delta_abs")),
        "Persistentes 3 sem": ns.p_number(len((cmp.get("persistentes") or {}).get("b1", []))), "Resolución VC %": ns.p_number(cmp.get("resolucion_b1")),
        "Respondió": ns.p_select(resp.get("respondio") or RESPONDIO[0]),
        "Compromisos": ns.p_number(sc.get("n_compromisos", 0)), "Cumplidos": ns.p_number(sc.get("n_cumplidos", 0)),
        "Respuesta %": ns.p_number(sc.get("respuesta_pct")), "Cumplimiento %": ns.p_number(sc.get("cumplimiento_pct")),
        "Compromisos detalle": ns.p_text(_json.dumps(resp.get("compromisos") or [], ensure_ascii=False)[:ns.MAX_TEXTO]),
        "Notas": ns.p_text(str(resp.get("notas") or "")[:ns.MAX_TEXTO]),
        "Registrado desde": ns.p_select("nube" if ns.en_nube() else "laptop"),
    }
    if enviado:
        props["Enviado"] = ns.p_date((fecha_envio or _date.today().isoformat())[:10])
    if resp.get("fecha_respuesta"):
        props["Fecha respuesta"] = ns.p_date(str(resp["fecha_respuesta"])[:10])
    return props


def props_respuesta_solo(marca: str, semana_iso: str, respuesta: dict, hechos: dict | None = None) -> dict:
    """Props de respuesta para una semana anterior (no recalcula KPIs). Si no hay `hechos`,
    el % de respuesta no se puede calcular y se deja vacío; compromisos y cumplidos sí."""
    import notion_store as ns
    resp = respuesta or {}
    sc = score_respuesta(hechos, resp) if hechos else {"n_compromisos": len(resp.get("compromisos") or []),
                                                       "n_cumplidos": sum(1 for c in (resp.get("compromisos") or []) if c.get("cumplido")),
                                                       "respuesta_pct": None,
                                                       "cumplimiento_pct": (round(sum(1 for c in resp["compromisos"] if c.get("cumplido")) / len(resp["compromisos"]) * 100, 1) if resp.get("compromisos") else None)}
    props = {"Marca × Semana": ns.p_title(f"{str(marca).upper().strip()} · {semana_iso}"), "Marca": ns.p_text(str(marca).upper().strip()), "Semana ISO": ns.p_text(semana_iso),
             "Respondió": ns.p_select(resp.get("respondio") or RESPONDIO[0]), "Compromisos": ns.p_number(sc["n_compromisos"]), "Cumplidos": ns.p_number(sc["n_cumplidos"]),
             "Respuesta %": ns.p_number(sc.get("respuesta_pct")), "Cumplimiento %": ns.p_number(sc.get("cumplimiento_pct")),
             "Compromisos detalle": ns.p_text(_json.dumps(resp.get("compromisos") or [], ensure_ascii=False)[:ns.MAX_TEXTO]),
             "Notas": ns.p_text(str(resp.get("notas") or "")[:ns.MAX_TEXTO]), "Registrado desde": ns.p_select("nube" if ns.en_nube() else "laptop")}
    if resp.get("fecha_respuesta"):
        props["Fecha respuesta"] = ns.p_date(str(resp["fecha_respuesta"])[:10])
    return props

