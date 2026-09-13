"""
acciones_log.py — Registro estructurado de acciones de gestión (Gap G3) con persistencia en Notion.

Origen: auditoría integral 2026-08-23. Sin registro de acciones, ningún delta de capital es
atribuible ("eso fue el cambio de temporada"). Este log es joinable contra los snapshots por
(semana_iso, sku / sku×tienda).

Dos niveles (decisión Franco 2026-09-12):
  • Acción suelta: una fila (markdown, correo a proveedor, empuje marcado en Match…).
  • LOTE: una fila resumen + detalle SKU×tienda×uds en CSV. Es lo que realmente sale a
    inventories/tiendas: el Excel de giro (📦 Reposición) y la lista de venta cero (📲).
    Sin el lote no hay "pedido" contra el cual medir cumplimiento (S6) ni activación (K1).

Persistencia:
  1. CSV local `acciones/acciones_log.csv` + `acciones/lotes/{lote}.csv` (siempre).
  2. Notion 📋 Acciones Capi (si hay NOTION_TOKEN): cada fila es una página; el detalle del
     lote va adjunto. `cargar()` une lo remoto con lo local pendiente de sincronizar
     (`notion_url` vacío) y `sincronizar_pendientes()` empuja lo que quedó en la laptop.
  En Streamlit Cloud el CSV se borra en cada reinicio: Notion es la fuente de verdad ahí.
"""
from __future__ import annotations

import io
import os
import re
import time
from datetime import date, datetime

import pandas as pd

import notion_store as _ns

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "acciones")
RUTA_LOG = os.path.join(_DIR, "acciones_log.csv")
DIR_LOTES = os.path.join(_DIR, "lotes")

COLUMNAS = ["fecha_registro", "semana_iso", "tipo", "marca", "sku", "descripcion", "magnitud",
            "origen", "estado", "vista", "lote", "filas", "unidades", "tienda", "corte_base", "notion_url"]
COLS_DETALLE = ["sku", "tienda", "tienda_cod", "uds", "marca", "nombre", "categoria"]

TIPOS = ["Reposición / Empuje", "Venta Cero / Exhibición", "Markdown / Precio", "Transferencia",
         "Negociación Terceras", "Liquidación", "Exhibición / Tienda", "Otro"]
ORIGENES = ["Sugerida por Capi", "Manual"]
ESTADOS_ACCION = ["Ejecutada", "En curso", "Sugerida"]
VISTAS = ["Reposición", "Venta Cero", "Match", "Agente Terceras", "Caso de Éxito", "Manual"]

_CACHE_TTL = 60.0
_cache = {"t": 0.0, "df": None}


# ── utilidades ────────────────────────────────────────────────────────

def semana_actual() -> str:
    return f"{date.today().isocalendar()[0]}-{date.today().isocalendar()[1]:02d}"


def _semana_ok(s) -> bool:
    return bool(s and re.fullmatch(r"\d{4}-\d{2}", str(s).strip()))


def codigo_tienda(nombre: str) -> str:
    """'Jockey Plaza' → 'JP' (código del snapshot por tienda). Si ya es código o no se conoce, se devuelve igual."""
    try:
        from transformar_profundidad import STORE_NAMES
        inv = {v.strip().upper(): k for k, v in STORE_NAMES.items()}
        s = str(nombre).strip()
        if s in STORE_NAMES:
            return s
        return inv.get(s.upper(), s)
    except Exception:
        return str(nombre).strip()


def _vacio() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNAS)


def _completar(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in COLUMNAS:
        if c not in df.columns:
            df[c] = ""
    return df[COLUMNAS].fillna("").astype(str)


# ── CSV local ─────────────────────────────────────────────────────────

def _cargar_csv() -> pd.DataFrame:
    if os.path.exists(RUTA_LOG):
        try:
            return _completar(pd.read_csv(RUTA_LOG, dtype=str).fillna(""))
        except Exception:
            pass
    return _vacio()


def guardar(df: pd.DataFrame) -> None:
    os.makedirs(_DIR, exist_ok=True)
    _completar(df).to_csv(RUTA_LOG, index=False)
    _cache["t"] = 0.0


def _append_csv(fila: dict) -> None:
    df = _cargar_csv()
    df = pd.concat([df, pd.DataFrame([fila])], ignore_index=True)
    guardar(df)


# ── Notion ────────────────────────────────────────────────────────────

def _props_notion(fila: dict) -> dict:
    fr = str(fila.get("fecha_registro") or "")
    fecha = fr[:10] if fr else date.today().isoformat()
    vista = fila.get("vista") if fila.get("vista") in VISTAS else "Manual"
    tipo = fila.get("tipo") if fila.get("tipo") in TIPOS else "Otro"
    return {
        "Acción": _ns.p_title(fila.get("descripcion") or f"{tipo} {fila.get('marca', '')}".strip()),
        "Tipo": _ns.p_select(tipo),
        "Semana ISO": _ns.p_text(fila.get("semana_iso")),
        "Fecha": _ns.p_date(fecha),
        "Vista": _ns.p_select(vista),
        "Lote": _ns.p_text(fila.get("lote")),
        "Marca": _ns.p_text(fila.get("marca")),
        "SKU": _ns.p_text(fila.get("sku")),
        "Tienda": _ns.p_text(fila.get("tienda")),
        "Filas": _ns.p_number(fila.get("filas") or None),
        "Unidades": _ns.p_number(fila.get("unidades") or None),
        "Magnitud": _ns.p_text(fila.get("magnitud")),
        "Origen": _ns.p_select(fila.get("origen") if fila.get("origen") in ORIGENES else "Manual"),
        "Estado": _ns.p_select(fila.get("estado") if fila.get("estado") in ESTADOS_ACCION else "Ejecutada"),
        "Corte base": _ns.p_text(fila.get("corte_base")),
        "Registrado desde": _ns.p_select("nube" if _ns.en_nube() else "laptop"),
    }


def _subir_notion(fila: dict, detalle: pd.DataFrame | None = None) -> str:
    """Crea la página en 📋 Acciones Capi (con el CSV del lote adjunto si hay detalle). Devuelve la URL."""
    archivos = None
    if detalle is not None and not detalle.empty:
        fid = _ns.subir_archivo(f"{fila['lote']}.csv", detalle.to_csv(index=False).encode("utf-8"), "text/csv")
        archivos = [(fid, f"{fila['lote']}.csv")]
    page = _ns.crear_pagina(_ns.DB_ACCIONES, _props_notion(fila), archivos=archivos, prop_archivos="Detalle")
    return page.get("url", "")


def _pagina_a_fila(p: dict) -> dict:
    v = lambda n: _ns.valor(p, n)
    fecha = v("Fecha") or (p.get("created_time") or "")[:10]
    return {
        "fecha_registro": (p.get("created_time") or fecha or "")[:16].replace("T", " "),
        "semana_iso": v("Semana ISO") or "", "tipo": v("Tipo") or "", "marca": v("Marca") or "",
        "sku": v("SKU") or "", "descripcion": v("Acción") or "", "magnitud": v("Magnitud") or "",
        "origen": v("Origen") or "", "estado": v("Estado") or "", "vista": v("Vista") or "",
        "lote": v("Lote") or "", "filas": v("Filas") or "", "unidades": v("Unidades") or "",
        "tienda": v("Tienda") or "", "corte_base": v("Corte base") or "", "notion_url": p.get("url", ""),
        "_detalle_url": next((u for _n, u in (v("Detalle") or []) if u), ""),
    }


def _cargar_notion() -> pd.DataFrame:
    if _cache["df"] is not None and time.time() - _cache["t"] < _CACHE_TTL:
        return _cache["df"].copy()
    pages = _ns.consultar(_ns.DB_ACCIONES, sorts=[{"timestamp": "created_time", "direction": "descending"}])
    df = pd.DataFrame([_pagina_a_fila(p) for p in pages]) if pages else _vacio()
    df = _completar(df) if "_detalle_url" not in df.columns else df
    _cache["df"], _cache["t"] = df.copy(), time.time()
    return df


# ── API pública ───────────────────────────────────────────────────────

def cargar(usar_notion: bool = True) -> pd.DataFrame:
    """Log completo: lo que está en Notion + lo local que aún no se sincronizó (notion_url vacío)."""
    local = _cargar_csv()
    if usar_notion and _ns.disponible():
        try:
            remoto = _cargar_notion()
            df = pd.concat([_completar(remoto), local[local["notion_url"] == ""]], ignore_index=True)
            return df.sort_values("fecha_registro", ascending=False).reset_index(drop=True)
        except Exception:
            pass                                     # sin red / sin permisos: se muestra lo local
    return local.sort_values("fecha_registro", ascending=False).reset_index(drop=True)


def _fila(semana_iso, tipo, marca, descripcion, magnitud="", sku="", origen="Sugerida por Capi",
          estado="Ejecutada", vista="Manual", lote="", filas="", unidades="", tienda="", corte_base="") -> dict:
    return {
        "fecha_registro": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "semana_iso": str(semana_iso).strip() if _semana_ok(semana_iso) else semana_actual(),
        "tipo": tipo, "marca": str(marca or "").upper().strip(), "sku": str(sku or ""),
        "descripcion": descripcion, "magnitud": str(magnitud or ""), "origen": origen, "estado": estado,
        "vista": vista, "lote": lote, "filas": ("" if filas == "" else str(int(filas))),
        "unidades": ("" if unidades == "" else str(int(unidades))), "tienda": str(tienda or ""),
        "corte_base": str(corte_base or ""), "notion_url": "",
    }


def agregar(semana_iso: str, tipo: str, marca: str, descripcion: str, magnitud: str = "", sku: str = "",
            origen: str = "Sugerida por Capi", estado: str = "Ejecutada", vista: str = "Manual",
            tienda: str = "", corte_base: str = "") -> pd.DataFrame:
    """Agrega una acción suelta. Va a Notion si hay token (si falla, queda local pendiente). Devuelve el log."""
    fila = _fila(semana_iso, tipo, marca, descripcion, magnitud, sku, origen, estado, vista,
                 tienda=tienda, corte_base=corte_base)
    if _ns.disponible():
        try:
            fila["notion_url"] = _subir_notion(fila)
        except Exception:
            fila["notion_url"] = ""
    _append_csv(fila)
    return cargar()


def normalizar_detalle(detalle: pd.DataFrame) -> pd.DataFrame:
    """Detalle de un lote → columnas fijas (sku, tienda, tienda_cod, uds, marca, nombre, categoria), uds > 0."""
    d = detalle.copy()
    d.columns = [str(c) for c in d.columns]
    ren = {"SKU": "sku", "Tienda": "tienda", "Marca": "marca", "Producto": "nombre", "Línea": "categoria",
           "unidades_sugeridas": "uds", "a_reponer": "uds", "desde_cd": "uds", "Uds": "uds", "stock_total": "uds"}
    d = d.rename(columns={k: v for k, v in ren.items() if k in d.columns and v not in d.columns})
    if "tienda" not in d.columns and "sku" in d.columns:      # vino la matriz ancha SKU × tienda
        return matriz_a_detalle(d)
    if "uds" not in d.columns:
        d["uds"] = 1
    d["uds"] = pd.to_numeric(d["uds"], errors="coerce").fillna(0)
    d = d[d["uds"] > 0].copy()
    d["sku"] = d["sku"].astype(str).str.strip().str.lstrip("0")
    d["tienda"] = d["tienda"].astype(str).str.strip()
    d["tienda_cod"] = d["tienda"].map(codigo_tienda)
    for c in COLS_DETALLE:
        if c not in d.columns:
            d[c] = ""
    d["uds"] = d["uds"].round(0).astype(int)
    return d[COLS_DETALLE].reset_index(drop=True)


def matriz_a_detalle(matriz: pd.DataFrame, meta_cols=("sku", "nombre", "categoria", "marca", "stock_cd",
                                                          "TOTAL", "PENDIENTE (sin CD)", "CD", "Total Repo")) -> pd.DataFrame:
    """Matriz SKU × tienda (Excel de giro) → detalle largo con uds > 0."""
    m = matriz.rename(columns={"SKU": "sku", "Producto": "nombre", "Línea": "categoria", "Marca": "marca",
                               "Stock CD": "stock_cd"})            # encabezados del Excel de giro ya exportado
    matriz = m
    tiendas = [c for c in matriz.columns if c not in meta_cols and not str(c).startswith("Unnamed")]
    ids = [c for c in ("sku", "nombre", "categoria", "marca") if c in matriz.columns]
    largo = matriz[ids + tiendas].melt(id_vars=ids, var_name="tienda", value_name="uds")
    return normalizar_detalle(largo)


def registrar_lote(tipo: str, vista: str, lote: str, semana_iso: str, detalle: pd.DataFrame,
                   descripcion: str, marca: str = "", corte_base: str = "", estado: str = "Ejecutada",
                   origen: str = "Sugerida por Capi") -> dict:
    """Registra un lote (giro, venta cero…): fila resumen en el log + detalle SKU×tienda×uds en
    `acciones/lotes/{lote}.csv` + página en Notion con el CSV adjunto (si hay token)."""
    lote = re.sub(r"[^A-Za-z0-9._-]+", "-", str(lote)).strip("-").lower()
    det = normalizar_detalle(detalle)
    os.makedirs(DIR_LOTES, exist_ok=True)
    det.to_csv(os.path.join(DIR_LOTES, f"{lote}.csv"), index=False)
    fila = _fila(semana_iso, tipo, marca, descripcion, magnitud=f"{len(det):,} combos · {int(det['uds'].sum()):,} uds",
                 origen=origen, estado=estado, vista=vista, lote=lote, filas=len(det),
                 unidades=int(det["uds"].sum()), corte_base=corte_base)
    error = ""
    if _ns.disponible():
        try:
            fila["notion_url"] = _subir_notion(fila, det)
        except Exception as e:                       # queda local, pendiente de sincronizar
            error = str(e)[:200]
    _append_csv(fila)
    return {"lote": lote, "filas": int(len(det)), "unidades": int(det["uds"].sum()),
            "notion_url": fila["notion_url"], "error": error, "detalle": det}


def detalle_lote(lote: str) -> pd.DataFrame:
    """Detalle SKU×tienda×uds de un lote: archivo local o, si no está, el CSV adjunto en Notion (se cachea)."""
    p = os.path.join(DIR_LOTES, f"{lote}.csv")
    if os.path.exists(p):
        return normalizar_detalle(pd.read_csv(p, dtype={"sku": str}))
    if _ns.disponible():
        try:
            df = _cargar_notion()
            url = df.loc[df["lote"] == lote, "_detalle_url"] if "_detalle_url" in df.columns else pd.Series(dtype=str)
            url = next((u for u in url if u), "")
            if url:
                det = pd.read_csv(io.BytesIO(_ns.descargar(url)), dtype={"sku": str})
                os.makedirs(DIR_LOTES, exist_ok=True)
                det.to_csv(p, index=False)
                return normalizar_detalle(det)
        except Exception:
            pass
    return pd.DataFrame(columns=COLS_DETALLE)


def lotes(semana_iso: str | None = None, tipo: str | None = None, df: pd.DataFrame | None = None) -> pd.DataFrame:
    df = cargar() if df is None else df
    d = df[df["lote"].astype(str) != ""]
    if semana_iso:
        d = d[d["semana_iso"] == semana_iso]
    if tipo:
        d = d[d["tipo"].astype(str).str.contains(tipo, case=False, na=False)]
    return d.reset_index(drop=True)


def expandir_lotes(df: pd.DataFrame) -> pd.DataFrame:
    """Filas del log → una fila por SKU×tienda pedido: sku, tienda, tienda_cod, uds, tipo, semana_iso, lote, marca.
    Las acciones sueltas con SKU se conservan (uds vacío); las filas sin lote ni SKU se descartan."""
    out = []
    for _, r in df.iterrows():
        if str(r.get("lote", "")):
            d = detalle_lote(str(r["lote"]))
            if not d.empty:
                d = d.assign(tipo=r["tipo"], semana_iso=r["semana_iso"], lote=r["lote"])
                out.append(d[["sku", "tienda", "tienda_cod", "uds", "marca", "tipo", "semana_iso", "lote"]])
        elif str(r.get("sku", "")).strip():
            out.append(pd.DataFrame([{"sku": str(r["sku"]).strip().lstrip("0"), "tienda": r.get("tienda", ""),
                                      "tienda_cod": codigo_tienda(r.get("tienda", "")), "uds": pd.NA, "marca": r.get("marca", ""),
                                      "tipo": r["tipo"], "semana_iso": r["semana_iso"], "lote": ""}]))
    cols = ["sku", "tienda", "tienda_cod", "uds", "marca", "tipo", "semana_iso", "lote"]
    return pd.concat(out, ignore_index=True)[cols] if out else pd.DataFrame(columns=cols)


def pedidos_sku_tienda(semana_iso: str, tipo: str = "Empuje") -> pd.DataFrame:
    """Lo pedido en una semana (lotes + acciones sueltas) del tipo dado, a nivel SKU×tienda."""
    df = cargar()
    d = df[(df["semana_iso"] == semana_iso) & df["tipo"].astype(str).str.contains(tipo, case=False, na=False)]
    return expandir_lotes(d)


def sincronizar_pendientes() -> dict:
    """Empuja a Notion las filas locales sin notion_url (registradas sin token o con la red caída)."""
    if not _ns.disponible():
        return {"subidas": 0, "pendientes": 0, "error": "sin NOTION_TOKEN"}
    df = _cargar_csv()
    pend = df.index[df["notion_url"] == ""]
    subidas, errores = 0, []
    for i in pend:
        fila = df.loc[i].to_dict()
        try:
            det = detalle_lote(fila["lote"]) if fila.get("lote") else None
            df.at[i, "notion_url"] = _subir_notion(fila, det)
            subidas += 1
        except Exception as e:
            errores.append(f"{fila.get('descripcion', '')[:40]}: {str(e)[:80]}")
    if subidas:
        guardar(df)
    return {"subidas": subidas, "pendientes": int(len(pend) - subidas), "error": "; ".join(errores[:3])}


def acciones_de_semanas(desde: str = None, hasta: str = None) -> pd.DataFrame:
    df = cargar()
    if df.empty:
        return df
    if desde:
        df = df[df["semana_iso"] >= desde]
    if hasta:
        df = df[df["semana_iso"] <= hasta]
    return df
