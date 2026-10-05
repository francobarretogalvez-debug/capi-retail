"""
vista_pv_transito.py — Vista "🚢 PV en Tránsito" de Capi (2026-10-04).

Módulo aislado a propósito: `app_streamlit.py` es un monolito donde cada `elif nav_page`
es una isla de scope. Toda la pantalla vive acá; el app solo registra el botón y llama a
`render(st, df_cob)`. El cálculo vive en pv_transito.py y la persistencia en eta_store.py:
esta vista solo pinta y dispara acciones.

Qué muestra, en el orden en que Franco decide (norte Capi: avisar de un porrazo, la acción
al lado del dato, entendible en 30 segundos):
  0. Fuente      — qué archivo, de qué fecha, qué tan viejo, qué formato (DETALLE o llegadas)
  1. 3 KPIs      — % recibido de la compra, unidades atrasadas, semana con más llegadas
  2. Por marca   — resumen con semáforo (+ apertura por línea)
  3. Por semana  — pendiente por semana Ripley de llegada
  4. Por OC      — detalle con la ETA editable; cada cambio queda en el historial
  5. Carga masiva de ETA desde el Excel del proveedor
  6. Export      — pv_transito_AAAA-MM-DD.xlsx (contrato fijo de 13 columnas) + registrar corte

Claves de session_state (todas con prefijo pvt_ para no chocar con otras vistas):
  pvt_archivo_path (ruta elegida/subida), pvt_res (resultado de procesar), pvt_fp (huella),
  _pvt_restaurado (historial traído de Notion una vez por sesión), _pvt_sync::<fp> (corte ya
  sincronizado al historial).
"""
from __future__ import annotations

import glob
import os
from datetime import date

import numpy as np
import pandas as pd

import eta_store as es
import pv_transito as pt

_SEMAFORO = {"verde": "🟢", "ambar": "🟡", "rojo": "🔴"}


def _kpi(st, label, valor, sub="", color=""):
    st.markdown(
        f'<div class="kpi-card {color}"><div class="kpi-label">{label}</div>'
        f'<div class="kpi-val">{valor}</div><div class="kpi-sub">{sub}</div></div>',
        unsafe_allow_html=True)


def _fmt_u(v) -> str:
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:,.0f}"


def _fmt_pct(v) -> str:
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.0%}"


def _fmt_sol(v) -> str:
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"S/ {v/1e6:,.2f} M" if abs(v) >= 1e6 else f"S/ {v:,.0f}"


def cambios_editor(original: pd.DataFrame, editado: pd.DataFrame) -> list[dict]:
    """Compara la tabla por OC que se mostró con la que devolvió st.data_editor y devuelve solo las
    OC cuya ETA cambió (incluye borrar la fecha → NaT). Puro: así se prueba sin Streamlit."""
    base_eta = original.set_index("oc")["eta"]
    cambios = []
    for _, r in editado.iterrows():
        nueva, prev = r.get("eta"), base_eta.get(r["oc"])
        nueva_ts = pd.Timestamp(nueva) if nueva is not None and not pd.isna(nueva) else pd.NaT
        prev_ts = pd.Timestamp(prev) if prev is not None and not pd.isna(prev) else pd.NaT
        if (pd.isna(nueva_ts) and pd.isna(prev_ts)) or (pd.notna(nueva_ts) and pd.notna(prev_ts) and nueva_ts == prev_ts):
            continue
        cambios.append({"oc": r["oc"], "eta": nueva_ts, "comentario": str(r.get("comentario", "") or ""),
                        "marca": r.get("marca", ""), "modelo": r.get("modelo", "")})
    return cambios


# ── Fuente ───────────────────────────────────────────────────────────────────
def _elegir_archivo(st, cfg) -> tuple[str | None, str | None]:
    """Devuelve (ruta, nombre). Uploader manda; si no, un archivo de la carpeta de inputs."""
    carpeta = pt.inputs_dir(cfg)
    c1, c2 = st.columns([1.3, 1])
    with c1:
        up = st.file_uploader("Reporte de comex (DETALLE xlsx o LLEGADAS xlsb)", type=["xlsx", "xlsb", "xlsm"],
                              key="pvt_upload",
                              help="El DETALLE que exporta comex (una fila por OC × SKU, con FOB y recibidos) o "
                                   "las hojas 'LLEGADAS RETRASOS COMEX' (solo pendientes). Se guarda en la carpeta "
                                   f"de inputs ({carpeta}), que está fuera del repo.")
    if up is not None:
        destino = os.path.join(carpeta, up.name)
        with open(destino, "wb") as f:
            f.write(up.getbuffer())
        st.session_state["pvt_archivo_path"] = destino
    with c2:
        existentes = sorted(glob.glob(os.path.join(carpeta, "*.xls*")), key=os.path.getmtime, reverse=True)
        opciones = ["(subir arriba)"] + [os.path.basename(p) for p in existentes]
        actual = st.session_state.get("pvt_archivo_path")
        idx = opciones.index(os.path.basename(actual)) if actual and os.path.basename(actual) in opciones else 0
        sel = st.selectbox("…o uno ya cargado", opciones, index=idx, key="pvt_sel")
        if sel != "(subir arriba)":
            st.session_state["pvt_archivo_path"] = os.path.join(carpeta, sel)
    ruta = st.session_state.get("pvt_archivo_path")
    if ruta and not os.path.exists(ruta):
        ruta = None
    return ruta, (os.path.basename(ruta) if ruta else None)


def _procesar_cacheado(st, ruta, nombre, hoy, temporada, fecha_reporte, base, cfg):
    hist = es.cargar_historial()
    vig = es.vigente(hist)
    fp = f"{ruta}|{os.path.getmtime(ruta)}|{temporada}|{fecha_reporte}|{len(hist)}|{hoy}"
    if st.session_state.get("pvt_fp") != fp or "pvt_res" not in st.session_state:
        with st.spinner("Leyendo el reporte…"):
            res = pt.procesar(ruta, nombre, hoy=hoy, temporada=temporada, base=base, vigente=vig,
                              fecha_reporte=fecha_reporte, cfg=cfg)
        st.session_state["pvt_res"] = res
        st.session_state["pvt_fp"] = fp
    return st.session_state["pvt_res"], vig, hist


# ── Pantalla ─────────────────────────────────────────────────────────────────
def render(st, df_cob: pd.DataFrame | None = None, hoy: date | None = None):
    cfg = pt.cargar_config()
    hoy = hoy or date.today()
    st.markdown('<div class="section-header"><h3>🚢 PV en Tránsito</h3>'
                '<span class="live-badge">COMPRA · RECIBIDO · ETA · ATRASOS</span></div>', unsafe_allow_html=True)

    # Historial: si la nube borró el disco, lo trae de Notion una vez por sesión
    if not st.session_state.get("_pvt_restaurado"):
        try:
            n = es.restaurar_desde_notion()
            if n:
                st.caption(f"Historial de ETA restaurado desde Notion: {n} registros.")
        except Exception:
            pass
        st.session_state["_pvt_restaurado"] = True

    ruta, nombre = _elegir_archivo(st, cfg)
    if not ruta:
        st.info("Sube el DETALLE de comex (o las hojas de LLEGADAS) para empezar. Pídeselo a comex tal cual lo exporta, sin pivotes.")
        return

    c1, c2, c3 = st.columns([1, 1, 1.4])
    with c1:
        temporada = st.radio("Temporada", ["PV", "OI", "Todas"], horizontal=True, key="pvt_temp",
                             index=["PV", "OI", "Todas"].index(cfg.get("temporada_default", "PV")))
    with c2:
        f_default = pt.fecha_reporte_de(nombre, pt._mtime(ruta), hoy)
        fecha_reporte = st.date_input("Fecha del reporte", value=f_default, key="pvt_fecha", format="DD/MM/YYYY",
                                      help="Se deduce del nombre del archivo; corrígela si comex lo exportó otro día.")
    base = pt.base_desde_df_cob(df_cob) if df_cob is not None and not getattr(df_cob, "empty", True) else None
    with c3:
        st.caption(("Costo S/ y stock en tienda desde la Base cargada (cruce por código de modelo)." if base is not None
                    else "Sin Base cargada: costo = FOB × factor; 'en tienda' vacío."))

    try:
        res, vig, hist = _procesar_cacheado(st, ruta, nombre, hoy, None if temporada == "Todas" else temporada, fecha_reporte, base, cfg)
    except ValueError as e:
        st.error(f"No pude leer el archivo: {e}")
        return

    # Sincronizar el historial con este corte (una vez por archivo): las ETA que cambiaron quedan registradas
    fp_sync = f"_pvt_sync::{ruta}|{os.path.getmtime(ruta)}"
    if not st.session_state.get(fp_sync):
        # Solo el universo del módulo (marcas foco + división), todas las temporadas: así OI también
        # queda con historial sin arrastrar las OC de otras divisiones del DETALLE (MUJER, CALZADO…)
        universo = pt.filtrar(res["oc_todo"], cfg["marcas_foco"], cfg.get("division_filtro") or None, None)
        n_nuevas = es.sincronizar_desde_reporte(universo, fecha_reporte=fecha_reporte, usuario="comex")
        if res["formato"] == "llegadas":
            es.actualizar_maestro(universo, fecha_reporte, "llegadas")
        st.session_state[fp_sync] = True
        if n_nuevas:
            st.session_state.pop("pvt_fp", None)        # fuerza recalcular con la ETA vigente nueva
            st.toast(f"{n_nuevas} ETA nuevas o movidas registradas en el historial (fuente comex).")
            res, vig, hist = _procesar_cacheado(st, ruta, nombre, hoy, None if temporada == "Todas" else temporada, fecha_reporte, base, cfg)

    oc, resumen = res["oc"], res["resumen"]
    antig = (pd.Timestamp(hoy) - pd.Timestamp(fecha_reporte)).days
    aviso_antig = f" · ⚠️ tiene {antig} días" if antig > 7 else f" · {antig} días"
    st.caption(f"**{nombre}** · formato **{res['formato']}** · fecha del reporte **{fecha_reporte:%d/%m/%Y}**{aviso_antig} · "
               f"{res['oc_todo']['oc'].nunique()} OC en el archivo, {oc['oc'].nunique()} de las marcas foco en {temporada}"
               + (f" · {res['n_lineas_partidas']} líneas partidas sumadas" if res["n_lineas_partidas"] else ""))
    if res["formato"] == "llegadas":
        st.warning("Este archivo solo trae lo **pendiente**: no hay costo ni OC recibidas, así que el % recibido no se puede calcular. "
                   "Pídele a comex el DETALLE para tener la foto completa.")
    if not res["hallazgos"].empty:
        with st.expander(f"⚠️ {len(res['hallazgos'])} avisos de calidad del archivo", expanded=False):
            st.dataframe(res["hallazgos"], hide_index=True, use_container_width=True)

    if oc.empty:
        st.warning("No hay filas de las marcas foco para esa temporada en este archivo.")
        return

    # ── 1. KPIs ──
    u_tot, u_rec, u_atr = resumen["und_compra_pv"].sum(), resumen["und_recibida_cd"].sum(), resumen["und_atrasadas"].sum()
    c_tot, c_rec = resumen["costo_compra_pv"].sum(min_count=1), resumen["costo_recibido_cd"].sum(min_count=1)
    pct_u = u_rec / u_tot if u_tot else np.nan
    pct_c = (c_rec / c_tot) if pd.notna(c_tot) and c_tot else np.nan
    ps = res["por_semana"]
    sem_pico = ps[ps["semana"].str.startswith("W")].groupby("semana")["uds"].sum().sort_values(ascending=False)
    k1, k2, k3 = st.columns(3)
    with k1:
        _kpi(st, f"Recibido de la compra {temporada}", _fmt_pct(pct_u),
             f"{_fmt_u(u_rec)} de {_fmt_u(u_tot)} u" + (f" · {_fmt_pct(pct_c)} a costo" if pd.notna(pct_c) else ""),
             "green" if pct_u >= 0.7 else "yellow" if pct_u >= 0.4 else "red")
    with k2:
        _kpi(st, "Unidades atrasadas", _fmt_u(u_atr),
             f"{int(resumen['oc_atrasadas'].sum())} OC con ETA vencida o sin ETA · {_fmt_pct(u_atr / (u_tot - u_rec)) if u_tot > u_rec else '—'} del pendiente",
             "red" if u_atr > 0 else "green")
    with k3:
        if not sem_pico.empty:
            s = sem_pico.index[0]
            cierre = ps.loc[ps["semana"] == s, "cierre"].iloc[0]
            _kpi(st, "Semana con más llegadas", f"{_fmt_u(sem_pico.iloc[0])} u",
                 f"{s} · cierra {cierre:%d/%m}" if pd.notna(cierre) else s, "blue")
        else:
            _kpi(st, "Semana con más llegadas", "—", "nada pendiente con ETA futura", "green")

    # ── 2. Por marca ──
    st.markdown("#### Por marca")
    tabla = resumen.copy()
    tabla.insert(0, " ", tabla["semaforo"].map(_SEMAFORO))
    cols_show = [" ", "marca", "n_oc", "und_compra_pv", "und_recibida_cd", "pct_recibido_und", "pct_recibido_costo",
                 "und_pendiente", "und_atrasadas", "proxima_eta", "pct_en_tienda", "detalle_llegadas"]
    st.dataframe(tabla[cols_show], hide_index=True, use_container_width=True,
                 column_config={
                     "marca": "Marca", "n_oc": st.column_config.NumberColumn("OC", format="%d"),
                     "und_compra_pv": st.column_config.NumberColumn("Compra u", format="%,d"),
                     "und_recibida_cd": st.column_config.NumberColumn("Recibido u", format="%,d"),
                     "pct_recibido_und": st.column_config.ProgressColumn("% recibido u", format="percent", min_value=0, max_value=1),
                     "pct_recibido_costo": st.column_config.NumberColumn("% a costo", format="percent"),
                     "und_pendiente": st.column_config.NumberColumn("Pendiente u", format="%,d"),
                     "und_atrasadas": st.column_config.NumberColumn("Atrasadas u", format="%,d"),
                     "proxima_eta": st.column_config.DateColumn("Próxima ETA", format="DD/MM"),
                     "pct_en_tienda": st.column_config.NumberColumn("% compra en tienda", format="percent",
                                                                    help="Stock hoy en tiendas de los modelos de esta compra ÷ unidades compradas. Incluye lo ya vendido como 'no en tienda'."),
                     "detalle_llegadas": st.column_config.TextColumn("Qué llega cuándo", width="large"),
                 })
    if any("estimado" in n for n in res["notas"]):
        st.caption(f"Costo S/ estimado = FOB USD × {cfg['factor_fob_a_costo']} donde el modelo no tiene Costo S/. en la Base "
                   f"(razón medida 04-oct-2026, banda ±{cfg['banda_costo_pct']:.0%}). Los % a costo no dependen del factor.")
    with st.expander("Abrir por línea"):
        rl = res["resumen_linea"]
        st.dataframe(rl[["marca", "linea", "n_oc", "und_compra_pv", "und_recibida_cd", "pct_recibido_und", "und_pendiente", "und_atrasadas", "proxima_eta", "detalle_llegadas"]],
                     hide_index=True, use_container_width=True,
                     column_config={"pct_recibido_und": st.column_config.NumberColumn("% recibido u", format="percent"),
                                    "proxima_eta": st.column_config.DateColumn("Próxima ETA", format="DD/MM")})

    # ── 3. Por semana ──
    st.markdown("#### Llegadas por semana Ripley")
    if ps.empty:
        st.caption("Nada pendiente.")
    else:
        piv = ps.pivot_table(index="semana", columns="marca", values="uds", aggfunc="sum", fill_value=0)
        orden = [s for s in ["VENCIDA", "SIN ETA"] if s in piv.index] + sorted(s for s in piv.index if s.startswith("W")) + \
                [s for s in piv.index if s not in ("VENCIDA", "SIN ETA") and not s.startswith("W")]
        piv = piv.loc[orden]
        piv["Total"] = piv.sum(axis=1)
        cierres = ps.drop_duplicates("semana").set_index("semana")["cierre"]
        piv.insert(0, "cierra", piv.index.map(lambda s: f"{cierres.get(s):%d/%m}" if pd.notna(cierres.get(s)) else ""))
        st.dataframe(piv, use_container_width=True)
        st.bar_chart(piv.drop(columns=["cierra", "Total"]), height=220)

    # ── 4. Detalle por OC con ETA editable ──
    st.markdown("#### Por OC — corregir una ETA")
    st.caption("Edita la columna **ETA vigente** y guarda: cada cambio se agrega al historial (nunca se pisa el anterior). "
               "La ETA de comex de este corte queda en su columna para comparar.")
    por_oc = (oc.sort_values(["marca_display", "eta"], na_position="last")
              .groupby("oc", as_index=False, sort=False)
              .agg(marca=("marca_display", "first"), modelo=("modelo", "first"), linea=("linea", "first"),
                   estado=("estado", "first"), uds=("uds", "sum"), recibida=("recibida", "max"),
                   eta_comex=("eta_cd", "first"), eta=("eta", "first"), eta_fuente=("eta_fuente", "first"),
                   motivo=("motivo_retraso", "first")))
    v = vig.set_index("oc") if not vig.empty else None
    por_oc["n_cambios"] = por_oc["oc"].map(v["n_cambios"]).fillna(0).astype(int) if v is not None else 0
    por_oc["dias_corrimiento"] = por_oc["oc"].map(v["dias_corrimiento"]) if v is not None else np.nan
    por_oc["atrasada"] = ~por_oc["recibida"] & (por_oc["eta"].isna() | (por_oc["eta"] < pd.Timestamp(hoy)))
    por_oc["comentario"] = ""
    por_oc["eta"] = pd.to_datetime(por_oc["eta"]).dt.date
    por_oc["eta_comex"] = pd.to_datetime(por_oc["eta_comex"]).dt.date
    solo_pend = st.checkbox("Solo pendientes", value=True, key="pvt_solo_pend")
    vista_oc = por_oc[~por_oc["recibida"]] if solo_pend else por_oc
    editado = st.data_editor(
        vista_oc[["oc", "marca", "modelo", "linea", "estado", "uds", "atrasada", "eta", "eta_comex", "eta_fuente", "n_cambios", "dias_corrimiento", "motivo", "comentario"]],
        hide_index=True, use_container_width=True, key="pvt_editor", num_rows="fixed",
        disabled=["oc", "marca", "modelo", "linea", "estado", "uds", "atrasada", "eta_comex", "eta_fuente", "n_cambios", "dias_corrimiento", "motivo"],
        column_config={
            "oc": "OC", "uds": st.column_config.NumberColumn("u", format="%,d"), "atrasada": st.column_config.CheckboxColumn("Atrasada"),
            "eta": st.column_config.DateColumn("ETA vigente ✏️", format="DD/MM/YYYY"),
            "eta_comex": st.column_config.DateColumn("ETA comex (este corte)", format="DD/MM/YYYY"),
            "eta_fuente": "Fuente", "n_cambios": st.column_config.NumberColumn("Veces movida", format="%d"),
            "dias_corrimiento": st.column_config.NumberColumn("Días corridos", format="%d"),
            "motivo": "Motivo (comex)", "comentario": st.column_config.TextColumn("Comentario ✏️"),
        })
    if st.button("💾 Guardar ETA corregidas", key="pvt_guardar"):
        cambios = cambios_editor(vista_oc, editado)
        if not cambios:
            st.info("No hay ETA distintas a la vigente.")
        else:
            n = es.registrar_lote(pd.DataFrame(cambios), "manual", usuario=os.environ.get("USER", "capi"), solo_si_cambia=False)
            st.success(f"{n} ETA registradas en el historial (fuente manual).")
            st.session_state.pop("pvt_fp", None)
            st.rerun()
    with st.expander("Historial de una OC"):
        ocs = sorted(por_oc["oc"].unique())
        sel = st.selectbox("OC", ocs, key="pvt_hist_oc") if ocs else None
        if sel is not None:
            h = hist[hist["oc"] == sel].sort_values("registrado_en")
            if h.empty:
                st.caption("Sin registros todavía: la primera carga de un corte los crea.")
            else:
                st.dataframe(h[["registrado_en", "eta", "fuente", "comentario", "usuario", "semana_ripley"]], hide_index=True, use_container_width=True,
                             column_config={"registrado_en": st.column_config.DatetimeColumn("Registrado", format="DD/MM/YYYY"),
                                            "eta": st.column_config.DateColumn("ETA", format="DD/MM/YYYY")})

    # ── 5. Carga masiva ──
    with st.expander("Carga masiva de ETA desde Excel (proveedor / comex)"):
        st.caption("Columnas por nombre: **OC**, **ETA** (o 'Nueva ETA'), **Comentario** opcional. Solo se registran las que cambian.")
        up2 = st.file_uploader("Excel con ETA", type=["xlsx", "xlsb"], key="pvt_masivo")
        if up2 is not None:
            try:
                cambios = es.cargar_masivo_excel(up2.getvalue(), up2.name)
                st.dataframe(cambios.head(50), hide_index=True, use_container_width=True)
                st.caption(f"{len(cambios)} filas legibles · {cambios.attrs.get('descartadas', 0)} descartadas (sin OC o sin fecha).")
                if st.button(f"Registrar {len(cambios)} ETA (fuente proveedor)", key="pvt_masivo_btn"):
                    n = es.registrar_lote(cambios, "proveedor", usuario=os.environ.get("USER", "capi"))
                    st.success(f"{n} ETA registradas ({len(cambios) - n} ya estaban vigentes).")
                    st.session_state.pop("pvt_fp", None)
                    st.rerun()
            except ValueError as e:
                st.error(str(e))

    # ── 6. Export ──
    st.markdown("#### Exportar para el formato semanal")
    e1, e2 = st.columns([1, 1])
    with e1:
        st.download_button(f"⬇️ {pt.nombre_export(hoy)}", data=pt.export_bytes(resumen, notas=res["notas"], detalle_oc=por_oc),
                           file_name=pt.nombre_export(hoy), mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           key="pvt_dl", help="Hoja 1 'pv_transito': exactamente las 13 columnas que consume tu formato. Hoja 2 'notas', hoja 3 'detalle_oc'.")
    with e2:
        if st.button("📌 Registrar corte", key="pvt_corte", help="Guarda el resumen y la tabla de OC de este corte en snapshots/pv_transito/ para compararlo la próxima semana."):
            d = es._dir()
            resumen.assign(fecha_reporte=pd.Timestamp(fecha_reporte), temporada=temporada).to_parquet(os.path.join(d, f"corte_{fecha_reporte:%Y-%m-%d}_resumen.parquet"), index=False)
            oc.to_parquet(os.path.join(d, f"corte_{fecha_reporte:%Y-%m-%d}_oc.parquet"), index=False)
            st.success(f"Corte del {fecha_reporte:%d/%m/%Y} guardado en {d}.")
    with st.expander("Notas del cálculo"):
        for n in res["notas"]:
            st.markdown(f"- {n}")
