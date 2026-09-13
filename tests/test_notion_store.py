"""notion_store: cliente HTTP con un servidor falso (sin red). Verifica las formas de los payloads
que la API de Notion exige: subida de archivo en 2 pasos, adjunto con file_upload, paginación."""
import json

import pytest

import notion_store as ns


class _Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body or {}, headers or {}
        self.text = json.dumps(self._body)
        self.content = b""

    def json(self):
        return self._body


class FakeAPI:
    """Registra las llamadas y responde como Notion."""
    def __init__(self):
        self.calls, self.fail_first = [], 0

    def request(self, method, url, headers=None, json=None, files=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "json": json, "files": files})
        if self.fail_first > 0:
            self.fail_first -= 1
            return _Resp(429, {"message": "rate"}, {"Retry-After": "0"})
        if url.endswith("/file_uploads"):
            return _Resp(200, {"id": "fu_1", "status": "pending"})
        if url.endswith("/send"):
            return _Resp(200, {"id": "fu_1", "status": "uploaded"})
        if url.endswith("/pages"):
            return _Resp(200, {"id": "pg_1", "url": "https://notion.so/pg_1", "properties": json["properties"]})
        if url.endswith("/query"):
            if json.get("start_cursor"):
                return _Resp(200, {"results": [{"id": "p2"}], "has_more": False})
            return _Resp(200, {"results": [{"id": "p1"}], "has_more": True, "next_cursor": "c2"})
        if "/pages/" in url and method == "PATCH":
            return _Resp(200, {"id": "pg_1", "archived": json.get("archived", False)})
        return _Resp(404, {"message": "no"})


@pytest.fixture
def api(monkeypatch):
    fake = FakeAPI()
    monkeypatch.setattr(ns.requests, "request", fake.request)
    monkeypatch.setenv("NOTION_TOKEN", "ntn_test")
    monkeypatch.setattr(ns.time, "sleep", lambda s: None)
    return fake


def test_sin_token_no_disponible(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.setattr(ns, "token", lambda: "")
    assert ns.disponible() is False


def test_subir_archivo_dos_pasos(api):
    fid = ns.subir_archivo("lote.csv", b"sku,tienda\n1,JP\n", "text/csv")
    assert fid == "fu_1"
    crear, enviar = api.calls
    assert crear["json"] == {"filename": "lote.csv", "content_type": "text/csv"}
    assert crear["headers"]["Notion-Version"] == ns.VERSION and crear["headers"]["Authorization"] == "Bearer ntn_test"
    assert enviar["url"].endswith("/file_uploads/fu_1/send")
    assert "Content-Type" not in enviar["headers"], "multipart: requests pone el boundary, no se fuerza JSON"
    assert enviar["files"]["file"][0] == "lote.csv"


def test_crear_pagina_con_adjunto(api):
    page = ns.crear_pagina("db1", {"Acción": ns.p_title("giro")}, archivos=[("fu_1", "giro.csv")], prop_archivos="Detalle")
    body = api.calls[-1]["json"]
    assert body["parent"] == {"database_id": "db1"}
    assert body["properties"]["Detalle"] == {"files": [{"type": "file_upload", "file_upload": {"id": "fu_1"}, "name": "giro.csv"}]}
    assert body["properties"]["Acción"]["title"][0]["text"]["content"] == "giro"
    assert page["url"].startswith("https://")


def test_props_vacias_y_numeros():
    assert ns.p_text("") == {"rich_text": []}
    assert ns.p_select(None) == {"select": None}
    assert ns.p_number("x") == {"number": None}
    assert ns.p_number("12") == {"number": 12.0}
    assert ns.p_date(None) == {"date": None}
    assert len(ns.p_text("a" * 5000)["rich_text"][0]["text"]["content"]) == ns.MAX_TEXTO


def test_consultar_pagina_con_cursor(api):
    pages = ns.consultar("db1", filtro=ns.filtro_texto("Lote", "x"))
    assert [p["id"] for p in pages] == ["p1", "p2"]
    assert api.calls[0]["json"]["filter"] == {"property": "Lote", "rich_text": {"equals": "x"}}
    assert api.calls[1]["json"]["start_cursor"] == "c2"


def test_reintenta_en_429(api):
    api.fail_first = 1
    ns.consultar("db1")
    assert len(api.calls) == 3 and api.calls[0]["url"] == api.calls[1]["url"]


def test_error_4xx_levanta(api):
    with pytest.raises(ns.NotionError):
        ns._req("GET", "/nada")


def test_valor_lee_tipos():
    page = {"properties": {
        "Acción": {"type": "title", "title": [{"plain_text": "gi"}, {"plain_text": "ro"}]},
        "Tipo": {"type": "select", "select": {"name": "Reposición / Empuje"}},
        "Filas": {"type": "number", "number": 3},
        "Fecha": {"type": "date", "date": {"start": "2026-09-14"}},
        "Detalle": {"type": "files", "files": [{"name": "a.csv", "file": {"url": "https://s3/a"}}]},
        "Vacio": {"type": "select", "select": None},
    }}
    assert ns.valor(page, "Acción") == "giro"
    assert ns.valor(page, "Tipo") == "Reposición / Empuje"
    assert ns.valor(page, "Filas") == 3 and ns.valor(page, "Fecha") == "2026-09-14"
    assert ns.valor(page, "Detalle") == [("a.csv", "https://s3/a")]
    assert ns.valor(page, "Vacio") is None and ns.valor(page, "NoExiste") is None
