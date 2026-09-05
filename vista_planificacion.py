"""
vista_planificacion.py — Vista "📊 Planificación" de Capi.

Módulo aislado a propósito: `app_streamlit.py` es un monolito donde cada
`elif nav_page` es una isla de scope, y además lleva WIP de otras sesiones.
Toda la lógica de pantalla vive acá; el app solo registra el nav y llama a
`render(st)`.

Qué muestra, en el orden en que Franco decide:
  1. Integridad     — semáforo de la identidad de inventario (guarda permanente)
  2. Flujo          — la tabla de Planificación con su mismo vocabulario
  3. Pace           — voy sobre o bajo plan, con banda medida
  4. OTB            — cuánto se abre o se cierra respecto del plan
  5. Cobertura      — consumo acumulado vs el promedio del Excel
"""

import json
import os
import warnings

import numpy as np
import pandas as pd

import flujo_engine as fe
import flujo_ingesta as fi

MESES = {11: "Ene", 12: "Feb", 1: "Mar", 2: "Abr", 3: "May", 4: "Jun",
         5: "Jul", 6: "Ago", 7: "Set", 8: "Oct", 9: "Nov", 10: "Dic"}

_CFG_LT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "config_lead_times_compra.json")


def _lead_time_default() -> float:
    try:
        with open(_CFG_LT, encoding="utf-8") as f:
            return float(json.load(f).get("_default_meses", 2.5))
    except (OSError, ValueError):
        return 2.5


def _kpi(st, label, valor, sub="", color=""):
    st.markdown(
        f'<div class="kpi-card {color}"><div class="kpi-label">{label}</div>'
        f'<div class="kpi-val">{valor}</div><div class="kpi-sub">{sub}</div></div>',
        unsafe_allow_html=True)


def _cargar(st, path):
    """Carga con caché de sesión; la validación de integridad corre sola."""
    key = f"_plan_df::{path}::{os.path.getmtime(path)}"
    if key not in st.session_state:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            df = fi.cargar_bd_fl(path)
            avisos = [str(x.message) for x in w if "Integridad" in str(x.message)]
        st.session_state[key] = (df, avisos)
    return st.session_state[key]


def render(st, xlsm_path: str = None):
    st.markdown('<div class="section-header"><h3>📊 Planificación</h3>'
                '<span class="live-badge">FLUJO · PACE · OTB</span></div>',
                unsafe_allow_html=True)

    # ── Fuente ──
    up = st.file_uploader("Archivo de flujo de Planificación (.xlsm)", type=["xlsm", "xlsx"],
                          key="plan_upload",
                          help="El 'Flujo por Línea' que arma Planificación. Se lee la hoja "
                               "'BD - FL Consolidado' y 'OC's Adic'.")
    if up is not None:
        tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data2", "_flujo_planificacion.xlsm")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(up.getbuffer())
        xlsm_path = tmp
    elif xlsm_path is None:
        cand = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data2", "_flujo_planificacion.xlsm")
        xlsm_path = cand if os.path.exists(cand) else None

    if not xlsm_path or not os.path.exists(xlsm_path):
        st.info("Sube el archivo de flujo de Planificación para empezar.")
        return

    df, avisos = _cargar(st, xlsm_path)

    # ── Selector ──
    c1, c2, c3 = st.columns([1.2, 1.2, 1])
    marcas = sorted(df["marca"].unique())
    with c1:
        marca = st.selectbox("Marca", marcas,
                             index=marcas.index("CACHAREL") if "CACHAREL" in marcas else 0,
                             key="plan_marca")
    lineas = ["(Todas)"] + sorted(df[df["marca"] == marca]["linea"].unique())
    with c2:
        linea = st.selectbox("Línea", lineas, key="plan_linea")
    anios = sorted(df["anio"].dropna().unique())
    with c3:
        anio = st.selectbox("Año", anios, index=len(anios) - 1, key="plan_anio")

    filtros = {"marca": marca}
    if linea != "(Todas)":
        filtros["linea"] = linea
    serie = fe.serie(df, **filtros)
    curva = fe.curva_estacional(df, **filtros)
    marcado = fe.marcar_confiabilidad(serie)

    # ── 1. Integridad ──
    st.markdown("#### 🔒 Integridad del inventario")
    ev = marcado[marcado["dif_identidad_soles"].notna() & (marcado["anio"] == anio)]
    rotos = ev[~ev["identidad_ok"]]
    k1, k2, k3 = st.columns(3)
    with k1:
        _kpi(st, "Periodos evaluados", f"{len(ev)}", f"año {anio}")
    with k2:
        _kpi(st, "No cuadran", f"{len(rotos)}",
             "stock ini + compra − vta costo ≠ stock fin",
             "red" if len(rotos) else "green")
    with k3:
        ancla = marcado[marcado["ancla_ok"] & (marcado["anio"] == anio)]
        _kpi(st, "Ancla para proyectar",
             ancla["periodo"].max() if len(ancla) else "—",
             "último periodo confiable", "green" if len(ancla) else "red")
    if len(rotos):
        with st.expander(f"Ver los {len(rotos)} periodos que no cuadran"):
            st.dataframe(rotos[["periodo", "stock_soles_ini", "compra_soles",
                                "vta_costo_calc", "stock_soles", "dif_identidad_soles",
                                "dif_identidad_pct"]].round(0), hide_index=True,
                         use_container_width=True)
    if avisos:
        st.caption("⚠️ " + avisos[0][:300])

    # ── 2. Flujo ──
    st.markdown("#### 📋 Flujo mensual")
    st.caption("Mismo vocabulario que Planificación. `Vta Cto` = venta a costo, calculada "
               "desde la contribución oficial. `MV` = meses de venta, por consumo acumulado.")
    fl = marcado[marcado["anio"] == anio].copy()
    fl["Mes"] = fl["periodo_num"].map(MESES)
    vc = fl["vta_costo_calc"].tolist()
    fl["MV"] = [fe.cobertura_consumo(fl["stock_soles"].iloc[i], vc[i + 1:])
                if i + 1 < len(vc) else np.nan for i in range(len(fl))]
    tabla = pd.DataFrame({
        "Periodo": fl["periodo"], "Mes": fl["Mes"],
        "Und": fl["vta_uu"].round(0), "Vta S/": fl["vta_soles"].round(0),
        "Con S/": fl["contrib"].round(0), "GM%": (fl["contrib_pct"] * 100).round(1),
        "Vta Cto S/": fl["vta_costo_calc"].round(0),
        "Compra uu": fl["compra_uu"].round(0), "Compra S/": fl["compra_soles"].round(0),
        "Stock uu": fl["stock_uu"].round(0), "Stock S/": fl["stock_soles"].round(0),
        "MV": fl["MV"].round(2), "✓": fl["identidad_ok"].map({True: "", False: "✗"}),
    })
    st.dataframe(tabla, hide_index=True, use_container_width=True)

    # ── 3. Pace ──
    st.markdown("#### 🎯 Pace — ¿voy sobre o bajo plan?")
    completos = sorted(fl["periodo_num"].unique())
    if not completos:
        st.info("Sin periodos para el año elegido.")
        return
    c1, c2 = st.columns([1, 2])
    with c1:
        hasta = st.select_slider("Hasta el periodo", options=completos,
                                 value=completos[-1],
                                 format_func=lambda p: f"P{p:02d} {MESES[p]}", key="plan_hasta")
        factor_plan = st.number_input("Crecimiento planificado vs LY (%)",
                                      value=15.0, step=1.0, key="plan_factor") / 100
    try:
        r = fe.otb_que_se_abre(serie, curva, int(anio), factor_plan, hasta_periodo_num=hasta)
    except ValueError as e:
        st.warning(str(e))
        return
    with c2:
        color = {"sobre plan": "green", "bajo plan": "red"}.get(r["estado"], "")
        p1, p2, p3 = st.columns(3)
        with p1:
            _kpi(st, "Pace", f"{r['pace']:.3f}",
                 f"banda {r['pace_banda_baja']:.2f}–{r['pace_banda_alta']:.2f}", color)
        with p2:
            _kpi(st, "Estado", r["estado"].upper(),
                 f"{r['periodos_transcurridos']} periodos observados", color)
        with p3:
            _kpi(st, "Ritmo real vs LY", f"{r['factor_real']:+.1%}",
                 f"plan {r['factor_plan']:+.1%}", color)
    st.caption("La banda no es un umbral fijo: sale de la dispersión histórica de la "
               "fracción acumulada del año. Se estrecha sola al avanzar.")

    # ── 4. OTB ──
    st.markdown("#### 💰 OTB que se abre")
    o1, o2, o3 = st.columns(3)
    with o1:
        _kpi(st, "Sobrecumplimiento", f"{r['sobrecumplimiento']:+.1%}", "ritmo real − plan")
    with o2:
        _kpi(st, "Venta restante", f"{r['venta_restante_ritmo_real']:,.0f} uds",
             f"plan: {r['venta_restante_plan']:,.0f} uds")
    with o3:
        signo = "green" if r["otb_que_se_abre"] > 0 else "red" if r["otb_que_se_abre"] < 0 else ""
        _kpi(st, "OTB que se abre", f"{r['otb_que_se_abre']:+,.0f} uds",
             "negativo = cancelar o diferir", signo)

    # ── 5. Cobertura ──
    st.markdown("#### 📦 Cobertura: consumo acumulado vs promedio de 3 meses")
    lt = st.number_input("Lead time de compra (meses)", value=_lead_time_default(),
                         step=0.5, key="plan_lt")
    objetivo = fe.cobertura_objetivo_por_lead_time(lt)
    st.caption(f"Objetivo de cobertura = lead time × 1.25 = **{objetivo:.2f} meses**. "
               f"El promedio del Excel y el consumo coinciden solo si el objetivo es 3.")
    filas = []
    stk = fl["stock_soles"].tolist()
    for i in range(len(fl) - 1):
        fwd = vc[i + 1:]
        cons = fe.cobertura_consumo(stk[i], fwd)
        prom = stk[i] / np.mean(vc[i + 1:i + 4]) if len(vc[i + 1:i + 4]) and np.mean(vc[i + 1:i + 4]) > 0 else np.nan
        filas.append({"Periodo": fl["periodo"].iloc[i], "Mes": fl["Mes"].iloc[i],
                      "Stock S/": round(stk[i]),
                      "MV promedio (Excel)": f"{prom:.2f}" if np.isfinite(prom) else "—",
                      # Todo texto: mezclar float y str en una columna rompe Arrow.
                      "MV consumo": f"{cons:.2f}" if np.isfinite(cons) else "≥ horizonte",
                      "Dif": f"{(prom / cons - 1) * 100:+.0f}%" if np.isfinite(cons) and cons > 0 else "—"})
    st.dataframe(pd.DataFrame(filas), hide_index=True, use_container_width=True)
