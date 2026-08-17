"""
rendimiento_tienda.py — Rendimiento comercial por tienda.

Responde lo que Capi no tenía: **contribución por tienda** y **contribución/m²**,
que según `CLAUDE.md` es uno de los dos KPIs que mira el dueño para área comercial
(el otro es EBITDA) y que hasta ahora estaba documentado pero sin implementar.

Genérico por marca: Spavaldi es el primer caso de uso, no el único.

Sin Streamlit adentro a propósito — funciones puras sobre DataFrames, para poder
validarlas contra el oráculo sin levantar la app.

──────────────────────────────────────────────────────────────────────────────
De dónde sale cada número
──────────────────────────────────────────────────────────────────────────────
El Micro **formato nuevo** (387 columnas, desde ~ago-2026) trae 6 columnas por
tienda: `Stk`, `Unidades`, `Vta S/.`, `On Order`, `UME`, `Precio Prom`. Con venta
en soles por tienda la contribución **se calcula, no se estima**:

    contribucion_tienda = Σ_sku  [{cód} Vta S/.] − [{cód} Unidades × Costo S/.]

Validado sobre Spavaldi en `Base al 11.08.xlsx` (semana 1ant, 103 SKUs, 15 tiendas):
unidades 477 = 477, venta 46,805 vs 46,803, **contribución 18,346 vs 18,351 (0.03%)**.

El Micro **viejo** (301 col) NO trae soles por tienda — se rechaza explícitamente
en vez de calcular mal.

──────────────────────────────────────────────────────────────────────────────
Dos trampas del archivo que ya costaron caro
──────────────────────────────────────────────────────────────────────────────
1. Los prefijos de tienda NO son consistentes entre las 6 columnas:
   `FSF MAC LO Vta S/.` contra `FSC MAC LO Precio Prom`, y ` JULIACA Vta S/.`
   con espacio inicial. Construir el nombre con f-string falla en silencio, así
   que cada columna se resuelve matcheando por nombre normalizado.

2. Los nombres de tienda del transaccional no calzan con los del Micro — ni por
   mayúsculas (`SALAVERRY` vs `Salaverry`) ni por nombre (`PLAZA LIMA NORTE` vs
   `Plaza Norte`). Sin resolver, **7.1% de las unidades desaparecen sin error**.
   Por eso el mapeo vive en `config_tiendas.json` con alias explícitos y acá se
   ABORTA si algo no mapea.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

from config import UMBRALES_DEFAULT

IGV = 1.18
# Mismo umbral que usa taxonomia.py para separar LIQUIDAR/MUERTO del resto.
# Se importa en vez de hardcodear 26 para que se ajuste en un solo lugar.
EDAD_LIQUIDACION = UMBRALES_DEFAULT["edad_maduro"]

_SUFIJOS_TIENDA = ("Stk", "Unidades", "Vta S/.", "On Order", "UME", "Precio Prom")
_CONFIG = Path(__file__).with_name("config_tiendas.json")


class FormatoMicroError(ValueError):
    """El Micro no es del formato nuevo (sin soles por tienda)."""


class TiendaSinMapearError(ValueError):
    """Una tienda con venta no se pudo mapear — abortar antes de perder plata."""


# ──────────────────────────────────────────────────────────────
#  Utilidades
# ──────────────────────────────────────────────────────────────

def _norm(s) -> str:
    """Mayúsculas sin tildes, espacios colapsados. La única forma de comparar
    nombres entre el Micro y el transaccional sin perder tiendas."""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.upper()).strip()


def cargar_config(path: str | Path | None = None) -> dict:
    with open(path or _CONFIG, encoding="utf-8") as f:
        return json.load(f)


def _indice_nombres(cfg: dict) -> dict[str, str]:
    """{nombre normalizado -> código}, incluyendo alias del transaccional."""
    idx = {}
    for cod, meta in cfg["tiendas"].items():
        idx[_norm(meta["nombre"])] = cod
        idx[_norm(cod)] = cod
        for a in meta.get("alias_transaccional", []):
            idx[_norm(a)] = cod
    return idx


def resolver_tienda(nombre, cfg: dict) -> str | None:
    """Nombre del transaccional → código del Micro. None si no mapea."""
    return _indice_nombres(cfg).get(_norm(nombre))


# ──────────────────────────────────────────────────────────────
#  1. Ingesta del Micro
# ──────────────────────────────────────────────────────────────

def _columnas_por_tienda(df: pd.DataFrame) -> dict[str, dict[str, str]]:
    """{código: {sufijo: nombre real de la columna}}.

    Resuelve por nombre normalizado porque los prefijos vienen inconsistentes
    (FSF/FSC) y algunos traen espacios de más.
    """
    reales = {_norm(c): c for c in df.columns}
    codigos = [c[: -len(" Stk")].strip() for c in df.columns if str(c).strip().endswith(" Stk")]
    out = {}
    for cod in codigos:
        cols = {}
        for suf in _SUFIJOS_TIENDA:
            real = reales.get(_norm(f"{cod} {suf}"))
            if real is not None:
                cols[suf] = real
        out[cod] = cols
    return out


def desde_micro(df_micro: pd.DataFrame, marcas=None, cfg: dict | None = None) -> pd.DataFrame:
    """Micro (wide, 1 fila = 1 SKU) → largo `sku × tienda` con plata real.

    Devuelve una fila por SKU×tienda con venta de la semana 1ant y stock actual.
    Solo tiendas activas del config; las inactivas (MAC, EST, VTAS CORP…) se
    descartan porque no son locales reales.
    """
    cfg = cfg or cargar_config()
    tiendas_cfg = cfg["tiendas"]
    cols_t = _columnas_por_tienda(df_micro)

    # Formato viejo: existe "{cód} Stk" pero no "{cód} Vta S/." en ninguna tienda.
    if not any("Vta S/." in c for c in cols_t.values()):
        raise FormatoMicroError(
            "Este Micro es del formato viejo (sin columnas '{tienda} Vta S/.'). "
            "El módulo necesita el formato nuevo (387 columnas) para calcular "
            "contribución por tienda. Descarga una base actualizada."
        )

    df = df_micro
    if marcas:
        objetivo = {_norm(m) for m in ([marcas] if isinstance(marcas, str) else marcas)}
        df = df[df["Marca"].map(_norm).isin(objetivo)]

    base_cols = {
        "Cód. Prod.": "sku", "Descripción": "descripcion", "Marca": "marca",
        "Línea": "linea", "Sub Línea": "sublinea", "Dpto": "departamento",
        "Temp.": "temporada", "Costo S/.": "costo_unitario",
        "P.Blanco": "precio_blanco", "P.Vigente PMM": "precio_vigente",
        "Antigüedad semanal": "edad_semanas",
    }
    presentes = {k: v for k, v in base_cols.items() if k in df.columns}
    faltan = set(base_cols) - set(presentes)
    if faltan:
        raise FormatoMicroError(f"Faltan columnas base en el Micro: {sorted(faltan)}")

    filas = []
    for cod, cols in cols_t.items():
        meta = tiendas_cfg.get(cod)
        if meta is None or not meta["activa"]:
            continue
        if len(cols) < len(_SUFIJOS_TIENDA):
            raise TiendaSinMapearError(
                f"A la tienda activa '{cod}' le faltan columnas en el Micro: "
                f"{sorted(set(_SUFIJOS_TIENDA) - set(cols))}. Revisa el archivo "
                f"o marca la tienda como inactiva en config_tiendas.json."
            )
        sub = df[list(presentes)].rename(columns=presentes).copy()
        sub["cod_tienda"] = cod
        sub["tienda"] = meta["nombre"]
        sub["canal"] = meta["canal"]
        sub["unidades"] = pd.to_numeric(df[cols["Unidades"]], errors="coerce").fillna(0)
        sub["venta_soles"] = pd.to_numeric(df[cols["Vta S/."]], errors="coerce").fillna(0)
        sub["stock_uds"] = pd.to_numeric(df[cols["Stk"]], errors="coerce").fillna(0)
        sub["on_order"] = pd.to_numeric(df[cols["On Order"]], errors="coerce").fillna(0)
        sub["ume"] = pd.to_numeric(df[cols["UME"]], errors="coerce").fillna(0)
        sub["precio_prom"] = pd.to_numeric(df[cols["Precio Prom"]], errors="coerce")
        filas.append(sub)

    largo = pd.concat(filas, ignore_index=True)
    largo = largo[(largo["unidades"] != 0) | (largo["stock_uds"] != 0)].copy()

    # Contribución REAL, no prorrateada: la venta en soles ya viene por tienda.
    largo["contribucion"] = largo["venta_soles"] - largo["unidades"] * largo["costo_unitario"].fillna(0)
    # El Precio Prom viene ex-IGV; P.Blanco lleva IGV.
    largo["dscto_efectivo"] = np.where(
        largo["precio_blanco"] > 0, 1 - largo["precio_prom"] * IGV / largo["precio_blanco"], np.nan)
    return largo


# ──────────────────────────────────────────────────────────────
#  2. Ingesta del transaccional (historia acumulada)
# ──────────────────────────────────────────────────────────────

def cargar_transaccional(path, corte=None, division="HOMBRE", cfg: dict | None = None) -> pd.DataFrame:
    """BBDD transaccional (ticket × SKU × tienda × día) → formato del módulo.

    Es la única fuente con historia por tienda hacia atrás; el Micro solo trae
    la última semana. Aborta si alguna tienda con venta no mapea.
    """
    cfg = cfg or cargar_config()
    df = pd.read_excel(path) if not isinstance(path, pd.DataFrame) else path.copy()
    if division and "Division" in df.columns:
        df = df[df["Division"] == division]
    if corte is not None:
        df = df[df["Fecha"] <= pd.Timestamp(corte)]

    idx = _indice_nombres(cfg)
    df["cod_tienda"] = df["Sucursal"].map(lambda x: idx.get(_norm(x)))

    huerfanas = df.loc[df["cod_tienda"].isna(), "Sucursal"].value_counts()
    conocidas = {_norm(k) for k in cfg.get("_sin_codigo_micro", {})}
    reales = [s for s in huerfanas.index if _norm(s) not in conocidas]
    if reales:
        raise TiendaSinMapearError(
            f"Sucursales sin código de tienda: {reales}. Agrega el alias en "
            f"config_tiendas.json — si se ignoran, su venta desaparece sin aviso."
        )

    out = pd.DataFrame({
        "sku": df.get("CodVariacion", df.get("Cód. Prod.")),
        "descripcion": df.get("Modelo", df.get("Descripción")),
        "marca": df["Marca"], "linea": df["Linea"],
        "departamento": df.get("Dpto"), "temporada": df.get("Temporada"),
        "fecha": df["Fecha"], "periodo": df.get("Periodo"),
        "cod_tienda": df["cod_tienda"], "tienda": df["Sucursal"],
        "unidades": df["VtaUnd"],
        "venta_soles": df["VtaSMF"],           # neta ex-IGV, misma base que el Micro
        "contribucion": df["Contr"],
        "precio_blanco": df.get("PrecioMaster"),
    })
    out["canal"] = out["cod_tienda"].map(
        lambda c: cfg["tiendas"].get(c, {}).get("canal", "tienda") if c else "tienda")
    out["tienda"] = out["cod_tienda"].map(
        lambda c: cfg["tiendas"].get(c, {}).get("nombre") if c else None).fillna(out["tienda"])
    return out


# ──────────────────────────────────────────────────────────────
#  3. Temporada vs liquidación
# ──────────────────────────────────────────────────────────────

def clasificar_liquidacion(df: pd.DataFrame, col_edad="edad_semanas",
                           umbral: float | None = None) -> pd.DataFrame:
    """Marca cada venta como `temporada` o `liquidacion`.

    Usa el mismo umbral de edad con que `taxonomia.py` separa LIQUIDAR/MUERTO,
    para que el módulo hable el idioma del resto de Capi.

    Importa desde que la directriz cambió (14-ago-2026): la liquidación ya no se
    consolida en un canal, **muere en cada tienda**. Sin este corte, el margen de
    todas las tiendas va a caer con el tiempo y nadie va a saber por qué.
    """
    u = EDAD_LIQUIDACION if umbral is None else umbral
    out = df.copy()
    edad = pd.to_numeric(out.get(col_edad), errors="coerce")
    out["tipo_venta"] = np.where(edad > u, "liquidacion",
                                 np.where(edad.isna(), "sin_edad", "temporada"))
    return out


# ──────────────────────────────────────────────────────────────
#  4. Métricas por tienda
# ──────────────────────────────────────────────────────────────

def _ratio(num, den):
    return np.where(den > 0, num / den, np.nan)


def metricas_por_tienda(base: pd.DataFrame, marca=None, cfg: dict | None = None,
                        semanas: float = 1.0) -> pd.DataFrame:
    """Una fila por tienda con el P&L partido en temporada vs liquidación.

    `semanas` es la ventana que cubre `base`, para anualizar la cobertura
    (el Micro trae 1 semana; el transaccional, muchas).

    La contribución/m² sale **vacía, no cero**, cuando la tienda no tiene m²
    cargado — un cero se lee como "no rinde", y para outlets y liquidadoras
    directamente no se carga m² a propósito.
    """
    cfg = cfg or cargar_config()
    df = base if marca is None else base[base["marca"].map(_norm) == _norm(marca)]
    if df.empty:
        return pd.DataFrame()
    if "tipo_venta" not in df.columns:
        df = clasificar_liquidacion(df)

    g = df.groupby(["cod_tienda", "tienda", "canal"], dropna=False)
    m = g.agg(
        unidades=("unidades", "sum"),
        venta_soles=("venta_soles", "sum"),
        contribucion=("contribucion", "sum"),
        stock_uds=("stock_uds", "sum") if "stock_uds" in df.columns else ("unidades", "size"),
        n_skus=("sku", "nunique"),
    ).reset_index()

    liq = df[df["tipo_venta"] == "liquidacion"].groupby("cod_tienda").agg(
        und_liq=("unidades", "sum"), venta_liq=("venta_soles", "sum"),
        contr_liq=("contribucion", "sum"))
    tmp = df[df["tipo_venta"] == "temporada"].groupby("cod_tienda").agg(
        und_tmp=("unidades", "sum"), venta_tmp=("venta_soles", "sum"),
        contr_tmp=("contribucion", "sum"))
    m = m.join(liq, on="cod_tienda").join(tmp, on="cod_tienda").fillna(
        {c: 0 for c in ("und_liq", "venta_liq", "contr_liq", "und_tmp", "venta_tmp", "contr_tmp")})

    m["margen"] = _ratio(m["contribucion"], m["venta_soles"])
    m["margen_temporada"] = _ratio(m["contr_tmp"], m["venta_tmp"])
    m["margen_liquidacion"] = _ratio(m["contr_liq"], m["venta_liq"])
    m["pct_venta_liquidacion"] = _ratio(m["venta_liq"], m["venta_soles"])
    m["pct_und_liquidacion"] = _ratio(m["und_liq"], m["unidades"])

    if "edad_semanas" in df.columns:
        pos = df[df["unidades"] > 0]
        m = m.join(pos.groupby("cod_tienda")["edad_semanas"].median().rename("edad_mediana"),
                   on="cod_tienda")

    if "stock_uds" in df.columns:
        m["cobertura_sem"] = _ratio(m["stock_uds"], m["unidades"] / max(semanas, 1e-9))

    # ── contribución / m² ────────────────────────────────────────────
    marca_key = _norm(marca) if marca else None
    def _m2(cod):
        meta = cfg["tiendas"].get(cod) or {}
        m2 = {(_norm(k)): v for k, v in (meta.get("m2_corner") or {}).items()}
        v = m2.get(marca_key) if marca_key else (sum(m2.values()) or None)
        return v if v else np.nan

    m["m2"] = m["cod_tienda"].map(_m2)
    m["contrib_x_m2"] = _ratio(m["contribucion"], m["m2"])
    m["contrib_temporada_x_m2"] = _ratio(m["contr_tmp"], m["m2"])
    m["venta_x_m2"] = _ratio(m["venta_soles"], m["m2"])
    # Sin m² no hay métrica: NaN, jamás 0.
    sin_m2 = m["m2"].isna()
    m.loc[sin_m2, ["contrib_x_m2", "contrib_temporada_x_m2", "venta_x_m2"]] = np.nan

    return m.sort_values("venta_soles", ascending=False).reset_index(drop=True)


def tiendas_en_perdida(metricas: pd.DataFrame, umbral: float = 0.0) -> pd.DataFrame:
    """Tiendas con contribución bajo el umbral, con el contexto que las explica.

    El margen total por sí solo engaña: Chorrillos marcaba −24% pero su margen
    de temporada es +43.6% — el 94% de lo que vende es liquidación. Por eso acá
    van siempre las dos lecturas juntas.
    """
    cols = [c for c in ("cod_tienda", "tienda", "canal", "venta_soles", "contribucion",
                        "margen", "margen_temporada", "pct_venta_liquidacion",
                        "edad_mediana") if c in metricas.columns]
    out = metricas[metricas["contribucion"] <= umbral][cols].copy()
    out["diagnostico"] = np.where(
        out.get("margen_temporada", pd.Series(np.nan, index=out.index)) > 0,
        "sano en temporada — la pérdida viene de la liquidación",
        "pierde también en mercadería corriente — revisar")
    return out.sort_values("contribucion")


# ──────────────────────────────────────────────────────────────
#  5. Comparación entre marcas
# ──────────────────────────────────────────────────────────────

def comparar_marcas(base: pd.DataFrame, marcas, por=("periodo", "tienda"),
                    metrica="venta_soles") -> pd.DataFrame:
    """Participación entre marcas, por el eje que se pida.

    Ojo con el denominador: dos marcas no están en las mismas tiendas. La
    participación se calcula **solo sobre las filas donde ambas venden**, y se
    devuelve `n_tiendas` para poder declararlo.
    """
    objetivo = [_norm(m) for m in marcas]
    df = base[base["marca"].map(_norm).isin(objetivo)].copy()
    df["marca_n"] = df["marca"].map(_norm)
    ejes = [e for e in por if e in df.columns]

    piv = df.pivot_table(index=ejes, columns="marca_n", values=metrica,
                         aggfunc="sum", fill_value=0)
    piv["_total"] = piv.sum(axis=1)
    for m in objetivo:
        if m in piv.columns:
            piv[f"pct_{m}"] = _ratio(piv[m], piv["_total"])
    # Ratio directo entre las dos primeras marcas — es como lo pide Majo
    # ("Jockey Plaza es el 60% de Cacharel").
    if len(objetivo) == 2 and all(m in piv.columns for m in objetivo):
        a, b = objetivo
        piv[f"{a}_vs_{b}"] = _ratio(piv[a], piv[b])
    return piv.reset_index()


# ──────────────────────────────────────────────────────────────
#  6. Apertura real
# ──────────────────────────────────────────────────────────────

def apertura_real(trans: pd.DataFrame, corners: dict | None = None) -> pd.DataFrame:
    """Fecha en que la tienda realmente empezó a vender la marca.

    NO es el primer registro: en varias tiendas los primeros meses son solo
    devoluciones de compras hechas en otro local (Atocongo arrastraba 116 días
    de ruido, Plaza Lima Norte 40). Se usa la fecha fin de obra del corner
    cuando existe, y si no, el primer día con venta neta positiva.
    """
    dia = trans.groupby(["tienda", "fecha"])["unidades"].sum().reset_index()
    pos = dia[dia["unidades"] > 0].groupby("tienda")["fecha"].min()
    out = pos.rename("apertura").reset_index()
    out["origen"] = "primera venta neta positiva"
    if corners:
        cmap = {_norm(k): pd.Timestamp(v) for k, v in corners.items()}
        hit = out["tienda"].map(lambda t: cmap.get(_norm(t)))
        out.loc[hit.notna(), "origen"] = "fin de obra del corner"
        out["apertura"] = hit.fillna(out["apertura"])
    return out
