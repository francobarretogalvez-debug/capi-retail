"""
🧵 Talla y Color (piloto) — vista aislada, S9 ingesta (2026-09-05).

Sube el reporte "Stock para Reposición Modelo Variación" (.xlsb) y muestra, por marca y
modelo/programa, el peso de cada talla y color en la VENTA vs en el STOCK, la cobertura por
talla, las opciones con curva rota (regla de tallas mayores) y el diagnóstico por tienda
("faltan tallas" vs "revisar exhibición"). Es la base del motor TT (lógica Pima): primero
ver el mix, después automatizar la reposición.

Scoping: todo vive acá; app_streamlit.py solo delega con render(st).
"""
from __future__ import annotations

import io
import os
import tempfile

import pandas as pd

import stock_variacion as sv


def _cargar(st, uploaded):
    key = f"_tc_{uploaded.name}_{uploaded.size}"
    if key in st.session_state:
        return st.session_state[key]
    suf = ".xlsb" if uploaded.name.lower().endswith(".xlsb") else ".xlsx"
    with tempfile.NamedTemporaryFile(suffix=suf, delete=False) as tmp:
        tmp.write(uploaded.getvalue())
        p = tmp.name
    try:
        df = sv.leer_xlsb(p)
        base = st.session_state.get("_base_profundidad_path")
        if base and os.path.exists(base):
            df = sv.enriquecer_con_micro(df, base)
    finally:
        os.unlink(p)
    st.session_state[key] = df
    return df


def render(st):
    st.markdown('<div class="section-header"><h3>🧵 Talla y Color</h3><span class="live-badge">PILOTO</span></div>',
                unsafe_allow_html=True)
    st.caption("¿Qué talla y qué color se venden vs qué tenemos en stock? Sube el reporte "
               "'Stock para Reposición Modelo Variación' (.xlsb). Con la Base Profundidad cargada se cruza la marca.")
    up = st.file_uploader("Reporte de stock por variación (.xlsb / .xlsx)", type=["xlsb", "xlsx"], key="tc_upload")
    if up is None:
        st.info("Sin reporte cargado. Ejemplo de lo que sale: peso de venta vs stock por talla y por color, "
                "cobertura por talla, opciones con curva rota y diagnóstico por tienda.")
        return
    try:
        df = _cargar(st, up)
    except Exception as e:
        st.error(f"No se pudo leer el reporte: {e}")
        return
    if df.empty:
        st.warning("El reporte no trae filas con stock o venta.")
        return

    tiendas = sorted(df["tienda"].unique().tolist())
    st.caption(f"{len(df):,} variaciones × tienda · {df['cod_modelo'].nunique():,} modelos · {len(tiendas)} tienda(s): "
               + ", ".join(tiendas[:8]) + (" …" if len(tiendas) > 8 else "")
               + ("" if len(tiendas) > 1 else "  ·  ⚠️ el reporte viene filtrado a una sola tienda"))

    c1, c2, c3 = st.columns(3)
    with c1:
        marcas = ["Todas"] + sorted(df["marca"].dropna().unique().tolist()) if "marca" in df.columns else ["Todas"]
        marca = st.selectbox("Marca", marcas, key="tc_marca")
    d = df if marca == "Todas" or "marca" not in df.columns else df[df["marca"] == marca]
    with c2:
        modelos = (d.groupby("modelo")["vta_uds_3s"].sum().sort_values(ascending=False))
        modelo = st.selectbox("Modelo / programa", ["Todos"] + modelos.index.tolist(), key="tc_modelo",
                              format_func=lambda m: m if m == "Todos" else f"{m} ({int(modelos.get(m, 0))} uds 3 sem)")
    if modelo != "Todos":
        d = d[d["modelo"] == modelo]
    with c3:
        tienda = st.selectbox("Tienda", ["Todas"] + tiendas, key="tc_tienda")
    dt = d if tienda == "Todas" else d[d["tienda"] == tienda]

    k1, k2, k3, k4 = st.columns(4)
    vta, stk = int(dt["vta_uds_3s"].sum()), int(dt["stock_uds"].sum())
    k1.metric("Venta 3 semanas (uds)", f"{vta:,}")
    k2.metric("Stock (uds)", f"{stk:,}")
    k3.metric("Cobertura (sem)", f"{(stk / (vta / 3)):.1f}" if vta else "—")
    k4.metric("On order (uds)", f"{int(dt['on_order'].sum()):,}")

    fmt = {"peso_venta": "{:.1%}", "peso_stock": "{:.1%}", "gap_pp": "{:+.1f}", "cobertura_sem": "{:.1f}",
           "vta_uds_3s": "{:,.0f}", "stock_uds": "{:,.0f}", "on_order": "{:,.0f}"}
    ren = {"vta_uds_3s": "Venta 3s", "stock_uds": "Stock", "on_order": "On order", "peso_venta": "% venta",
           "peso_stock": "% stock", "gap_pp": "Gap (pp)", "cobertura_sem": "Cob (sem)", "diagnostico": "Diagnóstico"}
    t1, t2, t3, t4 = st.tabs(["📏 Por talla", "🎨 Por color", "🧩 Curva rota", "🏬 Por tienda"])
    with t1:
        mt = sv.mix_por_eje(dt, "talla")
        st.dataframe(mt.rename(columns=ren).style.format({ren.get(k, k): v for k, v in fmt.items()}, na_rep="—"),
                     use_container_width=True, hide_index=True, height=min(60 + 35 * len(mt), 420))
        st.caption("Gap = % en venta − % en stock. Positivo: la talla vende más de lo que pesa en el stock (sub-stock); "
                   "negativo: pesa más de lo que vende (sobre-stock). Es la regla que Franco aplica a mano en el Pima.")
    with t2:
        mc = sv.mix_por_eje(dt, "color")
        st.dataframe(mc.rename(columns=ren).style.format({ren.get(k, k): v for k, v in fmt.items()}, na_rep="—"),
                     use_container_width=True, hide_index=True, height=min(60 + 35 * len(mc), 420))
    with t3:
        cr = sv.curva_rota_por_tienda(dt)
        rotas = cr[cr["curva_rota"]]
        st.caption(f"{len(rotas):,} opciones (modelo × color × tienda) con stock pero sin alguna talla mayor (S/M/L/XL). "
                   "Regla Zara: sin tallas mayores completas, la opción no existe en el piso.")
        st.dataframe(rotas.head(300).style.format({"vta_uds_3s": "{:,.0f}", "stock_uds": "{:,.0f}"}),
                     use_container_width=True, hide_index=True, height=340)
    with t4:
        dg = sv.diagnostico_tiendas(d)
        st.dataframe(dg.rename(columns={**ren, "opciones_curva_rota": "Opciones curva rota"})
                     .style.format({"Venta 3s": "{:,.0f}", "Stock": "{:,.0f}", "On order": "{:,.0f}",
                                    "Cob (sem)": "{:.1f}", "Opciones curva rota": "{:,.0f}"}, na_rep="—"),
                     use_container_width=True, hide_index=True, height=min(60 + 35 * len(dg), 480))

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        sv.mix_por_eje(dt, "talla").to_excel(w, sheet_name="Por talla", index=False)
        sv.mix_por_eje(dt, "color").to_excel(w, sheet_name="Por color", index=False)
        sv.curva_rota_por_tienda(dt).to_excel(w, sheet_name="Curva rota", index=False)
        sv.diagnostico_tiendas(d).to_excel(w, sheet_name="Por tienda", index=False)
        dt.to_excel(w, sheet_name="Detalle", index=False)
    buf.seek(0)
    st.download_button("📥 Excel — mix talla/color, curva rota y diagnóstico por tienda", buf.getvalue(),
                       file_name=f"Capi_TallaColor_{(modelo if modelo != 'Todos' else marca)[:30]}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="tc_dl")
