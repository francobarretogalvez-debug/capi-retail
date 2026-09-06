"""
Stock y venta por VARIACIÓN (talla × color) × tienda — S9 ingesta (2026-09-05).

Fuente: reporte "Stock para Reposición Modelo Variación" (.xlsb, pivot de Ripley):
  fila 8 = nombre de tienda repetido por bloque, fila 9 = métricas, datos desde fila 10.
  Cada tienda es un bloque de 10 columnas:
    Venta S/. · Venta Unid. (3 últimas semanas) · Stock S/. · Stock Unid. · Stk Oh Disp Und ·
    Stk OO Und · Stk Oh Disp CD Und · Stk OO CD Und · Costo Asignado · Und Asignada
  Identidad: CODMOD (modelo = Cód. Prod. del Micro), CODVAR (variación = SKU hijo),
  VARIACION = "<modelo> <color> <talla>".

Es la fuente que faltaba: el Micro llega a estilo (producto×color) y el transaccional
tiene talla pero no stock. Con esto se puede cruzar peso en venta vs peso en stock por
color y por talla — la base del motor TT (lógica Pima) y del chequeo de destallado.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

METRICAS = {
    "Venta S/.": "vta_soles_3s", "Venta Unid.": "vta_uds_3s", "Stock S/.": "stock_soles",
    "Stock Unid.": "stock_uds", "Stk Oh Disp Und": "oh_disp", "Stk OO Und": "on_order",
    "Stk Oh Disp CD Und": "cd_disp", "Stk OO CD Und": "cd_on_order",
    "Costo Asignado": "costo_asignado", "Und Asignada": "uds_asignadas",
}
TALLAS_ORDEN = ["XS", "S", "M", "L", "XL", "XXL", "XXXL", "28", "30", "32", "34", "36", "38", "40", "42", "44"]


def _split_variacion(modelo: str, variacion: str):
    """'CHOMPA K. STEVENS VNCOT-ESS VIO1 S' con modelo 'CHOMPA K. STEVENS VNCOT-ESS' → ('VIO1', 'S')."""
    v = str(variacion or "").strip()
    m = str(modelo or "").strip()
    resto = v[len(m):].strip() if m and v.upper().startswith(m.upper()) else v
    partes = [x.strip(".").strip() for x in resto.split() if x.strip(".").strip()]  # 'L.' → 'L'
    if len(partes) >= 2:
        return partes[-2].upper(), partes[-1].upper()
    if len(partes) == 1:
        return "", partes[0].upper()
    return "", ""


def leer_xlsb(path: str) -> pd.DataFrame:
    """Pivot .xlsb/.xlsx → tabla larga: una fila por variación × tienda (solo con actividad)."""
    engine = "pyxlsb" if str(path).lower().endswith(".xlsb") else None
    raw = pd.read_excel(path, header=None, engine=engine)
    # localizar fila de cabecera (la que contiene CODMOD)
    hdr_idx = next(i for i in range(min(30, len(raw))) if str(raw.iat[i, 0]).strip() == "CODMOD")
    tiendas_row = raw.iloc[hdr_idx - 1].tolist()
    metric_row = raw.iloc[hdr_idx].tolist()
    data = raw.iloc[hdr_idx + 1:].reset_index(drop=True)
    id_cols = [str(c).strip() for c in metric_row[:7]]
    ident = data.iloc[:, :7].copy()
    ident.columns = id_cols
    # bloques de tienda
    bloques = {}
    tienda_actual = None
    for j in range(7, len(metric_row)):
        t = tiendas_row[j]
        if isinstance(t, str) and t.strip():
            tienda_actual = t.strip()
        met = str(metric_row[j]).strip()
        if tienda_actual and met in METRICAS:
            bloques.setdefault(tienda_actual, {})[METRICAS[met]] = j
    frames = []
    for t, cols in bloques.items():
        b = ident.copy()
        b["tienda"] = t
        for k, j in cols.items():
            b[k] = pd.to_numeric(data.iloc[:, j], errors="coerce").fillna(0)
        for k in METRICAS.values():
            if k not in b.columns:
                b[k] = 0.0
        act = (b["stock_uds"] != 0) | (b["vta_uds_3s"] != 0) | (b["on_order"] != 0)
        frames.append(b[act])
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if out.empty:
        return out
    out = out.rename(columns={"CODMOD": "cod_modelo", "MODELO": "modelo", "CODVAR": "cod_variacion",
                              "VARIACION": "variacion", "CODPROV": "cod_proveedor", "PROVEEDOR": "proveedor",
                              "RANGO": "rango"})
    out["cod_modelo"] = out["cod_modelo"].astype(str).str.strip()
    out["cod_variacion"] = out["cod_variacion"].astype(str).str.strip()
    out["proveedor"] = out["proveedor"].astype(str).str.strip()
    ct = [_split_variacion(m, v) for m, v in zip(out["modelo"], out["variacion"])]
    out["color"] = [c for c, _ in ct]
    out["talla"] = [t for _, t in ct]
    return out.reset_index(drop=True)


def enriquecer_con_micro(df: pd.DataFrame, micro_path: str) -> pd.DataFrame:
    """Cruza cod_modelo con 'Cód. Prod.' del Micro para traer marca, línea, tipo de producto y temporada."""
    m = pd.read_excel(micro_path, usecols=["Cód. Prod.", "Marca", "Línea", "Tipo de producto", "Temp.", "Dpto"])
    m["Cód. Prod."] = m["Cód. Prod."].astype(str).str.strip()
    m = m.drop_duplicates("Cód. Prod.").rename(columns={"Cód. Prod.": "cod_modelo", "Marca": "marca", "Línea": "linea",
                                                        "Tipo de producto": "tipo_producto", "Temp.": "temporada", "Dpto": "departamento"})
    return df.merge(m, on="cod_modelo", how="left")


# ── Diagnóstico de mix (base del motor TT / destallado) ─────────────────────────

def mix_por_eje(df: pd.DataFrame, eje: str, tienda: str | None = None) -> pd.DataFrame:
    """Peso en venta vs peso en stock por `eje` ('color' o 'talla') dentro de un conjunto
    (un modelo o un programa). Cobertura en semanas = stock / (venta 3 sem / 3)."""
    d = df if tienda is None else df[df["tienda"] == tienda]
    g = d.groupby(eje).agg(vta_uds_3s=("vta_uds_3s", "sum"), stock_uds=("stock_uds", "sum"),
                           on_order=("on_order", "sum")).reset_index()
    tv, ts = g["vta_uds_3s"].sum(), g["stock_uds"].sum()
    g["peso_venta"] = g["vta_uds_3s"] / tv if tv else np.nan
    g["peso_stock"] = g["stock_uds"] / ts if ts else np.nan
    g["gap_pp"] = (g["peso_venta"] - g["peso_stock"]) * 100
    g["cobertura_sem"] = np.where(g["vta_uds_3s"] > 0, g["stock_uds"] / (g["vta_uds_3s"] / 3), np.inf)
    g["diagnostico"] = np.select(
        [(g["vta_uds_3s"] > 0) & (g["stock_uds"] == 0), g["gap_pp"] >= 5, g["gap_pp"] <= -5],
        ["🔴 quiebre", "🟠 sub-stock (vende más de lo que pesa)", "🟡 sobre-stock (pesa más de lo que vende)"], "🟢 alineado")
    if eje == "talla":
        g["_o"] = g["talla"].map({t: i for i, t in enumerate(TALLAS_ORDEN)}).fillna(99)
        g = g.sort_values("_o").drop(columns="_o")
    else:
        g = g.sort_values("vta_uds_3s", ascending=False)
    return g.reset_index(drop=True)


def curva_rota_por_tienda(df: pd.DataFrame, tallas_mayores=("S", "M", "L", "XL")) -> pd.DataFrame:
    """Por modelo × color × tienda: cuántas tallas mayores faltan (stock 0) aunque el modelo venda.
    Regla Zara (research S13): sin tallas mayores completas la opción no existe en el piso."""
    d = df[df["talla"].isin(tallas_mayores)]
    g = d.groupby(["cod_modelo", "modelo", "color", "tienda"]).agg(
        tallas_con_stock=("stock_uds", lambda x: int((x > 0).sum())),
        vta_uds_3s=("vta_uds_3s", "sum"), stock_uds=("stock_uds", "sum")).reset_index()
    g["tallas_faltantes"] = len(tallas_mayores) - g["tallas_con_stock"]
    g["curva_rota"] = (g["tallas_faltantes"] > 0) & (g["stock_uds"] > 0)
    return g.sort_values(["curva_rota", "vta_uds_3s"], ascending=[False, False]).reset_index(drop=True)


def diagnostico_tiendas(df: pd.DataFrame, cob_objetivo_sem: float = 4.0) -> pd.DataFrame:
    """Por tienda: cobertura del conjunto, venta, y diagnóstico 'faltan tallas' vs 'revisar exhibición'
    (tiene stock y curva pero no vende)."""
    cr = curva_rota_por_tienda(df)
    g = df.groupby("tienda").agg(vta_uds_3s=("vta_uds_3s", "sum"), stock_uds=("stock_uds", "sum"),
                                 on_order=("on_order", "sum")).reset_index()
    rotas = cr[cr["curva_rota"]].groupby("tienda").size().rename("opciones_curva_rota")
    g = g.merge(rotas, on="tienda", how="left").fillna({"opciones_curva_rota": 0})
    g["cobertura_sem"] = np.where(g["vta_uds_3s"] > 0, g["stock_uds"] / (g["vta_uds_3s"] / 3), np.inf)
    g["diagnostico"] = np.select(
        [g["vta_uds_3s"] == 0, g["opciones_curva_rota"] > 0, g["cobertura_sem"] < cob_objetivo_sem],
        ["👁️ Stock sin venta: revisar exhibición", "📏 Faltan tallas: completar curva", "🚚 Cobertura baja: reponer"],
        "🟢 OK")
    return g.sort_values("vta_uds_3s", ascending=False).reset_index(drop=True)
