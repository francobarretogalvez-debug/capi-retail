"""
Auditoría numérica independiente del motor de transición.
Recomputa cada número desde los archivos crudos y lo compara con lo que
produce transicion_engine. Reporta PASS/FAIL por chequeo.
"""
import pandas as pd, numpy as np, glob, os
import transicion_engine as te

cfg = te.CONFIG
BD = cfg["ruta_bd_modelo"]
FILES = cfg["rutas_profundidad"]
DIEGOL = ["27390791"]; DARIO = ["35207068", "36224956", "34056750"]; BREMEN = ["36210326"]
PAS = {t.upper() for t in cfg["pasarela_tiendas"]}
EXC = {x.upper() for x in cfg["tiendas_excluir"]}

oks = []
def chk(nombre, a, b, tol=1.0):
    ok = abs(a - b) <= tol
    oks.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {nombre}: motor={a:,.2f} vs audit={b:,.2f}  (dif {a-b:+.2f})")

# Motor
r = te.run(cfg, pedir_input=False)
dimv = r["dimension"]; dp = dimv["por_programa"].set_index("programa")
proy = r["proy_dario"]; e = proy["escenarios"]

print("\n================ AUDITORÍA ================\n")

# ---- A. Totales BD por código ----
bd = pd.read_excel(BD, sheet_name="BD", usecols=["CodModelo","Linea","Marca","VtaUnd","VtaSMF","Contr","PBxVtaUnd","PVxVtaUnd"])
bd["CodModelo"] = bd["CodModelo"].astype(str).str.strip()
for c in ["VtaUnd","VtaSMF","Contr","PBxVtaUnd","PVxVtaUnd"]: bd[c] = pd.to_numeric(bd[c],errors="coerce").fillna(0)
def tot(codes, col): return bd[bd.CodModelo.isin(codes)][col].sum()
chk("Diegol uds", float(dp.loc["DIEGOL","uds_total"]), tot(DIEGOL,"VtaUnd"))
chk("Diegol S/",  float(dp.loc["DIEGOL","venta_sol"]), tot(DIEGOL,"VtaSMF"))
chk("Diegol margen", float(dp.loc["DIEGOL","margen_sol"]), tot(DIEGOL,"Contr"))
chk("Dario uds",  float(dp.loc["DARIO","uds_total"]),  tot(DARIO,"VtaUnd"))
chk("Bremen uds", float(dp.loc["BREMEN","uds_total"]), tot(BREMEN,"VtaUnd"))

# ---- B. economics y costo_u ----
eco = dimv["economics"]
for prog,codes in [("DIEGOL",DIEGOL),("DARIO",DARIO),("BREMEN",BREMEN)]:
    vu,vs,ct = tot(codes,"VtaUnd"), tot(codes,"VtaSMF"), tot(codes,"Contr")
    chk(f"{prog} precio_u", eco[prog]["precio_u"], vs/vu, 0.05)
    chk(f"{prog} costo_u",  eco[prog]["costo_u"], (vs-ct)/vu, 0.05)

# costo_u vs columna Costo (OJO: 'Costo' es el costo TOTAL por fila, no unitario)
bd2 = pd.read_excel(BD, sheet_name="BD", usecols=["CodModelo","VtaUnd","Costo"])
bd2["CodModelo"]=bd2["CodModelo"].astype(str).str.strip()
for c in ["VtaUnd","Costo"]: bd2[c]=pd.to_numeric(bd2[c],errors="coerce").fillna(0)
d_dar = bd2[bd2.CodModelo.isin(DARIO)]
costo_real = d_dar["Costo"].sum()/d_dar["VtaUnd"].sum()   # Costo total / unidades
chk("Dario costo_u vs columna Costo", eco["DARIO"]["costo_u"], costo_real, 0.05)

# ---- C. captura cadena ----
chk("Captura cadena", dimv["captura_chain"], tot(DARIO,"VtaUnd")/tot(DIEGOL,"VtaUnd"), 0.005)

# ---- D. descuento categoria ----
mq = bd[bd.Marca.astype(str).str.upper().str.contains("MARQUIS",na=False) if "Marca" in bd else bd.index>=0]
# recomputar dscto sobre pantalon marquis
bdc = pd.read_excel(BD, sheet_name="BD", usecols=["Marca","Linea","PBxVtaUnd","PVxVtaUnd"])
for c in ["PBxVtaUnd","PVxVtaUnd"]: bdc[c]=pd.to_numeric(bdc[c],errors="coerce").fillna(0)
bdc=bdc[bdc.Marca.astype(str).str.upper().str.contains("MARQUIS",na=False)]
bdc=bdc[bdc.Linea.astype(str).str.upper().str.contains("PANTAL",na=False)]
dscto_audit = 1 - bdc["PVxVtaUnd"].sum()/bdc["PBxVtaUnd"].sum()
chk("Dscto categoria", dimv["dscto_categoria"], dscto_audit, 0.005)

# ---- E. Stock prom (suma SKUs por snapshot, prom snapshots) ----
def stock_prom(codes):
    snaps=[]
    for f in FILES:
        df=pd.read_excel(f,sheet_name="Base"); df["Cód. Prod."]=df["Cód. Prod."].astype(str).str.strip()
        d=df[df["Cód. Prod."].isin(codes)]
        if d.empty: continue
        scols=[c for c in df.columns if str(c).endswith(" Stk")]
        tot_snap=0
        for _,row in d.iterrows():
            for sc in scols:
                if sc[:-4].strip().upper() in EXC: continue
                v=pd.to_numeric(row[sc],errors="coerce")
                if pd.notna(v): tot_snap+=float(v)
        snaps.append(tot_snap)
    return np.mean(snaps)
def venta_tot(codes):
    tot_v=0
    for f in FILES:
        df=pd.read_excel(f,sheet_name="Base"); df["Cód. Prod."]=df["Cód. Prod."].astype(str).str.strip()
        d=df[df["Cód. Prod."].isin(codes)]
        vcols=[c for c in df.columns if str(c).endswith(" Vta")]
        for _,row in d.iterrows():
            for vc in vcols:
                if vc[:-4].strip().upper() in EXC: continue
                v=pd.to_numeric(row[vc],errors="coerce")
                if pd.notna(v) and v!=0: tot_v+=float(v)
    return tot_v
ps=r["prod_stock"]
chk("Stock prom Diegol", ps["diegol"]["stock_prom"], stock_prom(DIEGOL))
chk("Stock prom Dario",  ps["dario"]["stock_prom"],  stock_prom(DARIO))
chk("Venta 7sem Diegol", ps["diegol"]["vta"], venta_tot(DIEGOL))
chk("Venta 7sem Dario",  ps["dario"]["vta"],  venta_tot(DARIO))

# ---- F. Exhibición uplift ----
def cap_7sem(normalizar):
    die={}; dar={}
    for f in FILES:
        df=pd.read_excel(f,sheet_name="Base"); df["Cód. Prod."]=df["Cód. Prod."].astype(str).str.strip()
        for codes,acc in [(DIEGOL,die),(DARIO,dar)]:
            d=df[df["Cód. Prod."].isin(codes)]
            vcols=[c for c in df.columns if str(c).endswith(" Vta")]
            for _,row in d.iterrows():
                for vc in vcols:
                    t=vc[:-4].strip()
                    if t.upper() in EXC: continue
                    v=pd.to_numeric(row[vc],errors="coerce")
                    if pd.notna(v) and v!=0: acc[t]=acc.get(t,0)+float(v)
    com=set(die)&set(dar)
    Dar=sum(max(dar[t],0) for t in com)
    if normalizar:
        Die=sum((die[t]/2 if t.upper() in PAS else die[t]) for t in com)
    else:
        Die=sum(die[t] for t in com)
    return Dar/Die
cap_raw=cap_7sem(False); cap_norm=cap_7sem(True)
chk("Uplift exhibicion", proy["uplift_exhibicion"], cap_norm/cap_raw, 0.02)
chk("Captura base (norm 7sem, antes precio)", proy["captura_base"]/proy["factor_precio"], cap_norm, 0.01)

# ---- G. Rango captura: fórmula ----
fp=proy["factor_precio"]
chk("factor_precio", fp, 1+cfg["elasticidad_precio_dario"]*(cfg["precio_vigente_diegol"]/cfg["precio_vigente_dario"]-1), 0.001)
chk("captura piso", proy["captura_piso"], min(dimv["captura_chain"]*proy["uplift_exhibicion"],1.0)*fp, 0.005)
chk("captura techo", proy["captura_techo"], 1.0*fp, 0.005)
# venta = diegol_uds × captura
diegol_uds=float(dp.loc["DIEGOL","uds_total"])
for k in ["piso","base","techo"]:
    chk(f"venta Dario {k}", e[k]["venta_uds"], diegol_uds*e[k]["captura"], 1.0)
    chk(f"carga Dario {k}", e[k]["carga_uds"], e[k]["venta_uds"]/proy["agotamiento"], 1.0)

# ---- H. Bremen/Bronco ----
pb=r["proy_bremen"]; po=r["proy_bronco"]
chk("Bremen carga", pb["carga_uds"], r["proy_bremen"]["detalle"]["carga"].sum())
chk("Bremen venta", pb["venta_uds"], pb["carga_uds"]*pb["agotamiento"], 1.0)
chk("Bronco venta", po["venta_uds"], po["carga_uds"]*po["agotamiento"], 1.0)

# ---- I. Mitigación arithmetic ----
for esc in ["piso","base","techo"]:
    m=r["mits"][esc]
    rep=m[m.concepto=="Reemplazo total"].iloc[0]
    suma=e[esc]["venta_uds"]+pb["venta_uds"]+po["venta_uds"]
    chk(f"Reemplazo uds {esc}", rep["uds"], suma, 1.0)
    caida=m[m.concepto.str.startswith("CAÍDA")].iloc[0]
    chk(f"Caída neta uds {esc}", caida["uds"], diegol_uds-suma, 1.0)

# ---- J. Pitch pedido ----
blocks=dict(te.construir_pitch(cfg,dimv,ps,{"uplift":proy["uplift_exhibicion"]},proy,pb,po,r["mits"]))
ped=blocks["7. EL PEDIDO (presupuesto de compra)"].set_index("Escenario")
for esc in ["PISO","BASE","TECHO"]:
    k=esc.lower()
    compra=e[k]["carga_uds"]+pb["carga_uds"]+po["carga_uds"]
    inv=e[k]["carga_uds"]*eco["DARIO"]["costo_u"]+pb["carga_uds"]*eco["BREMEN"]["costo_u"]+po["carga_uds"]*eco["BREMEN"]["costo_u"]
    chk(f"Pedido compra uds {esc}", float(ped.loc[esc,"Compra total uds"]), compra, 2.0)
    chk(f"Pedido inversión {esc}", float(ped.loc[esc,"Inversión total S/"]), inv, 5.0)

print(f"\n================ RESULTADO: {sum(oks)}/{len(oks)} PASS ================")
if all(oks): print("✅ Todos los cálculos cuadran.")
else: print("❌ Hay discrepancias — revisar los FAIL arriba.")
