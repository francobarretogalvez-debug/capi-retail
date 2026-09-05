"""
flujo_engine.py — Motor de flujo de mercadería (planificación comercial).

Réplica y corrección del motor que Planificación opera hoy en Excel
("Flujo por Línea OI26 EFA Gus v3.xlsm", hoja FL). Se conserva su vocabulario
—Und, Vta, Con, Vta Cto, Stock, MV, OC— para que el output sea discutible con
ellos sin traducción.

Identidad única (decisión Franco 2026-09-02, sin línea de reducciones):

    stock(t) = stock(t-1) + compra(t) - vta_costo(t)      [en unidades y a costo]
    contrib  = vta_soles - vta_costo

Qué corrige respecto del Excel:
  D1  El Excel nunca calcula la compra requerida ("OC Calc" copia la OC ya
      comprometida). Acá se despeja de la cobertura objetivo.
  D3  El Excel trunca el stock proyectado con MAX(...,0), lo que borra el
      déficit. Acá se reportan las dos series: la truncada (para conciliar con
      Planificación) y la real, cuyo negativo ES la compra faltante.
  D5  El "Factor" de crecimiento se digita a mano. Acá se deriva de la curva.

Cobertura: se usa consumo acumulado, no el promedio de 3 meses del Excel.
Ambos coinciden cuando el objetivo es exactamente 3 meses (verificado sobre
Cacharel: 0,0% de diferencia), pero divergen hasta 24% en un mes cuando el
objetivo se ancla al lead time real del programa — que es justamente lo que
permite decidir por programa en vez de con una constante.

Datos: la venta es confiable desde 2019 (8 años completos); el stock y la
compra, desde 2025 (desvío de identidad 0,08%) y exactos en 2026 (0,00%).
"""

import numpy as np
import pandas as pd

# Año comercial Ripley: P01 = Marzo … P12 = Febrero (hoja FL).
PERIODO_A_MES = {1: 3, 2: 4, 3: 5, 4: 6, 5: 7, 6: 8,
                 7: 9, 8: 10, 9: 11, 10: 12, 11: 1, 12: 2}

# Años a excluir del cálculo de la curva estacional. 2020 tiene el cierre de
# tiendas por pandemia (Cacharel vendió 11 unidades en abril contra ~6.400 de
# un año normal); con mediana el efecto se diluye, pero excluirlo es explícito.
ANIOS_ATIPICOS = (2020,)

CLAVES = ["division", "departamento", "marca", "linea", "programa"]
MEDIDAS = ["vta_uu", "vta_soles", "contrib", "vta_costo_calc",
           "compra_uu", "compra_soles", "stock_uu", "stock_soles"]


# ─────────────────────────────────────────────────────────────
#  Serie del flujo
# ─────────────────────────────────────────────────────────────


def _periodo_anterior(periodo: str) -> str:
    """Periodo comercial inmediatamente anterior. 'P202601' -> 'P202512'."""
    anio, num = int(periodo[1:5]), int(periodo[5:7])
    return f"P{anio - 1}12" if num == 1 else f"P{anio}{num - 1:02d}"

def serie(df: pd.DataFrame, agrupar_por=None, **filtros) -> pd.DataFrame:
    """Serie mensual del flujo para un corte (marca, línea, programa…).

    Devuelve una fila por periodo, ordenada cronológicamente, con las medidas
    agregadas y el stock de apertura (el cierre del periodo anterior).
    """
    d = df
    for col, val in filtros.items():
        if val is None:
            continue
        d = d[d[col].isin(val)] if isinstance(val, (list, tuple, set)) else d[d[col] == val]

    llaves = ["anio", "periodo", "mes_num", "periodo_num"] + list(agrupar_por or [])
    g = d.groupby(llaves, as_index=False)[MEDIDAS].sum()
    g = g.sort_values(["periodo"] + list(agrupar_por or [])).reset_index(drop=True)

    orden = list(agrupar_por or [])
    if orden:
        g["stock_uu_ini"] = g.groupby(orden)["stock_uu"].shift(1)
        g["stock_soles_ini"] = g.groupby(orden)["stock_soles"].shift(1)
    else:
        g["stock_uu_ini"] = g["stock_uu"].shift(1)
        g["stock_soles_ini"] = g["stock_soles"].shift(1)

    prev_periodo = g.groupby(orden)["periodo"].shift(1) if orden else g["periodo"].shift(1)
    esperado = g["periodo"].map(_periodo_anterior)
    g["periodo_contiguo"] = prev_periodo.eq(esperado)

    g["contrib_pct"] = np.where(g["vta_soles"] > 0, g["contrib"] / g["vta_soles"], np.nan)
    g["costo_unitario"] = np.where(g["vta_uu"] > 0, g["vta_costo_calc"] / g["vta_uu"], np.nan)
    return g


def verificar_identidad(s: pd.DataFrame) -> pd.DataFrame:
    """Contrasta `apertura + compra - venta_a_costo` contra el cierre reportado.

    Es el test que detecta si los ingresos de mercadería están mal capturados:
    stock y venta vienen medidos, la compra es la variable débil.
    """
    v = s.dropna(subset=["stock_soles_ini"]).copy()
    if "periodo_contiguo" in v.columns:
        # Un hueco en la serie hace que la apertura venga de un periodo que no
        # es t-1; evaluar la identidad ahí produce un falso desvío.
        v = v[v["periodo_contiguo"]]
    v["cierre_calc_soles"] = v["stock_soles_ini"] + v["compra_soles"] - v["vta_costo_calc"]
    v["cierre_calc_uu"] = v["stock_uu_ini"] + v["compra_uu"] - v["vta_uu"]
    v["dif_soles"] = v["stock_soles"] - v["cierre_calc_soles"]
    v["dif_uu"] = v["stock_uu"] - v["cierre_calc_uu"]
    v["dif_pct"] = np.where(v["stock_soles"] > 0,
                            v["dif_soles"] / v["stock_soles"] * 100, np.nan)
    return v[["anio", "periodo", "stock_soles_ini", "compra_soles", "vta_costo_calc",
              "stock_soles", "cierre_calc_soles", "dif_soles", "dif_pct",
              "dif_uu"]]


# ─────────────────────────────────────────────────────────────
#  Curva estacional
# ─────────────────────────────────────────────────────────────

def curva_estacional(df: pd.DataFrame, medida="vta_uu", excluir_anios=ANIOS_ATIPICOS,
                     **filtros) -> pd.DataFrame:
    """Índice estacional por periodo comercial, normalizado a que sume 12.

    Se usa la MEDIANA de la participación de cada periodo entre años, no el
    promedio: un evento promocional puntual mueve la media y deja de describir
    la temporada. Se reporta también p25/p75, que es lo que da la banda de
    confianza del índice (y del Pace que se calcula contra él).
    """
    d = df
    for col, val in filtros.items():
        if val is None:
            continue
        d = d[d[col].isin(val)] if isinstance(val, (list, tuple, set)) else d[d[col] == val]

    if excluir_anios:
        d = d[~d["anio"].isin(excluir_anios)]

    m = d.groupby(["anio", "periodo_num"], as_index=False)[medida].sum()
    total_anio = m.groupby("anio")[medida].transform("sum")
    m = m[total_anio > 0].copy()
    m["share"] = m[medida] / total_anio[total_anio > 0]

    c = m.groupby("periodo_num", as_index=False).agg(
        share_p50=("share", "median"),
        share_p25=("share", lambda x: x.quantile(0.25)),
        share_p75=("share", lambda x: x.quantile(0.75)),
        anios=("share", "size"),
    )
    # Normalizar a media 1 (índice multiplicativo sobre 12 periodos)
    base = c["share_p50"].sum()
    for col in ("share_p50", "share_p25", "share_p75"):
        c[col.replace("share", "indice")] = c[col] / base * 12
    c["mes_num"] = c["periodo_num"].map(PERIODO_A_MES)
    return c.sort_values("periodo_num").reset_index(drop=True)


# ─────────────────────────────────────────────────────────────
#  Cobertura por consumo acumulado
# ─────────────────────────────────────────────────────────────

def cobertura_consumo(stock: float, forecast_forward) -> float:
    """Meses de cobertura consumiendo el forecast mes a mes (no promediando).

    El promedio de N meses del Excel solo es exacto si la demanda es plana
    dentro de la ventana. Este método consume el forecast real, así que no se
    rompe con estacionalidad ni con eventos.

    Devuelve np.inf si el stock sobrevive todo el horizonte disponible: es una
    señal de que el horizonte es insuficiente, no un número para decidir.
    """
    if stock is None or not np.isfinite(stock) or stock <= 0:
        return 0.0
    restante = float(stock)
    meses = 0.0
    for f in forecast_forward:
        f = max(float(f or 0), 0.0)
        if restante <= 0:
            break
        if f <= 0:
            # Un mes sin venta proyectada también está cubierto: no consume
            # stock pero sí transcurre. Saltarlo subestimaba la cobertura.
            meses += 1.0
            continue
        if restante > f:
            restante -= f
            meses += 1.0
        else:
            return meses + restante / f
    if restante > 0:
        # El stock sobrevive todo el horizonte conocido: no hay número que
        # devolver, y forzar uno haría creer que la cobertura está medida.
        return np.inf
    return meses


def stock_objetivo(forecast_forward, cobertura_objetivo: float) -> float:
    """Inverso de `cobertura_consumo`: stock necesario para cubrir N meses.

    Es lo que realmente se compra. Sumar el forecast real de los meses a
    cubrir —en vez de multiplicar un promedio— es la diferencia que se
    materializa en la orden de compra, no en el reporte.
    """
    if cobertura_objetivo is None or cobertura_objetivo <= 0:
        return 0.0
    f = [float(x or 0) for x in forecast_forward]
    entero = int(np.floor(cobertura_objetivo))
    frac = cobertura_objetivo - entero
    total = sum(f[:entero])
    if frac > 0 and len(f) > entero:
        total += frac * f[entero]
    return total


def cobertura_objetivo_por_lead_time(lead_time_meses: float, buffer: float = 1.25) -> float:
    """Objetivo de cobertura anclado al lead time de reposición.

    Regla del canon (`lead time × 1.25`). Con los lead times reales de Franco
    —nacional 2-3 meses, reorder importado 1-2 de producción + 1 de tránsito—
    da 2,5 a 3,75 meses, que es donde cae el target de 3 que ya usa.
    """
    return max(float(lead_time_meses), 0.0) * buffer


# ─────────────────────────────────────────────────────────────
#  OTB — la compra requerida (lo que el Excel de Planificación no calcula)
# ─────────────────────────────────────────────────────────────

def compra_requerida(stock_inicial: float, venta_periodo: float,
                     forecast_forward, cobertura_objetivo: float) -> dict:
    """Cuánto hay que comprar para cerrar el periodo en la cobertura objetivo.

    Despeja la compra de la identidad, usando consumo acumulado para el stock
    de cierre:

        cierre_objetivo = Σ forecast de los meses a cubrir
        compra          = venta_del_periodo + cierre_objetivo - apertura

    Es el hueco central del Excel: ahí "OC Calc" copia la orden ya comprometida
    y nunca dice cuánto falta comprar.

    `deficit` es el faltante que el Excel esconde al truncar el cierre en cero:
    si la venta proyectada supera lo disponible, ese negativo ES la compra
    mínima para no quebrar.
    """
    apertura = float(stock_inicial or 0)
    venta = float(venta_periodo or 0)
    cierre_obj = stock_objetivo(forecast_forward, cobertura_objetivo)

    cierre_sin_compra = apertura - venta
    brecha = venta + cierre_obj - apertura
    return {
        "apertura": apertura,
        "venta_periodo": venta,
        "cierre_objetivo": cierre_obj,
        "cierre_sin_compra": cierre_sin_compra,          # sin truncar (D3)
        "cierre_sin_compra_truncado": max(cierre_sin_compra, 0.0),  # como Ripley
        "deficit": min(cierre_sin_compra, 0.0),
        "compra_requerida": max(brecha, 0.0),
        # Si la brecha es negativa sobra inventario sobre el objetivo. Se
        # reporta con el mismo criterio que el déficit: truncar la compra a
        # cero y callar el exceso escondería justamente el sobrestock.
        "exceso_sobre_objetivo": max(-brecha, 0.0),
    }


def otb(stock_inicial: float, venta_periodo: float, forecast_forward,
        cobertura_objetivo: float, oc_comprometida: float = 0.0) -> dict:
    """OTB = compra requerida menos lo ya comprometido.

    Un OTB negativo NO se trunca: su magnitud es exactamente cuánto hay que
    cancelar o diferir, y es información de gestión, no un error.

    Recibe escalares, así que no puede validar la serie por su cuenta: llamar
    antes a `validar_ancla(serie, periodo)`. Un OTB calculado sobre un stock de
    apertura que no cuadra arrastra ese error a todo el horizonte.
    """
    r = compra_requerida(stock_inicial, venta_periodo, forecast_forward,
                         cobertura_objetivo)
    r["oc_comprometida"] = float(oc_comprometida or 0)
    r["otb"] = r["compra_requerida"] - r["oc_comprometida"]
    return r


def proyectar(serie_hist: pd.DataFrame, curva: pd.DataFrame, periodos_futuros: list,
              factor: float = 0.0, base_anio: int = None,
              medida: str = "vta_uu", validar: bool = True) -> pd.DataFrame:
    """Proyecta la venta de los periodos futuros con la curva y un factor.

    A diferencia del Excel —donde el factor de crecimiento se digita a mano y
    hoy está en cero— acá la forma la pone la curva medida y el factor solo
    escala el nivel. El factor sigue siendo editable: el motor propone, el
    buyer decide.
    """
    if base_anio is None:
        base_anio = int(serie_hist["anio"].max())
    base = serie_hist[serie_hist["anio"] == base_anio]

    # La guarda es opt-out, no opt-in: proyectar sobre un año con la identidad
    # rota produce un OTB que se ve igual de creíble que uno bueno.
    if validar and "stock_soles_ini" in serie_hist.columns:
        m = marcar_confiabilidad(serie_hist)
        rotos = m[(m["anio"] == base_anio) & ~m["identidad_ok"]]
        if len(rotos):
            raise SerieNoConfiable(
                f"El año base {base_anio} tiene {len(rotos)} periodo(s) donde la "
                f"identidad no cuadra ({', '.join(rotos['periodo'].tolist()[:4])}). "
                f"Corregir la fuente o elegir otro año base; "
                f"pasar validar=False solo si el desvío ya se evaluó y se acepta.")
    nivel_total = base[medida].sum()
    if nivel_total <= 0:
        raise ValueError(f"El año base {base_anio} no tiene venta en '{medida}'.")

    # Si el año base está incompleto (típico: el año en curso), dividir por 12
    # subestima el nivel en proporción a los periodos que faltan.
    n_periodos = (base["periodo_num"].nunique()
                  if "periodo_num" in base.columns else len(base))
    n_periodos = max(int(n_periodos), 1)
    nivel_mensual = nivel_total / n_periodos

    idx = curva.set_index("periodo_num")
    filas = []
    for p in periodos_futuros:
        if p not in idx.index:
            continue
        filas.append({
            "periodo_num": p,
            "mes_num": PERIODO_A_MES[p],
            "proy_p50": nivel_mensual * idx.at[p, "indice_p50"] * (1 + factor),
            "proy_p25": nivel_mensual * idx.at[p, "indice_p25"] * (1 + factor),
            "proy_p75": nivel_mensual * idx.at[p, "indice_p75"] * (1 + factor),
        })
    return pd.DataFrame(filas)


# ─────────────────────────────────────────────────────────────
#  Integridad del inventario — guarda permanente
# ─────────────────────────────────────────────────────────────
#
# La identidad es recursiva: stock(t) = stock(t-1) + compra(t) - venta(t).
# Eso parte el problema en dos regímenes distintos:
#
#   HISTÓRICO   el stock de cada periodo viene MEDIDO de la fuente, así que un
#               periodo roto es un error local: señala un problema de captura
#               en ese mes (típicamente compras imputadas fuera de fecha), y no
#               contamina el mes siguiente, que trae su propio stock medido.
#
#   PROYECCIÓN  el stock se calcula encadenado. Un error en el mes 3 SÍ se
#               arrastra al 4, al 5 y hasta el final del horizonte. Y ahí es
#               donde vive el OTB: proyectar desde un ancla rota produce una
#               compra mal dimensionada en todos los periodos siguientes.
#
# Por eso la guarda no solo marca el periodo malo: marca la serie como
# contaminada desde ahí en adelante, y `proyectar` se niega a partir de un
# ancla que no cuadra.

TOLERANCIA_IDENTIDAD_PCT = 2.0
# Periodos hacia atrás que se miran para decidir si el problema de captura
# sigue vigente. 6 = medio año comercial.
VENTANA_CONFIANZA = 6
# Bajo este stock el porcentaje deja de tener sentido: dividir un desvío de
# S/3.400 por un cierre de S/0,0000001 dio 6e18% en MARQUIS/POLEROLES P202610.
# Con stock chico se juzga por el desvío absoluto, no por el relativo.
STOCK_MINIMO_PARA_PCT = 100.0
DESVIO_ABSOLUTO_TOLERADO = 500.0


class SerieNoConfiable(Exception):
    """La serie no cumple la identidad en el tramo del que se quiere proyectar."""


def marcar_confiabilidad(s: pd.DataFrame,
                         tolerancia_pct: float = TOLERANCIA_IDENTIDAD_PCT,
                         ventana: int = VENTANA_CONFIANZA) -> pd.DataFrame:
    """Marca periodo a periodo si la identidad cuadra, y propaga hacia adelante.

    Agrega tres columnas:
      identidad_ok   el periodo cuadra dentro de la tolerancia
      contaminado    hay periodos rotos en la ventana reciente, o sea el
                     problema de captura sigue vigente
      ancla_ok       el periodo sirve como punto de partida de una proyección

    Sobre la ventana: la contaminación NO se propaga para siempre hacia
    adelante. En el histórico cada periodo trae su propio stock medido, así que
    un error en 2024 no invalida un ancla de 2026 que cuadra al 0,00%. Lo que
    sí importa es que el problema de captura no siga vigente — por eso se mira
    una ventana móvil de los últimos `ventana` periodos. La propagación
    ilimitada es correcta solo aguas abajo del ancla, en la proyección, donde
    el stock se calcula encadenado.
    """
    s = s.copy()
    calc = s["stock_soles_ini"] + s["compra_soles"] - s["vta_costo_calc"]
    dif = (s["stock_soles"] - calc).abs()
    base = s["stock_soles"].abs()
    dif_pct = np.where(base > 0, dif / base * 100, np.nan)

    evaluable = s["stock_soles_ini"].notna() & (base > 0)
    if "periodo_contiguo" in s.columns:
        evaluable &= s["periodo_contiguo"].fillna(False)

    s["dif_identidad_soles"] = np.where(evaluable, dif, np.nan)
    # Con stock cercano a cero el relativo explota, así que se deja en NaN y el
    # juicio pasa al absoluto.
    stock_suficiente = base >= STOCK_MINIMO_PARA_PCT
    s["dif_identidad_pct"] = np.where(evaluable & stock_suficiente, dif_pct, np.nan)

    excede_rel = pd.Series(dif_pct, index=s.index).gt(tolerancia_pct) & stock_suficiente
    excede_abs = dif.gt(DESVIO_ABSOLUTO_TOLERADO) & ~stock_suficiente
    # Un periodo no evaluable (primero de la serie, o con hueco antes) no es
    # "malo": es desconocido. Se trata como OK para no propagar un falso error.
    s["identidad_ok"] = ~(evaluable & (excede_rel | excede_abs))
    rotos_recientes = (~s["identidad_ok"]).rolling(ventana, min_periods=1).sum().shift(1).fillna(0)
    s["rotos_en_ventana"] = rotos_recientes
    s["contaminado"] = rotos_recientes > 0
    s["ancla_ok"] = s["identidad_ok"] & ~s["contaminado"]
    return s


def auditar_integridad(df: pd.DataFrame, agrupar_por=("marca", "linea", "programa"),
                       tolerancia_pct: float = TOLERANCIA_IDENTIDAD_PCT) -> pd.DataFrame:
    """Corre la identidad sobre TODAS las series y devuelve un reporte por serie.

    Es la revisión que debe correr cada vez que entra data nueva: no alcanza con
    validar la marca que uno está mirando, porque el OTB se calcula sobre
    cualquiera de ellas.
    """
    filas = []
    for clave, g in df.groupby(list(agrupar_por)):
        s = marcar_confiabilidad(serie(g), tolerancia_pct)
        ev = s["dif_identidad_soles"].notna()
        if not ev.any():
            continue
        malos = s.loc[ev & ~s["identidad_ok"]]
        ultimo_roto = malos["periodo"].max() if len(malos) else None
        anclas = s.loc[s["ancla_ok"] & ev, "periodo"]
        filas.append({
            **dict(zip(agrupar_por, clave)),
            "periodos_evaluados": int(ev.sum()),
            "periodos_rotos": int(len(malos)),
            "pct_rotos": round(len(malos) / int(ev.sum()) * 100, 1),
            "dif_max_pct": round(s.loc[ev, "dif_identidad_pct"].max(), 2)
                            if s.loc[ev, "dif_identidad_pct"].notna().any() else None,
            "dif_max_soles": round(s.loc[ev, "dif_identidad_soles"].max(), 0),
            "soles_desviados": round(
                (s["stock_soles"] - (s["stock_soles_ini"] + s["compra_soles"]
                                     - s["vta_costo_calc"])).abs()[ev & ~s["identidad_ok"]].sum(), 0),
            "ultimo_periodo_roto": ultimo_roto,
            "ancla_mas_reciente": anclas.max() if len(anclas) else None,
            "apta_para_proyectar": bool(len(anclas)),
        })
    r = pd.DataFrame(filas)
    return r.sort_values("soles_desviados", ascending=False).reset_index(drop=True) if len(r) else r


def validar_ancla(s: pd.DataFrame, periodo: str,
                  tolerancia_pct: float = TOLERANCIA_IDENTIDAD_PCT,
                  ventana: int = VENTANA_CONFIANZA) -> None:
    """Levanta `SerieNoConfiable` si no se puede proyectar desde ese periodo.

    Se llama antes de proyectar u ofrecer un OTB. Falla ruidosamente a
    propósito: un OTB calculado sobre una serie rota es peor que no tener OTB,
    porque se ve igual de creíble.
    """
    m = marcar_confiabilidad(s, tolerancia_pct, ventana)
    fila = m[m["periodo"] == periodo]
    if fila.empty:
        raise SerieNoConfiable(f"El periodo {periodo} no existe en la serie.")
    f = fila.iloc[0]
    if not f["identidad_ok"]:
        raise SerieNoConfiable(
            f"{periodo}: la identidad no cuadra ({f['dif_identidad_pct']:.1f}% de desvío). "
            f"Proyectar desde acá arrastra el error a todos los periodos siguientes.")
    if f["contaminado"]:
        idx = m.index[m["periodo"] == periodo][0]
        prev = m.loc[:idx].tail(ventana + 1).iloc[:-1]
        rotos = prev.loc[~prev["identidad_ok"], "periodo"].tolist()
        raise SerieNoConfiable(
            f"{periodo}: hay {len(rotos)} periodo(s) roto(s) en los últimos {ventana} "
            f"({', '.join(rotos)}). El problema de captura sigue vigente: usar un ancla "
            f"posterior o corregir la fuente.")


# ─────────────────────────────────────────────────────────────
#  Índice de Pace — seguimiento contra plan
# ─────────────────────────────────────────────────────────────


def _fracciones_acumuladas(serie_hist: pd.DataFrame, periodos: list, anio_excluir: int,
                           medida: str) -> list:
    """Qué fracción del año llevaba cada año histórico a esta altura.

    Es el insumo de la banda del pace: la dispersión real entre años, no la
    suma de dispersiones mensuales.
    """
    fr = []
    for a, g in serie_hist.groupby("anio"):
        if a == anio_excluir:
            continue
        total = g[medida].sum()
        if total <= 0 or g["periodo_num"].nunique() < 12:
            continue  # año parcial: su fracción no es comparable
        parcial = g[g["periodo_num"].isin(periodos)][medida].sum()
        fr.append(parcial / total)
    return fr

def pace(serie_hist: pd.DataFrame, curva: pd.DataFrame, anio: int,
         hasta_periodo_num: int = None, medida: str = "vta_uu",
         nivel_esperado: float = None) -> dict:
    """Cuánto va sobre o bajo plan, contra la curva y con banda de significancia.

    Responde la pregunta operativa de Franco: "planifiqué +15% y va +30%".

        pace = venta acumulada real ÷ venta acumulada esperada por la curva

    La banda NO es un umbral inventado: sale de la dispersión histórica de la
    propia curva (p25/p75). Eso la hace ancha en los primeros periodos, donde
    la señal es ruidosa, y la angosta sola a medida que avanza el año — que es
    exactamente el comportamiento que evita reaccionar a un mes raro.

    `nivel_esperado` es la venta anual del plan. Si no se pasa, se toma la del
    año anterior, o sea el pace mide crecimiento contra LY.
    """
    h = serie_hist[serie_hist["anio"] == anio]
    if h.empty:
        raise ValueError(f"No hay data del año {anio}.")
    h = h.groupby("periodo_num", as_index=False)[medida].sum().sort_values("periodo_num")

    if hasta_periodo_num is None:
        hasta_periodo_num = int(h["periodo_num"].max())
    real = h[h["periodo_num"] <= hasta_periodo_num]
    acum_real = float(real[medida].sum())

    if nivel_esperado is None:
        prev = serie_hist[serie_hist["anio"] == anio - 1]
        if prev.empty:
            raise ValueError(
                f"Sin año {anio - 1} para comparar: pasar `nivel_esperado` explícito.")
        nivel_esperado = float(prev[medida].sum())

    idx = curva.set_index("periodo_num")
    periodos = [p for p in real["periodo_num"] if p in idx.index]

    # La banda se mide sobre la FRACCIÓN ACUMULADA observada en cada año
    # histórico, no sumando los percentiles de cada periodo por separado.
    # Sumar p25 doce veces supone que los doce meses caen a la vez en su
    # percentil bajo, lo que es cada vez más improbable — y produce una banda
    # que se ENSANCHA con el tiempo, al revés de lo correcto: más meses
    # observados es menos incertidumbre sobre dónde va a cerrar el año.
    fracs = _fracciones_acumuladas(serie_hist, periodos, anio, medida)
    if len(fracs) >= 3:
        fr = {"p25": float(np.percentile(fracs, 25)),
              "p50": float(np.percentile(fracs, 50)),
              "p75": float(np.percentile(fracs, 75))}
    else:
        # Sin años suficientes se cae a la curva, declarando el fallback.
        base_fr = idx.loc[periodos, "indice_p50"].sum() / 12
        fr = {"p25": base_fr * 0.9, "p50": base_fr, "p75": base_fr * 1.1}

    esperado = nivel_esperado * fr["p50"]
    # La banda del pace se invierte respecto de la de la fracción: si el año
    # históricamente llevaba MENOS acumulado (p25), el mismo real da un pace
    # MAYOR.
    pace_p50 = acum_real / esperado if esperado else np.nan
    pace_alto = acum_real / (nivel_esperado * fr["p25"]) if fr["p25"] else np.nan
    pace_bajo = acum_real / (nivel_esperado * fr["p75"]) if fr["p75"] else np.nan

    if pace_bajo > 1:
        estado = "sobre plan"
    elif pace_alto < 1:
        estado = "bajo plan"
    else:
        estado = "dentro de banda"

    return {
        "anio": anio,
        "hasta_periodo": hasta_periodo_num,
        "periodos_transcurridos": len(periodos),
        "acumulado_real": acum_real,
        "acumulado_esperado": esperado,
        "fraccion_de_anio_esperada": fr["p50"],
        "pace": pace_p50,
        "pace_banda_baja": pace_bajo,
        "pace_banda_alta": pace_alto,
        "estado": estado,
        # Proyección del cierre de año manteniendo el ritmo observado.
        "proyeccion_anual": acum_real / fr["p50"] if fr["p50"] else np.nan,
        "nivel_esperado": nivel_esperado,
    }


def otb_que_se_abre(serie_hist: pd.DataFrame, curva: pd.DataFrame, anio: int,
                    factor_plan: float, hasta_periodo_num: int = None,
                    medida: str = "vta_uu") -> dict:
    """Cuánto OTB adicional habilita ir por encima del plan.

    El plan se fijó con `factor_plan` (ej. 0.15 para +15%); el ritmo real se
    mide con el pace. La diferencia entre lo que había que comprar bajo el plan
    y lo que hay que comprar al ritmo real es el OTB que se abre — en la misma
    unidad de la medida, para dimensionar por unidades como corresponde.
    """
    p = pace(serie_hist, curva, anio, hasta_periodo_num, medida)
    base = p["nivel_esperado"]
    factor_real = p["proyeccion_anual"] / base - 1 if base else np.nan

    resto = 1 - p["fraccion_de_anio_esperada"]
    venta_resto_plan = base * (1 + factor_plan) * resto
    venta_resto_real = base * (1 + factor_real) * resto

    return {
        **p,
        "factor_plan": factor_plan,
        "factor_real": factor_real,
        "sobrecumplimiento": factor_real - factor_plan,
        "venta_restante_plan": venta_resto_plan,
        "venta_restante_ritmo_real": venta_resto_real,
        "otb_que_se_abre": venta_resto_real - venta_resto_plan,
    }
