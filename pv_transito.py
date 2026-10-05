"""
pv_transito.py — Motor del módulo "🚢 PV en Tránsito" (2026-10-04).

Responde la pregunta semanal de Majo: ¿qué % de la compra Primavera-Verano ya
está en el CD y cuándo llega lo que falta? Hoy Franco lo saca a mano del Excel
de comex; este motor lo calcula desde el mismo archivo.

Fuentes (ver docs/Guia-PV-Transito.md y el plan aprobado 2026-10-04):
  * DETALLE de comex (Luis Huerta): una fila por OC × SKU, con MONTO_FOB (USD),
    TEMP, VENTANA, COD PADRE, ESTADO_EMBARQUE_FINAL, INGRESOCD y la ETA al CD
    (PROYECTADO INGRESO CD3). Es la fuente primaria: trae lo recibido y lo pendiente.
  * Fallback: las hojas "LLEGADAS RETRASOS COMEX" de Franco (una fila por OC ×
    modelo, solo lo pendiente, sin costo). Sirven para pendiente, ETA y atrasos.

Todo cálculo parte de la tabla estándar `oc` que devuelve `leer_reporte` (una
fila por OC × SKU, o por OC × modelo en el fallback). Esa tabla es la "oferta
programada" que un futuro motor MRP consumiría: cantidad + fecha + estado. Aquí
no se construye el MRP; solo no se le cierra la puerta.

Reglas medidas (2026-10-04, DETALLE del 24-sep, HOMBRE × 4 marcas propias):
  * recibida = estado ∈ {1.Almacenado, 2.En CD} ∨ INGRESOCD no nulo (100 % consistente).
  * Costo S/ = Costo S/. de la Base Profundidad si el modelo lo tiene; si no,
    FOB USD × 4,04 (mediana medida, p10–p90 3,91–4,39), rotulado "estimado".
  * Cruce con la Base por COD PADRE ↔ Cód. Prod. (340/371 modelos).
  * Pares OC × SKU repetidos son líneas partidas legítimas: se suman, no se botan.

Pandas puro: nada de Streamlit aquí. La vista vive en vista_pv_transito.py y la
persistencia (historial de ETA) en eta_store.py.
"""
from __future__ import annotations

import io
import json
import os
import re
import unicodedata
from datetime import date, datetime

import numpy as np
import pandas as pd

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_CFG_PATH = os.path.join(_BASE_DIR, "config_pv_transito.json")
_ALIAS_PATH = os.path.join(_BASE_DIR, "config_marcas_alias.json")

CFG_DEFAULT = {
    "inputs_dir": "inputs",
    "marcas_foco": ["MARQUIS", "NAVIGATA", "CACHAREL", "SPAVALDI"],
    "division_filtro": "HOMBRE",
    "estados_recibido": ["1.Almacenado", "2.En CD"],
    "factor_fob_a_costo": 4.04,
    "banda_costo_pct": 0.06,
    "umbral_atraso_ambar": 0.10,
    "temporada_default": "PV",
    "ventana_temporada": {"E": "PV", "F": "PV", "A": "OI"},
    "max_items_detalle_llegadas": 6,
}

# Columna estándar → encabezados aceptados (se comparan normalizados: mayúsculas,
# sin tildes, espacios colapsados). Primero los del DETALLE, luego los de las hojas
# de Franco. Agregar un alias aquí es la única "programación" que debería hacer
# falta si comex renombra una columna.
COLUMN_MAP = {
    "oc": ["OC", "N OC", "NRO OC", "N° OC", "ORDEN DE COMPRA", "PO"],
    "sku": ["SKU"],
    "cod_padre": ["COD PADRE", "CODIGO PADRE", "COD. PROD.", "COD PROD", "CODMOD"],
    "embarque": ["EMBARQUE"],
    "estado": ["ESTADO_EMBARQUE_FINAL", "ESTADO EMBARQUE FINAL", "ESTADO EMBARQUE", "ESTADO"],
    "subestado": ["SUBESTADO_EMBARQUE_FINAL", "SUBESTADO"],
    "status_naviero": ["STATUS", "ESTADO BK NAVIERA"],
    "temporada_raw": ["TEMP", "TEMPORADA", "TEMP."],
    "ventana": ["VENTANA"],
    "division": ["DIVISION"],
    "departamento": ["DEPARTAMENTO", "DPTO"],
    "linea": ["LINEA"],
    "sublinea": ["SUBLINEA", "SUB LINEA"],
    "marca": ["MARCA"],
    "modelo": ["MODELO", "DESCRIPCION", "DESCRIPCION PRODUCTO"],
    "proveedor": ["PROVEEDOR"],
    "pais": ["PAIS", "ZONA_COMPRA", "ZONA COMPRA"],
    "via": ["VIA_TRANSP_REAL", "VIA TRANSP REAL", "VIA"],
    "uds": ["UND", "UNIDADES", "UNID", "CANTIDAD", "UU"],
    "fob_usd": ["MONTO_FOB", "MONTO FOB", "FOB", "FOB USD"],
    "fecha_lsd": ["LSD"],
    "etd": ["ETD", "ETD1"],
    "eta_puerto": ["ETA", "ETA CALLAO", "ETA PUERTO"],
    "eta_cd": ["PROYECTADO INGRESO CD3", "PROYECTADO INGRESO CD", "PROYECTADO_INGRESO_CD", "ETA CD", "FECHA LLEGADA CD"],
    "fecha_meta": ["FECHA_META_GENERAL", "FECHA META GENERAL"],
    "fecha_ingreso_cd": ["INGRESOCD", "INGRESO CD", "FECHA INGRESO CD", "FECHA_INGRESO_CD"],
    "motivo_retraso": ["MOTIVO_RETRASO", "MOTIVO RETRASO"],
    "llegada": ["LLEGADA"],
}
COLUMNAS_OBLIGATORIAS = ("oc", "marca", "uds", "eta_cd")
COLUMNAS_FECHA = ("fecha_lsd", "etd", "eta_puerto", "eta_cd", "fecha_meta", "fecha_ingreso_cd")
COLUMNAS_TEXTO = ("oc", "sku", "cod_padre", "embarque", "estado", "subestado", "status_naviero",
                  "temporada_raw", "ventana", "division", "departamento", "linea", "sublinea",
                  "marca", "modelo", "proveedor", "pais", "via", "motivo_retraso", "llegada")

# Contrato del export que consume el formato semanal (prompt Franco 2026-10-04).
# NO cambiar el orden ni los nombres sin cambiar el formato.
EXPORT_COLS = [
    "marca", "und_compra_pv", "und_recibida_cd", "und_en_tienda", "costo_compra_pv",
    "costo_recibido_cd", "pct_recibido_und", "pct_recibido_costo", "und_pendiente",
    "costo_pendiente", "proxima_eta", "und_atrasadas", "detalle_llegadas",
]

_VALORES_BASURA = {"", "NAN", "NONE", "(EN BLANCO)", "#N/D", "#N/A", "TOTAL GENERAL", "TOTAL"}


# ══════════════════════════════════════════════════════════════════════════════
#  Config y alias
# ══════════════════════════════════════════════════════════════════════════════
def cargar_config(path: str | None = None) -> dict:
    """config_pv_transito.json con defaults; las claves con '_' son documentación."""
    cfg = dict(CFG_DEFAULT)
    try:
        with open(path or _CFG_PATH, encoding="utf-8") as f:
            for k, v in json.load(f).items():
                if not k.startswith("_"):
                    cfg[k] = v
    except (OSError, ValueError):
        pass
    return cfg


def cargar_alias(path: str | None = None) -> dict:
    """{'alias': {texto → canónica}, 'display': {canónica → nombre bonito}}."""
    try:
        with open(path or _ALIAS_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return {"alias": {_norm_txt(k): str(v).upper().strip() for k, v in d.get("alias", {}).items()},
                "display": {str(k).upper().strip(): str(v) for k, v in d.get("display", {}).items()}}
    except (OSError, ValueError):
        return {"alias": {}, "display": {}}


def _norm_txt(s) -> str:
    """'  U.S. Pólo ' → 'U.S. POLO': mayúsculas, sin tildes, espacios colapsados."""
    if s is None or (isinstance(s, float) and np.isnan(s)):
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().upper()


def normalizar_marca(serie: pd.Series, alias: dict | None = None) -> tuple[pd.Series, pd.Series]:
    """Devuelve (marca_norm en MAYÚSCULAS canónicas, marca_display para exports)."""
    al = alias or cargar_alias()
    norm = serie.map(_norm_txt)
    norm = norm.map(lambda m: al["alias"].get(m, m))
    display = norm.map(lambda m: al["display"].get(m, m.title() if m else ""))
    return norm, display


def inputs_dir(cfg: dict | None = None) -> str:
    """Carpeta de inputs: CAPI_INPUTS_DIR > config > <repo>/inputs. Se crea si no existe."""
    cfg = cfg or cargar_config()
    d = os.environ.get("CAPI_INPUTS_DIR") or cfg.get("inputs_dir", "inputs")
    if not os.path.isabs(d):
        d = os.path.join(_BASE_DIR, d)
    os.makedirs(d, exist_ok=True)
    return d


# ══════════════════════════════════════════════════════════════════════════════
#  Lectura de archivos
# ══════════════════════════════════════════════════════════════════════════════
def _engine(nombre: str | None) -> str | None:
    return "pyxlsb" if str(nombre or "").lower().endswith(".xlsb") else None


def _leer_hojas(archivo, nombre: str | None = None) -> dict[str, pd.DataFrame]:
    """Todas las hojas crudas (header=None). `archivo` = ruta o bytes/buffer (uploader)."""
    if isinstance(archivo, (str, os.PathLike)):
        nombre = nombre or str(archivo)
        xl = pd.ExcelFile(archivo, engine=_engine(nombre))
    else:
        data = archivo.getvalue() if hasattr(archivo, "getvalue") else archivo
        xl = pd.ExcelFile(io.BytesIO(data), engine=_engine(nombre))
    return {sh: xl.parse(sh, header=None) for sh in xl.sheet_names}


def _alias_oc() -> set:
    return {_norm_txt(a) for a in COLUMN_MAP["oc"]}


def _fila_encabezado(raw: pd.DataFrame, max_filas: int = 30) -> int | None:
    """Primera fila (de las primeras 30) con una celda que sea un alias de OC ('OC', 'N° OC'…). None si no hay."""
    alias = _alias_oc()
    for i in range(min(max_filas, len(raw))):
        vals = {_norm_txt(v) for v in raw.iloc[i].tolist()}
        if vals & alias:
            return i
    return None


def _tabla_desde_raw(raw: pd.DataFrame) -> pd.DataFrame | None:
    """Convierte una hoja cruda en tabla con encabezado; bota columnas vacías y filas
    'Total general' / '(en blanco)' / sin OC. None si la hoja no tiene tabla de OC."""
    h = _fila_encabezado(raw)
    if h is None:
        return None
    df = raw.iloc[h + 1:].copy()
    df.columns = [_norm_txt(c) for c in raw.iloc[h].tolist()]
    df = df.loc[:, [c for c in df.columns if c and c != "NAN"]]
    df = df.loc[:, ~pd.Index(df.columns).duplicated()]
    col_oc = next((c for c in df.columns if c in _alias_oc()), None)
    if col_oc is None:
        return None
    if col_oc != "OC":
        df = df.rename(columns={col_oc: "OC"})          # la columna de OC siempre se llama OC de aquí en adelante
    oc = df["OC"].map(_oc_str)
    df = df[oc.ne("")].copy()
    df["OC"] = oc[oc.ne("")]
    return df.reset_index(drop=True)


def _oc_str(v) -> str:
    """2806634.0 → '2806634'; basura ('Total general', '(en blanco)', NaN) → ''."""
    s = _norm_txt(v)
    if s in _VALORES_BASURA:
        return ""
    s = re.sub(r"\.0+$", "", s)
    return s if re.fullmatch(r"[A-Z0-9\-/]+", s) else ""


def mapear_columnas(columnas, column_map: dict | None = None) -> dict[str, str]:
    """{estándar → encabezado presente}. Busca por nombre, nunca por posición."""
    cm = column_map or COLUMN_MAP
    presentes = {_norm_txt(c): c for c in columnas}
    out = {}
    for std, aliases in cm.items():
        for a in aliases:
            if _norm_txt(a) in presentes:
                out[std] = presentes[_norm_txt(a)]
                break
    return out


def detectar_formato(hojas: dict[str, pd.DataFrame]) -> str:
    """'detalle' si alguna hoja trae SKU y FOB (DETALLE de comex); 'llegadas' si trae OC
    sin esos campos (hojas de Franco); 'desconocido' si ninguna hoja tiene tabla de OC."""
    vio_oc = False
    for raw in hojas.values():
        t = _tabla_desde_raw(raw)
        if t is None:
            continue
        vio_oc = True
        m = mapear_columnas(t.columns)
        if "sku" in m and "fob_usd" in m:
            return "detalle"
    return "llegadas" if vio_oc else "desconocido"


def fecha_reporte_de(nombre: str | None, fallback: date | None = None, hoy: date | None = None) -> date:
    """Fecha de corte del reporte desde el nombre del archivo ('… 24.09.xlsb', '…_2026-09-24.xlsx').
    Sin fecha en el nombre → fallback (p. ej. la fecha de modificación) o hoy."""
    hoy = hoy or date.today()
    s = os.path.basename(str(nombre or ""))
    m = re.search(r"(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})", s)            # 2026-09-24
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.search(r"(?<!\d)(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?(?!\d)", s)   # 24.09 ó 24-09-2026
        if not m:
            return fallback or hoy
        d, mo = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else hoy.year
        if y < 100:
            y += 2000
    try:
        f = date(y, mo, d)
    except ValueError:
        return fallback or hoy
    return f if f <= hoy else f.replace(year=f.year - 1)


def _a_fecha(serie: pd.Series) -> pd.Series:
    """Serial Excel (acotado a 40000–60000: 2009–2064), datetime o texto → Timestamp normalizado.
    Fuera de rango (el 0 que pone comex cuando no hay fecha) → NaT."""
    if pd.api.types.is_datetime64_any_dtype(serie):
        return pd.to_datetime(serie).dt.normalize()
    num = pd.to_numeric(serie, errors="coerce").astype(float)
    es_num = num.notna()
    out = pd.Series(pd.NaT, index=serie.index, dtype="datetime64[ns]")
    if es_num.any():
        v = num.where((num > 40000) & (num < 60000))
        out[es_num] = (pd.Timestamp("1899-12-30") + pd.to_timedelta(v[es_num], unit="D")).dt.normalize()
    resto = ~es_num & serie.notna()
    if resto.any():
        out[resto] = pd.to_datetime(serie[resto], errors="coerce", dayfirst=True).dt.normalize()
    return out


def temporada_norm(raw) -> str:
    """'PV 26/27' → 'PV'; 'OI 27' → 'OI'; 'TT' → 'TT'; otro → ''."""
    s = _norm_txt(raw)
    for t in ("PV", "OI", "TT"):
        if s == t or s.startswith(t + " ") or s.startswith(t + "-"):
            return t
    if "PRIMAVERA" in s or "VERANO" in s:
        return "PV"
    if "OTO" in s or "INVIERNO" in s:
        return "OI"
    return ""


def _estandarizar(t: pd.DataFrame, fuente: str, fecha_reporte: date, cfg: dict, alias: dict) -> pd.DataFrame:
    """Tabla con encabezados crudos → tabla estándar `oc` (ver docstring del módulo)."""
    m = mapear_columnas(t.columns)
    faltan = [c for c in COLUMNAS_OBLIGATORIAS if c not in m]
    if faltan:
        raise ValueError(f"Al reporte le faltan columnas obligatorias: {faltan}. "
                         f"Encabezados vistos: {list(t.columns)[:20]}")
    oc = pd.DataFrame(index=t.index)
    for std in COLUMN_MAP:
        oc[std] = t[m[std]] if std in m else np.nan
    for c in COLUMNAS_TEXTO:
        oc[c] = oc[c].map(lambda v: "" if _norm_txt(v) in _VALORES_BASURA else str(v).strip())
    oc["oc"] = oc["oc"].map(_oc_str)
    oc["sku"] = oc["sku"].map(lambda v: re.sub(r"\.0+$", "", v))
    oc["cod_padre"] = oc["cod_padre"].map(lambda v: re.sub(r"\.0+$", "", v))
    oc["uds"] = pd.to_numeric(oc["uds"], errors="coerce").fillna(0.0)
    oc["fob_usd"] = pd.to_numeric(oc["fob_usd"], errors="coerce")
    for c in COLUMNAS_FECHA:
        oc[c] = _a_fecha(oc[c])
    oc["ventana"] = oc["ventana"].map(_norm_txt)
    oc["temporada"] = oc["temporada_raw"].map(temporada_norm)
    sin_temp = oc["temporada"].eq("")
    if sin_temp.any():
        oc.loc[sin_temp, "temporada"] = oc.loc[sin_temp, "ventana"].map(cfg["ventana_temporada"]).fillna("")
    oc["marca_norm"], oc["marca_display"] = normalizar_marca(oc["marca"], alias)
    oc["fuente"] = fuente
    oc["fecha_reporte"] = pd.Timestamp(fecha_reporte)
    oc["columnas_mapeadas"] = len(m)
    oc = marcar_recibida(oc, cfg)
    return oc.reset_index(drop=True)


def leer_detalle(archivo, nombre: str | None = None, fecha_reporte: date | None = None,
                 hoja: str | None = None, cfg: dict | None = None) -> pd.DataFrame:
    """DETALLE de comex → tabla estándar (una fila por OC × SKU). Toma la hoja `hoja`, o
    'Export' si existe, o la primera con SKU + FOB."""
    cfg = cfg or cargar_config()
    hojas = _leer_hojas(archivo, nombre)
    elegida = None
    if hoja and hoja in hojas:
        elegida = _tabla_desde_raw(hojas[hoja])
    else:
        orden = (["Export"] if "Export" in hojas else []) + [h for h in hojas if h != "Export"]
        for h in orden:
            t = _tabla_desde_raw(hojas[h])
            if t is not None and {"sku", "fob_usd"} <= set(mapear_columnas(t.columns)):
                elegida = t
                break
    if elegida is None:
        raise ValueError("Ninguna hoja parece el DETALLE de comex (hace falta OC, SKU y MONTO_FOB).")
    fr = fecha_reporte or fecha_reporte_de(nombre, _mtime(archivo))
    return _estandarizar(elegida, "detalle", fr, cfg, cargar_alias())


def leer_llegadas(archivo, nombre: str | None = None, fecha_reporte: date | None = None,
                  cfg: dict | None = None) -> pd.DataFrame:
    """Hojas 'LLEGADAS RETRASOS COMEX' de Franco → tabla estándar (una fila por OC × modelo).
    Usa CONSOLIDADO si existe; si no, une las hojas con tabla de OC (una OC una vez). La ventana
    sale del nombre de la hoja ('VENTANA E …' → 'E') y se pega a la OC aunque se lea CONSOLIDADO."""
    cfg = cfg or cargar_config()
    hojas = _leer_hojas(archivo, nombre)
    tablas = {h: t for h, t in ((h, _tabla_desde_raw(r)) for h, r in hojas.items()) if t is not None}
    if not tablas:
        raise ValueError("Ninguna hoja tiene una tabla con columna OC.")
    ventana_oc: dict[str, str] = {}
    for h, t in tablas.items():
        mv = re.search(r"VENTANA\s+(?:[A-Z]{2}\d{2}\s+)?([A-Z])\b", _norm_txt(h))
        if mv:
            for o in t["OC"]:
                ventana_oc.setdefault(o, mv.group(1))
    consolidado = next((h for h in tablas if "CONSOLIDADO" in _norm_txt(h)), None)
    if consolidado:
        t = tablas[consolidado]
    else:
        t = pd.concat(tablas.values(), ignore_index=True).drop_duplicates("OC", keep="first")
    t = t.copy()
    if "VENTANA" not in t.columns:
        t["VENTANA"] = t["OC"].map(ventana_oc).fillna("")
    fr = fecha_reporte or fecha_reporte_de(nombre, _mtime(archivo))
    return _estandarizar(t, "llegadas", fr, cfg, cargar_alias())


def leer_reporte(archivo, nombre: str | None = None, fecha_reporte: date | None = None,
                 cfg: dict | None = None) -> tuple[pd.DataFrame, str]:
    """Detecta el formato y devuelve (tabla estándar, 'detalle' | 'llegadas')."""
    cfg = cfg or cargar_config()
    hojas = _leer_hojas(archivo, nombre)
    fmt = detectar_formato(hojas)
    if fmt == "detalle":
        return leer_detalle(archivo, nombre, fecha_reporte, cfg=cfg), fmt
    if fmt == "llegadas":
        return leer_llegadas(archivo, nombre, fecha_reporte, cfg=cfg), fmt
    raise ValueError("El archivo no tiene ninguna hoja con una tabla de OC.")


def _mtime(archivo) -> date | None:
    try:
        return datetime.fromtimestamp(os.path.getmtime(archivo)).date()
    except (TypeError, OSError, ValueError):
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  Filtros, recibido, líneas partidas, validación
# ══════════════════════════════════════════════════════════════════════════════
def marcar_recibida(oc: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """recibida = estado ∈ estados_recibido ∨ fecha_ingreso_cd no nula ∨ recibida_inferida."""
    cfg = cfg or cargar_config()
    est = {_norm_txt(e) for e in cfg["estados_recibido"]}
    rec = oc["estado"].map(_norm_txt).isin(est) | oc["fecha_ingreso_cd"].notna()
    if "recibida_inferida" in oc.columns:
        rec = rec | oc["recibida_inferida"].fillna(False).astype(bool)
    oc = oc.copy()
    oc["recibida"] = rec.astype(bool)
    return oc


def filtrar(oc: pd.DataFrame, marcas: list[str] | None = None, division: str | None = None,
            temporada: str | None = None) -> pd.DataFrame:
    """Marcas foco (canónicas), división (contiene) y temporada ('PV'/'OI'/'TT'). None = no filtra.
    Si el reporte no trae DIVISION (hojas de Franco) el filtro de división no aplica."""
    out = oc
    if marcas:
        canon = {_norm_txt(m) for m in marcas}
        out = out[out["marca_norm"].isin(canon)]
    if division:
        tiene = out["division"].astype(str).str.strip().ne("")
        out = out[~tiene | out["division"].map(_norm_txt).str.contains(_norm_txt(division), regex=False)]
    if temporada:
        out = out[out["temporada"].eq(temporada.upper())]
    return out.copy()


def sumar_lineas_partidas(oc: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Pares OC × SKU repetidos (líneas partidas de una misma OC) se suman en uds y FOB.
    Devuelve (tabla, n_pares_sumados). Sin SKU (fallback) la clave es OC × modelo."""
    clave = ["oc", "sku"] if oc["sku"].astype(str).str.strip().ne("").any() else ["oc", "modelo"]
    dup = oc.duplicated(clave, keep=False)
    n = int(oc[dup].groupby(clave).ngroups) if dup.any() else 0
    if n == 0:
        return oc.copy(), 0
    agg = {c: "first" for c in oc.columns if c not in clave}
    agg["uds"] = "sum"
    agg["fob_usd"] = "sum"
    if "costo_total_sol" in oc.columns:
        agg["costo_total_sol"] = "sum"
    out = oc.groupby(clave, as_index=False, sort=False).agg(agg)
    return out[oc.columns], n


def validar(oc: pd.DataFrame) -> pd.DataFrame:
    """Hallazgos de calidad: tipo · oc · detalle. Vacío = todo limpio.
    Tipos: SIN_MARCA · SIN_ETA · UDS_NO_POSITIVAS · COSTO_CERO · LINEA_PARTIDA ·
    RECIBIDO_MAYOR_PEDIDO (solo si el reporte trae ambas cantidades) · SIN_TEMPORADA."""
    filas = []

    def add(tipo, sub, detalle):
        for o in sub["oc"].drop_duplicates():
            filas.append({"tipo": tipo, "oc": o, "detalle": detalle})

    add("SIN_MARCA", oc[oc["marca_norm"].eq("")], "la OC no trae marca; queda fuera del resumen por marca")
    add("SIN_ETA", oc[~oc["recibida"] & oc["eta_cd"].isna()], "pendiente sin fecha proyectada de ingreso al CD; cuenta como atrasada")
    add("UDS_NO_POSITIVAS", oc[oc["uds"] <= 0], "unidades cero o negativas")
    if oc["fob_usd"].notna().any():
        add("COSTO_CERO", oc[oc["fob_usd"].fillna(0) <= 0], "MONTO_FOB cero o vacío; el costo sale de la Base o queda vacío")
    add("SIN_TEMPORADA", oc[oc["temporada"].eq("")], "sin TEMP ni ventana reconocible; no entra en el filtro PV/OI")
    clave = ["oc", "sku"] if oc["sku"].astype(str).str.strip().ne("").any() else ["oc", "modelo"]
    add("LINEA_PARTIDA", oc[oc.duplicated(clave, keep=False)], "misma OC y SKU en más de una fila; se suman")
    if "uds_recibidas" in oc.columns:
        add("RECIBIDO_MAYOR_PEDIDO", oc[oc["uds_recibidas"] > oc["uds"]], "recibido mayor que pedido")
    return pd.DataFrame(filas, columns=["tipo", "oc", "detalle"])


# ══════════════════════════════════════════════════════════════════════════════
#  Costo y cruce con la Base Profundidad
# ══════════════════════════════════════════════════════════════════════════════
def base_desde_df_cob(df_cob: pd.DataFrame) -> pd.DataFrame:
    """Adapta el df_cob del motor (SKU × tienda) a la tabla que espera enriquecer_con_base:
    cod_prod · costo · stock_tiendas · stock_cd (una fila por modelo)."""
    if df_cob is None or df_cob.empty or "sku" not in df_cob.columns:
        return pd.DataFrame(columns=["cod_prod", "costo", "stock_tiendas", "stock_cd"])
    d = df_cob.copy()
    d["cod_prod"] = d["sku"].astype(str).str.replace(r"\.0+$", "", regex=True).str.strip()
    col_stk = "stock_uds" if "stock_uds" in d.columns else ("stock_total" if "stock_total" in d.columns else None)
    agg = {"costo": ("costo", "max")} if "costo" in d.columns else {}
    if col_stk:
        agg["stock_tiendas"] = (col_stk, "sum")
    if "stock_cd" in d.columns:
        agg["stock_cd"] = ("stock_cd", "max")
    out = d.groupby("cod_prod").agg(**agg).reset_index() if agg else d[["cod_prod"]].drop_duplicates()
    for c in ("costo", "stock_tiendas", "stock_cd"):
        if c not in out.columns:
            out[c] = np.nan
    return out[["cod_prod", "costo", "stock_tiendas", "stock_cd"]]


def enriquecer_con_base(oc: pd.DataFrame, base: pd.DataFrame | None, cfg: dict | None = None) -> pd.DataFrame:
    """Costo S/ y stock en cadena por modelo.
    costo_unit_sol = Costo S/. de la Base (cod_padre ↔ cod_prod) si > 0; si no, FOB/uds × factor.
    costo_fuente ∈ {'base', 'fob_x_factor', ''}; costo_total_sol = uds × costo_unit_sol.
    stock_tiendas / stock_cd quedan en la fila para el '% en tienda' (NaN si no hay Base)."""
    cfg = cfg or cargar_config()
    oc = oc.copy()
    factor = float(cfg["factor_fob_a_costo"])
    fob_u = oc["fob_usd"] / oc["uds"].replace(0, np.nan)
    fob_u = fob_u.where(fob_u > 0)                     # FOB cero o vacío → sin costo (NaN), no 0
    oc["costo_unit_sol"] = fob_u * factor
    oc["costo_fuente"] = np.where(oc["costo_unit_sol"].fillna(0) > 0, "fob_x_factor", "")
    oc["stock_tiendas"] = np.nan
    oc["stock_cd"] = np.nan
    if base is not None and not base.empty and oc["cod_padre"].astype(str).str.strip().ne("").any():
        b = base.copy()
        b["cod_prod"] = b["cod_prod"].astype(str).str.strip()
        b = b.drop_duplicates("cod_prod").set_index("cod_prod")
        costo_b = oc["cod_padre"].map(b["costo"]) if "costo" in b.columns else pd.Series(np.nan, index=oc.index)
        usa_base = costo_b.fillna(0) > 0
        oc.loc[usa_base, "costo_unit_sol"] = costo_b[usa_base]
        oc.loc[usa_base, "costo_fuente"] = "base"
        if "stock_tiendas" in b.columns:
            oc["stock_tiendas"] = oc["cod_padre"].map(b["stock_tiendas"])
        if "stock_cd" in b.columns:
            oc["stock_cd"] = oc["cod_padre"].map(b["stock_cd"])
    oc["costo_total_sol"] = oc["uds"] * oc["costo_unit_sol"]
    return oc


# ══════════════════════════════════════════════════════════════════════════════
#  ETA vigente y semana Ripley
# ══════════════════════════════════════════════════════════════════════════════
def aplicar_eta_vigente(oc: pd.DataFrame, vigente: pd.DataFrame | None) -> pd.DataFrame:
    """Columna `eta` = ETA vigente del historial (eta_store.vigente: oc · eta · fuente) si existe
    para la OC; si no, la del reporte (eta_cd). `eta_fuente` dice de dónde salió."""
    oc = oc.copy()
    oc["eta"] = oc["eta_cd"]
    oc["eta_fuente"] = np.where(oc["eta_cd"].notna(), "comex", "")
    if vigente is not None and not vigente.empty:
        v = vigente.drop_duplicates("oc", keep="last").set_index("oc")
        e = oc["oc"].map(v["eta"])
        tiene = e.notna()
        oc.loc[tiene, "eta"] = pd.to_datetime(e[tiene]).dt.normalize()
        if "fuente" in v.columns:
            oc.loc[tiene, "eta_fuente"] = oc.loc[tiene, "oc"].map(v["fuente"]).fillna("historial")
        else:
            oc.loc[tiene, "eta_fuente"] = "historial"
    return oc


def semana_ripley(fechas: pd.Series) -> pd.Series:
    """Fecha → 'W202634' (semana comercial Ripley, nunca ISO). Vectorizado con merge_asof sobre
    calendario_ripley._tabla(). Fuera del calendario (csv hasta 14/02/2027) → ''."""
    import calendario_ripley as cr
    tab = cr._tabla()[["fecha_inicio", "fecha_fin", "semact"]].copy()
    tab["fecha_inicio"] = pd.to_datetime(tab["fecha_inicio"])
    tab["fecha_fin"] = pd.to_datetime(tab["fecha_fin"])
    f = pd.to_datetime(fechas).dt.normalize()
    izq = pd.DataFrame({"f": f, "_i": np.arange(len(f))}).dropna(subset=["f"]).sort_values("f")
    if izq.empty:
        return pd.Series("", index=fechas.index, dtype=object)
    m = pd.merge_asof(izq, tab.sort_values("fecha_inicio"), left_on="f", right_on="fecha_inicio", direction="backward")
    m["semact"] = m["semact"].where(m["f"] <= m["fecha_fin"], "")
    out = pd.Series("", index=fechas.index, dtype=object)
    out.iloc[m["_i"].to_numpy()] = m["semact"].fillna("").to_numpy()
    return out


def cierre_semana(semact: str) -> pd.Timestamp | None:
    import calendario_ripley as cr
    t = cr._tabla()
    f = t.loc[t["semact"] == semact, "fecha_fin"]
    return pd.Timestamp(f.iloc[0]) if len(f) else None


# ══════════════════════════════════════════════════════════════════════════════
#  Cálculos
# ══════════════════════════════════════════════════════════════════════════════
def _hoy(hoy) -> pd.Timestamp:
    return pd.Timestamp(hoy or date.today()).normalize()


def _eta_col(oc: pd.DataFrame) -> pd.Series:
    return oc["eta"] if "eta" in oc.columns else oc["eta_cd"]


def atrasos(oc: pd.DataFrame, hoy=None) -> pd.DataFrame:
    """Pendiente con ETA anterior a hoy o sin ETA. Agrega dias_atraso (NaN si no hay ETA)."""
    h = _hoy(hoy)
    eta = _eta_col(oc)
    a = oc[~oc["recibida"] & (eta.isna() | (eta < h))].copy()
    a["eta"] = eta[a.index]
    a["dias_atraso"] = (h - a["eta"]).dt.days
    return a


def semaforo(und_atrasadas: float, und_pendiente: float, cfg: dict | None = None) -> str:
    """'verde' sin atrasos · 'ambar' < umbral del pendiente · 'rojo' ≥ umbral. Sin pendiente → verde."""
    cfg = cfg or cargar_config()
    if und_atrasadas <= 0 or und_pendiente <= 0:
        return "verde"
    return "ambar" if und_atrasadas / und_pendiente < float(cfg["umbral_atraso_ambar"]) else "rojo"


def detalle_llegadas_texto(pend: pd.DataFrame, hoy=None, max_items: int | None = None, cfg: dict | None = None) -> str:
    """'Camisas M/C 1,200 u 15/10; Polos M/C 800 u 22/10': por línea, unidades pendientes con ETA
    futura y su próxima fecha, ordenado por fecha. Lo vencido/sin ETA va al final como 'atrasado'."""
    cfg = cfg or cargar_config()
    n_max = max_items or int(cfg["max_items_detalle_llegadas"])
    h = _hoy(hoy)
    eta = _eta_col(pend)
    fut = pend[eta >= h].assign(_eta=eta[eta >= h])
    partes = []
    if not fut.empty:
        g = fut.groupby(fut["linea"].map(lambda s: s.title() if s else "Sin línea")).agg(uds=("uds", "sum"), eta=("_eta", "min"))
        for linea, r in g.sort_values("eta").iterrows():
            partes.append(f"{linea} {r['uds']:,.0f} u {r['eta']:%d/%m}")
    atr = pend[eta.isna() | (eta < h)]
    if not atr.empty:
        partes.append(f"atrasado {atr['uds'].sum():,.0f} u")
    if len(partes) > n_max:
        partes = partes[:n_max] + ["…"]
    return "; ".join(partes)


def resumen_marca(oc: pd.DataFrame, hoy=None, por_linea: bool = False, cfg: dict | None = None) -> pd.DataFrame:
    """Una fila por marca (o marca × línea) con las 13 columnas del contrato de export más
    n_oc, oc_atrasadas, semaforo, costo_fuente y pct_en_tienda.
    Espera la tabla `oc` ya filtrada (marcas, división, temporada) y, si se quiere costo/en tienda,
    pasada por enriquecer_con_base. Sin costo → columnas de costo en NaN (no en 0)."""
    cfg = cfg or cargar_config()
    h = _hoy(hoy)
    oc = oc.copy()
    oc["_eta"] = _eta_col(oc)
    if "costo_total_sol" not in oc.columns:
        oc["costo_total_sol"] = np.nan
    if "stock_tiendas" not in oc.columns:
        oc["stock_tiendas"] = np.nan
    claves = ["marca_display"] + (["linea"] if por_linea else [])
    filas = []
    for k, g in oc.groupby(claves, sort=True):
        k = k if isinstance(k, tuple) else (k,)
        rec, pen = g[g["recibida"]], g[~g["recibida"]]
        atr = pen[pen["_eta"].isna() | (pen["_eta"] < h)]
        prox = pen.loc[pen["_eta"] >= h, "_eta"].min()
        tiene_costo = g["costo_total_sol"].notna().any()
        c_tot = g["costo_total_sol"].sum() if tiene_costo else np.nan
        c_rec = rec["costo_total_sol"].sum() if tiene_costo else np.nan
        c_pen = pen["costo_total_sol"].sum() if tiene_costo else np.nan
        # stock en tienda: por modelo (una vez por cod_padre), no por fila OC×SKU
        if g["stock_tiendas"].notna().any():
            por_modelo = g.drop_duplicates("cod_padre") if g["cod_padre"].astype(str).str.strip().ne("").any() else g.drop_duplicates("modelo")
            en_tienda = float(por_modelo["stock_tiendas"].fillna(0).sum())
        else:
            en_tienda = np.nan
        u_tot = float(g["uds"].sum())
        fila = {
            "marca": k[0],
            **({"linea": k[1]} if por_linea else {}),
            "und_compra_pv": u_tot,
            "und_recibida_cd": float(rec["uds"].sum()),
            "und_en_tienda": en_tienda,
            "costo_compra_pv": c_tot,
            "costo_recibido_cd": c_rec,
            "pct_recibido_und": float(rec["uds"].sum()) / u_tot if u_tot > 0 else np.nan,
            "pct_recibido_costo": (c_rec / c_tot) if tiene_costo and c_tot and c_tot > 0 else np.nan,
            "und_pendiente": float(pen["uds"].sum()),
            "costo_pendiente": c_pen,
            "proxima_eta": prox.date() if pd.notna(prox) else None,
            "und_atrasadas": float(atr["uds"].sum()),
            "detalle_llegadas": detalle_llegadas_texto(pen, h, cfg=cfg),
            "n_oc": int(g["oc"].nunique()),
            "oc_atrasadas": int(atr["oc"].nunique()),
            "semaforo": semaforo(float(atr["uds"].sum()), float(pen["uds"].sum()), cfg),
            "costo_fuente": ", ".join(sorted(x for x in g.get("costo_fuente", pd.Series(dtype=str)).dropna().unique() if x)),
            "pct_en_tienda": (en_tienda / u_tot) if pd.notna(en_tienda) and u_tot > 0 else np.nan,
        }
        filas.append(fila)
    cols = (["marca"] + (["linea"] if por_linea else []) + EXPORT_COLS[1:]
            + ["n_oc", "oc_atrasadas", "semaforo", "costo_fuente", "pct_en_tienda"])
    return pd.DataFrame(filas, columns=cols)


def pendiente_por_semana(oc: pd.DataFrame, hoy=None, por_linea: bool = False) -> pd.DataFrame:
    """Pendiente agrupado por marca × semana Ripley de llegada (ETA al CD).
    Buckets especiales: 'VENCIDA' (ETA < hoy) y 'SIN ETA'. Columnas: marca · semana · cierre ·
    uds · n_oc (· linea). Ordenado: VENCIDA, SIN ETA, luego semanas."""
    h = _hoy(hoy)
    pen = oc[~oc["recibida"]].copy()
    if pen.empty:
        return pd.DataFrame(columns=["marca", "semana", "cierre", "uds", "n_oc"] + (["linea"] if por_linea else []))
    pen["_eta"] = _eta_col(pen)
    sem = semana_ripley(pen["_eta"])
    pen["semana"] = np.where(pen["_eta"].isna(), "SIN ETA", np.where(pen["_eta"] < h, "VENCIDA", sem.replace("", "FUERA DE CALENDARIO")))
    claves = ["marca_display", "semana"] + (["linea"] if por_linea else [])
    g = pen.groupby(claves, as_index=False).agg(uds=("uds", "sum"), n_oc=("oc", "nunique")).rename(columns={"marca_display": "marca"})
    g["cierre"] = g["semana"].map(lambda s: cierre_semana(s) if s.startswith("W") else pd.NaT)
    orden = {"VENCIDA": 0, "SIN ETA": 1}
    g["_o"] = g["semana"].map(lambda s: orden.get(s, 2 if s.startswith("W") else 3))
    g = g.sort_values(["_o", "semana", "marca"]).drop(columns="_o").reset_index(drop=True)
    return g[["marca", "semana", "cierre", "uds", "n_oc"] + (["linea"] if por_linea else [])]


# ══════════════════════════════════════════════════════════════════════════════
#  Export (contrato fijo)
# ══════════════════════════════════════════════════════════════════════════════
def export_formato(resumen: pd.DataFrame) -> pd.DataFrame:
    """Exactamente las 13 columnas de EXPORT_COLS, en ese orden, una fila por marca.
    pct_* como fracción 0–1; proxima_eta como fecha (o vacío); und_en_tienda vacío si no hay Base."""
    df = resumen.copy()
    if "linea" in df.columns:          # el export es por marca; si vino por línea, se agrega
        df = resumen_desde_lineas(df)
    for c in EXPORT_COLS:
        if c not in df.columns:
            df[c] = np.nan
    out = df[EXPORT_COLS].copy()
    for c in ("pct_recibido_und", "pct_recibido_costo"):
        out[c] = pd.to_numeric(out[c], errors="coerce").clip(0, 1)
    out["detalle_llegadas"] = out["detalle_llegadas"].fillna("").astype(str)
    return out.reset_index(drop=True)


def resumen_desde_lineas(res_linea: pd.DataFrame) -> pd.DataFrame:
    """Agrega un resumen por marca × línea a marca (suma; % recalculado; próxima ETA mínima)."""
    g = res_linea.groupby("marca", as_index=False).agg(
        und_compra_pv=("und_compra_pv", "sum"), und_recibida_cd=("und_recibida_cd", "sum"),
        und_en_tienda=("und_en_tienda", lambda s: s.sum() if s.notna().any() else np.nan),
        costo_compra_pv=("costo_compra_pv", lambda s: s.sum() if s.notna().any() else np.nan),
        costo_recibido_cd=("costo_recibido_cd", lambda s: s.sum() if s.notna().any() else np.nan),
        und_pendiente=("und_pendiente", "sum"), costo_pendiente=("costo_pendiente", lambda s: s.sum() if s.notna().any() else np.nan),
        proxima_eta=("proxima_eta", lambda s: min((x for x in s if x is not None and pd.notna(x)), default=None)),
        und_atrasadas=("und_atrasadas", "sum"),
        detalle_llegadas=("detalle_llegadas", lambda s: "; ".join(x for x in s if x)),
    )
    g["pct_recibido_und"] = g["und_recibida_cd"] / g["und_compra_pv"].replace(0, np.nan)
    g["pct_recibido_costo"] = g["costo_recibido_cd"] / g["costo_compra_pv"].replace(0, np.nan)
    return g


def nombre_export(hoy=None) -> str:
    return f"pv_transito_{_hoy(hoy):%Y-%m-%d}.xlsx"


def escribir_export(resumen: pd.DataFrame, destino_dir: str, hoy=None, notas: list[str] | None = None,
                    detalle_oc: pd.DataFrame | None = None) -> str:
    """Escribe pv_transito_AAAA-MM-DD.xlsx: hoja 'pv_transito' con el esquema fijo; hoja 'notas'
    (opcional) con fuente, fecha del reporte y advertencias; hoja 'detalle_oc' (opcional).
    Devuelve la ruta. El esquema de la hoja 1 nunca cambia aunque haya notas."""
    os.makedirs(destino_dir, exist_ok=True)
    ruta = os.path.join(destino_dir, nombre_export(hoy))
    with pd.ExcelWriter(ruta, engine="openpyxl") as w:
        export_formato(resumen).to_excel(w, sheet_name="pv_transito", index=False)
        if notas:
            pd.DataFrame({"nota": notas}).to_excel(w, sheet_name="notas", index=False)
        if detalle_oc is not None and not detalle_oc.empty:
            detalle_oc.to_excel(w, sheet_name="detalle_oc", index=False)
    return ruta


def export_bytes(resumen: pd.DataFrame, notas: list[str] | None = None, detalle_oc: pd.DataFrame | None = None) -> bytes:
    """Mismo contenido que escribir_export, en memoria (para st.download_button)."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        export_formato(resumen).to_excel(w, sheet_name="pv_transito", index=False)
        if notas:
            pd.DataFrame({"nota": notas}).to_excel(w, sheet_name="notas", index=False)
        if detalle_oc is not None and not detalle_oc.empty:
            detalle_oc.to_excel(w, sheet_name="detalle_oc", index=False)
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
#  Pipeline de conveniencia
# ══════════════════════════════════════════════════════════════════════════════
def procesar(archivo, nombre: str | None = None, hoy=None, temporada: str | None = "PV",
             base: pd.DataFrame | None = None, vigente: pd.DataFrame | None = None,
             fecha_reporte: date | None = None, cfg: dict | None = None) -> dict:
    """Lee → filtra (marcas foco, división, temporada) → suma líneas partidas → costo → ETA
    vigente → resumen por marca, por línea, pendiente por semana, atrasos, hallazgos.
    Devuelve un dict con todo; la vista solo pinta."""
    cfg = cfg or cargar_config()
    oc_todo, fmt = leer_reporte(archivo, nombre, fecha_reporte, cfg)
    hallazgos = validar(oc_todo)
    oc = filtrar(oc_todo, cfg["marcas_foco"], cfg.get("division_filtro") or None, temporada)
    oc, n_partidas = sumar_lineas_partidas(oc)
    oc = enriquecer_con_base(oc, base, cfg)
    oc = aplicar_eta_vigente(oc, vigente)
    notas = [f"fuente: {fmt}", f"fecha del reporte: {oc_todo['fecha_reporte'].iloc[0]:%d/%m/%Y}" if len(oc_todo) else "sin filas",
             f"columnas mapeadas: {int(oc_todo['columnas_mapeadas'].iloc[0]) if len(oc_todo) else 0} de {len(COLUMN_MAP)}"]
    if fmt == "llegadas":
        notas.append("reporte de llegadas pendientes (sin costo ni OC recibidas): % recibido no calculable")
    if oc["costo_fuente"].eq("fob_x_factor").any():
        notas.append(f"costo S/ estimado = FOB USD × {cfg['factor_fob_a_costo']} en modelos sin Costo S/. en la Base (banda ±{cfg['banda_costo_pct']:.0%})")
    faltan = sorted(set(_norm_txt(m) for m in cfg["marcas_foco"]) - set(oc["marca_norm"]))
    if faltan:
        notas.append("marcas foco sin filas en el reporte: " + ", ".join(faltan))
    return {
        "formato": fmt, "oc_todo": oc_todo, "oc": oc, "hallazgos": hallazgos, "n_lineas_partidas": n_partidas,
        "resumen": resumen_marca(oc, hoy, cfg=cfg), "resumen_linea": resumen_marca(oc, hoy, por_linea=True, cfg=cfg),
        "por_semana": pendiente_por_semana(oc, hoy), "atrasos": atrasos(oc, hoy), "notas": notas,
        "fecha_reporte": oc_todo["fecha_reporte"].iloc[0].date() if len(oc_todo) else None,
    }
