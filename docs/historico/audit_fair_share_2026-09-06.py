"""Auditoría de la decisión fair share (06-sep-2026): robustez al proxy de demanda, variantes de Franco
(cluster A +2/+4 sem, ponderar por precio realizado), paridad por SKU y por tipo de marca, giros < UME."""
import sys, math, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/francobarreto/capi-retail")
import numpy as np, pandas as pd
from snapshots_engine import tienda as st_t
COB_T, PISO, UC = 12, 4, 4.0
TERCERAS = {"LACOSTE", "NAUTICA", "PSYCHO BUNNY", "DOCKERS", "US POLO", "U.S. POLO ASSN.", "JACK & JONES", "OSCAR DE LA RENTA"}
W = {w: st_t.load_tienda(f"2026-{w}").assign(sku=lambda d: d["sku"].astype(str)) for w in range(30, 36)}
CH = {w: pd.read_parquet(f"snapshots/2026-{w}/snapshot.parquet").assign(sku=lambda d: d["sku"].astype(str)) for w in range(30, 36)}
# índice de precio realizado por tienda × marca (base 30-ago, última semana): precio tienda / precio cadena de la marca
b = pd.read_excel("data2/bases antiguas/Base al 30.08.xlsx")
codes = sorted({c[:-8] for c in b.columns if c.endswith(" Vta S/.")} & {c[:-9] for c in b.columns if c.endswith(" Unidades")})
rows = []
for c in codes:
    rows.append(pd.DataFrame({"tienda": c, "marca": b["Marca"].astype(str).str.upper().str.strip(), "soles": pd.to_numeric(b[f"{c} Vta S/."], errors="coerce").fillna(0), "uds": pd.to_numeric(b[f"{c} Unidades"], errors="coerce").fillna(0)}))
pv = pd.concat(rows); pv = pv[pv["uds"] > 0]
tm = pv.groupby(["tienda", "marca"])[["soles", "uds"]].sum(); tm = (tm["soles"] / tm["uds"]).rename("p_t")
cm = pv.groupby("marca")[["soles", "uds"]].sum(); cm = (cm["soles"] / cm["uds"]).rename("p_c")
IDX = (tm / tm.index.get_level_values("marca").map(cm).values).clip(0.6, 1.4)   # (tienda, marca) → índice
marca_sku = CH[35].set_index("sku")["marca"].astype(str).str.upper().str.strip()
tot = pd.concat(W.values()).groupby("tienda")["vta_uds_sem"].sum().sort_values(ascending=False)
CLUSTER_A = set(tot.head(6).index); print("Cluster A (proxy, top-6 por venta):", sorted(CLUSTER_A))

def velocidad(w):
    ws = [x for x in range(w - 2, w + 1) if x in W]
    v = pd.concat([W[x][["sku", "tienda", "vta_uds_sem"]] for x in ws]).assign(vta_uds_sem=lambda d: d["vta_uds_sem"].clip(lower=0))
    return v.groupby(["sku", "tienda"])["vta_uds_sem"].sum() / len(ws)
def urg(stock, cob): return 3 if (stock <= 0 or cob < UC) else 2 if cob < COB_T * 0.5 else 1

def regla_A(c, g, **k):
    g = g.sort_values(["urg", "cob"], ascending=[False, True]); rem, out = c, {}
    for i, r in g.iterrows():
        x = min(r.need12, rem); out[i] = x; rem -= x
        if rem <= 0: break
    return out
def regla_B(c, g, **k):
    g = g.sort_values(["urg", "cob"], ascending=[False, True]); rem, out = c, {i: 0 for i in g.index}
    for i, r in g.iterrows():
        x = min(r.need4, rem); out[i] += x; rem -= x
    for _ in range(4):
        if rem <= 0: break
        resto = {i: g.at[i, "need12"] - out[i] for i in g.index if g.at[i, "need12"] - out[i] > 0}
        if not resto: break
        vs = sum(g.at[i, "v"] for i in resto); asig = {i: min(resto[i], math.floor(rem * g.at[i, "v"] / vs)) for i in resto}
        if sum(asig.values()) == 0: i = max(resto, key=lambda q: g.at[q, "v"]); asig = {i: 1}
        for i, x in asig.items(): out[i] += x; rem -= x
    return out
def regla_C(c, g, extra=None, peso=None, **_):
    """fair share generalizado: nivel L; tienda i apunta a (L + extra_i) · peso_i semanas, topado a need12."""
    ex = extra if extra is not None else pd.Series(0.0, index=g.index); pe = peso if peso is not None else pd.Series(1.0, index=g.index)
    def need(L): return sum(min(r.need12, max(0.0, (L + ex[i]) * pe[i] * r.v - r.stock)) for i, r in g.iterrows())
    lo, hi = 0.0, float(COB_T)
    if need(hi) <= c: L = hi
    else:
        for _ in range(40):
            mid = (lo + hi) / 2; lo, hi = (mid, hi) if need(mid) <= c else (lo, mid)
        L = lo
    out = {i: int(min(r.need12, math.floor(max(0.0, (L + ex[i]) * pe[i] * r.v - r.stock)))) for i, r in g.iterrows()}
    rem = c - sum(out.values())
    for i, r in g.sort_values("cob").iterrows():
        if rem <= 0: break
        x = min(r.need12 - out[i], rem); out[i] += x; rem -= x
    return out
def C_plus(k): return lambda c, g, **_: regla_C(c, g, extra=pd.Series([k if t in CLUSTER_A else 0.0 for t in g.index], index=g.index))
def C_peso(c, g, marca=None, **_): return regla_C(c, g, peso=pd.Series([float(IDX.get((t, marca), 1.0)) for t in g.index], index=g.index))
REGLAS = {"A actual": regla_A, "B dos pasos": regla_B, "C fair share": regla_C, "C +2 sem cluster A": C_plus(2), "C +4 sem cluster A": C_plus(4), "C × precio tienda": C_peso}

def backtest(w, H):
    v = velocidad(w); base = W[w].set_index(["sku", "tienda"])[["stock_uds", "ume"]].join(v.rename("v"), how="outer").fillna(0)
    base = base[base["v"] > 0].reset_index(); base["cob"] = base["stock_uds"] / base["v"]; base = base[base["cob"] < COB_T].copy()
    base["need12"] = np.maximum(0, np.ceil(COB_T * base["v"] - base["stock_uds"])).astype(int); base["need4"] = np.maximum(0, np.ceil(PISO * base["v"] - base["stock_uds"])).astype(int)
    base["urg"] = [urg(s, c) for s, c in zip(base["stock_uds"], base["cob"])]; base = base.rename(columns={"stock_uds": "stock"})
    cd = CH[w].set_index("sku")["stock_cd"].clip(lower=0); precio = CH[w].set_index("sku")["precio_vigente"]
    fut = {k: W[w + k].set_index(["sku", "tienda"])[["stock_uds", "vta_uds_sem"]] for k in range(1, H + 1) if (w + k) in W}
    res = {r: {p: dict(uds=0.0, soles=0.0) for p in ("mixto", "velocidad", "real")} | dict(lineas=0, sub_ume=0, skus=0) for r in REGLAS}
    per_sku = []
    for sku, g in base.groupby("sku"):
        c = int(cd.get(sku, 0))
        if c <= 0 or g["need12"].sum() == 0: continue
        g = g.set_index("tienda"); p = float(precio.get(sku, 0) or 0); marca = marca_sku.get(sku, "")
        dem = {t: {"mixto": [], "velocidad": [], "real": []} for t in g.index}
        for t, row in g.iterrows():
            for k, f in fut.items():
                if (sku, t) in f.index:
                    st_r, vt = f.loc[(sku, t)]; real = max(0.0, vt); mix = real if (st_r > 0 or vt > 0) else row.v
                else: real, mix = 0.0, row.v
                dem[t]["mixto"].append(mix); dem[t]["velocidad"].append(row.v); dem[t]["real"].append(real)
        fila = {"sku": sku, "tercera": marca in TERCERAS}
        for r, fn in REGLAS.items():
            alloc = fn(c, g, marca=marca); res[r]["skus"] += 1
            for t, x in alloc.items():
                if x > 0:
                    res[r]["lineas"] += 1; u = g.at[t, "ume"]
                    if u > 0 and x < u: res[r]["sub_ume"] += 1
            for prox in ("mixto", "velocidad", "real"):
                lost_u = lost_s = 0.0
                for t, row in g.iterrows():
                    S = row.stock + alloc.get(t, 0); pt = p * float(IDX.get((t, marca), 1.0))
                    for d in dem[t][prox]:
                        l = max(0.0, d - S); lost_u += l; lost_s += l * pt; S = max(0.0, S - d)
                res[r][prox]["uds"] += lost_u; res[r][prox]["soles"] += lost_s
                if prox == "mixto": fila[r] = lost_u
        per_sku.append(fila)
    return res, pd.DataFrame(per_sku)

out, sk_all = [], []
for w, H in ((31, 2), (32, 2), (33, 2), (31, 4)):
    res, sk = backtest(w, H); sk["semana"] = w; sk["H"] = H; sk_all.append(sk)
    for r, d in res.items():
        out.append({"semana": w, "H": H, "regla": r, **{f"{p}_uds": round(d[p]["uds"]) for p in ("mixto", "velocidad", "real")}, **{f"{p}_S": round(d[p]["soles"]) for p in ("mixto", "velocidad", "real")}, "lineas": d["lineas"], "sub_ume": d["sub_ume"]})
df = pd.DataFrame(out); pd.set_option("display.width", 250); print(df.to_string(index=False))
sk = pd.concat(sk_all)
print("\n=== Paridad por SKU (demanda mixta): C fair share vs A actual")
d = sk["A actual"] - sk["C fair share"]
print(f"SKUs: {len(sk)} | C mejor: {(d>0).sum()} ({(d>0).mean():.0%}) | empate: {(d==0).sum()} ({(d==0).mean():.0%}) | A mejor: {(d<0).sum()} ({(d<0).mean():.0%}) | uds ganadas cuando C gana: {d[d>0].sum():.0f} | perdidas cuando A gana: {-d[d<0].sum():.0f}")
for terc, lab in ((False, "propias"), (True, "terceras")):
    s = sk[sk["tercera"] == terc]; dd = s["A actual"] - s["C fair share"]
    print(f"  {lab}: SKUs {len(s)} | Σ A {s['A actual'].sum():.0f} → Σ C {s['C fair share'].sum():.0f} ({(s['C fair share'].sum()/max(s['A actual'].sum(),1)-1):+.0%}) | A mejor en {(dd<0).mean():.0%} de SKUs")
df.to_csv("/private/tmp/claude-501/-Users-francobarreto-Claude-Context/bb7dc39b-76e3-44c2-9286-107a61dcb097/scratchpad/audit_fair_share.csv", index=False)
