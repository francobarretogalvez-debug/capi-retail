"""
Snapshot liviano SKU×tienda — decisión sprint Chile (2026-09-05).

El snapshot semanal (`snapshot.parquet`) es 1 fila por SKU agregado cadena; eso bloquea
todo "por tienda" en series semanales (Pareto, obsoletos, cumplimiento de empujes).
En vez de refactorizar las 17 funciones de `api.py`, se guarda un SEGUNDO parquet por
semana (`tienda.parquet`) con solo lo necesario:

    semana_iso, sku, tienda, stock_uds, vta_uds_sem, on_order, ume, stock_costo

Medido con el Micro del 30-ago: ~23K filas (stock u on-order ≠ 0), ~2 MB/semana.
Se genera junto al snapshot normal (loader) y se puede backfillear desde
`data2/bases antiguas/`. Las 17 funciones existentes no se tocan.
"""
from __future__ import annotations

import os
import re

import numpy as np
import pandas as pd

from .config import SNAPSHOTS_DIR
from .storage import list_available_weeks

COLS = ["semana_iso", "sku", "tienda", "stock_uds", "vta_uds_sem", "on_order", "ume", "stock_costo"]


def _detect_stores(columns) -> list:
    cols = set(columns)
    return [c[:-4] for c in columns if c.endswith(" Stk") and f"{c[:-4]} UME" in cols and f"{c[:-4]} On Order" in cols]


def build_from_base(df_raw: pd.DataFrame, semana_iso: str) -> pd.DataFrame:
    """Base Profundidad (formato ancho, columnas originales de Ripley) → tabla larga SKU×tienda.
    Solo filas con stock u on-order ≠ 0 (la ausencia se infiere)."""
    stores = _detect_stores(list(df_raw.columns))
    if not stores:
        raise ValueError("No se detectaron tiendas (firma '{t} Stk' + '{t} UME' + '{t} On Order').")
    sku_col = "Cód. Prod." if "Cód. Prod." in df_raw.columns else df_raw.columns[3]
    costo = pd.to_numeric(df_raw.get("Costo S/."), errors="coerce")
    frames = []
    for t in stores:
        stk = pd.to_numeric(df_raw[f"{t} Stk"], errors="coerce").fillna(0)
        oo = pd.to_numeric(df_raw[f"{t} On Order"], errors="coerce").fillna(0)
        vta_col = f"{t} Unidades" if f"{t} Unidades" in df_raw.columns else (f"{t} Vta" if f"{t} Vta" in df_raw.columns else None)
        vta = pd.to_numeric(df_raw[vta_col], errors="coerce").fillna(0) if vta_col else pd.Series(0, index=df_raw.index)
        ume = pd.to_numeric(df_raw[f"{t} UME"], errors="coerce").fillna(0)
        m = (stk != 0) | (oo != 0)
        if not m.any():
            continue
        frames.append(pd.DataFrame({
            "semana_iso": semana_iso,
            "sku": df_raw.loc[m, sku_col].astype(str).str.strip().values,
            "tienda": t,
            "stock_uds": stk[m].astype("int64").values,
            "vta_uds_sem": vta[m].astype("int64").values,
            "on_order": oo[m].astype("int64").values,
            "ume": ume[m].astype("int64").values,
            "stock_costo": (stk[m] * costo[m].fillna(0)).round(2).values,
        }))
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLS)
    return out[COLS]


def _path(semana_iso: str) -> str:
    return os.path.join(SNAPSHOTS_DIR, semana_iso, "tienda.parquet")


def save_tienda(df: pd.DataFrame, semana_iso: str, force: bool = False) -> dict:
    p = _path(semana_iso)
    if os.path.exists(p) and not force:
        raise FileExistsError(f"tienda.parquet de {semana_iso} ya existe (force=True para sobrescribir)")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    df.to_parquet(p, index=False)
    return {"semana_iso": semana_iso, "n_filas": int(len(df)), "n_tiendas": int(df["tienda"].nunique()),
            "n_skus": int(df["sku"].nunique()), "mb": round(os.path.getsize(p) / 1e6, 2), "path": p}


def process_base(filepath: str, semana_iso: str, force: bool = False) -> dict:
    df_raw = pd.read_excel(filepath)
    return save_tienda(build_from_base(df_raw, semana_iso), semana_iso, force=force)


def list_tienda_weeks() -> list:
    if not os.path.isdir(SNAPSHOTS_DIR):
        return []
    return sorted(d for d in os.listdir(SNAPSHOTS_DIR) if os.path.exists(_path(d)))


def load_tienda(semana_iso: str) -> pd.DataFrame:
    p = _path(semana_iso)
    if not os.path.exists(p):
        raise FileNotFoundError(p)
    return pd.read_parquet(p)


def semana_de_nombre(nombre: str) -> str | None:
    """'Base al 30.08.xlsx' → '2026-35' usando el mismo criterio del loader."""
    from .loader import _fecha_to_semana_iso
    m = re.search(r"(\d{2}\.\d{2}(?:\.\d{2,4})?)", os.path.basename(nombre))
    return _fecha_to_semana_iso(m.group(1))[0] if m else None


def backfill(directory: str, force: bool = False) -> list:
    """Genera tienda.parquet para cada base del directorio que tenga snapshot y no tenga tienda."""
    out = []
    for f in sorted(os.listdir(directory)):
        if not f.lower().endswith(".xlsx"):
            continue
        w = semana_de_nombre(f)
        if not w or w not in list_available_weeks():
            continue
        if os.path.exists(_path(w)) and not force:
            continue
        try:
            meta = process_base(os.path.join(directory, f), w, force=force)
            out.append(meta)
            print(f"   ✅ tienda {w}: {meta['n_filas']:,} filas · {meta['n_tiendas']} tiendas · {meta['mb']} MB")
        except Exception as e:
            print(f"   ❌ tienda {w} ({f}): {e}")
    return out


# ── Consultas básicas por tienda ─────────────────────────────

def stock_tienda_dos_semanas(sem_a: str, sem_b: str) -> pd.DataFrame:
    """SKU×tienda con stock/venta/on-order de dos semanas, para detectar despachos
    (stock_b > stock_a − venta_b → llegó mercadería) y quiebres por tienda."""
    a, b = load_tienda(sem_a), load_tienda(sem_b)
    m = a.merge(b, on=["sku", "tienda"], how="outer", suffixes=("_a", "_b"))
    for c in ("stock_uds_a", "stock_uds_b", "vta_uds_sem_a", "vta_uds_sem_b", "on_order_a", "on_order_b"):
        m[c] = pd.to_numeric(m[c], errors="coerce").fillna(0)
    m["esperado_b"] = m["stock_uds_a"] - m["vta_uds_sem_b"]
    m["recibido_uds"] = (m["stock_uds_b"] - m["esperado_b"]).clip(lower=0).round(0)
    m["recibido"] = m["recibido_uds"] > 0
    return m


def capital_por_tienda(semana_iso: str) -> pd.DataFrame:
    d = load_tienda(semana_iso)
    return (d.groupby("tienda").agg(stock_uds=("stock_uds", "sum"), stock_costo=("stock_costo", "sum"),
                                    skus=("sku", "nunique"), vta_uds_sem=("vta_uds_sem", "sum"))
             .reset_index().sort_values("stock_costo", ascending=False))
