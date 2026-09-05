"""
flujo_ingesta.py — Lectura del archivo de flujo de Planificación Ripley.

Fuente: "Flujo por Línea OI26 EFA Gus v3.xlsm" (Planificación, entregado por
Franco 2026-09-01). Se ingiere la hoja "BD - FL Consolidado" (la base ancha,
12.970 filas desde 2019) y "OC's Adic" (órdenes con mes de entrega).

Ojo con las otras hojas: "BD FL" y "FL" están FILTRADAS a una sola combinación
División/Depto/Marca/Línea/Programa a la vez (23 filas) — son la vista de
trabajo del planificador, no la base. Leer de ahí trae una sola serie.

Identidad que sostiene todo el módulo (decisión de Franco 2026-09-02, sin
línea de reducciones):

    stock_soles(t) = stock_soles(t-1) + compra_soles(t) - vta_costo(t)
    contrib(t)     = vta_soles(t) - vta_costo(t)

`vta_costo` se deriva SIEMPRE de la contribución oficial (vta_soles - contrib),
nunca del campo "Costo S/." del micro, que subestima el margen 11,7 pp en
terceras nacionales.
"""


import warnings

import pandas as pd

# ── Hojas del archivo de Planificación ──
HOJA_CONSOLIDADO = "BD - FL Consolidado"
HOJA_OC_ADIC = "OC's Adic"

# Mapeo de la base ancha al esquema canónico del módulo
COLUMN_MAP_FL = {
    "División": "division",
    "Departamento": "departamento",
    "Marca": "marca",
    "Línea": "linea",
    "Programa": "programa",
    "Temp.": "temporada",
    "PERIODO": "periodo",
    "Año": "anio",
    "Mes (Desc.)": "mes",
    "Vta (uu.)": "vta_uu",
    "Vta (S/.)": "vta_soles",
    "Contri": "contrib",
    "Costo": "vta_costo",
    "Compra (uu)": "compra_uu",
    "Compra (S/.)": "compra_soles",
    "Stock Unid.": "stock_uu",
    "Stock S/.": "stock_soles",
}

COLUMN_MAP_OC = {
    "Año": "anio",
    "Mes": "mes",
    "División": "division",
    "Dpto": "departamento",
    "Marca": "marca",
    "Línea": "linea",
    "Programa": "programa",
    "Temporada": "temporada",
    "Descripción": "descripcion",
    "OC (uu.)": "oc_uu",
    "OC (S/.)": "oc_soles",
    "Cto Unit": "costo_unitario",
    "OBS": "observacion",
}

NUMERICAS_FL = ["vta_uu", "vta_soles", "contrib", "vta_costo",
                "compra_uu", "compra_soles", "stock_uu", "stock_soles"]

# El año comercial Ripley arranca en marzo: P01 = Marzo ... P12 = Febrero.
# (confirmado en la hoja FL del archivo de Planificación)
MES_A_PERIODO = {
    "Marzo": 1, "Abril": 2, "Mayo": 3, "Junio": 4, "Julio": 5, "Agosto": 6,
    "Setiembre": 7, "Septiembre": 7, "Octubre": 8, "Noviembre": 9,
    "Diciembre": 10, "Enero": 11, "Febrero": 12,
}

MES_A_NUM = {
    "Enero": 1, "Febrero": 2, "Marzo": 3, "Abril": 4, "Mayo": 5, "Junio": 6,
    "Julio": 7, "Agosto": 8, "Setiembre": 9, "Septiembre": 9, "Octubre": 10,
    "Noviembre": 11, "Diciembre": 12,
}


def _normalizar(df: pd.DataFrame, mapping: dict, numericas: list) -> pd.DataFrame:
    """Renombra al esquema canónico, castea numéricas y limpia texto."""
    presentes = {k: v for k, v in mapping.items() if k in df.columns}
    faltantes = set(mapping) - set(presentes)
    df = df.rename(columns=presentes)
    df = df[[c for c in mapping.values() if c in df.columns]].copy()

    for col in numericas:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    for col in df.columns:
        if col not in numericas and df[col].dtype == object:
            df[col] = df[col].astype(str).str.strip()

    if faltantes:
        df.attrs["columnas_faltantes"] = sorted(faltantes)
    return df


def cargar_bd_fl(path: str, validar: bool = True, anio_vigente: int = 2026) -> pd.DataFrame:
    """Base ancha del flujo: una fila por combinación x periodo.

    Devuelve el esquema canónico + `mes_num`, `periodo_num` y `vta_costo_calc`
    (la venta a costo recalculada desde la contribución, que es la que manda).

    Con `validar=True` (default) corre la identidad de inventario sobre TODAS
    las series del año vigente en cuanto entra la data, y avisa si alguna no
    cuadra. Es la revisión crítica: la identidad es recursiva, así que un
    inventario descuadrado se arrastra a toda proyección hecha desde ahí. Avisa
    y deja pasar — el bloqueo duro está en `flujo_engine.proyectar`, que es
    donde el error se volvería un OTB mal dimensionado.
    """
    df = pd.read_excel(path, sheet_name=HOJA_CONSOLIDADO)
    df = _normalizar(df, COLUMN_MAP_FL, NUMERICAS_FL)
    df = df[df["periodo"].notna() & (df["periodo"] != "")]
    df = df[df["periodo"].str.startswith("P")]

    df["anio"] = pd.to_numeric(df["anio"], errors="coerce").astype("Int64")
    df["mes_num"] = df["mes"].map(MES_A_NUM).astype("Int64")
    df["periodo_num"] = df["mes"].map(MES_A_PERIODO).astype("Int64")

    # La venta a costo canónica viene de la contribución oficial, no del campo
    # de costo unitario. Se conserva la original para poder auditar el gap.
    df["vta_costo_calc"] = df["vta_soles"] - df["contrib"]
    df = df.reset_index(drop=True)

    if validar:
        _avisar_integridad(df, anio_vigente)
    return df


def _avisar_integridad(df: pd.DataFrame, anio_vigente: int) -> None:
    """Corre la identidad sobre el año vigente y emite un warning si falla."""
    from flujo_engine import auditar_integridad  # import diferido: evita ciclo

    vig = df[df["anio"] == anio_vigente]
    if vig.empty:
        return
    rep = auditar_integridad(vig)
    if rep.empty:
        return
    malas = rep[rep["periodos_rotos"] > 0]
    if malas.empty:
        return

    detalle = "; ".join(
        f"{r.marca}/{r.linea} ({int(r.periodos_rotos)} periodo(s), "
        f"S/{r.dif_max_soles:,.0f})" for r in malas.head(5).itertuples())
    warnings.warn(
        f"Integridad de inventario {anio_vigente}: {len(malas)} serie(s) no cuadran "
        f"(stock inicial + compra − venta a costo ≠ stock final). Toda proyección "
        f"hecha desde esos periodos arrastra el error. Detalle: {detalle}",
        stacklevel=2,
    )


def cargar_oc_adic(path: str) -> pd.DataFrame:
    """Órdenes de compra adicionales, con el mes de ENTREGA (no de emisión).

    Es la pieza que permite separar OTB por periodo de OTB por ventana: el
    on-order se descuenta por fecha de entrega.
    """
    df = pd.read_excel(path, sheet_name=HOJA_OC_ADIC)
    df = _normalizar(df, COLUMN_MAP_OC, ["oc_uu", "oc_soles", "costo_unitario"])
    df = df[df["anio"].notna() & (df["anio"] != "")]
    df["anio"] = pd.to_numeric(df["anio"], errors="coerce").astype("Int64")
    df["mes_num"] = df["mes"].map(MES_A_NUM).astype("Int64")
    df["periodo_num"] = df["mes"].map(MES_A_PERIODO).astype("Int64")
    return df.dropna(subset=["anio"]).reset_index(drop=True)


def auditar_costo(df: pd.DataFrame) -> pd.DataFrame:
    """Compara el `Costo` del archivo contra `vta_soles - contrib` por marca.

    Un gap material señala que el campo de costo del origen no es la venta a
    costo (el problema conocido de terceras nacionales). El módulo usa siempre
    `vta_costo_calc`; esto solo mide cuánto se estaba desviando.
    """
    g = df.groupby("marca", as_index=False)[
        ["vta_soles", "contrib", "vta_costo", "vta_costo_calc"]
    ].sum()
    g["gap_soles"] = g["vta_costo"] - g["vta_costo_calc"]
    g["gap_pct"] = (g["gap_soles"] / g["vta_costo_calc"].replace(0, pd.NA)) * 100
    return g.sort_values("gap_soles", key=abs, ascending=False)
