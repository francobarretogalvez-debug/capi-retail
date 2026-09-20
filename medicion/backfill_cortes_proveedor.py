#!/usr/bin/env python3
"""Backfill de cortes del reporte al proveedor (decisión Franco 2026-09-19): corre el MISMO motor
sobre bases históricas y persiste snapshots/<semana>/proveedor.parquet para todas las marcas,
marcado enviado=False (histórico real, no aproximación). Con esto el primer correo ya sale con
bloque 0 (evolución) y rachas.

Uso:
    python medicion/backfill_cortes_proveedor.py "data2/bases antiguas/Base al 23.08.xlsx" "data2/bases antiguas/Base al 30.08.xlsx" ...
Idempotente por (marca, semana). No sube nada a Notion.
"""
import os, sys, time, tempfile, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import motor_v2, transformar_profundidad as etl, reporte_proveedor as rp, reportes_marcas as rm  # noqa: E402
from snapshots_engine import tienda as tsem  # noqa: E402
import venta_perdida_semanal as vps  # noqa: E402


def main(bases):
    for base in bases:
        nombre = os.path.basename(base)
        sem = tsem.semana_de_nombre(nombre)
        if not sem:
            print(f"[skip] {nombre}: no se detecta la semana"); continue
        t0 = time.time()
        pl = os.path.join(tempfile.mkdtemp(), "pl.xlsx")
        etl.transform(base, output_path=pl, fecha_corte=etl.fecha_corte_desde_nombre(nombre))
        res = motor_v2.run_analysis(pl)
        corte = str(etl.fecha_corte_desde_nombre(nombre)).replace("/", ".")
        try:
            vp = vps.venta_perdida_semana(sem).get("detalle")
        except Exception:
            vp = None
        n = 0
        for m in rm.marcas_reporte(res["cobertura"]):
            if rp.slice_marca(res["cobertura"], m).empty:
                continue
            bl = rp.bloques_marca(m, res["cobertura"], res["transferencias"], vp, res["acciones_precio"], res["reposiciones"], res["alertas"], corte=corte, semana_iso=sem, cortes_prev=rp.cargar_cortes(m, hasta=sem))
            rp.persistir_corte(bl, sem, enviado=False); n += 1
        print(f"[ok] {nombre} → {sem}: {n} marcas persistidas en {time.time()-t0:.0f}s ({rp._ruta_corte(sem)})")


if __name__ == "__main__":
    main(sys.argv[1:])
