"""acciones_log con lotes: registro local, expansión SKU×tienda, sincronización a Notion (fake) y
el wrapper de cumplimiento leyendo el pedido desde el lote (antes solo veía acciones con SKU suelto)."""
import os

import pandas as pd
import pytest

import acciones_log as al
import notion_store as ns


@pytest.fixture
def log_tmp(tmp_path, monkeypatch):
    d = tmp_path / "acciones"
    monkeypatch.setattr(al, "_DIR", str(d))
    monkeypatch.setattr(al, "RUTA_LOG", str(d / "acciones_log.csv"))
    monkeypatch.setattr(al, "DIR_LOTES", str(d / "lotes"))
    monkeypatch.setattr(ns, "token", lambda: "")          # sin Notion
    al._cache["t"] = 0.0
    return d


def _matriz():
    return pd.DataFrame({"sku": ["101", "202"], "nombre": ["Polo", "Jean"], "categoria": ["Polos", "Jeans"],
                         "marca": ["CACHAREL", "DOCKERS"], "stock_cd": [50, 10],
                         "Jockey Plaza": [12, 0], "San Miguel": [0, 6], "Arequipa": [3, 0], "TOTAL": [15, 6],
                         "PENDIENTE (sin CD)": [0, 4]})


def test_matriz_a_detalle_solo_positivos_y_codigo_tienda():
    det = al.matriz_a_detalle(_matriz())
    assert len(det) == 3 and int(det["uds"].sum()) == 21
    assert set(det["tienda_cod"]) == {"JP", "SM", "AQP"}
    assert list(det.columns) == al.COLS_DETALLE


def test_registrar_lote_local_sin_notion(log_tmp):
    r = al.registrar_lote("Reposición / Empuje", "Reposición", "giro 2026-37 Propias", "2026-37",
                          _matriz(), "Excel de giro propias", corte_base="Base al 06.09")
    assert r["lote"] == "giro-2026-37-propias" and r["filas"] == 3 and r["unidades"] == 21
    assert r["notion_url"] == "" and r["error"] == ""
    assert os.path.exists(os.path.join(al.DIR_LOTES, "giro-2026-37-propias.csv"))
    log = al.cargar()
    assert len(log) == 1 and log.iloc[0]["lote"] == "giro-2026-37-propias"
    assert log.iloc[0]["magnitud"].startswith("3 combos")
    det = al.detalle_lote("giro-2026-37-propias")
    assert int(det["uds"].sum()) == 21


def test_expandir_lotes_y_pedidos(log_tmp):
    al.registrar_lote("Reposición / Empuje", "Reposición", "giro-2026-37", "2026-37", _matriz(), "giro")
    al.agregar("2026-37", "Markdown / Precio", "LACOSTE", "40% en 3 modelos", sku="303")
    al.agregar("2026-37", "Reposición / Empuje", "NAUTICA", "empuje Match", sku="404", tienda="Jockey Plaza")
    ped = al.pedidos_sku_tienda("2026-37", "Empuje")
    assert sorted(ped["sku"].unique()) == ["101", "202", "404"], "lote expandido + acción suelta; el markdown no entra"
    assert ped.loc[ped["sku"] == "404", "tienda_cod"].iloc[0] == "JP"
    assert al.pedidos_sku_tienda("2026-36", "Empuje").empty


def test_cumplimiento_lee_el_lote(log_tmp, monkeypatch):
    """S6 v1 leía solo filas con SKU: un giro registrado como lote daba 'sin pedidos'."""
    import analisis_estados
    al.registrar_lote("Reposición / Empuje", "Reposición", "giro-2026-34", "2026-34", _matriz(), "giro")
    a = pd.DataFrame([("101", "M", 100, 50, 200), ("202", "M", 100, 50, 200), ("909", "M", 100, 50, 200)],
                     columns=["sku", "marca", "stock_cd", "stock_tiendas", "unidades_vendidas"])
    b = pd.DataFrame([("101", "M", 80, 70, 210), ("202", "M", 100, 40, 210), ("909", "M", 100, 45, 205)],
                     columns=["sku", "marca", "stock_cd", "stock_tiendas", "unidades_vendidas"])
    monkeypatch.setattr(analisis_estados, "load_snapshot", lambda w: {"2026-34": a, "2026-35": b}[w])
    r = analisis_estados.cumplimiento_empujes("2026-34", "2026-35")
    assert r["n_pedidos"] == 2 and r["n_cumplidos"] == 1 and r["pct"] == 50.0


def test_sincroniza_pendientes_con_notion_fake(log_tmp, monkeypatch):
    al.registrar_lote("Reposición / Empuje", "Reposición", "giro-2026-37", "2026-37", _matriz(), "giro")
    al.agregar("2026-37", "Otro", "X", "suelta")
    assert (al._cargar_csv()["notion_url"] == "").all()
    subidas = []
    monkeypatch.setattr(ns, "token", lambda: "ntn_x")
    monkeypatch.setattr(ns, "subir_archivo", lambda n, d, ct=None: "fu_" + n)
    monkeypatch.setattr(ns, "crear_pagina", lambda db, props, archivos=None, prop_archivos=None:
                        subidas.append((props, archivos)) or {"url": f"https://notion.so/{len(subidas)}"})
    monkeypatch.setattr(ns, "consultar", lambda *a, **k: [])
    r = al.sincronizar_pendientes()
    assert r["subidas"] == 2 and r["pendientes"] == 0
    props, archivos = subidas[0]
    assert archivos == [("fu_giro-2026-37.csv", "giro-2026-37.csv")]
    assert props["Tipo"] == {"select": {"name": "Reposición / Empuje"}} and props["Filas"] == {"number": 3.0}
    assert subidas[1][1] is None, "una acción suelta no adjunta archivo"
    assert (al._cargar_csv()["notion_url"] != "").all()


def test_cargar_une_notion_y_local_pendiente(log_tmp, monkeypatch):
    al.agregar("2026-37", "Otro", "X", "local pendiente")
    monkeypatch.setattr(ns, "token", lambda: "ntn_x")
    pagina = {"url": "https://notion.so/remota", "created_time": "2026-09-14T10:00:00.000Z", "properties": {
        "Acción": {"type": "title", "title": [{"plain_text": "remota"}]},
        "Tipo": {"type": "select", "select": {"name": "Reposición / Empuje"}},
        "Semana ISO": {"type": "rich_text", "rich_text": [{"plain_text": "2026-37"}]},
        "Lote": {"type": "rich_text", "rich_text": [{"plain_text": "giro-2026-37"}]},
        "Filas": {"type": "number", "number": 3}, "Detalle": {"type": "files", "files": []}}}
    monkeypatch.setattr(ns, "consultar", lambda *a, **k: [pagina])
    al._cache["t"] = 0.0
    df = al.cargar()
    assert set(df["descripcion"]) == {"remota", "local pendiente"}
    assert df.loc[df["descripcion"] == "remota", "lote"].iloc[0] == "giro-2026-37"
