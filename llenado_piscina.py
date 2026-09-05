"""
llenado_piscina.py — Abrir una compra a tienda (llenado de espacio físico)
==========================================================================

Caso: llenado de piscina (mueble/espacio físico) de Shorts Marquis y Camisas
M/C de Marquis, Navigata y Cacharel.

Input: presupuesto de compra EN SOLES A COSTO por producto/marca (lo da Franco).
Tarea: abrir esa compra a tienda.

Método (decidido con Franco):
  - Presupuesto por producto/marca (4 combos), a COSTO.
  - unidades_totales = presupuesto_costo / costo_unitario (de la BD).
  - Reparto a tienda PROPORCIONAL a la venta histórica de esa categoría por
    tienda (suma real de los snapshots de profundidad, sin prorrateo).
  - Universo = matriz Capi por marca (config_marca_tiendas.json, tiendas_code).

Reutiliza los aprendizajes del motor de transición (match real por tienda,
costo de la BD, sin prorrateo). Ver APRENDIZAJE_Motor_Transicion.md.
"""

import os
import io
import glob
import json
import numpy as np
import pandas as pd

_BASES = "/Users/francobarreto/Claude_Context/Ripley.md/Bases"
_PROJ = os.path.dirname(os.path.abspath(__file__))

CONFIG = {
    "ruta_bd": os.path.join(_BASES, "BD 17.11.25 al 24.05.26.xlsx"),
    "rutas_profundidad": sorted(glob.glob(os.path.join(_BASES, "Base al*.xlsx"))),
    # Pesos del reparto: para productos VERANIEGOS la profundidad reciente (invierno)
    # los subrepresenta. Usar la venta de verano por tienda (Data Micro, SKU×tienda).
    "ruta_ventas_hist": "/Users/francobarreto/Claude_Context/Ripley.md/Data Micro.xlsx",
    "matriz_marca": os.path.join(_PROJ, "config_marca_tiendas.json"),
    "tiendas_excluir": ["TV", "TV PI", "VTAS CORP", "FSF MAC LO", "ASIA"],
    # Plazas frías: no llevan productos veraniegos (shorts / camisas M/C). Se sacan del universo.
    "tiendas_excluir_universo": ["CAJ", "HYO", "JULIACA"],
    "fill_min": 0,   # piso mínimo de uds por tienda del universo (0 = puro proporcional)
    # Fuente del reparto de verano: parquet (verano completo, robusto, nivel marca).
    "usar_parquet_verano": True,
    "ruta_parquet": os.path.join(_PROJ, "data", "ly_venta_marca_tienda_semana.parquet"),
    # Verano peruano: dic (sem 48-52) + ene-feb (sem 1-9).
    "semanas_verano": {"2024": list(range(48, 53)), "2025": list(range(1, 10))},
    # Ponderador de CLIMA. OJO: los códigos están "cruzados" — peso por la CIUDAD REAL:
    #   CBT=Chiclayo, CHII=Chimbote, CHIC=Chiclayo2, SB=Salaverry, SI=SanBorja,
    #   SJL=SanIsidro, SLVR=SJLurigancho(Lima), LO=MegaPlaza.
    "ponderar_clima": True,
    "pesos_clima": {
        "PIU2": 3.0,                          # Piura — la más caliente
        "CBT": 2.5, "CHIC": 2.5,              # Chiclayo y Chiclayo 2
        "TRUJ": 2.0, "SB": 2.0,               # Trujillo y Salaverry
        "CHII": 1.8,                          # Chimbote
        "IQT": 2.2, "PUCALPA I": 2.2,         # selva caliente
        "ICA": 1.6,                           # costa sur cálida
        "AQP": 1.0, "CAY": 1.0,               # Arequipa (templada)
    },
    "peso_clima_default": 1.0,                # Lima y demás (incl. SLVR=SJL, LO=MegaPlaza)
    # Presupuesto de compra A COSTO por combo (lo entrega Franco). None = pedir al correr.
    "combos": [
        {"nombre": "Shorts Marquis",        "marca": "MARQUIS",  "linea": "SHORTS",      "presupuesto_costo": 160000, "costo_u_override": 40},
        {"nombre": "Camisas M/C Marquis",   "marca": "MARQUIS",  "linea": "CAMISAS M/C", "presupuesto_costo": 50000,  "costo_u_override": 42},
        {"nombre": "Camisas M/C Navigata",  "marca": "NAVIGATA", "linea": "CAMISAS M/C", "presupuesto_costo": 50000,  "costo_u_override": 42},
        {"nombre": "Camisas M/C Cacharel",  "marca": "CACHAREL", "linea": "CAMISAS M/C", "presupuesto_costo": 50000,  "costo_u_override": 42},
    ],
    "salida_excel": os.path.join(_BASES, "Llenado_Piscina_Marquis.xlsx"),
}


def _num(s):
    return pd.to_numeric(s, errors="coerce").fillna(0)


def costo_unitario(cfg, marca, linea):
    """Costo unitario (BD): Σ Costo / Σ VtaUnd para la marca×línea. Costo es total por fila."""
    bd = pd.read_excel(cfg["ruta_bd"], sheet_name="BD", usecols=["Marca", "Linea", "VtaUnd", "Costo"])
    bd["Marca"] = bd["Marca"].astype(str).str.upper()
    bd["Linea"] = bd["Linea"].astype(str).str.upper()
    for c in ["VtaUnd", "Costo"]:
        bd[c] = _num(bd[c])
    d = bd[(bd["Marca"].str.contains(marca.upper())) & (bd["Linea"] == linea.upper())]
    u = d["VtaUnd"].sum()
    return float(d["Costo"].sum() / u) if u else 0.0


def contribucion_unitaria(cfg, marca, linea):
    """Contribución (margen) unitaria (BD): Σ Contr / Σ VtaUnd para la marca×línea."""
    bd = pd.read_excel(cfg["ruta_bd"], sheet_name="BD", usecols=["Marca", "Linea", "VtaUnd", "Contr"])
    bd["Marca"] = bd["Marca"].astype(str).str.upper()
    bd["Linea"] = bd["Linea"].astype(str).str.upper()
    for c in ["VtaUnd", "Contr"]:
        bd[c] = _num(bd[c])
    d = bd[(bd["Marca"].str.contains(marca.upper())) & (bd["Linea"] == linea.upper())]
    u = d["VtaUnd"].sum()
    return float(d["Contr"].sum() / u) if u else 0.0


def ventas_por_tienda(cfg, marca, linea):
    """
    Venta REAL por tienda de la marca×línea: suma de '<tienda> Vta'. Para productos
    veraniegos usa la base de verano (cfg['ruta_ventas_hist'], Data Micro); si no,
    suma los snapshots de profundidad reciente. Excluye canales no-retail.
    Devuelve Series indexada por código de tienda.
    """
    excluir = {x.upper() for x in cfg["tiendas_excluir"]}
    fuentes = [cfg["ruta_ventas_hist"]] if cfg.get("ruta_ventas_hist") else cfg["rutas_profundidad"]
    acc = {}
    for f in fuentes:
        df = pd.read_excel(f, sheet_name="Base")
        df["Marca"] = df["Marca"].astype(str).str.upper()
        df["Línea"] = df["Línea"].astype(str).str.upper()
        d = df[(df["Marca"].str.contains(marca.upper())) & (df["Línea"] == linea.upper())]
        if d.empty:
            continue
        vcols = [c for c in df.columns if str(c).endswith(" Vta")]
        for _, row in d.iterrows():
            for vc in vcols:
                t = vc[:-4].strip()
                if t.upper() in excluir:
                    continue
                v = pd.to_numeric(row[vc], errors="coerce")
                if pd.notna(v) and v != 0:   # negativos = devoluciones (netean)
                    acc[t] = acc.get(t, 0.0) + float(v)
    return pd.Series(acc, dtype=float)


def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().upper()
    return "".join(s.split())


def ventas_verano_parquet(cfg, marca):
    """
    Venta de VERANO por tienda (código) desde el parquet last-year (marca×tienda×
    semana). Robusto (verano completo) pero a nivel marca (no línea). Mapea los
    nombres del parquet a los códigos de la matriz, sumando los dos Piura en PIU2.
    """
    df = pd.read_parquet(cfg["ruta_parquet"])
    sv = cfg["semanas_verano"]
    mask = pd.Series(False, index=df.index)
    for anio, sems in sv.items():
        mask = mask | ((df["Año"] == int(anio)) & (df["SemanaSola"].isin(sems)))
    d = df[mask & df["Marca"].astype(str).str.upper().str.contains(marca.upper())]
    vol = d.groupby("Sucursal")["VtaUnd"].sum()

    mt = json.load(open(cfg["matriz_marca"], encoding="utf-8"))
    k = [x for x in mt if x.upper() == marca.upper()][0]
    norm2code = {_norm(n): c for n, c in zip(mt[k]["tiendas"], mt[k]["tiendas_code"])}
    # parquet_norm → matriz_norm (variantes de nombre)
    overrides = {
        "PIURA": "PIURA", "PIURAII": "PIURA",          # ambos Piura → PIU2
        "CHICLAYOII": "CHICLAYO2", "RIPLEYCALLAO": "CALLAO",
        "PLAZALIMANORTE": "PLAZANORTE", "MEGAPLAZA": "MEGAPLAZA",
    }
    out = {}
    for name, v in vol.items():
        nn = _norm(name)
        nn = _norm(overrides.get(nn, nn))
        code = norm2code.get(nn)
        if code:
            out[code] = out.get(code, 0.0) + float(v)
    return pd.Series(out, dtype=float)


def matriz_tiendas(cfg, marca):
    """Códigos de tienda de la marca según la matriz Capi."""
    mt = json.load(open(cfg["matriz_marca"], encoding="utf-8"))
    k = [x for x in mt if x.upper() == marca.upper()]
    if not k:
        return []
    entry = mt[k[0]]
    return entry.get("tiendas_code") or entry.get("tiendas") or []


def abrir_compra(cfg, combo):
    """
    Abre la compra de un combo a tienda, proporcional a la venta histórica,
    restringido al universo de la matriz Capi.
    """
    marca, linea = combo["marca"], combo["linea"]
    presup = combo.get("presupuesto_costo") or 0.0
    cu = combo.get("costo_u_override") or costo_unitario(cfg, marca, linea)
    contr_u = contribucion_unitaria(cfg, marca, linea)
    uds_total = (presup / cu) if cu else 0.0

    if cfg.get("usar_parquet_verano"):
        ventas = ventas_verano_parquet(cfg, marca)   # robusto, nivel marca
    else:
        ventas = ventas_por_tienda(cfg, marca, linea)
    universo = matriz_tiendas(cfg, marca)
    # Sacar plazas frías del universo (no llevan veraniegos)
    excluir_univ = {x.upper() for x in cfg.get("tiendas_excluir_universo", [])}
    universo = [t for t in universo if t.upper() not in excluir_univ]

    # Pesos: venta histórica × peso de clima (si aplica) de las tiendas del universo
    v_univ = ventas[ventas.index.isin(universo)]
    pesos_clima = cfg.get("pesos_clima", {})
    pc_def = cfg.get("peso_clima_default", 1.0)

    def peso_clima(t):
        return pesos_clima.get(t, pc_def) if cfg.get("ponderar_clima") else 1.0

    # peso efectivo por tienda = venta × clima
    w_univ = {t: float(v_univ.get(t, 0.0)) * peso_clima(t) for t in universo}
    total_w = float(sum(w_univ.values()))
    fill_min = cfg.get("fill_min", 0)

    rows = []
    for t in universo:
        sv = float(v_univ.get(t, 0.0))
        share = (w_univ[t] / total_w) if total_w else 0.0
        uds = max(round(uds_total * share), fill_min if sv > 0 else 0)
        rows.append({"tienda": t, "venta_hist_uds": round(sv, 1),
                     "peso_clima": peso_clima(t),
                     "contrib_hist_S/": round(sv * contr_u),   # margen que generó la tienda
                     "share": round(share, 4), "uds_compra": int(uds),
                     "soles_compra": round(uds * cu),
                     "contrib_potencial_S/": round(uds * contr_u)})  # margen si se vende todo
    df = pd.DataFrame(rows).sort_values("uds_compra", ascending=False).reset_index(drop=True)
    sin_venta = [t for t in universo if float(v_univ.get(t, 0.0)) == 0]

    return {
        "combo": combo["nombre"], "marca": marca, "linea": linea,
        "presupuesto_costo": presup, "costo_u": cu, "contr_u": contr_u,
        "uds_total_teorico": round(uds_total), "uds_total_asignado": int(df["uds_compra"].sum()),
        "soles_asignado": int(df["soles_compra"].sum()),
        "contrib_potencial": int(df["contrib_potencial_S/"].sum()),
        "n_tiendas": len(universo), "n_sin_venta": len(sin_venta),
        "sin_venta": sin_venta, "detalle": df,
    }


def exportar_excel(cfg, resultados):
    resumen = pd.DataFrame([{
        "Producto": r["combo"], "Presupuesto S/ (costo)": round(r["presupuesto_costo"]),
        "Costo unit S/": round(r["costo_u"], 2), "Contrib unit S/": round(r["contr_u"], 1),
        "Uds a comprar": r["uds_total_asignado"], "S/ asignado": r["soles_asignado"],
        "Contrib. potencial S/": r["contrib_potencial"], "N° tiendas": r["n_tiendas"],
    } for r in resultados])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        resumen.to_excel(w, sheet_name="Resumen", index=False)
        for r in resultados:
            sh = r["combo"][:31].replace("/", "-")
            r["detalle"].to_excel(w, sheet_name=sh, index=False)
    with open(cfg["salida_excel"], "wb") as f:
        f.write(buf.getvalue())
    return cfg["salida_excel"]


def run(cfg=CONFIG):
    resultados = []
    print("▶ Abriendo compra a tienda (proporcional a venta histórica)…\n")
    for combo in cfg["combos"]:
        if not combo.get("presupuesto_costo"):
            print(f"  ⚠ {combo['nombre']}: sin presupuesto, omitido.")
            continue
        r = abrir_compra(cfg, combo)
        resultados.append(r)
        print(f"  {r['combo']}: S/{r['presupuesto_costo']:,.0f} ÷ costo {r['costo_u']:.2f} = "
              f"{r['uds_total_asignado']:,} uds → {r['n_tiendas']} tiendas "
              f"({r['n_sin_venta']} sin venta hist.)")
    if resultados:
        ruta = exportar_excel(cfg, resultados)
        print(f"\n✅ Excel: {ruta}")
    return resultados


if __name__ == "__main__":
    run(CONFIG)
