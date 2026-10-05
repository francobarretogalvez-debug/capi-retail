"""
eta_store.py — Historial de ETA y maestro de OC del módulo "🚢 PV en Tránsito" (2026-10-04).

Dos tablas, ambas en parquet local (caché) y una de ellas también en Notion (fuente de verdad):

  eta_historial  — APPEND-ONLY. Una fila por cada cambio de ETA de una OC. Nunca se sobrescribe:
                   así se sabe cuántas veces se movió una fecha y cuánto corrió. La ETA "vigente"
                   es la última fila de cada OC (vigente()). Fuentes: 'comex' (vino en el reporte
                   semanal), 'manual' (editada en la pantalla), 'proveedor' (carga masiva desde
                   el Excel del proveedor). Cada fila se sube a Notion 🚢 ETA Capi si hay token.
  maestro_oc     — Una fila por OC vista alguna vez: primera y última vez vista, y si "desapareció"
                   del reporte de llegadas pendientes (recibida_inferida). Solo importa para el
                   fallback de las hojas de Franco, donde lo recibido deja de aparecer.

Rutas: <CAPI_SNAPSHOTS_DIR o snapshots>/pv_transito/{eta_historial,maestro_oc}.parquet. La carpeta
está en .gitignore. En Streamlit Cloud el disco se borra en cada push: por eso al arrancar la vista
se llama restaurar_desde_notion() si el parquet local está vacío.
"""
from __future__ import annotations

import os
from datetime import date, datetime

import numpy as np
import pandas as pd

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

COLS_HIST = ["oc", "eta", "fuente", "comentario", "registrado_en", "semana_ripley", "usuario", "marca", "modelo", "notion_url"]
COLS_MAESTRO = ["oc", "marca", "modelo", "primera_vez_vista", "ultima_vez_vista", "recibida_inferida", "semana_recibida", "fuente"]
FUENTES = ("comex", "manual", "proveedor")


# ── Rutas ────────────────────────────────────────────────────────────────────
def _dir(base_dir: str | None = None) -> str:
    """Carpeta de persistencia; se resuelve en la llamada para que CAPI_SNAPSHOTS_DIR aísle tests."""
    if base_dir:
        raiz = base_dir
    elif os.environ.get("CAPI_SNAPSHOTS_DIR"):
        raiz = os.environ["CAPI_SNAPSHOTS_DIR"]
    else:
        try:
            from snapshots_engine.config import SNAPSHOTS_DIR
            raiz = SNAPSHOTS_DIR
        except Exception:  # pragma: no cover
            raiz = os.path.join(_BASE_DIR, "snapshots")
    d = os.path.join(raiz, "pv_transito")
    os.makedirs(d, exist_ok=True)
    return d


def ruta_historial(base_dir: str | None = None) -> str:
    return os.path.join(_dir(base_dir), "eta_historial.parquet")


def ruta_maestro(base_dir: str | None = None) -> str:
    return os.path.join(_dir(base_dir), "maestro_oc.parquet")


# ── Historial ────────────────────────────────────────────────────────────────
def _vacio(cols) -> pd.DataFrame:
    df = pd.DataFrame({c: pd.Series(dtype=object) for c in cols})
    for c in ("eta", "registrado_en", "primera_vez_vista", "ultima_vez_vista"):
        if c in df.columns:
            df[c] = pd.Series(dtype="datetime64[ns]")
    return df


def cargar_historial(base_dir: str | None = None) -> pd.DataFrame:
    p = ruta_historial(base_dir)
    if not os.path.exists(p):
        return _vacio(COLS_HIST)
    df = pd.read_parquet(p)
    for c in COLS_HIST:
        if c not in df.columns:
            df[c] = np.nan
    df["eta"] = pd.to_datetime(df["eta"], errors="coerce").dt.normalize()
    df["registrado_en"] = pd.to_datetime(df["registrado_en"], errors="coerce")
    return df[COLS_HIST]


def guardar_historial(df: pd.DataFrame, base_dir: str | None = None) -> str:
    p = ruta_historial(base_dir)
    df[COLS_HIST].to_parquet(p, index=False)
    return p


def _semana(fecha) -> str:
    try:
        import calendario_ripley as cr
        return (cr.info_fecha(pd.Timestamp(fecha).date()) or {}).get("semact", "") or ""
    except Exception:  # pragma: no cover
        return ""


def registrar(oc: str, eta, fuente: str, comentario: str = "", usuario: str = "", marca: str = "",
              modelo: str = "", registrado_en=None, base_dir: str | None = None, notion: bool = True) -> dict:
    """Agrega UNA fila al historial (append) y la sube a Notion si hay token. Devuelve la fila.
    `eta` puede ser None (la OC perdió su fecha): queda registrado como cambio a 'sin ETA'."""
    if fuente not in FUENTES:
        raise ValueError(f"fuente debe ser una de {FUENTES}")
    reg = pd.Timestamp(registrado_en or datetime.now())
    eta_ts = pd.Timestamp(eta).normalize() if eta is not None and not pd.isna(eta) else pd.NaT
    fila = {"oc": str(oc).strip(), "eta": eta_ts, "fuente": fuente, "comentario": str(comentario or ""),
            "registrado_en": reg, "semana_ripley": _semana(reg), "usuario": str(usuario or ""),
            "marca": str(marca or ""), "modelo": str(modelo or ""), "notion_url": ""}
    if notion:
        try:
            import notion_store
            r = notion_store.registrar_eta(fila["oc"], None if pd.isna(eta_ts) else eta_ts.date().isoformat(), fuente,
                                           comentario, fila["semana_ripley"], marca, modelo, reg.date().isoformat(), usuario)
            if r.get("ok"):
                fila["notion_url"] = r.get("url") or ""
        except Exception:  # pragma: no cover — Notion nunca debe tumbar el registro local
            pass
    hist = cargar_historial(base_dir)
    hist = pd.concat([hist, pd.DataFrame([fila])], ignore_index=True)
    guardar_historial(hist, base_dir)
    return fila


def registrar_lote(cambios: pd.DataFrame, fuente: str, usuario: str = "", registrado_en=None,
                   base_dir: str | None = None, notion: bool = True, solo_si_cambia: bool = True) -> int:
    """Varias filas de una vez: `cambios` con columnas oc · eta (· comentario · marca · modelo).
    Con solo_si_cambia, una OC cuya ETA es igual a la vigente no genera fila. El parquet se lee y
    escribe UNA vez (no por fila); Notion recibe una página por fila. Devuelve n registradas."""
    if cambios is None or cambios.empty:
        return 0
    if fuente not in FUENTES:
        raise ValueError(f"fuente debe ser una de {FUENTES}")
    hist = cargar_historial(base_dir)
    vig = vigente(hist).set_index("oc")["eta"] if solo_si_cambia and not hist.empty else pd.Series(dtype="datetime64[ns]")
    reg = pd.Timestamp(registrado_en or datetime.now())
    sem = _semana(reg)
    filas = []
    for _, r in cambios.iterrows():
        oc = str(r["oc"]).strip()
        eta = pd.Timestamp(r["eta"]).normalize() if pd.notna(r.get("eta")) else pd.NaT
        if solo_si_cambia and oc in vig.index:
            prev = vig[oc]
            if (pd.isna(prev) and pd.isna(eta)) or (pd.notna(prev) and pd.notna(eta) and prev == eta):
                continue
        fila = {"oc": oc, "eta": eta, "fuente": fuente, "comentario": str(r.get("comentario", "") or ""),
                "registrado_en": reg, "semana_ripley": sem, "usuario": str(usuario or ""),
                "marca": str(r.get("marca", "") or ""), "modelo": str(r.get("modelo", "") or ""), "notion_url": ""}
        if notion:
            try:
                import notion_store
                rn = notion_store.registrar_eta(oc, None if pd.isna(eta) else eta.date().isoformat(), fuente, fila["comentario"],
                                                sem, fila["marca"], fila["modelo"], reg.date().isoformat(), fila["usuario"])
                if rn.get("ok"):
                    fila["notion_url"] = rn.get("url") or ""
            except Exception:  # pragma: no cover
                pass
        filas.append(fila)
    if not filas:
        return 0
    hist = pd.concat([hist, pd.DataFrame(filas)], ignore_index=True)
    guardar_historial(hist, base_dir)
    return len(filas)


def vigente(hist: pd.DataFrame) -> pd.DataFrame:
    """Última ETA por OC + cuántas veces se movió y cuántos días corrió desde la primera.
    Columnas: oc · eta · fuente · registrado_en · comentario · n_cambios · eta_primera · dias_corrimiento."""
    if hist is None or hist.empty:
        return pd.DataFrame(columns=["oc", "eta", "fuente", "registrado_en", "comentario", "n_cambios", "eta_primera", "dias_corrimiento"])
    h = hist.sort_values(["registrado_en"], kind="stable")
    ult = h.groupby("oc", sort=False).tail(1).set_index("oc")
    pri = h.groupby("oc", sort=False).head(1).set_index("oc")["eta"]
    out = ult[["eta", "fuente", "registrado_en", "comentario"]].copy()
    out["n_cambios"] = h.groupby("oc").size()
    out["eta_primera"] = pri
    out["dias_corrimiento"] = (out["eta"] - out["eta_primera"]).dt.days
    return out.reset_index()


def sincronizar_desde_reporte(oc_tabla: pd.DataFrame, fecha_reporte=None, usuario: str = "",
                              base_dir: str | None = None, notion: bool = True) -> int:
    """Al cargar un corte de comex: por cada OC cuya ETA al CD (eta_cd) difiere de la vigente
    (o no tiene registro) se agrega una fila fuente='comex' fechada en fecha_reporte.
    Así el historial se arma solo, semana a semana, sin tipear. Devuelve n filas nuevas."""
    if oc_tabla is None or oc_tabla.empty:
        return 0
    por_oc = (oc_tabla.sort_values("eta_cd", na_position="last").drop_duplicates("oc")
              [["oc", "eta_cd", "marca_norm", "modelo"]]
              .rename(columns={"eta_cd": "eta", "marca_norm": "marca"}))
    por_oc["comentario"] = ""
    return registrar_lote(por_oc, "comex", usuario, fecha_reporte, base_dir, notion, solo_si_cambia=True)


# ── Maestro de OC (para el fallback de llegadas pendientes) ──────────────────
def cargar_maestro(base_dir: str | None = None) -> pd.DataFrame:
    p = ruta_maestro(base_dir)
    if not os.path.exists(p):
        return _vacio(COLS_MAESTRO)
    df = pd.read_parquet(p)
    for c in COLS_MAESTRO:
        if c not in df.columns:
            df[c] = np.nan
    return df[COLS_MAESTRO]


def actualizar_maestro(oc_tabla: pd.DataFrame, fecha_reporte, fuente: str, base_dir: str | None = None,
                       inferir_recibidas: bool = True) -> pd.DataFrame:
    """Upsert de las OC del corte (primera/última vez vista). Si inferir_recibidas, las OC del
    mismo tipo de reporte que estaban y ya no aparecen se marcan recibida_inferida con la semana
    Ripley del corte. Solo tiene sentido con fuente='llegadas' (el DETALLE sí trae lo recibido)."""
    m = cargar_maestro(base_dir)
    f = pd.Timestamp(fecha_reporte).normalize()
    vistas = oc_tabla.drop_duplicates("oc")[["oc", "marca_norm", "modelo"]].rename(columns={"marca_norm": "marca"})
    m = m.set_index("oc")
    for _, r in vistas.iterrows():
        if r["oc"] in m.index:
            m.loc[r["oc"], "ultima_vez_vista"] = f
            m.loc[r["oc"], "recibida_inferida"] = False
            m.loc[r["oc"], "semana_recibida"] = ""
        else:
            m.loc[r["oc"], ["marca", "modelo", "primera_vez_vista", "ultima_vez_vista", "recibida_inferida", "semana_recibida", "fuente"]] = \
                [r["marca"], r["modelo"], f, f, False, "", fuente]
    if inferir_recibidas:
        ausentes = (~m.index.isin(vistas["oc"])) & (m["fuente"] == fuente) & (pd.to_datetime(m["ultima_vez_vista"]) < f) \
                   & ~m["recibida_inferida"].fillna(False).astype(bool)
        m.loc[ausentes, "recibida_inferida"] = True
        m.loc[ausentes, "semana_recibida"] = _semana(f)
    m = m.reset_index()
    m["recibida_inferida"] = m["recibida_inferida"].fillna(False).astype(bool)
    m[COLS_MAESTRO].to_parquet(ruta_maestro(base_dir), index=False)
    return m[COLS_MAESTRO]


# ── Carga masiva y restauración ──────────────────────────────────────────────
def cargar_masivo_excel(archivo, nombre: str | None = None) -> pd.DataFrame:
    """Excel con columnas OC · ETA (· COMENTARIO) buscadas por nombre → DataFrame oc · eta · comentario.
    Filas sin OC o con ETA ilegible se descartan (se devuelven en el atributo .attrs['descartadas'])."""
    import pv_transito as pt
    hojas = pt._leer_hojas(archivo, nombre)
    for raw in hojas.values():
        t = pt._tabla_desde_raw(raw)
        if t is None:
            continue
        m = pt.mapear_columnas(t.columns, {"oc": ["OC", "N OC", "NRO OC", "ORDEN DE COMPRA", "PO"],
                                           "eta": ["ETA", "ETA CD", "NUEVA ETA", "FECHA", "FECHA LLEGADA", "PROYECTADO INGRESO CD3"],
                                           "comentario": ["COMENTARIO", "OBS", "OBSERVACION", "MOTIVO"]})
        if "oc" not in m or "eta" not in m:
            continue
        out = pd.DataFrame({"oc": t[m["oc"]].astype(str).str.replace(r"\.0+$", "", regex=True).str.strip(),
                            "eta": pt._a_fecha(t[m["eta"]]),
                            "comentario": t[m["comentario"]].fillna("").astype(str) if "comentario" in m else ""})
        ok = out["oc"].ne("") & out["eta"].notna()
        res = out[ok].reset_index(drop=True)
        res.attrs["descartadas"] = int((~ok).sum())
        return res
    raise ValueError("El Excel no tiene una hoja con columnas OC y ETA.")


def restaurar_desde_notion(base_dir: str | None = None, forzar: bool = False) -> int:
    """Si el parquet local está vacío (o forzar) y hay token, reconstruye el historial desde
    🚢 ETA Capi. Devuelve n filas restauradas (0 si no hizo nada)."""
    hist = cargar_historial(base_dir)
    if not hist.empty and not forzar:
        return 0
    try:
        import notion_store
        filas = notion_store.listar_eta()
    except Exception:  # pragma: no cover
        filas = []
    if not filas:
        return 0
    df = pd.DataFrame(filas)
    for c in COLS_HIST:
        if c not in df.columns:
            df[c] = ""
    df["eta"] = pd.to_datetime(df["eta"], errors="coerce").dt.normalize()
    df["registrado_en"] = pd.to_datetime(df["registrado_en"], errors="coerce")
    guardar_historial(df[COLS_HIST], base_dir)
    return len(df)
