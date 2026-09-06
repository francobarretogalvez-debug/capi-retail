"""
🔍 Auditoría de predistribución — vista aislada (S15, Franco 2026-09-06).
Capa 1: qué perdimos · Capa 2: por qué (causa raíz y dueño) · Capa 3 (preliminar): índice de acierto
por tienda × línea (faltó vs sobró). Capas 3-4 completas cuando lleguen la curva plan y los clusters.
"""
from __future__ import annotations

import io

import pandas as pd

import auditoria_predistribucion as ap
import venta_perdida_semanal as vp


def render(st, df_cob: pd.DataFrame, etiqueta_semana=None):
    st.markdown('<div class="section-header"><h3>🔍 Auditoría de predistribución</h3><span class="live-badge">S15 · CAPAS 1-2</span></div>',
                unsafe_allow_html=True)
    st.caption("La venta perdida con su causa raíz: qué perdimos, por qué y a quién le toca. "
               "Cuando estén la curva plan por tienda y los clusters, se compara plan vs observado y se propone la curva Capi.")
    try:
        r = vp.venta_perdida_semana()
    except Exception as e:
        st.error(f"No se pudo calcular la venta perdida: {e}")
        return
    if not r or r.get("insuficiente") or r["detalle"].empty:
        st.info("Aún no hay suficientes snapshots por tienda (mínimo 3 cortes).")
        return
    sem = r["semana"]
    et = etiqueta_semana(sem) if etiqueta_semana else sem
    d = ap.enriquecer(r["detalle"], sem)
    st.markdown(f"**Semana {sem} ({et})** · venta perdida neta S/ {r['neto_min']:,.0f} – {r['neto_max']:,.0f} · "
                f"{r.get('n_en_quiebre', 0):,} SKU×tienda en quiebre, {len(d):,} con pérdida")

    # ── Capa 2: causa raíz ──
    st.markdown("#### Por qué se perdió · causa raíz y dueño")
    pc = ap.por_causa(d)
    st.dataframe(pc[["causa", "dueno", "combos", "skus", "tiendas", "recurrentes", "neto_min", "neto_max", "pct"]]
                 .rename(columns={"causa": "Causa", "dueno": "A quién le toca", "combos": "SKU×tienda", "skus": "SKUs", "tiendas": "Tiendas",
                                  "recurrentes": "Recurrentes (≥3 sem)", "neto_min": "Neto mín S/", "neto_max": "Neto máx S/", "pct": "% del total"})
                 .style.format({"SKU×tienda": "{:,.0f}", "SKUs": "{:,.0f}", "Tiendas": "{:,.0f}", "Recurrentes (≥3 sem)": "{:,.0f}",
                                "Neto mín S/": "S/ {:,.0f}", "Neto máx S/": "S/ {:,.0f}", "% del total": "{:.0%}"}),
                 use_container_width=True, hide_index=True, height=min(60 + 35 * len(pc), 260))
    _cd = pc[pc["causa_key"] == "1_cd"]
    if len(_cd) and _cd["pct"].iloc[0] >= 0.5:
        st.warning(f"⚠️ El {_cd['pct'].iloc[0]*100:.0f}% de la venta perdida ocurrió con stock en el CD: no es un problema de compra, es de bajada a tienda. "
                   f"{int(_cd['recurrentes'].iloc[0]):,} combos llevan 3 o más semanas así.")

    # ── Capa 1: qué perdimos ──
    st.markdown("#### Qué perdimos")
    t1, t2, t3, t4 = st.tabs(["🏬 Tienda × línea", "🏷️ Marca", "📦 Tipo · procedencia · temporada", "🔁 SKUs recurrentes"])
    tl = ap.tienda_linea(d)
    with t1:
        try:
            import plotly.express as px
            piv = tl.pivot_table(index="tienda", columns="linea", values="neto_max", aggfunc="sum").fillna(0)
            piv = piv.loc[piv.sum(axis=1).sort_values(ascending=False).index[:20], piv.sum().sort_values(ascending=False).index[:12]]
            fig = px.imshow(piv, color_continuous_scale=["#F7F6FA", "#6D3B8E"], aspect="auto", labels=dict(color="Neto máx S/"))
            fig.update_layout(height=520, margin=dict(l=10, r=10, t=10, b=10), coloraxis_showscale=False)
            fig.update_traces(hovertemplate="%{y} · %{x}<br>S/ %{z:,.0f}<extra></extra>")
            st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.caption(f"Heatmap no disponible: {e}")
        st.dataframe(tl.head(60).rename(columns={"tienda": "Tienda", "linea": "Línea", "combos": "SKU×tienda", "neto_min": "Neto mín S/", "neto_max": "Neto máx S/",
                                                 "sem_quiebre_prom": "Sem en quiebre (prom)", "recurrentes": "Recurrentes", "con_cd": "Con stock en CD",
                                                 "causa_dominante": "Causa dominante"})
                     .style.format({"SKU×tienda": "{:,.0f}", "Neto mín S/": "S/ {:,.0f}", "Neto máx S/": "S/ {:,.0f}", "Sem en quiebre (prom)": "{:.1f}",
                                    "Recurrentes": "{:,.0f}", "Con stock en CD": "{:,.0f}"}),
                     use_container_width=True, hide_index=True, height=380)
    with t2:
        pm = ap.por_dimension(d, "marca")
        st.dataframe(pm.rename(columns={"marca": "Marca", "combos": "SKU×tienda", "neto_min": "Neto mín S/", "neto_max": "Neto máx S/", "recurrentes": "Recurrentes", "pct": "% del total"})
                     .style.format({"SKU×tienda": "{:,.0f}", "Neto mín S/": "S/ {:,.0f}", "Neto máx S/": "S/ {:,.0f}", "Recurrentes": "{:,.0f}", "% del total": "{:.0%}"}),
                     use_container_width=True, hide_index=True, height=min(60 + 35 * len(pm), 420))
    with t3:
        c1, c2, c3 = st.columns(3)
        for col, dim, lab in ((c1, "tipo_producto", "Tipo de producto"), (c2, "procedencia", "Procedencia"), (c3, "temporada", "Temporada")):
            g = ap.por_dimension(d, dim)
            if not g.empty:
                col.markdown(f"**{lab}**")
                col.dataframe(g[[dim, "combos", "neto_max", "pct"]].rename(columns={dim: lab, "combos": "SKU×tienda", "neto_max": "Neto máx S/", "pct": "%"})
                              .style.format({"SKU×tienda": "{:,.0f}", "Neto máx S/": "S/ {:,.0f}", "%": "{:.0%}"}), use_container_width=True, hide_index=True)
    with t4:
        rec = ap.skus_recurrentes(d)
        st.dataframe(rec.rename(columns={"sku": "SKU", "descripcion": "Producto", "marca": "Marca", "linea": "Línea", "tiendas": "Tiendas", "sem_prom": "Sem en quiebre (prom)",
                                         "neto_max": "Neto máx S/", "stock_cd": "Stock CD", "causa": "Causa"})
                     .style.format({"Tiendas": "{:,.0f}", "Sem en quiebre (prom)": "{:.1f}", "Neto máx S/": "S/ {:,.0f}", "Stock CD": "{:,.0f}"}),
                     use_container_width=True, hide_index=True, height=420)

    # ── Capa 3 preliminar: índice de acierto ──
    st.markdown("#### ¿Acierta la predistribución? · por tienda × línea (preliminar)")
    st.caption("Faltó = venta perdida con stock en CD o en otras tiendas (evitable por predistribución). Sobró = capital en sobrestock, estancado o "
               "sin venta en esa tienda × línea. Cuando llegue la curva plan por tienda y los clusters, esto se convierte en plan vs observado y curva Capi.")
    ia = ap.indice_acierto(d, df_cob)
    if ia.empty:
        st.info("Sin datos para el índice.")
    else:
        resumen = ia.groupby("diagnostico").agg(cruces=("linea", "size"), falto=("falto_neto", "sum"), sobro=("sobro_capital", "sum")).reset_index()
        st.dataframe(resumen.rename(columns={"diagnostico": "Diagnóstico", "cruces": "Tienda×línea", "falto": "Faltó (neto S/ sem)", "sobro": "Sobró (capital S/)"})
                     .style.format({"Tienda×línea": "{:,.0f}", "Faltó (neto S/ sem)": "S/ {:,.0f}", "Sobró (capital S/)": "S/ {:,.0f}"}),
                     use_container_width=True, hide_index=True, height=min(60 + 35 * len(resumen), 220))
        st.dataframe(ia.head(80)[["tienda", "linea", "diagnostico", "falto_neto", "combos_falto", "recurrentes", "sobro_capital", "pct_sobro", "capital"]]
                     .rename(columns={"tienda": "Tienda", "linea": "Línea", "diagnostico": "Diagnóstico", "falto_neto": "Faltó (neto S/ sem)", "combos_falto": "Combos faltó",
                                      "recurrentes": "Recurrentes", "sobro_capital": "Sobró (capital S/)", "pct_sobro": "% del capital que sobra", "capital": "Capital tienda×línea S/"})
                     .style.format({"Faltó (neto S/ sem)": "S/ {:,.0f}", "Combos faltó": "{:,.0f}", "Recurrentes": "{:,.0f}", "Sobró (capital S/)": "S/ {:,.0f}",
                                    "% del capital que sobra": "{:.0%}", "Capital tienda×línea S/": "S/ {:,.0f}"}),
                     use_container_width=True, hide_index=True, height=440)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pc.to_excel(w, sheet_name="Por causa", index=False); tl.to_excel(w, sheet_name="Tienda x linea", index=False)
        ap.por_dimension(d, "marca").to_excel(w, sheet_name="Por marca", index=False); ap.skus_recurrentes(d, 500).to_excel(w, sheet_name="SKUs recurrentes", index=False)
        if not ia.empty: ia.to_excel(w, sheet_name="Indice acierto", index=False)
        d.to_excel(w, sheet_name="Detalle SKU x tienda", index=False)
    buf.seek(0)
    st.download_button("📥 Excel — auditoría de predistribución (causa raíz, tienda×línea, recurrentes, índice)", buf.getvalue(),
                       file_name=f"Capi_Auditoria_Predistribucion_{sem}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_ap")
    st.info("**Qué hacer hoy** (otra venta, la que aún se salva): 🎯 Match Producto-Plaza para los empujes, 🔄 Transferencias para lo mal distribuido, 📦 Reposición para el reorden nacional.")
