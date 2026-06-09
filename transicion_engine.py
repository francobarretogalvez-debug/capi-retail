"""
transicion_engine.py — Motor de cálculo de compra para programas en transición
==============================================================================

Caso: Showroom Mayo 2026 · Pantalón Moda Masculina · Marca Marquis (Ripley).
El bestseller "El Diegol" (CodModelo 27390791, PANT SPORT DIEGOL MQS COR TT)
sale de Marquis (pasa a Kenneth Stevens). Hay que dimensionar la compra de los
reemplazos (Dario, Bremen, Bronco) y cuantificar la caída neta de la categoría.

Dos fuentes (carpeta Bases):
  1. Profundidad abierta a tienda ("Base al DD.MM.xlsx", formato wide) → Ruta A
     (captura/ranking Dario vs Diegol por tienda para priorizar rollout). Se
     ingesta con el loader del motor (transformador_base_ripley.load_from_base_ripley).
  2. BD agregada por modelo-color ("BD 17.11.25 al 24.05.26.xlsx", nivel cadena)
     → magnitud/estacionalidad/margen, baseline Diegol y captura temporada completa.

Decisiones clave (Franco):
  - Match por CÓDIGO DE MODELO exacto, no por nombre.
  - Captura del Dario = temporada completa a nivel cadena (BD) = Dario/Diegol.
    El nivel-tienda (wide, reciente) está contaminado por el Diegol en liquidación
    (capturas >100% espurias), así que solo se usa para ranking de rollout, no para
    la magnitud de la proyección.
  - Dario = toda temporada (flujo): venta = Diegol × captura; carga = venta / agotamiento.
  - Bremen (ventana D) / Bronco (ventana B): carga manual × agotamiento eventual.
  - Mitigación: Diegol temporada completa vs reemplazo, en uds / S/ / margen.

Reutilizable cambiando CONFIG. Prototipo del módulo de predistribución de Capi (§9).
"""

import os
import io
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from transformador_base_ripley import load_from_base_ripley  # noqa: E402


# ─────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────

_BASES = "/Users/francobarreto/Claude_Context/Ripley.md/Bases"

import glob

CONFIG = {
    "ruta_profundidad": os.path.join(_BASES, "Base al 25.05.xlsx"),   # último snapshot (agotamiento + afinidad)
    "rutas_profundidad": sorted(glob.glob(os.path.join(_BASES, "Base al*.xlsx"))),  # 7 semanas reales
    "ruta_bd_modelo":   os.path.join(_BASES, "BD 17.11.25 al 24.05.26.xlsx"),
    "marca": "MARQUIS",
    "tiendas_excluir": ["TV", "TV PI", "VTAS CORP", "FSF MAC LO", "ASIA"],  # canales no-retail
    # Tiendas con pasarela: el Diegol tiene doble exhibición (mesa corner + pasarela);
    # el Dario tiene una sola. Se normaliza la venta del Diegol entre 2 puntos para
    # comparar venta-por-exhibición (guardrail de normalización por facing).
    "pasarela_tiendas": ["CHIC", "CHO", "JP", "LO", "PIU2", "PLN", "SLVR", "SB", "SM", "TRUJ", "SJL"],
    "exhibiciones_diegol_pasarela": 2,
    "linea_filtro": "PANTAL",
    # Match por código de modelo (CodModelo en BD / 'Cód. Prod.' en wide)
    "codigos": {
        "DIEGOL": ["27390791"],                          # bestseller saliente (COR TT)
        "DARIO":  ["35207068", "36224956", "34056750"],  # Dario Chino SS25/TT/FW25
        "BREMEN": ["36210326"],                           # Bremen Formal FW26
        "BRONCO": [],                                      # sin historia → manual
    },
    "ventana_periodo": {"B": "P202512", "C": "P202601", "D": "P202602"},  # Dic/Ene/Feb
    "ventana_programa": {"B": "BRONCO", "D": "BREMEN"},                    # Dario(C) = flujo
    # Ruta A
    "n_top_tiendas": 3,
    "min_uds_diegol_tienda": 20,   # umbral de volumen 4 sem para que la tienda cuente como señal
    "cobertura_target_sem": 13,
    "fill_min_pasarela": 120,
    "agotamiento_dario": None,     # None → se calcula de la data wide (vta/(vta+stock))
    # Precios vigente de referencia (ticket). El Dario debió estar a 99.99 (paridad
    # con Diegol) pero salió a 79.99 por error → su captura observada está inflada.
    # Se castiga subiéndolo a paridad con elasticidad asumida.
    "precio_vigente_dario": 79.99,
    "precio_vigente_diegol": 99.99,
    "elasticidad_precio_dario": -1.0,   # castigo captura por reprecio 79.99→99.99 (+25%)
    # Como se castigan las unidades del Dario por subirlo a paridad (99.99), la venta
    # se valoriza a ese precio (VtaSMF del Diegol/u), no al precio viejo barato. Consistente.
    "valorizar_dario_paridad": True,
    # Ruta B: distribución escalonada por volumen de venta de la línea (compartida
    # por Bremen y Bronco). Tiers por terciles de venta de pantalón Marquis por tienda.
    "distribucion_tiers": {"alto": 200, "medio": 150, "bajo": 100},
    "incluir_virtual_distribucion": True,    # 30 tiendas + tienda virtual (TV)
    "bremen": {"compra_inicial_uds": 1141, "compra_inicial_costo": 50289,
               "usar_distribucion": True, "agotamiento": 0.40},
    "bronco": {"usar_distribucion": True, "agotamiento": 0.60},
    "salida_excel": os.path.join(_BASES, "Transicion_Marquis_Mayo2026.xlsx"),
}


# ─────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────

def _num(s):
    return pd.to_numeric(s, errors="coerce").fillna(0)


def classify_by_code(cod, codigos):
    """Mapea un código de modelo a su programa según las listas de CONFIG['codigos']."""
    c = str(cod).strip()
    for prog, codes in codigos.items():
        if c in codes:
            return prog
    return None


# ─────────────────────────────────────────────────────────────
#  CAPA BD (nivel cadena) — magnitud, ventanas, margen, captura
# ─────────────────────────────────────────────────────────────

def dimensionar_programas(cfg):
    """
    Lee la BD, filtra Marquis pantalón, clasifica por CÓDIGO y devuelve métricas
    por programa: totales (uds/S/margen), por ventana B/C/D, economics unitarios.
    """
    df = pd.read_excel(cfg["ruta_bd_modelo"], sheet_name="BD",
                       usecols=["Periodo", "Semana", "Linea", "Marca", "CodModelo",
                                "Modelo", "Color", "Talla", "VtaUnd", "VtaSMF",
                                "Contr", "PBxVtaUnd", "PVxVtaUnd"])
    df = df[df["Marca"].astype(str).str.upper().str.contains(cfg["marca"], na=False)]
    df = df[df["Linea"].astype(str).str.upper().str.contains(cfg["linea_filtro"], na=False)]
    for c in ["VtaUnd", "VtaSMF", "Contr", "PBxVtaUnd", "PVxVtaUnd"]:
        df[c] = _num(df[c])

    # Descuento promedio de la categoría (pantalón Marquis), ponderado por unidades.
    # Base = precio lista (PBxVtaUnd). Informativo: VtaSMF ya viene con dscto incorporado.
    _pb_cat, _pv_cat = df["PBxVtaUnd"].sum(), df["PVxVtaUnd"].sum()
    dscto_categoria = float(1 - _pv_cat / _pb_cat) if _pb_cat else 0.0

    df["prog"] = df["CodModelo"].map(lambda c: classify_by_code(c, cfg["codigos"]))
    df = df[df["prog"].notna()].copy()

    rows, economics = [], {}
    for prog, d in df.groupby("prog"):
        vu, vs, ct = d["VtaUnd"].sum(), d["VtaSMF"].sum(), d["Contr"].sum()
        pb = d["PBxVtaUnd"].sum()
        rec = {"programa": prog, "uds_total": vu, "venta_sol": vs, "margen_sol": ct,
               "precio_u": round(vs / vu, 1) if vu else 0.0,
               "dscto_vs_lista": round(1 - vs / pb, 3) if pb else 0.0}
        for letra, per in cfg["ventana_periodo"].items():
            dd = d[d["Periodo"] == per]
            rec[f"uds_{letra}"] = dd["VtaUnd"].sum()
            rec[f"venta_{letra}"] = dd["VtaSMF"].sum()
        rows.append(rec)
        economics[prog] = {"precio_u": (vs / vu) if vu else 0.0,
                           "margen_u": (ct / vu) if vu else 0.0,
                           "costo_u": ((vs - ct) / vu) if vu else 0.0}  # venta neta − margen

    por_programa = pd.DataFrame(rows).sort_values("uds_total", ascending=False).reset_index(drop=True)
    curva = df.pivot_table(index="prog", columns="Periodo", values="VtaUnd",
                           aggfunc="sum", fill_value=0).astype(int)

    dp = por_programa.set_index("programa")["uds_total"]
    captura_chain = float(dp.get("DARIO", 0) / dp.get("DIEGOL", 1)) if dp.get("DIEGOL", 0) else 0.0

    return {"por_programa": por_programa, "economics": economics,
            "curva": curva, "captura_chain": captura_chain,
            "dscto_categoria": dscto_categoria, "raw": df}


def agotamiento_desde_wide(cfg, programa):
    """Agotamiento a la fecha de un programa (wide): vta_acum / (vta_acum + stock)."""
    df = pd.read_excel(cfg["ruta_profundidad"], sheet_name="Base",
                       usecols=["Cód. Prod.", "Vta. Unidades", "STOCK ACTUAL (UNIDADES)"])
    df["Cód. Prod."] = df["Cód. Prod."].astype(str).str.strip()
    codes = cfg["codigos"].get(programa.upper(), [])
    d = df[df["Cód. Prod."].isin(codes)]
    vta = _num(d["Vta. Unidades"]).sum()
    stk = _num(d["STOCK ACTUAL (UNIDADES)"]).sum()
    return float(vta / (vta + stk)) if (vta + stk) > 0 else 0.0


# ─────────────────────────────────────────────────────────────
#  RUTA A — Dario (ranking de tiendas para rollout)
# ─────────────────────────────────────────────────────────────

def cargar_profundidad_tienda(cfg):
    """
    Por (programa, tienda) a lo largo de los snapshots semanales:
      - vta_sem  = SUMA de '<tienda> Vta' (venta real, ~7 semanas)
      - stock_prom = PROMEDIO de '<tienda> Stk' (stock promedio del periodo)
    Match por código. Excluye canales no-retail. Sin prorrateo.
    """
    allcodes = {c: prog for prog, cs in cfg["codigos"].items() for c in cs}
    excluir = {x.upper() for x in cfg.get("tiendas_excluir", [])}
    vta_acc = {}        # (prog, tienda) -> venta total (suma SKUs × semanas)
    stk_snap = {}       # (prog, tienda) -> lista de totales por snapshot
    n_sem = 0
    for f in cfg["rutas_profundidad"]:
        df = pd.read_excel(f, sheet_name="Base")
        df["Cód. Prod."] = df["Cód. Prod."].astype(str).str.strip()
        d = df[df["Cód. Prod."].isin(allcodes)]
        if d.empty:
            continue
        n_sem += 1
        vta_cols = [c for c in df.columns if str(c).endswith(" Vta")]
        stk_cols = [c for c in df.columns if str(c).endswith(" Stk")]
        snap_stk = {}   # (prog, tienda) -> stock total de ESTE snapshot (suma SKUs)
        for _, row in d.iterrows():
            prog = allcodes[row["Cód. Prod."]]
            for vc in vta_cols:
                t = vc[:-4].strip()
                if t.upper() in excluir:
                    continue
                v = pd.to_numeric(row[vc], errors="coerce")
                if pd.notna(v) and v != 0:        # negativos = devoluciones (netean)
                    vta_acc[(prog, t)] = vta_acc.get((prog, t), 0.0) + float(v)
            for sc in stk_cols:
                t = sc[:-4].strip()
                if t.upper() in excluir:
                    continue
                s = pd.to_numeric(row[sc], errors="coerce")
                if pd.notna(s):
                    # suma los SKUs del programa dentro del mismo snapshot
                    snap_stk[(prog, t)] = snap_stk.get((prog, t), 0.0) + float(s)
        for k, v in snap_stk.items():
            stk_snap.setdefault(k, []).append(v)
    keys = set(vta_acc) | set(stk_snap)
    rows = [{"prog": k[0], "tienda": k[1],
             "vta_sem": round(vta_acc.get(k, 0.0), 1),
             # promedio de los totales por snapshot (no por SKU×snapshot)
             "stock_prom": round(np.mean(stk_snap[k]), 1) if k in stk_snap else 0.0}
            for k in keys]
    out = pd.DataFrame(rows)
    out.attrs["n_semanas"] = n_sem
    return out


def productividad_stock(por_tienda):
    """
    Productividad = venta / stock a nivel cadena para Diegol y Dario, y el factor
    de corrección de stock. Demuestra si el Dario está sub-comprado (alta
    productividad + bajo stock ⇒ su captura cruda subestima el potencial).
    """
    res = {}
    for prog in ["DIEGOL", "DARIO"]:
        d = por_tienda[por_tienda.prog == prog]
        v, s = d["vta_sem"].sum(), d["stock_prom"].sum()
        res[prog] = {"vta": float(v), "stock_prom": float(s),
                     "productividad": float(v / s) if s else 0.0}
    fac = (res["DARIO"]["productividad"] / res["DIEGOL"]["productividad"]) \
        if res["DIEGOL"]["productividad"] else 0.0
    ratio_stock = (res["DIEGOL"]["stock_prom"] / res["DARIO"]["stock_prom"]) \
        if res["DARIO"]["stock_prom"] else 0.0
    return {"diegol": res["DIEGOL"], "dario": res["DARIO"],
            "factor_productividad": fac, "ratio_stock": ratio_stock}


def ranking_captura(por_tienda, cfg):
    """
    Ranking de tiendas por captura Dario/Diegol (venta real multi-semana).
    Normaliza por exhibición: en tiendas con pasarela el Diegol tiene doble
    exhibición → se divide su venta entre las exhibiciones antes de comparar.
    Incluye stock/productividad. Solo cuentan tiendas con Diegol ≥ umbral.
    """
    pasarela = {t.upper() for t in cfg.get("pasarela_tiendas", [])}
    n_exh = cfg.get("exhibiciones_diegol_pasarela", 2)
    die = por_tienda[por_tienda.prog == "DIEGOL"].set_index("tienda")
    dar = por_tienda[por_tienda.prog == "DARIO"].set_index("tienda")
    comunes = die.index.intersection(dar.index)
    rows = []
    for t in comunes:
        dg = float(die.loc[t, "vta_sem"])
        if dg < cfg["min_uds_diegol_tienda"]:
            continue
        da = max(float(dar.loc[t, "vta_sem"]), 0.0)
        tiene_pas = t.upper() in pasarela
        dg_norm = dg / n_exh if tiene_pas else dg
        s_dg, s_da = float(die.loc[t, "stock_prom"]), float(dar.loc[t, "stock_prom"])
        rows.append({"tienda": t, "pasarela": tiene_pas,
                     "diegol_uds": round(dg, 1), "diegol_norm": round(dg_norm, 1),
                     "dario_uds": round(da, 1),
                     "captura_cruda": round(da / dg, 3) if dg else 0.0,
                     "captura_norm_exh": round(da / dg_norm, 3) if dg_norm else 0.0,
                     "stock_diegol": round(s_dg, 1), "stock_dario": round(s_da, 1),
                     "prod_diegol": round(dg / s_dg, 2) if s_dg else 0.0,
                     "prod_dario": round(da / s_da, 2) if s_da else 0.0})
    return pd.DataFrame(rows).sort_values("captura_norm_exh", ascending=False).reset_index(drop=True)


def uplift_exhibicion(por_tienda, cfg):
    """
    Factor de uplift por exhibición a nivel cadena (7 sem): cuánto sube la captura
    al normalizar la doble exhibición del Diegol en tiendas con pasarela.
    uplift = captura_normalizada / captura_cruda.
    """
    pasarela = {t.upper() for t in cfg.get("pasarela_tiendas", [])}
    n_exh = cfg.get("exhibiciones_diegol_pasarela", 2)
    die = por_tienda[por_tienda.prog == "DIEGOL"].set_index("tienda")["vta_sem"]
    dar = por_tienda[por_tienda.prog == "DARIO"].set_index("tienda")["vta_sem"]
    com = die.index.intersection(dar.index)
    Dar = float(dar[com].clip(lower=0).sum())
    Die_raw = float(die[com].sum())
    Die_norm = float(sum((die[t] / n_exh if t.upper() in pasarela else die[t]) for t in com))
    cap_raw = Dar / Die_raw if Die_raw else 0.0
    cap_norm = Dar / Die_norm if Die_norm else 0.0
    return {"captura_cruda_7sem": cap_raw, "captura_norm_7sem": cap_norm,
            "uplift": (cap_norm / cap_raw) if cap_raw else 1.0}


def benchmark_rollout(rank, cfg):
    """Captura (normalizada por exhibición) de las mejores tiendas, ponderada por Diegol."""
    top = rank.head(cfg["n_top_tiendas"]).copy()
    w = top["diegol_norm"]
    cap = float(np.average(top["captura_norm_exh"], weights=w)) if w.sum() > 0 else 0.0
    return cap, top


def perfil_tiendas(top_tiendas, cfg):
    """Describe tiendas top con cluster afinidad. Falla suave."""
    perfil = pd.DataFrame({"tienda": top_tiendas["tienda"].tolist()})
    try:
        from afinidad_engine import load_tienda_data, cluster_tiendas, _load_config
        afcfg = _load_config()
        df_long, _ = load_tienda_data(cfg["ruta_profundidad"], afcfg)
        clusters_df, _, _ = cluster_tiendas(df_long, afcfg)
        perfil = perfil.merge(
            clusters_df[["tienda", "cluster_id", "cluster_nombre", "vta_total"]],
            on="tienda", how="left")
    except Exception as e:  # noqa: BLE001
        perfil["cluster_nombre"] = f"(afinidad no disponible: {e})"
    return perfil


def proyectar_dario(dimension, cfg, prod_stock=None, uplift_exh=1.0,
                    captura_base_norm=None, captura_techo_norm=None):
    """
    Dario = toda temporada (flujo). venta = Diegol × captura.

    Rango de captura — los TRES anclados en datos reales (sin punto medio arbitrario):
      - PISO  = captura cadena temporada completa × uplift exhibición (conservador, full season).
      - BASE  = captura reciente normalizada por exhibición a nivel cadena (7 sem) — el dato típico.
      - TECHO = captura normalizada de las MEJORES tiendas (7 sem) — lo alcanzable real,
                respeta la premisa del brief (no es 100%/reemplazo 1:1).
    Los tres se castigan después por precio (factor_precio). Carga = venta / agotamiento_dario.
    """
    dp = dimension["por_programa"].set_index("programa")
    diegol_uds = float(dp.loc["DIEGOL", "uds_total"])
    eco = dimension["economics"]["DARIO"]

    cap_cruda = dimension["captura_chain"]
    cap_piso = min(cap_cruda * uplift_exh, 1.0)                  # full season conservador
    cap_base = min(captura_base_norm or cap_piso, 1.0)          # reciente cadena normalizada
    cap_techo = min(captura_techo_norm or 1.0, 1.0)             # reciente mejores tiendas normalizada

    # Castigo por precio: el Dario debió estar a 99.99 (paridad Diegol) y no a 79.99.
    # Subir a paridad (+pct) reduce volumen según elasticidad → factor sobre captura.
    pct_subida = (cfg["precio_vigente_diegol"] / cfg["precio_vigente_dario"]) - 1
    factor_precio = max(1 + cfg.get("elasticidad_precio_dario", 0.0) * pct_subida, 0.0)
    cap_piso *= factor_precio
    cap_base *= factor_precio
    cap_techo *= factor_precio

    agot = cfg["agotamiento_dario"]
    if agot is None:
        agot = agotamiento_desde_wide(cfg, "DARIO")
    agot = max(agot, 0.01)

    # Precio de venta: paridad (VtaSMF/u del Diegol) si se reprecia el Dario a 99.99.
    precio_venta = eco["precio_u"]
    margen_venta = eco["margen_u"]
    if cfg.get("valorizar_dario_paridad"):
        precio_venta = dimension["economics"]["DIEGOL"]["precio_u"]   # ~72.2 (paridad)
        margen_venta = precio_venta - eco["costo_u"]                  # mismo costo, mayor margen

    def escenario(cap):
        vu = diegol_uds * cap
        return {"captura": cap, "venta_uds": vu,
                "venta_sol": vu * precio_venta, "venta_margen": vu * margen_venta,
                "carga_uds": vu / agot}

    piso, base, techo = escenario(cap_piso), escenario(cap_base), escenario(cap_techo)
    return {
        "programa": "DARIO", "es_flujo": True, "agotamiento": agot,
        "captura_cruda": cap_cruda, "uplift_exhibicion": uplift_exh,
        "factor_precio": factor_precio,
        "captura_piso": cap_piso, "captura_base": cap_base, "captura_techo": cap_techo,
        "prod_stock": prod_stock,
        # default/headline = base
        "venta_uds": base["venta_uds"], "venta_sol": base["venta_sol"],
        "venta_margen": base["venta_margen"], "carga_uds": base["carga_uds"],
        "escenarios": {"piso": piso, "base": base, "techo": techo},
    }


# ─────────────────────────────────────────────────────────────
#  RUTA B — Bremen / Bronco (manual × agotamiento eventual)
# ─────────────────────────────────────────────────────────────

def distribucion_por_tienda(cfg):
    """
    Distribución escalonada de carga por tienda según el volumen de venta de la
    línea pantalón Marquis (suma real de los 7 snapshots). Terciles de venta →
    carga alto/medio/bajo (CONFIG['distribucion_tiers']). Incluye la tienda
    virtual si incluir_virtual_distribucion=True.
    Compartida por Bremen y Bronco.
    """
    excluir = set() if cfg.get("incluir_virtual_distribucion") else \
        {x.upper() for x in cfg.get("tiendas_excluir", [])}
    acc = {}
    for f in cfg["rutas_profundidad"]:
        df = pd.read_excel(f, sheet_name="Base")
        df = df[df["Marca"].astype(str).str.upper().str.contains(cfg["marca"], na=False)]
        df = df[df["Línea"].astype(str).str.upper().str.contains(cfg["linea_filtro"], na=False)]
        vta_cols = [c for c in df.columns if str(c).endswith(" Vta")]
        sums = df[vta_cols].apply(lambda s: _num(s)).sum()
        for vc, val in sums.items():
            t = vc[:-4].strip()
            if t.upper() in excluir:
                continue
            acc[t] = acc.get(t, 0.0) + float(val)
    s = pd.Series(acc)
    s = s[s > 0].sort_values(ascending=False)

    tiers = cfg["distribucion_tiers"]
    q_alto, q_medio = s.quantile(2 / 3), s.quantile(1 / 3)

    def tier(v):
        return "alto" if v >= q_alto else ("medio" if v >= q_medio else "bajo")

    dist = pd.DataFrame({"tienda": s.index, "vta_linea": s.values.round(0)})
    dist["tier"] = dist["vta_linea"].map(tier)
    dist["carga"] = dist["tier"].map(tiers)
    return dist


def proyeccion_manual(programa, dimension, cfg, dist=None):
    """
    venta = carga × agotamiento. Soporta dos modos:
      - distribución escalonada (dist por tienda) si pc['usar_distribucion'].
      - carga_por_tienda × n_tiendas (flat) en caso contrario.
    Economics de BD; Bronco hereda Bremen.
    """
    pc = cfg[programa.lower()]
    agot = pc.get("agotamiento", 0.609)
    ventana = next((k for k, v in cfg["ventana_programa"].items() if v == programa.upper()), "—")

    if pc.get("usar_distribucion") and dist is not None:
        carga = float(dist["carga"].sum())
        n_t = int(len(dist))
        detalle = dist.copy()
        detalle["venta_proy_uds"] = (detalle["carga"] * agot).round(1)
    else:
        carga_pt, n_t = pc.get("carga_por_tienda"), pc.get("n_tiendas")
        if carga_pt is None or n_t is None:
            return {"programa": programa.upper(), "es_flujo": False, "ventana": ventana,
                    "carga_uds": 0.0, "venta_uds": 0.0, "venta_sol": 0.0,
                    "venta_margen": 0.0, "agotamiento": agot, "sin_input": True}
        carga = float(carga_pt) * float(n_t)
        detalle = None

    venta_uds = carga * agot
    eco = dimension["economics"].get(programa.upper())
    if not eco or eco["precio_u"] == 0:
        eco = dimension["economics"].get("BREMEN", {"precio_u": 0.0, "margen_u": 0.0})

    res = {"programa": programa.upper(), "es_flujo": False, "ventana": ventana,
           "n_tiendas": int(n_t), "carga_uds": carga, "agotamiento": agot,
           "venta_uds": venta_uds, "venta_sol": venta_uds * eco["precio_u"],
           "venta_margen": venta_uds * eco["margen_u"], "detalle": detalle,
           "sin_input": False}

    if programa.upper() == "BREMEN" and pc.get("compra_inicial_uds"):
        dp = dimension["por_programa"].set_index("programa")
        if "BREMEN" in dp.index:
            res["agotamiento_real_hist"] = float(dp.loc["BREMEN", "uds_total"]) / float(pc["compra_inicial_uds"])
    return res


# ─────────────────────────────────────────────────────────────
#  CIERRE — Mitigación
# ─────────────────────────────────────────────────────────────

def cuadro_mitigacion(dimension, proy_dario, proy_bremen, proy_bronco, escenario_dario="conservador"):
    """Diegol (temporada completa) vs reemplazo total → caída neta en uds/S/margen."""
    dp = dimension["por_programa"].set_index("programa")
    diegol = {"uds": float(dp.loc["DIEGOL", "uds_total"]),
              "sol": float(dp.loc["DIEGOL", "venta_sol"]),
              "margen": float(dp.loc["DIEGOL", "margen_sol"])}

    esc = proy_dario["escenarios"][escenario_dario]
    dario = {"programa": "DARIO", "venta_uds": esc["venta_uds"],
             "venta_sol": esc["venta_sol"], "venta_margen": esc["venta_margen"],
             "supuesto": f"captura {esc['captura']:.0%}"}
    proy_bremen = {**proy_bremen, "supuesto": f"agotamiento {proy_bremen['agotamiento']:.0%}"}
    proy_bronco = {**proy_bronco, "supuesto": f"agotamiento {proy_bronco['agotamiento']:.0%}"}
    reemplazos = [dario, proy_bremen, proy_bronco]
    rep = {k: sum(r[f"venta_{k}"] for r in reemplazos) for k in ["uds", "sol", "margen"]}

    rows = [{"concepto": "Diegol (baseline a defender)", **diegol,
             "supuesto": "real", "pct_del_hueco": np.nan}]
    for r in reemplazos:
        rows.append({"concepto": f"+ {r['programa']}", "supuesto": r["supuesto"],
                     "uds": r["venta_uds"], "sol": r["venta_sol"], "margen": r["venta_margen"],
                     "pct_del_hueco": (r["venta_uds"] / diegol["uds"]) if diegol["uds"] else 0})
    rows.append({"concepto": "Reemplazo total", "supuesto": "—", **rep,
                 "pct_del_hueco": (rep["uds"] / diegol["uds"]) if diegol["uds"] else 0})
    rows.append({"concepto": "CAÍDA NETA (Diegol − reemplazo)", "supuesto": "—",
                 "uds": diegol["uds"] - rep["uds"], "sol": diegol["sol"] - rep["sol"],
                 "margen": diegol["margen"] - rep["margen"],
                 "pct_del_hueco": 1 - ((rep["uds"] / diegol["uds"]) if diegol["uds"] else 0)})
    df = pd.DataFrame(rows, columns=["concepto", "supuesto", "uds", "sol", "margen",
                                     "pct_del_hueco"])
    df["escenario_dario"] = escenario_dario
    return df


# ─────────────────────────────────────────────────────────────
#  PITCH (para Planificación → apertura de presupuesto)
# ─────────────────────────────────────────────────────────────

def construir_pitch(cfg, dimension, prod, uex, proy_dario,
                    proy_bremen, proy_bronco, mits):
    """
    Arma los bloques del pitch como lista de (titulo, DataFrame). Tres escenarios
    de compra (piso/base/techo) con cubicaje (uds/tienda), captura por programa,
    variables analizadas, productividad de stock y el pedido de presupuesto.
    """
    dp = dimension["por_programa"].set_index("programa")
    eco = dimension["economics"]
    diegol_uds = float(dp.loc["DIEGOL", "uds_total"])
    diegol_sol = float(dp.loc["DIEGOL", "venta_sol"])
    diegol_mar = float(dp.loc["DIEGOL", "margen_sol"])

    n_t = int(proy_bremen["n_tiendas"])           # 30 + virtual
    agot_d = proy_dario["agotamiento"]
    costo_d = eco["DARIO"]["costo_u"]
    costo_b = eco.get("BREMEN", {}).get("costo_u", 0.0)

    pas = cfg.get("pasarela_tiendas", [])
    n_pas = len(pas)
    n_sin = n_t - n_pas

    # ── Bloque contexto ──
    b_ctx = pd.DataFrame({"Punto": [
        "Situación", "Hueco a cubrir", "Reemplazo", "Universo", "Pedido"
    ], "Detalle": [
        "El Diegol (bestseller, cód 27390791) sale de Marquis → Kenneth Stevens.",
        f"{diegol_uds:,.0f} uds · S/{diegol_sol:,.0f} venta · S/{diegol_mar:,.0f} margen (temporada completa).",
        "Dario (flujo, toda temporada) + Bremen (ventana D/Feb) + Bronco (ventana B/Dic).",
        f"{n_t} tiendas ({n_pas} con pasarela / {n_sin} sin pasarela).",
        "Abrir presupuesto de compra para los 3 programas — 3 escenarios.",
    ]})

    # ── Bloque variables ──
    b_var = pd.DataFrame({
        "Variable": ["Captura cadena (cruda)", "Exhibición (pasarela)", "Profundidad de stock",
                     "Precio", "Descuento categoría", "Valorización venta"],
        "Hallazgo": [
            f"Dario vende {proy_dario['captura_cruda']:.0%} de lo del Diegol (temporada completa).",
            f"{n_pas} tiendas: Diegol con doble exhibición (mesa+pasarela), Dario una sola. "
            f"Normalizado, captura ×{uex['uplift']:.2f}.",
            f"Dario {prod['factor_productividad']:.1f}× más productivo/stock; cobertura "
            f"Dario {prod['dario']['stock_prom']/(prod['dario']['vta']/7):.0f} sem (ajustado) "
            f"vs Diegol {prod['diegol']['stock_prom']/(prod['diegol']['vta']/7):.0f} sem (sobre-stock).",
            f"Dario salió a S/{cfg['precio_vigente_dario']} por error (debió ser S/{cfg['precio_vigente_diegol']}, paridad Diegol). Subir a paridad = +{cfg['precio_vigente_diegol']/cfg['precio_vigente_dario']-1:.0%}.",
            f"{dimension['dscto_categoria']:.0%} vs precio lista (informativo).",
            "VtaSMF (venta neta), igual para todos los programas.",
        ],
        "Implicancia": [
            "Punto de partida.",
            "Sube el piso de captura del Dario (corrección sólida y medible).",
            "Dario con cobertura sana (18.6 sem) y algo más eficiente (1.5×); upside por stock es modesto, no decisivo.",
            f"Captura CASTIGADA ×{proy_dario['factor_precio']:.2f} (elasticidad −1.0) por reprecio a paridad.",
            "No se aplica extra (VtaSMF ya lo incorpora).",
            "Comparación apples-to-apples con el Diegol.",
        ]})

    # ── Bloque productividad ──
    b_prod = pd.DataFrame([
        {"Programa": "DIEGOL", "Venta 7sem": round(prod["diegol"]["vta"]),
         "Stock prom": round(prod["diegol"]["stock_prom"]),
         "Cobertura sem": round(prod["diegol"]["stock_prom"] / (prod["diegol"]["vta"] / 7), 1),
         "Productividad (v/s)": round(prod["diegol"]["productividad"], 2)},
        {"Programa": "DARIO", "Venta 7sem": round(prod["dario"]["vta"]),
         "Stock prom": round(prod["dario"]["stock_prom"]),
         "Cobertura sem": round(prod["dario"]["stock_prom"] / (prod["dario"]["vta"] / 7), 1),
         "Productividad (v/s)": round(prod["dario"]["productividad"], 2)},
    ])

    # ── Bloque captura por programa ──
    e = proy_dario["escenarios"]
    b_cap = pd.DataFrame([
        {"Programa": "DARIO (piso)", "Supuesto": f"captura {e['piso']['captura']:.0%}",
         "Venta proy uds": round(e["piso"]["venta_uds"]), "% del hueco": f"{e['piso']['venta_uds']/diegol_uds:.0%}"},
        {"Programa": "DARIO (base)", "Supuesto": f"captura {e['base']['captura']:.0%}",
         "Venta proy uds": round(e["base"]["venta_uds"]), "% del hueco": f"{e['base']['venta_uds']/diegol_uds:.0%}"},
        {"Programa": "DARIO (techo)", "Supuesto": f"captura {e['techo']['captura']:.0%}",
         "Venta proy uds": round(e["techo"]["venta_uds"]), "% del hueco": f"{e['techo']['venta_uds']/diegol_uds:.0%}"},
        {"Programa": "BREMEN", "Supuesto": f"agotamiento {proy_bremen['agotamiento']:.0%}",
         "Venta proy uds": round(proy_bremen["venta_uds"]), "% del hueco": f"{proy_bremen['venta_uds']/diegol_uds:.0%}"},
        {"Programa": "BRONCO", "Supuesto": f"agotamiento {proy_bronco['agotamiento']:.0%}",
         "Venta proy uds": round(proy_bronco["venta_uds"]), "% del hueco": f"{proy_bronco['venta_uds']/diegol_uds:.0%}"},
    ])

    # ── Bloque propuesta de compra (3 escenarios, cubicaje = uds/tienda) ──
    filas = []
    for esc in ["piso", "base", "techo"]:
        venta_d = e[esc]["venta_uds"]
        compra_d = venta_d / agot_d
        cub_d = compra_d / n_t
        compra_b = proy_bremen["carga_uds"]
        compra_o = proy_bronco["carga_uds"]
        prog_rows = [
            ("DARIO", n_t, cub_d, compra_d, costo_d),
            ("BREMEN", proy_bremen["n_tiendas"], compra_b / proy_bremen["n_tiendas"], compra_b, costo_b),
            ("BRONCO", proy_bronco["n_tiendas"], compra_o / proy_bronco["n_tiendas"], compra_o, costo_b),
        ]
        tot_u = tot_s = 0
        for nombre, nt, cub, compra, costo in prog_rows:
            inv = compra * costo
            tot_u += compra; tot_s += inv
            filas.append({"Escenario": esc.upper(), "Programa": nombre, "N° tiendas": nt,
                          "Cubicaje (uds/tienda)": round(cub), "Compra uds": round(compra),
                          "Costo unit S/": round(costo, 1), "Inversión S/": round(inv)})
        filas.append({"Escenario": esc.upper(), "Programa": "TOTAL", "N° tiendas": "",
                      "Cubicaje (uds/tienda)": "", "Compra uds": round(tot_u),
                      "Costo unit S/": "", "Inversión S/": round(tot_s)})
    b_compra = pd.DataFrame(filas)

    # ── Bloque cubicaje por tienda (loading Bremen/Bronco tiered) ──
    det = proy_bremen.get("detalle")
    if det is not None:
        tier_ct = det.groupby("tier")["carga"].agg(["count", "first"]).reset_index()
        b_cub = tier_ct.rename(columns={"tier": "Tier", "count": "N° tiendas",
                                        "first": "Cubicaje Bremen/Bronco (uds/tienda)"})
        b_cub = b_cub.sort_values("Cubicaje Bremen/Bronco (uds/tienda)", ascending=False)
    else:
        b_cub = pd.DataFrame()

    # ── Bloque mitigación 3 escenarios ──
    mit_rows = []
    for esc in ["piso", "base", "techo"]:
        m = mits[esc]
        rep = m[m["concepto"] == "Reemplazo total"].iloc[0]
        caida = m[m["concepto"].str.startswith("CAÍDA")].iloc[0]
        mit_rows.append({"Escenario": esc.upper(),
                         "Reemplazo uds": round(rep["uds"]), "% hueco cubierto": f"{rep['pct_del_hueco']:.0%}",
                         "Caída neta uds": round(caida["uds"]), "Caída S/": round(caida["sol"]),
                         "Caída margen S/": round(caida["margen"])})
    b_mit = pd.DataFrame(mit_rows)

    # ── Bloque pedido ──
    ped_rows = []
    for esc in ["piso", "base", "techo"]:
        sub = b_compra[(b_compra["Escenario"] == esc.upper()) & (b_compra["Programa"] == "TOTAL")].iloc[0]
        venta_total = (e[esc]["venta_sol"] + proy_bremen["venta_sol"] + proy_bronco["venta_sol"])
        rep = mits[esc][mits[esc]["concepto"] == "Reemplazo total"].iloc[0]
        ped_rows.append({"Escenario": esc.upper(),
                         "Compra total uds": sub["Compra uds"], "Inversión total S/": sub["Inversión S/"],
                         "Venta proy S/": round(venta_total), "% hueco cubierto": f"{rep['pct_del_hueco']:.0%}",
                         "Recomendación": "◀ RECOMENDADO" if esc == "base" else ""})
    b_ped = pd.DataFrame(ped_rows)

    return [
        ("PITCH — APERTURA DE PRESUPUESTO · TRANSICIÓN PANTALÓN MARQUIS (Showroom Mayo 2026)", b_ctx),
        ("1. VARIABLES ANALIZADAS", b_var),
        ("2. PRODUCTIVIDAD DE STOCK (evidencia: el Dario está sub-comprado)", b_prod),
        ("3. CAPTURA ESPERADA POR PROGRAMA (cuánto del hueco del Diegol tapa cada uno)", b_cap),
        ("4. PROPUESTA DE COMPRA — 3 ESCENARIOS (cubicaje = uds/tienda)", b_compra),
        ("5. CUBICAJE POR TIENDA — loading Bremen/Bronco (por tier de venta)", b_cub),
        ("6. IMPACTO / MITIGACIÓN POR ESCENARIO", b_mit),
        ("7. EL PEDIDO (presupuesto de compra)", b_ped),
    ]


# ─────────────────────────────────────────────────────────────
#  EXPORT
# ─────────────────────────────────────────────────────────────

def exportar_excel(cfg, dimension, rank, perfil, proy_dario,
                   proy_bremen, proy_bronco, mits, prod=None, uex=None):
    resumen = []
    for r in [proy_dario, proy_bremen, proy_bronco]:
        fila = {"programa": r["programa"],
                "tipo": "flujo (toda temporada)" if r.get("es_flujo") else f"ventana {r.get('ventana','—')}",
                "carga_uds": round(r["carga_uds"]),
                "agotamiento": round(r["agotamiento"], 3),
                "venta_proy_uds": round(r["venta_uds"]),
                "venta_proy_sol": round(r["venta_sol"]),
                "venta_proy_margen": round(r["venta_margen"])}
        if r.get("es_flujo"):
            fila["captura_piso"] = round(r["captura_piso"], 3)
            fila["captura_base"] = round(r["captura_base"], 3)
            fila["captura_techo"] = round(r["captura_techo"], 3)
        resumen.append(fila)
    compra = pd.DataFrame(resumen)

    # Productividad de stock (justifica el rango de captura del Dario)
    prod = proy_dario.get("prod_stock") or {}
    prod_df = pd.DataFrame([
        {"programa": "DIEGOL", **prod.get("diegol", {})},
        {"programa": "DARIO", **prod.get("dario", {})},
    ]) if prod else pd.DataFrame()
    if not prod_df.empty:
        prod_df = prod_df.round(2)

    # Detalle de distribución escalonada Bremen/Bronco por tienda
    dist_detalle = None
    if proy_bremen.get("detalle") is not None:
        d = proy_bremen["detalle"][["tienda", "vta_linea", "tier", "carga"]].copy()
        d = d.rename(columns={"carga": "carga_uds", "vta_linea": "vta_linea_7sem"})
        d["bremen_venta_uds"] = (d["carga_uds"] * proy_bremen["agotamiento"]).round(1)
        d["bronco_venta_uds"] = (d["carga_uds"] * proy_bronco["agotamiento"]).round(1)
        dist_detalle = d

    # Nota informativa: descuento promedio de la categoría (no se aplica; VtaSMF ya lo trae)
    nota_dscto = pd.DataFrame({
        "referencia": ["Descuento promedio categoría pantalón Marquis (vs precio lista)",
                       "Nota"],
        "valor": [round(dimension.get("dscto_categoria", 0.0), 3),
                  "Informativo: la venta usa VtaSMF, que ya incorpora el descuento real de cada programa."]})

    # Hoja de Supuestos (clave para leer el análisis con criterio)
    pvd, pvg = cfg.get("precio_vigente_dario", 0), cfg.get("precio_vigente_diegol", 0)
    gap = (1 - pvd / pvg) if pvg else 0
    cp = proy_dario
    supuestos = pd.DataFrame({"tema": [
        "Diegol baseline",
        "Captura Dario (rango)",
        "Exhibición (pasarela)",
        "Corrección de stock",
        "Precio (efecto en captura)",
        "Venta valorizada",
        "Bremen",
        "Bronco",
        "Descuento categoría",
    ], "supuesto": [
        "Solo CodModelo 27390791 (PANT SPORT DIEGOL MQS COR TT), temporada completa: 9,546 uds.",
        f"Piso {cp.get('captura_piso',0):.0%} (cadena {cp.get('captura_cruda',0):.0%} × uplift exhibición) / "
        f"Base {cp.get('captura_base',0):.0%} (captura reciente normalizada real) / "
        f"Techo {cp.get('captura_techo',0):.0%} (mejores tiendas reales). Los 3 con castigo precio ×0.75.",
        f"En {len(cfg.get('pasarela_tiendas',[]))} tiendas el Diegol tiene doble exhibición (mesa + pasarela); "
        f"el Dario una sola. Normalizando (Diegol/2) la captura sube ×{cp.get('uplift_exhibicion',1):.2f}. Empuja arriba.",
        f"Dario {prod.get('factor_productividad',0):.1f}× más productivo por unidad de stock, "
        f"cobertura 18.6 sem (sana). Aporte modesto al alza; el techo lo sostiene la evidencia de mejores tiendas, no el stock.",
        f"Dario salió a S/{pvd} por error (debió ser S/{pvg}). Captura CASTIGADA ×{cp.get('factor_precio',1):.2f} "
        f"(elasticidad −1.0, reprecio a paridad +{pvg/pvd-1:.0%}). Empuja abajo, ya aplicado.",
        "VtaSMF (venta neta, ~73% del precio vigente), aplicada igual a todos los programas.",
        "Agotamiento 40% (su real histórico fue 60.9%; supuesto conservador). Ventana D.",
        "Agotamiento 60%; SIN historia propia, hereda precio/margen de Bremen — menor certeza. Ventana B.",
        f"{round(dimension.get('dscto_categoria',0)*100)}% vs precio lista; informativo, no aplicado (VtaSMF ya lo incorpora).",
    ]})

    # Pitch (primera hoja): bloques apilados con título
    pitch_blocks = construir_pitch(cfg, dimension, prod or {}, uex or {},
                                   proy_dario, proy_bremen, proy_bronco, mits)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        startr = 0
        for titulo, bdf in pitch_blocks:
            pd.DataFrame({titulo: []}).to_excel(w, sheet_name="Pitch", index=False, startrow=startr)
            startr += 1
            bdf.to_excel(w, sheet_name="Pitch", index=False, startrow=startr)
            startr += len(bdf) + 3
        supuestos.to_excel(w, sheet_name="Supuestos", index=False)
        dimension["por_programa"].to_excel(w, sheet_name="Dimension_Programas", index=False)
        nota_dscto.to_excel(w, sheet_name="Dimension_Programas", index=False,
                            startrow=len(dimension["por_programa"]) + 3)
        rank.to_excel(w, sheet_name="Ranking_Captura", index=False)
        if not prod_df.empty:
            prod_df.to_excel(w, sheet_name="Ranking_Captura", index=False,
                             startrow=len(rank) + 3)
        perfil.to_excel(w, sheet_name="Perfil_Tienda", index=False)
        compra.to_excel(w, sheet_name="Compra_Proyectada", index=False)
        if dist_detalle is not None:
            dist_detalle.to_excel(w, sheet_name="Distribucion_Bremen_Bronco", index=False)
        # Mitigación: piso / base / techo apiladas
        startr = 0
        for esc in ["piso", "base", "techo"]:
            mits[esc].to_excel(w, sheet_name="Mitigacion", index=False, startrow=startr)
            startr += len(mits[esc]) + 3
    with open(cfg["salida_excel"], "wb") as f:
        f.write(buf.getvalue())
    return cfg["salida_excel"]


# ─────────────────────────────────────────────────────────────
#  RUNNER
# ─────────────────────────────────────────────────────────────

def _pedir_carga(programa, cfg):
    pc = cfg[programa.lower()]
    if pc.get("carga_por_tienda") is not None and pc.get("n_tiendas") is not None:
        return
    ventana = next((k for k, v in cfg["ventana_programa"].items() if v == programa.upper()), "—")
    try:
        print(f"\n— Carga futura de {programa.upper()} (ventana {ventana}) —")
        cpt = input(f"  Carga por tienda (enter=0): ").strip()
        nt = input(f"  N° de tiendas (enter=0): ").strip()
        pc["carga_por_tienda"] = float(cpt) if cpt else 0.0
        pc["n_tiendas"] = float(nt) if nt else 0.0
        ag = input(f"  Agotamiento objetivo (enter={pc['agotamiento']}): ").strip()
        if ag:
            pc["agotamiento"] = float(ag)
    except (EOFError, KeyboardInterrupt):
        pc["carga_por_tienda"] = pc.get("carga_por_tienda") or 0.0
        pc["n_tiendas"] = pc.get("n_tiendas") or 0.0


def run(cfg=CONFIG, pedir_input=True):
    print("▶ Capa BD: dimensionando programas (match por código)…")
    dimension = dimensionar_programas(cfg)
    print(dimension["por_programa"].to_string(index=False))
    print(f"  Captura cadena temporada completa Dario/Diegol: {dimension['captura_chain']:.1%}")

    print("\n▶ Ruta A: captura real multi-semana + productividad de stock…")
    por_tienda = cargar_profundidad_tienda(cfg)
    rank = ranking_captura(por_tienda, cfg)
    cap_rollout, top = benchmark_rollout(rank, cfg)
    perfil = perfil_tiendas(top, cfg)
    prod = productividad_stock(por_tienda)
    uex = uplift_exhibicion(por_tienda, cfg)
    print(f"  Semanas: {por_tienda.attrs.get('n_semanas','?')} | "
          f"productividad Dario {prod['dario']['productividad']:.2f} vs "
          f"Diegol {prod['diegol']['productividad']:.2f} (factor {prod['factor_productividad']:.1f}×)")
    print(f"  Exhibición: captura cruda {uex['captura_cruda_7sem']:.0%} → "
          f"normalizada {uex['captura_norm_7sem']:.0%} (uplift {uex['uplift']:.2f}× por pasarela del Diegol)")

    proy_dario = proyectar_dario(dimension, cfg, prod_stock=prod, uplift_exh=uex["uplift"],
                                 captura_base_norm=uex["captura_norm_7sem"],
                                 captura_techo_norm=cap_rollout)
    e = proy_dario["escenarios"]
    print(f"  Dario PISO {e['piso']['captura']:.0%}→{e['piso']['venta_uds']:,.0f}u | "
          f"BASE {e['base']['captura']:.0%}→{e['base']['venta_uds']:,.0f}u | "
          f"TECHO {e['techo']['captura']:.0%}→{e['techo']['venta_uds']:,.0f}u")

    # Distribución escalonada compartida (si aplica)
    usa_dist = cfg["bremen"].get("usar_distribucion") or cfg["bronco"].get("usar_distribucion")
    dist = distribucion_por_tienda(cfg) if usa_dist else None
    if dist is not None:
        print(f"\n▶ Distribución escalonada ({len(dist)} tiendas): "
              f"{dist['tier'].value_counts().to_dict()} | carga total/programa: {int(dist['carga'].sum())} uds")
    if pedir_input and not cfg["bremen"].get("usar_distribucion"):
        _pedir_carga("BREMEN", cfg)
    if pedir_input and not cfg["bronco"].get("usar_distribucion"):
        _pedir_carga("BRONCO", cfg)

    print("\n▶ Ruta B: Bremen / Bronco…")
    proy_bremen = proyeccion_manual("BREMEN", dimension, cfg, dist=dist)
    proy_bronco = proyeccion_manual("BRONCO", dimension, cfg, dist=dist)
    print(f"  Bremen (agot {proy_bremen['agotamiento']:.0%}): carga {proy_bremen['carga_uds']:,.0f} → "
          f"venta {proy_bremen['venta_uds']:,.0f} uds")
    print(f"  Bronco (agot {proy_bronco['agotamiento']:.0%}): carga {proy_bronco['carga_uds']:,.0f} → "
          f"venta {proy_bronco['venta_uds']:,.0f} uds")

    print("\n▶ Mitigación (3 escenarios de Dario: piso / base / techo)…")
    mits = {esc: cuadro_mitigacion(dimension, proy_dario, proy_bremen, proy_bronco, esc)
            for esc in ["piso", "base", "techo"]}
    print(mits["base"].to_string(index=False))

    ruta = exportar_excel(cfg, dimension, rank, perfil, proy_dario,
                          proy_bremen, proy_bronco, mits, prod=prod, uex=uex)
    print(f"\n✅ Excel: {ruta}")
    return {"dimension": dimension, "rank": rank, "captura_rollout": cap_rollout,
            "perfil": perfil, "prod_stock": prod, "proy_dario": proy_dario,
            "proy_bremen": proy_bremen, "proy_bronco": proy_bronco,
            "mits": mits, "excel": ruta}


if __name__ == "__main__":
    run(CONFIG, pedir_input=True)
