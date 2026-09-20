"""
Cortes semanales en Notion (🗂️ Cortes Capi) — decisión Franco 2026-09-12.

Franco carga la base desde la URL de Streamlit Cloud. El snapshot que se genera ahí
(`snapshot.parquet` + `tienda.parquet`) vive en un disco que se borra en cada reinicio o
push, así que las semanas cargadas en la nube nunca llegaban al repo ni a la laptop.

Flujo:
  subir_corte(semana)      → zip con los dos parquet + página en la base (archiva la anterior de esa semana)
  restaurar_faltantes()    → al arrancar la app: baja de Notion las semanas que no están en disco
                             y las agrega al índice. Así la nube arranca con TODO lo cargado antes.
Sin NOTION_TOKEN no hace nada (devuelve listas vacías) y el resto sigue igual.
"""
from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import datetime

import notion_store as _ns
from .config import SNAPSHOTS_DIR, INDEX_PATH
from .storage import list_available_weeks, _update_index

# proveedor.parquet (reporte semanal al proveedor, 2026-09-19) es OPCIONAL: los cortes
# anteriores no lo traen; empaquetar lo omite si falta y restaurar solo exige snapshot.parquet.
ARCHIVOS = ("snapshot.parquet", "tienda.parquet", "proveedor.parquet")


def _meta_index(semana_iso: str) -> dict:
    try:
        for r in json.load(open(INDEX_PATH)):
            if r.get("semana_iso") == semana_iso:
                return r
    except Exception:
        pass
    return {}


def empaquetar(semana_iso: str) -> bytes:
    d = os.path.join(SNAPSHOTS_DIR, semana_iso)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for a in ARCHIVOS:
            p = os.path.join(d, a)
            if os.path.exists(p):
                z.write(p, a)
    return buf.getvalue()


def desempaquetar(data: bytes, semana_iso: str) -> list:
    d = os.path.join(SNAPSHOTS_DIR, semana_iso)
    os.makedirs(d, exist_ok=True)
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for a in z.namelist():
            if a in ARCHIVOS:
                z.extract(a, d)
                out.append(a)
    return out


def subir_corte(semana_iso: str, base_nombre: str = "", n_filas_tienda: int | None = None) -> dict:
    """Sube el corte de una semana a 🗂️ Cortes Capi. Si ya había una página de esa semana, la archiva."""
    if not _ns.disponible():
        return {"ok": False, "error": "sin NOTION_TOKEN"}
    meta = _meta_index(semana_iso)
    data = empaquetar(semana_iso)
    if not data or len(data) < 100:
        return {"ok": False, "error": f"no hay parquet para {semana_iso}"}
    nombre = f"corte_{semana_iso}.zip"
    fid = _ns.subir_archivo(nombre, data, "application/zip")
    if n_filas_tienda is None:
        p = os.path.join(SNAPSHOTS_DIR, semana_iso, "tienda.parquet")
        if os.path.exists(p):
            try:
                import pyarrow.parquet as pq
                n_filas_tienda = pq.ParquetFile(p).metadata.num_rows
            except Exception:
                n_filas_tienda = None
    for pag in _ns.consultar(_ns.DB_CORTES, filtro=_ns.filtro_texto("Semana ISO", semana_iso)):
        try:
            _ns.actualizar_pagina(pag["id"], archivada=True)
        except Exception:
            pass
    props = {
        "Semana": _ns.p_title(semana_iso),
        "Semana ISO": _ns.p_text(semana_iso),
        "Fecha cierre": _ns.p_date(meta.get("fecha_cierre") or None),
        "Base": _ns.p_text(base_nombre),
        "Filas": _ns.p_number(meta.get("n_filas")),
        "SKUs": _ns.p_number(meta.get("n_skus")),
        "Filas tienda": _ns.p_number(n_filas_tienda),
        "Hash": _ns.p_text(meta.get("hash")),
        "Subido desde": _ns.p_select("nube" if _ns.en_nube() else "laptop"),
        "Subido": _ns.p_date(datetime.now().strftime("%Y-%m-%dT%H:%M:%S")),
    }
    page = _ns.crear_pagina(_ns.DB_CORTES, props, archivos=[(fid, nombre)], prop_archivos="Archivos")
    return {"ok": True, "semana_iso": semana_iso, "mb": round(len(data) / 1e6, 2), "url": page.get("url", "")}


def cortes_en_notion() -> list:
    if not _ns.disponible():
        return []
    out = []
    for p in _ns.consultar(_ns.DB_CORTES):
        s = _ns.valor(p, "Semana ISO") or _ns.valor(p, "Semana")
        if not s:
            continue
        out.append({
            "semana_iso": s, "fecha_cierre": _ns.valor(p, "Fecha cierre") or "",
            "n_filas": _ns.valor(p, "Filas"), "n_skus": _ns.valor(p, "SKUs"), "hash": _ns.valor(p, "Hash") or "",
            "base": _ns.valor(p, "Base") or "", "url": p.get("url", ""),
            "archivos": [(n, u) for n, u in (_ns.valor(p, "Archivos") or []) if u],
        })
    return sorted(out, key=lambda x: x["semana_iso"])


def restaurar_faltantes() -> list:
    """Baja de Notion las semanas que no están en disco. Devuelve las semanas restauradas."""
    if not _ns.disponible():
        return []
    en_disco = set(list_available_weeks())
    restauradas = []
    for c in cortes_en_notion():
        if c["semana_iso"] in en_disco or not c["archivos"]:
            continue
        try:
            data = _ns.descargar(c["archivos"][0][1])
            archivos = desempaquetar(data, c["semana_iso"])
            if "snapshot.parquet" not in archivos:
                continue
            fpath = os.path.join(SNAPSHOTS_DIR, c["semana_iso"], "snapshot.parquet")
            _update_index({"semana_iso": c["semana_iso"], "fecha_cierre": c["fecha_cierre"],
                           "n_filas": int(c["n_filas"] or 0), "n_skus": int(c["n_skus"] or 0),
                           "timestamp": datetime.now().isoformat(), "path": fpath, "hash": c["hash"]})
            restauradas.append(c["semana_iso"])
        except Exception:
            continue
    return restauradas
