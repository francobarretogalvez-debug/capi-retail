"""Backtest de reglas de reparto del CD entre tiendas (Franco, 06-sep-2026).
Reglas: A actual (urgencia → menor cobertura, llenado completo a 12 sem) · B dos pasos (piso 4 sem + proporcional a velocidad)
· C fair share (misma cobertura post para todas). Demanda del horizonte = venta real si la tienda tuvo stock, si no la velocidad."""
import sys, math, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/francobarreto/capi-retail")
import numpy as np, pandas as pd
from snapshots_engine import tienda as st_t
COB_T, PISO, UC = 12, 4, float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
W = {w: st_t.load_tienda(f"2026-{w}") for w in range(30, 36)}
CH = {w: pd.read_parquet(f"snapshots/2026-{w}/snapshot.parquet").assign(sku=lambda d: d["sku"].astype(str)) for w in range(30, 36)}
for w in W: W[w]["sku"] = W[w]["sku"].astype(str)

def velocidad(w):
    ws = [x for x in range(w - 2, w + 1) if x in W]
    v = pd.concat([W[x][["sku", "tienda", "vta_uds_sem"]] for x in ws]).assign(vta_uds_sem=lambda d: d["vta_uds_sem"].clip(lower=0))
    return v.groupby(["sku", "tienda"])["vta_uds_sem"].sum() / len(ws)

def urg(stock, cob):
    return 3 if (stock <= 0 or cob < UC) else 2 if cob < COB_T * 0.5 else 1

def regla_A(c, g):
    g = g.sort_values(["urg", "cob"], ascending=[False, True]); rem, out = c, {}
    for i, r in g.iterrows():
        x = min(r.need12, rem); out[i] = x; rem -= x
        if rem <= 0: break
    return out
def regla_B(c, g):
    g = g.sort_values(["urg", "cob"], ascending=[False, True]); rem, out = c, {i: 0 for i in g.index}
    for i, r in g.iterrows():
        x = min(r.need4, rem); out[i] += x; rem -= x
    for _ in range(4):
        if rem <= 0: break
        resto = {i: g.at[i, "need12"] - out[i] for i in g.index if g.at[i, "need12"] - out[i] > 0}
        if not resto: break
        vs = sum(g.at[i, "v"] for i in resto)
        asig = {i: min(resto[i], math.floor(rem * g.at[i, "v"] / vs)) for i in resto}
        if sum(asig.values()) == 0:
            i = max(resto, key=lambda k: g.at[k, "v"]); asig = {i: 1}
        for i, x in asig.items(): out[i] += x; rem -= x
    return out
def regla_C(c, g):
    lo, hi = 0.0, float(COB_T)
    need = lambda L: sum(max(0.0, min(L, COB_T) * r.v - r.stock) for r in g.itertuples())
    if need(hi) <= c: L = hi
    else:
        for _ in range(40):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if need(mid) <= c else (lo, mid)
        L = lo
    out = {i: int(min(r.need12, math.floor(max(0.0, L * r.v - r.stock)))) for i, r in g.iterrows()}
    rem = c - sum(out.values())
    for i, r in g.sort_values("cob").iterrows():
        if rem <= 0: break
        x = min(r.need12 - out[i], rem); out[i] += x; rem -= x
    return out

def backtest(w, H):
    v = velocidad(w); base = W[w].set_index(["sku", "tienda"])[["stock_uds"]].join(v.rename("v"), how="outer").fillna(0)
    base = base[base["v"] > 0].reset_index()
    base["cob"] = base["stock_uds"] / base["v"]; base = base[base["cob"] < COB_T].copy()
    base["need12"] = np.maximum(0, np.ceil(COB_T * base["v"] - base["stock_uds"])).astype(int)
    base["need4"] = np.maximum(0, np.ceil(PISO * base["v"] - base["stock_uds"])).astype(int)
    base["urg"] = [urg(s, c) for s, c in zip(base["stock_uds"], base["cob"])]
    base = base.rename(columns={"stock_uds": "stock"})
    cd = CH[w].set_index("sku")["stock_cd"].clip(lower=0); precio = CH[w].set_index("sku")["precio_vigente"]
    fut = {k: W[w + k].set_index(["sku", "tienda"])[["stock_uds", "vta_uds_sem"]] for k in range(1, H + 1) if (w + k) in W}
    res = {r: dict(perd_uds=0.0, perd_soles=0.0, sobr=0.0, tiendas=0, top3=0.0, cd=0.0, skus=0) for r in "ABC"}
    for sku, g in base.groupby("sku"):
        c = int(cd.get(sku, 0))
        if c <= 0 or g["need12"].sum() == 0: continue
        g = g.set_index("tienda"); p = float(precio.get(sku, 0) or 0); top = set(g["v"].nlargest(3).index)
        for r, fn in (("A", regla_A), ("B", regla_B), ("C", regla_C)):
            alloc = fn(c, g); used = sum(alloc.values()); res[r]["cd"] += used; res[r]["skus"] += 1
            res[r]["tiendas"] += sum(1 for x in alloc.values() if x > 0); res[r]["top3"] += sum(x for t, x in alloc.items() if t in top)
            for t, row in g.iterrows():
                S = row.stock + alloc.get(t, 0); lost = 0.0
                for k, f in fut.items():
                    if (sku, t) in f.index:
                        st_r, vt = f.loc[(sku, t)]; d = max(0.0, vt) if (st_r > 0 or vt > 0) else row.v
                    else: d = row.v
                    lost += max(0.0, d - S); S = max(0.0, S - d)
                res[r]["perd_uds"] += lost; res[r]["perd_soles"] += lost * p; res[r]["sobr"] += min(S, alloc.get(t, 0))
    return res

rows = []
for w, H in ((31, 2), (32, 2), (33, 2), (31, 4)):
    res = backtest(w, H)
    for r, d in res.items():
        rows.append(dict(semana=f"2026-{w}", H=H, regla=r, skus=d["skus"], venta_perdida_uds=round(d["perd_uds"]), venta_perdida_soles=round(d["perd_soles"]),
                         uds_sobrantes=round(d["sobr"]), tiendas_por_sku=round(d["tiendas"] / max(d["skus"], 1), 1), pct_top3=round(d["top3"] / max(d["cd"], 1) * 100), uds_giradas=round(d["cd"])))
df = pd.DataFrame(rows); print(df.to_string(index=False))
df.to_csv("/private/tmp/claude-501/-Users-francobarreto-Claude-Context/bb7dc39b-76e3-44c2-9286-107a61dcb097/scratchpad/backtest_reparto_cd.csv", index=False)
