"""
analisis_estados.py — Conclusiones automáticas sobre los movimientos de
estados entre dos semanas de snapshot (sección "Análisis de estados" de la
vista Caso de Éxito).

Motor de REGLAS determinista (mismos números → mismas conclusiones), con las
lecturas de retail que un buyer haría: qué estado se movió, qué marca lo
impulsó, qué migró hacia dónde, y qué acción corresponde.

Origen: pedido de Franco 2026-08-24 a partir del análisis del rebote de la
semana 2026-33 (ESTANCADO +0.92M = compras nuevas acumulándose).
"""

import numpy as np
import pandas as pd

from snapshots_engine import api
from snapshots_engine.storage import load_snapshot

# Umbral de materialidad: un movimiento por debajo no genera conclusión
UMBRAL_SOLES = 100_000

# Interpretación retail de cada estado cuando SUBE su capital
_LECTURA_SUBIDA = {
    "ESTANCADO": ("Producto JOVEN con cobertura altísima: las compras nuevas están "
                  "entrando más rápido de lo que rotan. Acción: empuje/exhibición y "
                  "revisar profundidad de compra — NO markdown (todavía es producto vivo)."),
    "SOBRESTOCK": ("La cobertura crece en producto que sí vende. Acción: frenar "
                   "reposición de esos modelos y rebalancear entre tiendas antes de tocar precio."),
    "DORMIDO": ("SKUs que dejaron de vender. Acción: activar precio/exhibición ahora — "
                "cada semana dormido acerca la mercadería a MUERTO."),
    "MUERTO": ("Mercadería cruzó los 6 meses sin venta. Acción: liquidación directa "
               "por pirámide de descuentos; cada semana parada es margen que se pierde."),
    "LIQUIDAR": ("Producto maduro con rotación agotada. Acción: liquidación agresiva "
                 "o negociación de devolución/rebate con el proveedor."),
    "QUIEBRE": ("Más capital en riesgo de venta perdida. Acción: revisar reposiciones "
                "pendientes y transferencias desde tiendas con exceso."),
    "PRE-QUIEBRE": ("Stock acercándose al quiebre. Acción: adelantar reposición de los "
                    "modelos con venta confirmada."),
    "NUEVO SIN VENTA": ("Ingresos recientes aún en ventana de lanzamiento (<8 sem). "
                        "Monitorear: si no traccionan en 2-3 semanas, pasan a DORMIDO."),
}

_LECTURA_BAJADA = {
    "MUERTO": "Capital muerto liberado — la mejor noticia posible de la serie.",
    "LIQUIDAR": "Liquidación avanzando — capital maduro saliendo del sistema.",
    "DORMIDO": "Dormido bajando — mercadería reactivada o liquidada.",
    "ESTANCADO": "El producto joven empezó a rotar — el empuje/exhibición está funcionando.",
    "SOBRESTOCK": "Sobrestock drenando — la venta está alcanzando a la compra.",
    "QUIEBRE": "Menos capital en quiebre — la reposición está llegando.",
}

# Flujos de migración con lectura propia (estado_a → estado_b)
_LECTURA_FLUJOS = {
    ("DORMIDO", "MUERTO"): ("⚠️ Deterioro, no mejora: S/ {cap:,.0f} migró de DORMIDO a "
                            "MUERTO sin activarse. El dormido que no se toca, se muere."),
    ("NUEVO SIN VENTA", "DORMIDO"): ("⚠️ S/ {cap:,.0f} de mercadería nueva terminó su ventana "
                                     "de lanzamiento sin vender — revisar exhibición/precio de entrada."),
    ("ESTANCADO", "LIQUIDAR"): ("⚠️ S/ {cap:,.0f} de producto estancado maduró a LIQUIDAR — "
                                "el empuje no llegó a tiempo."),
}
_ESTADOS_VIVOS = {"ÓPTIMO", "ALTO", "QUIEBRE", "PRE-QUIEBRE", "SOBRESTOCK"}


def _capital_por_estado_marca(semana):
    """Capital por (estado, marca) de una semana — para saber qué marca impulsó."""
    from taxonomia import classify_series
    try:
        df = load_snapshot(semana)
    except FileNotFoundError:
        return pd.DataFrame()
    if df.empty or "stock_valor_costo" not in df.columns:
        return pd.DataFrame()
    df = df.copy()
    df["estado"] = classify_series(df["cobertura_sem"], edad=df.get("edad_semanas"),
                                   rango=df.get("rango_antiguedad"))
    return df.groupby(["estado", "marca"], as_index=False)["stock_valor_costo"].sum()


def conclusiones(sem_a: str, sem_b: str, acciones_df: pd.DataFrame = None) -> list:
    """Lista de conclusiones [{nivel, titulo, detalle}] sobre el paso sem_a → sem_b.

    nivel ∈ {'positivo', 'atencion', 'critico', 'info'} (mapea a
    st.success / st.warning / st.error / st.info en la UI).
    """
    out = []
    ca = api.capital_por_estado(sem_a)
    cb = api.capital_por_estado(sem_b)
    if ca.empty or cb.empty:
        return [{"nivel": "info", "titulo": "Sin datos suficientes",
                 "detalle": f"Falta el snapshot de {sem_a} o {sem_b}."}]

    a = ca.set_index("estado")["capital"]
    b = cb.set_index("estado")["capital"]
    delta = (b.reindex(a.index.union(b.index)).fillna(0)
             - a.reindex(a.index.union(b.index)).fillna(0))

    # 1) Titular: capital en exceso
    exc_a = a.reindex(api.ESTADOS_EXCESO).fillna(0).sum()
    exc_b = b.reindex(api.ESTADOS_EXCESO).fillna(0).sum()
    d_exc = exc_b - exc_a
    pct = (d_exc / exc_a * 100) if exc_a else 0
    if abs(d_exc) >= UMBRAL_SOLES:
        out.append({
            "nivel": "positivo" if d_exc < 0 else "atencion",
            "titulo": f"Capital en exceso: S/ {exc_a/1e6:,.2f}M → S/ {exc_b/1e6:,.2f}M ({pct:+.1f}%)",
            "detalle": ("El capital problemático está drenando — revisar abajo si es mejora "
                        "real o migración entre estados malos." if d_exc < 0 else
                        "El capital problemático creció — el desglose de abajo dice dónde y por qué."),
        })

    # 2) Marca que impulsa cada movimiento material
    ma = _capital_por_estado_marca(sem_a)
    mb = _capital_por_estado_marca(sem_b)
    driver = {}
    if not ma.empty and not mb.empty:
        mm = mb.merge(ma, on=["estado", "marca"], how="outer",
                      suffixes=("_b", "_a")).fillna(0)
        mm["d"] = mm["stock_valor_costo_b"] - mm["stock_valor_costo_a"]
        for est, g in mm.groupby("estado"):
            g = g.reindex(g["d"].abs().sort_values(ascending=False).index)
            if len(g) and abs(g.iloc[0]["d"]) >= UMBRAL_SOLES * 0.5:
                driver[est] = (g.iloc[0]["marca"], g.iloc[0]["d"])

    # 3) Conclusión por estado con movimiento material
    for est, d in delta.reindex(delta.abs().sort_values(ascending=False).index).items():
        if abs(d) < UMBRAL_SOLES:
            continue
        lectura = (_LECTURA_SUBIDA if d > 0 else _LECTURA_BAJADA).get(est)
        if not lectura:
            continue
        drv = driver.get(est)
        drv_txt = (f" Impulsado por {drv[0]} (S/ {drv[1]/1e6:+.2f}M)."
                   if drv and (drv[1] > 0) == (d > 0) else "")
        nivel = ("critico" if d > 0 and est in ("MUERTO", "LIQUIDAR")
                 else "atencion" if d > 0 else "positivo")
        out.append({"nivel": nivel,
                    "titulo": f"{est}: S/ {d/1e6:+.2f}M",
                    "detalle": lectura + drv_txt})

    # 4) Migraciones entre estados (¿mejora real o deterioro encubierto?)
    try:
        cambios = api.detect_state_changes(sem_a, sem_b)
    except Exception:
        cambios = pd.DataFrame()
    if not cambios.empty:
        snap_b = load_snapshot(sem_b)[["sku", "stock_valor_costo"]].drop_duplicates("sku")
        fl = cambios.merge(snap_b, on="sku", how="left").fillna({"stock_valor_costo": 0})
        flujos = (fl.groupby(["estado_a", "estado_b"])["stock_valor_costo"]
                    .sum().sort_values(ascending=False))
        for (ea, eb), cap in flujos.items():
            if cap < UMBRAL_SOLES:
                continue
            if (ea, eb) in _LECTURA_FLUJOS:
                out.append({"nivel": "atencion", "titulo": f"Migración {ea} → {eb}",
                            "detalle": _LECTURA_FLUJOS[(ea, eb)].format(cap=cap)})
            elif ea in ("MUERTO", "DORMIDO", "LIQUIDAR") and eb == "NUEVO SIN VENTA":
                out.append({"nivel": "info", "titulo": f"Relanzamiento {ea} → {eb}",
                            "detalle": (f"S/ {cap:,.0f} reapareció como producto nuevo "
                                        "(recodificación de temporada) — no es mejora "
                                        "comercial ni deterioro; monitorear su tracción.")})
            elif ea in ("MUERTO", "DORMIDO", "LIQUIDAR") and eb in _ESTADOS_VIVOS:
                out.append({"nivel": "positivo", "titulo": f"Reactivación {ea} → {eb}",
                            "detalle": f"S/ {cap:,.0f} volvió a tener venta — mejora REAL, no maquillaje."})

    # 5) Cruce quiebre: "mejora" que en realidad es sobre-venta sin reposición
    d_quiebre = float(delta.get("QUIEBRE", 0) or 0) + float(delta.get("PRE-QUIEBRE", 0) or 0)
    if d_exc < -UMBRAL_SOLES and d_quiebre > UMBRAL_SOLES:
        out.append({"nivel": "atencion", "titulo": "Ojo con leer la mejora completa",
                    "detalle": (f"El exceso bajó S/ {abs(d_exc)/1e6:.2f}M pero el capital en "
                                f"quiebre subió S/ {d_quiebre/1e6:.2f}M — parte de la 'mejora' "
                                "puede ser venta sin reposición, no gestión de exceso.")})

    # 6) Atribución: ¿hay acciones registradas en la semana?
    if acciones_df is not None and not acciones_df.empty:
        acc = acciones_df[acciones_df["semana_iso"].isin([sem_a, sem_b])]
        if len(acc):
            n_capi = int((acc["origen"] == "Sugerida por Capi").sum())
            out.append({"nivel": "info", "titulo": f"{len(acc)} acciones registradas en el período",
                        "detalle": (f"{n_capi} sugeridas por Capi. El delta de arriba tiene "
                                    "respaldo de gestión — citable ante gerencia.")})
        else:
            out.append({"nivel": "info", "titulo": "Sin acciones registradas en el período",
                        "detalle": ("El delta aún no es atribuible: registra las acciones que "
                                    "ejecutaste esta semana para poder defenderlo como resultado de gestión.")})
    return out


# ─────────────────────────────────────────────────────────────
#  MIGRACIONES ENTRE ESTADOS — medición semana a semana
#  (pedido Franco 2026-08-24: dónde están las oportunidades)
# ─────────────────────────────────────────────────────────────

# Escala de "salud de rotación": subir = el capital mejoró de estado.
# QUIEBRE puntúa 4 (venta fuerte pero riesgo): ÓPTIMO→QUIEBRE es deterioro,
# MUERTO→QUIEBRE es mejora (volvió a vender con fuerza).
SALUD_ESTADO = {
    "MUERTO": 0, "LIQUIDAR": 1, "DORMIDO": 2, "ESTANCADO": 3,
    "NUEVO SIN VENTA": 3, "SOBRESTOCK": 4, "QUIEBRE": 4,
    "PRE-QUIEBRE": 5, "ALTO": 5, "ÓPTIMO": 7,
}


def matriz_migraciones(sem_a: str, sem_b: str) -> pd.DataFrame:
    """Flujos de capital entre estados de sem_a → sem_b.

    Columnas: estado_a, estado_b, n_skus, capital (valorizado en sem_b),
    clase ('mejora' | 'deterioro' | 'lateral').
    """
    try:
        cambios = api.detect_state_changes(sem_a, sem_b)
    except Exception:
        return pd.DataFrame()
    if cambios is None or cambios.empty:
        return pd.DataFrame()
    snap_b = load_snapshot(sem_b)[["sku", "stock_valor_costo"]].drop_duplicates("sku")
    fl = cambios.merge(snap_b, on="sku", how="left").fillna({"stock_valor_costo": 0})
    out = (fl.groupby(["estado_a", "estado_b"], as_index=False)
             .agg(n_skus=("sku", "nunique"), capital=("stock_valor_costo", "sum")))
    _sa = out["estado_a"].map(SALUD_ESTADO).fillna(3)
    _sb = out["estado_b"].map(SALUD_ESTADO).fillna(3)
    out["clase"] = np.where(_sb > _sa, "mejora",
                            np.where(_sb < _sa, "deterioro", "lateral"))
    # Auditoría 2026-08-24: DORMIDO/MUERTO/LIQUIDAR → NUEVO SIN VENTA implica
    # edad reseteada (SKU recodificado/relanzado de temporada) — no es mejora
    # comercial ni deterioro; se clasifica aparte para no inflar los KPIs.
    _relanz = (out["estado_b"] == "NUEVO SIN VENTA") & \
              out["estado_a"].isin(["DORMIDO", "MUERTO", "LIQUIDAR"])
    out.loc[_relanz, "clase"] = "relanzamiento"
    return out.sort_values("capital", ascending=False).reset_index(drop=True)


def detalle_migracion(sem_a: str, sem_b: str, estado_a: str, estado_b: str,
                      top_n: int = 25) -> pd.DataFrame:
    """Top SKUs de un flujo específico (para accionar la oportunidad)."""
    try:
        cambios = api.detect_state_changes(sem_a, sem_b)
    except Exception:
        return pd.DataFrame()
    if cambios is None or cambios.empty:
        return pd.DataFrame()
    fl = cambios[(cambios["estado_a"] == estado_a) & (cambios["estado_b"] == estado_b)]
    if fl.empty:
        return pd.DataFrame()
    cols = ["sku", "descripcion", "marca", "stock_valor_costo", "stock_total",
            "cobertura_sem", "edad_semanas"]
    snap_b = load_snapshot(sem_b)
    snap_b = snap_b[[c for c in cols if c in snap_b.columns]].drop_duplicates("sku")
    det = fl[["sku", "marca"]].merge(snap_b.drop(columns=["marca"], errors="ignore"),
                                     on="sku", how="left")
    return det.sort_values("stock_valor_costo", ascending=False).head(top_n).reset_index(drop=True)


def serie_migraciones() -> pd.DataFrame:
    """Para cada par de semanas CONSECUTIVAS disponibles: capital que mejoró,
    que se deterioró y neto. Alimenta el gráfico WoW de migraciones."""
    from snapshots_engine.storage import list_available_weeks
    weeks = list_available_weeks()
    rows = []
    for a, b in zip(weeks[:-1], weeks[1:]):
        m = matriz_migraciones(a, b)
        if m.empty:
            continue
        mej = float(m.loc[m["clase"] == "mejora", "capital"].sum())
        det = float(m.loc[m["clase"] == "deterioro", "capital"].sum())
        rows.append({"desde": a, "hasta": b, "par": f"{a}→{b}",
                     "capital_mejora": mej, "capital_deterioro": det,
                     "neto": mej - det})
    return pd.DataFrame(rows)


def etiqueta_semana(semana: str) -> str:
    """'2026-33' → '2026-33 · cierre 16.08' (la fecha nadie la discute;
    el número ISO puede no coincidir con la semana comercial Ripley)."""
    import json, os
    _idx = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "snapshots", "snapshots_index.json")
    try:
        for r in json.load(open(_idx)):
            if r.get("semana_iso") == semana:
                f = str(r.get("fecha_cierre", ""))[5:]  # MM-DD
                if f:
                    return f"{semana} · cierre {f[3:5]}.{f[0:2]}"
    except Exception:
        pass
    return semana
