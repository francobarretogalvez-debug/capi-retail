"""Cortes en Notion: empaquetar/desempaquetar los parquet y restaurar semanas faltantes con un Notion falso."""
import os

import pandas as pd
import pytest

import notion_store as ns
from snapshots_engine import nube, storage


@pytest.fixture
def snaps(tmp_path, monkeypatch):
    d = tmp_path / "snapshots"
    monkeypatch.setattr(nube, "SNAPSHOTS_DIR", str(d))
    monkeypatch.setattr(nube, "INDEX_PATH", str(d / "snapshots_index.json"))
    monkeypatch.setattr(storage, "SNAPSHOTS_DIR", str(d))
    monkeypatch.setattr(storage, "INDEX_PATH", str(d / "snapshots_index.json"))
    os.makedirs(d / "2026-40", exist_ok=True)
    pd.DataFrame({"sku": [1, 2], "stock_total": [3, 4]}).to_parquet(d / "2026-40" / "snapshot.parquet", index=False)
    pd.DataFrame({"sku": ["1"], "tienda": ["JP"], "stock_uds": [3]}).to_parquet(d / "2026-40" / "tienda.parquet", index=False)
    return d


def test_zip_roundtrip(snaps):
    data = nube.empaquetar("2026-40")
    assert len(data) > 100
    archivos = nube.desempaquetar(data, "2026-41")
    assert sorted(archivos) == ["snapshot.parquet", "tienda.parquet"]
    assert pd.read_parquet(snaps / "2026-41" / "snapshot.parquet")["sku"].tolist() == [1, 2]


def test_sin_token_no_hace_nada(snaps, monkeypatch):
    monkeypatch.setattr(ns, "token", lambda: "")
    assert nube.subir_corte("2026-40") == {"ok": False, "error": "sin NOTION_TOKEN"}
    assert nube.restaurar_faltantes() == []


def test_subir_archiva_anterior_y_restaurar_baja_faltante(snaps, monkeypatch):
    monkeypatch.setattr(ns, "token", lambda: "ntn_x")
    llamadas = {"archivadas": [], "creadas": []}
    zips = {}
    monkeypatch.setattr(ns, "subir_archivo", lambda n, d, ct=None: zips.setdefault(n, d) and n)
    monkeypatch.setattr(ns, "consultar", lambda db, filtro=None, **k: [{"id": "vieja"}] if filtro else [
        {"url": "u", "properties": {
            "Semana ISO": {"type": "rich_text", "rich_text": [{"plain_text": "2026-40"}]},
            "Filas": {"type": "number", "number": 2}, "SKUs": {"type": "number", "number": 2},
            "Fecha cierre": {"type": "date", "date": {"start": "2026-10-04"}},
            "Hash": {"type": "rich_text", "rich_text": [{"plain_text": "abc"}]},
            "Archivos": {"type": "files", "files": [{"name": "corte_2026-40.zip", "file": {"url": "https://s3/z"}}]}}}])
    monkeypatch.setattr(ns, "actualizar_pagina", lambda pid, props=None, archivada=None: llamadas["archivadas"].append(pid))
    monkeypatch.setattr(ns, "crear_pagina", lambda db, props, archivos=None, prop_archivos=None:
                        llamadas["creadas"].append((props, archivos)) or {"url": "https://notion.so/c"})
    r = nube.subir_corte("2026-40", base_nombre="Base al 04.10.xlsx")
    assert r["ok"] and llamadas["archivadas"] == ["vieja"]
    props, archivos = llamadas["creadas"][0]
    assert archivos == [("corte_2026-40.zip", "corte_2026-40.zip")] and props["Base"]["rich_text"][0]["text"]["content"] == "Base al 04.10.xlsx"
    assert props["Filas tienda"] == {"number": 1.0}
    # borrar del disco y restaurar desde el "Notion"
    import shutil
    shutil.rmtree(snaps / "2026-40")
    assert storage.list_available_weeks() == []
    monkeypatch.setattr(ns, "descargar", lambda url: zips["corte_2026-40.zip"])
    assert nube.restaurar_faltantes() == ["2026-40"]
    assert storage.list_available_weeks() == ["2026-40"]
    assert os.path.exists(snaps / "2026-40" / "tienda.parquet")
    import json
    idx = json.load(open(snaps / "snapshots_index.json"))
    assert idx[0]["semana_iso"] == "2026-40" and idx[0]["hash"] == "abc"
