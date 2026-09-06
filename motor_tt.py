"""
Motor de reposición TT a talla × color — S9 (2026-09-05). Replica la lógica del Excel
"Empuje Polos Pima TT Cacharel" de Franco y la vuelve parametrizable:

Por variación (color × talla) y tienda:
  SI  (stock ideal por cubicaje) = EVEN(ROUND(cubicaje_tienda × peso_color × curva_talla / divisor))
  UU  = venta de la ventana (3 semanas)          OH = stock en tienda
  COB = OH / (UU / ventana)
  StockIdeal_vel = ROUND(UU/ventana) × cob_objetivo   Adic = ROUND(UU/ventana) × adic_semanas
  Repo_vel = max(0, StockIdeal_vel − OH + Adic) si OH > 0; si OH ≤ 0 → curva_talla × 3 (semilla)
  Repo_cub = max(0, SI − OH)
  Repo_final = según regla: "cubicaje" (lo que usa Franco hoy), "velocidad", o "max" (el mayor).

peso_color y curva_talla salen de la venta agregada de todas las tiendas (o se pasan a mano).
El divisor de la curva es Σcurva por defecto; Franco usa 6 con una curva que suma 9 — se puede
replicar pasando divisor=6 (flag abierta en el plan).

Además: mix ideal por color/talla (peso venta vs peso stock → sugerencia de reponderar) y
totales por tienda contra el stock disponible en CD.
"""
from __future__ import annotations

import json
import math
import os

import numpy as np
import pandas as pd

CONFIG_CUBICAJE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_cubicaje_tt.json")

# Nombres de tienda del Excel de Franco → nombres del reporte de stock por variación
ALIAS_TIENDA = {"Ripley Callao": "Callao", "Jiron Union": "Jiron de la Unión", "MegaPlaza": "Los Olivos",
                "Piuraii": "Piura II", "Plaza Lima Norte": "Plaza Lima Norte", "Chiclayo II": "Chiclayo II",
                "San Juan De Lurigancho": "SJL", "Pucallpa": "PUCALLPA I"}


def _even(x: float) -> int:
    """EVEN de Excel: redondea hacia arriba al par más cercano (en magnitud)."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return 0
    n = math.ceil(abs(x))
    if n % 2:
        n += 1
    return int(math.copysign(n, x)) if x != 0 else 0


def cargar_cubicajes(programa: str | None = None, path: str = CONFIG_CUBICAJE) -> dict:
    """{tienda: {'cubicaje': uds, 'cob_objetivo_sem': n}} para el programa (o el primero del archivo)."""
    if not os.path.exists(path):
        return {}
    cfg = {k: v for k, v in json.load(open(path)).items() if not k.startswith("_")}
    if programa and programa in cfg:
        return cfg[programa]
    return next(iter(cfg.values()), {}) if cfg else {}


def pesos_desde_venta(df: pd.DataFrame) -> tuple[dict, dict]:
    """peso_color (share de venta por color) y curva_talla (uds por talla normalizadas a enteros
    tipo S1-M3-L3-XL2) desde la venta agregada de todas las tiendas."""
    vc = df.groupby("color")["vta_uds_3s"].sum()
    peso = (vc / vc.sum()).round(4).to_dict() if vc.sum() else {}
    vt = df.groupby("talla")["vta_uds_3s"].sum()
    if vt.sum():
        share = vt / vt.sum()
        curva = (share / share.max() * 3).round().clip(lower=1).astype(int).to_dict()
    else:
        curva = {}
    return peso, curva


def reposicion_tt(df: pd.DataFrame, cubicajes: dict, cob_objetivo: float = 4.0, ventana_sem: int = 3,
                  adic_semanas: int = 2, regla: str = "cubicaje", peso_color: dict | None = None,
                  curva_talla: dict | None = None, divisor: float | None = None) -> pd.DataFrame:
    """Una fila por variación × tienda con la reposición sugerida. `df` = salida de stock_variacion
    (un modelo/programa). Tiendas sin cubicaje configurado → cubicaje 0 (solo regla velocidad)."""
    if df is None or df.empty:
        return pd.DataFrame()
    pc, ct = pesos_desde_venta(df)
    peso_color = peso_color or pc
    curva_talla = curva_talla or ct
    div = divisor or (sum(curva_talla.values()) or 1)
    alias_inv = {v: k for k, v in ALIAS_TIENDA.items()}
    rows = []
    for r in df.itertuples(index=False):
        t = r.tienda
        cfg = cubicajes.get(t) or cubicajes.get(alias_inv.get(t, "")) or {}
        cub = float(cfg.get("cubicaje", 0) or 0)
        cob_t = float(cfg.get("cob_objetivo_sem", cob_objetivo) or cob_objetivo)
        pcol, ctal = float(peso_color.get(r.color, 0)), float(curva_talla.get(r.talla, 0))
        oh, uu = float(r.stock_uds), float(r.vta_uds_3s)
        si = _even(round(cub * pcol * ctal / div)) if cub > 0 else 0
        vel = round(uu / ventana_sem)
        stock_ideal_vel = vel * cob_t
        adic = vel * adic_semanas
        cob = (oh / (uu / ventana_sem)) if uu > 0 else (np.inf if oh > 0 else 0.0)
        if oh > 0:
            repo_vel = max(0.0, stock_ideal_vel - oh + adic)
        else:
            repo_vel = ctal * 3 if uu > 0 or cub > 0 else 0.0
        repo_cub = max(0.0, si - oh)
        repo = repo_cub if regla == "cubicaje" else repo_vel if regla == "velocidad" else max(repo_cub, repo_vel)
        rows.append(dict(cod_modelo=r.cod_modelo, modelo=r.modelo, cod_variacion=r.cod_variacion, variacion=r.variacion,
                         color=r.color, talla=r.talla, tienda=t, cubicaje=cub, peso_color=pcol, curva_talla=ctal,
                         stock_ideal_cubicaje=si, oh=oh, vta_3s=uu, cobertura_sem=round(cob, 1) if np.isfinite(cob) else cob,
                         stock_ideal_velocidad=stock_ideal_vel, adicional=adic,
                         repo_velocidad=int(round(repo_vel)), repo_cubicaje=int(round(repo_cub)), repo_final=int(round(repo)),
                         cd_disp=float(getattr(r, "cd_disp", 0) or 0), on_order=float(getattr(r, "on_order", 0) or 0)))
    out = pd.DataFrame(rows)
    return out.sort_values(["tienda", "color", "talla"]).reset_index(drop=True)


def resumen_por_tienda(rep: pd.DataFrame) -> pd.DataFrame:
    if rep is None or rep.empty:
        return pd.DataFrame()
    g = rep.groupby("tienda").agg(cubicaje=("cubicaje", "first"), oh=("oh", "sum"), vta_3s=("vta_3s", "sum"),
                                  stock_ideal_cubicaje=("stock_ideal_cubicaje", "sum"),
                                  repo_velocidad=("repo_velocidad", "sum"), repo_cubicaje=("repo_cubicaje", "sum"),
                                  repo_final=("repo_final", "sum")).reset_index()
    g["cobertura_sem"] = np.where(g["vta_3s"] > 0, g["oh"] / (g["vta_3s"] / 3), np.inf)
    g["cobertura_post"] = np.where(g["vta_3s"] > 0, (g["oh"] + g["repo_final"]) / (g["vta_3s"] / 3), np.inf)
    return g.sort_values("repo_final", ascending=False).reset_index(drop=True)


def cobertura_cd(rep: pd.DataFrame) -> pd.DataFrame:
    """Por variación: cuánto pide la cadena vs cuánto hay en CD; % cubierto y faltante para compra."""
    if rep is None or rep.empty:
        return pd.DataFrame()
    g = rep.groupby(["cod_variacion", "variacion", "color", "talla"]).agg(
        pedido=("repo_final", "sum"), cd_disp=("cd_disp", "max"), on_order=("on_order", "max")).reset_index()
    g["cubierto_cd"] = np.minimum(g["pedido"], g["cd_disp"])
    g["faltante_compra"] = (g["pedido"] - g["cd_disp"]).clip(lower=0)
    g["pct_cubierto"] = np.where(g["pedido"] > 0, g["cubierto_cd"] / g["pedido"], np.nan)
    return g.sort_values("faltante_compra", ascending=False).reset_index(drop=True)


def mix_ideal(df: pd.DataFrame, eje: str) -> pd.DataFrame:
    """Sugerencia de reponderación del mix: peso objetivo = peso en venta; delta vs peso actual del stock
    en unidades (cuánto stock sobra o falta por color/talla para que el stock pese como la venta)."""
    g = df.groupby(eje).agg(vta_uds_3s=("vta_uds_3s", "sum"), stock_uds=("stock_uds", "sum")).reset_index()
    tv, ts = g["vta_uds_3s"].sum(), g["stock_uds"].sum()
    g["peso_venta"] = g["vta_uds_3s"] / tv if tv else np.nan
    g["peso_stock"] = g["stock_uds"] / ts if ts else np.nan
    g["stock_objetivo"] = (g["peso_venta"] * ts).round() if tv else np.nan
    g["ajuste_uds"] = (g["stock_objetivo"] - g["stock_uds"]).round()
    g["sugerencia"] = np.select([g["ajuste_uds"] >= max(5, 0.05 * ts), g["ajuste_uds"] <= -max(5, 0.05 * ts)],
                                ["⬆️ subir peso (comprar/reponer más)", "⬇️ bajar peso (frenar producción/reponer menos)"], "🟢 mantener")
    return g.sort_values("vta_uds_3s", ascending=False).reset_index(drop=True)
