"""
notion_store.py — Persistencia de Capi en Notion (decisión Franco 2026-09-12).

Por qué: Franco usa la URL de Streamlit Cloud desde la oficina y el disco de la nube es
efímero (cada reinicio o push borra lo escrito). Lo que se registre ahí tiene que salir a
un lugar que Franco lea y que Claude pueda consultar: Notion.

Dos bases creadas el 2026-09-12 bajo FRANCO OS:
  📋 Acciones Capi  → DB_ACCIONES  (una fila por acción o por lote; detalle SKU×tienda en CSV adjunto)
  🗂️ Cortes Capi    → DB_CORTES    (una fila por semana; snapshot + tienda en un zip adjunto)

Credencial: NOTION_TOKEN en el entorno (local: .env; nube: st.secrets, que app_streamlit
propaga a os.environ). La integración de Notion debe estar CONECTADA a las dos bases
(… → Conexiones → Capi); sin eso la API devuelve 404 aunque el token sea válido.

Sin token, `disponible()` es False y todo el resto de Capi sigue funcionando con el CSV local.
Notion-Version 2022-06-28: la API de subida de archivos (file_uploads) funciona con ella.
"""
from __future__ import annotations

import mimetypes
import os
import time

import requests

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
DB_ACCIONES = os.getenv("NOTION_DB_ACCIONES", "af83c41b8e6d480299dc66d4c06eccd4")
DB_CORTES = os.getenv("NOTION_DB_CORTES", "748c0ce7f70d491bbe4605eef0b3f2f1")
# 📈 Proveedores Capi (reporte semanal al proveedor, creada 2026-09-19 bajo FRANCO OS): una fila por marca × semana.
DB_PROVEEDORES = os.getenv("NOTION_DB_PROVEEDORES", "2df6fa17326b4f9da7f201845e46a41d")
TIMEOUT = 60
MAX_TEXTO = 2000          # límite de un rich_text en Notion


class NotionError(RuntimeError):
    pass


def token() -> str:
    t = os.getenv("NOTION_TOKEN", "").strip()
    if t:
        return t
    try:                                   # Streamlit Cloud: st.secrets (solo si streamlit está importable y con secrets)
        import streamlit as st
        return str(st.secrets.get("NOTION_TOKEN", "")).strip()
    except Exception:
        return ""


def disponible() -> bool:
    return bool(token())


def en_nube() -> bool:
    """Streamlit Cloud monta el repo en /mount/src; en la laptop no existe."""
    return os.path.isdir("/mount/src")


def _headers(json_: bool = True) -> dict:
    h = {"Authorization": f"Bearer {token()}", "Notion-Version": VERSION}
    if json_:
        h["Content-Type"] = "application/json"
    return h


def _req(method: str, path: str, *, json: dict | None = None, files: dict | None = None,
         reintentos: int = 3) -> dict:
    """Llamada a la API con reintento en 429/5xx. Levanta NotionError con el texto del error."""
    url = path if path.startswith("http") else f"{API}{path}"
    ultimo = None
    for i in range(reintentos):
        r = requests.request(method, url, headers=_headers(json_=files is None), json=json,
                             files=files, timeout=TIMEOUT)
        if r.status_code == 429 or r.status_code >= 500:
            ultimo = f"{r.status_code}: {r.text[:200]}"
            time.sleep(float(r.headers.get("Retry-After", 1 + i)))
            continue
        if r.status_code >= 400:
            raise NotionError(f"{method} {path} → {r.status_code}: {r.text[:300]}")
        return r.json()
    raise NotionError(f"{method} {path}: sin respuesta tras {reintentos} intentos ({ultimo})")


# ── Archivos ──────────────────────────────────────────────────────────

def subir_archivo(nombre: str, data: bytes, content_type: str | None = None) -> str:
    """Sube un archivo (≤20 MB, extensiones permitidas: csv, json, xlsx, zip, txt, pdf…) y
    devuelve el id del file_upload. Hay que adjuntarlo a una página dentro de 1 hora."""
    ct = content_type or mimetypes.guess_type(nombre)[0] or "application/octet-stream"
    up = _req("POST", "/file_uploads", json={"filename": nombre, "content_type": ct})
    _req("POST", f"/file_uploads/{up['id']}/send", files={"file": (nombre, data, ct)})
    return up["id"]


def descargar(url: str) -> bytes:
    """Descarga un archivo adjunto (URL firmada, vence en 1 h; no lleva el token)."""
    r = requests.get(url, timeout=TIMEOUT)
    if r.status_code >= 400:
        raise NotionError(f"descarga → {r.status_code}")
    return r.content


# ── Propiedades (construir) ───────────────────────────────────────────

def p_title(s) -> dict:
    return {"title": [{"text": {"content": str(s or "")[:MAX_TEXTO]}}]}


def p_text(s) -> dict:
    s = "" if s is None else str(s)
    return {"rich_text": ([{"text": {"content": s[:MAX_TEXTO]}}] if s else [])}


def p_select(s) -> dict:
    return {"select": ({"name": str(s)} if s not in (None, "") else None)}


def p_number(n) -> dict:
    try:
        v = float(n)
    except (TypeError, ValueError):
        v = None
    return {"number": (None if v is None or v != v else v)}


def p_date(iso) -> dict:
    return {"date": ({"start": str(iso)} if iso else None)}


def p_files(archivos: list) -> dict:
    """archivos: lista de (file_upload_id, nombre)."""
    return {"files": [{"type": "file_upload", "file_upload": {"id": fid}, "name": str(nombre)[:100]}
                      for fid, nombre in archivos]}


# ── Páginas ───────────────────────────────────────────────────────────

def crear_pagina(db_id: str, props: dict, archivos: list | None = None,
                 prop_archivos: str | None = None) -> dict:
    props = dict(props)
    if archivos and prop_archivos:
        props[prop_archivos] = p_files(archivos)
    return _req("POST", "/pages", json={"parent": {"database_id": db_id}, "properties": props})


def actualizar_pagina(page_id: str, props: dict | None = None, archivada: bool | None = None) -> dict:
    body = {}
    if props:
        body["properties"] = props
    if archivada is not None:
        body["archived"] = bool(archivada)
    return _req("PATCH", f"/pages/{page_id}", json=body)


def consultar(db_id: str, filtro: dict | None = None, sorts: list | None = None,
              page_size: int = 100, max_paginas: int = 20) -> list:
    """Todas las páginas de una base (pagina con start_cursor)."""
    out, cursor = [], None
    for _ in range(max_paginas):
        body = {"page_size": page_size}
        if filtro:
            body["filter"] = filtro
        if sorts:
            body["sorts"] = sorts
        if cursor:
            body["start_cursor"] = cursor
        r = _req("POST", f"/databases/{db_id}/query", json=body)
        out.extend(r.get("results", []))
        if not r.get("has_more"):
            break
        cursor = r.get("next_cursor")
    return out


def filtro_texto(prop: str, igual_a: str) -> dict:
    return {"property": prop, "rich_text": {"equals": str(igual_a)}}


# ── Propiedades (leer) ────────────────────────────────────────────────

def valor(page: dict, nombre: str):
    """Valor plano de una propiedad (title/rich_text → str, select → nombre, number, date → start,
    files → [(nombre, url)], created_time → str). None si no existe."""
    p = (page.get("properties") or {}).get(nombre)
    if not p:
        return None
    t = p.get("type")
    if t in ("title", "rich_text"):
        return "".join(x.get("plain_text", "") for x in p.get(t) or [])
    if t == "select":
        return (p.get("select") or {}).get("name")
    if t == "number":
        return p.get("number")
    if t == "date":
        return (p.get("date") or {}).get("start")
    if t == "files":
        out = []
        for f in p.get("files") or []:
            u = (f.get("file") or {}).get("url") or (f.get("external") or {}).get("url")
            out.append((f.get("name"), u))
        return out
    if t in ("created_time", "last_edited_time", "url", "checkbox"):
        return p.get(t)
    return None


# ── 📈 Proveedores Capi (marca × semana) ─────────────────────────────────────

def buscar_proveedor(marca: str, semana_iso: str) -> dict | None:
    """Página de la fila marca × semana, o None."""
    filtro = {"and": [filtro_texto("Marca", str(marca).upper().strip()), filtro_texto("Semana ISO", str(semana_iso))]}
    pags = consultar(DB_PROVEEDORES, filtro)
    return pags[0] if pags else None


def upsert_proveedor(marca: str, semana_iso: str, props: dict, archivos: list | None = None) -> dict:
    """Crea o actualiza la fila marca × semana. `props` ya en formato Notion (p_*). Los archivos
    (lista de (nombre, bytes)) se suben y se adjuntan en "Archivos" (reemplazan los previos).
    Devuelve {ok, page_id, url, creada, error}. Sin token → {ok: False, error: "sin NOTION_TOKEN"}."""
    if not disponible():
        return {"ok": False, "error": "sin NOTION_TOKEN", "page_id": None, "url": None, "creada": False}
    try:
        subidos = [(n, subir_archivo(n, d)) for n, d in (archivos or [])]
        pag = buscar_proveedor(marca, semana_iso)
        if pag:
            r = actualizar_pagina(pag["id"], props={**props, **({"Archivos": p_files(subidos)} if subidos else {})})
            creada = False
        else:
            r = crear_pagina(DB_PROVEEDORES, props, archivos=subidos or None, prop_archivos="Archivos" if subidos else None)
            creada = True
        return {"ok": True, "page_id": r.get("id"), "url": r.get("url"), "creada": creada, "error": None}
    except Exception as e:  # NotionError, requests
        return {"ok": False, "error": str(e)[:300], "page_id": None, "url": None, "creada": False}

