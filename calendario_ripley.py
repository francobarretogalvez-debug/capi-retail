"""
calendario_ripley.py — Calendario comercial Ripley (BD Periodo).

Fuente: "Copia de BD Periodo.xlsx" (Franco, 2026-08-24), convertida a
calendario_ripley.csv (versionado). Cubre 2018-02-26 → 2027-02-14 con:
semana comercial (SEMACT/W######), periodo, mes comercial, temporada,
temporada por ventanas, ventana de compra y evento.

El año Ripley arranca a fines de febrero, así que la semana comercial NO
coincide con la ISO (ej: ISO 2026-34 = Ripley W202627). Todas las etiquetas
visibles del sistema deben hablar en semanas Ripley; la ISO queda como clave
interna de los snapshots.
"""

import os
from datetime import date
from functools import lru_cache

import pandas as pd

_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calendario_ripley.csv")


@lru_cache(maxsize=1)
def _tabla() -> pd.DataFrame:
    df = pd.read_csv(_CSV, parse_dates=["fecha_inicio", "fecha_fin"])
    df["fecha_inicio"] = df["fecha_inicio"].dt.date
    df["fecha_fin"] = df["fecha_fin"].dt.date
    return df


def info_fecha(fecha) -> dict:
    """Fila del calendario Ripley que contiene la fecha. {} si está fuera."""
    if fecha is None:
        return {}
    if isinstance(fecha, str):
        try:
            fecha = date.fromisoformat(fecha[:10])
        except ValueError:
            return {}
    df = _tabla()
    fila = df[(df["fecha_inicio"] <= fecha) & (df["fecha_fin"] >= fecha)]
    return {} if fila.empty else fila.iloc[0].to_dict()


def etiqueta_fecha(fecha, corta: bool = False) -> str:
    """'Sem 27 Ripley · PV 26-27 · cierre 23.08' (o 'S27·23.08' en corta)."""
    info = info_fecha(fecha)
    if isinstance(fecha, str):
        try:
            fecha = date.fromisoformat(fecha[:10])
        except ValueError:
            fecha = None
    ddmm = f"{fecha.day:02d}.{fecha.month:02d}" if fecha else "?"
    if not info:
        return ddmm if corta else f"cierre {ddmm}"
    _vent = str(info.get("ventana", "") or "").strip()
    if corta:
        return f"S{int(info['sem_num'])}·{ddmm}"
    _vent_txt = f" · Vent. {_vent}" if _vent and _vent != "---" else ""
    return (f"Sem {int(info['sem_num'])} Ripley · {info['temporada_ventanas']}"
            f"{_vent_txt} · cierre {ddmm}")


def semanas_de_periodo(periodo: str) -> list[dict]:
    """Semanas comerciales que componen un periodo, en orden.

    Ripley usa calendario 4-4-5: un periodo tiene 4 o 5 semanas exactas. Sirve
    para saber cuántos cortes faltan para cerrarlo — una serie por periodo se
    puede componer exacta desde semanas; una por mes calendario, nunca.
    """
    df = _tabla()
    p = df[df["periodo"] == periodo].sort_values("fecha_inicio")
    return [{"semact": r.semact, "sem_num": int(r.sem_num),
             "inicio": r.fecha_inicio, "fin": r.fecha_fin} for r in p.itertuples()]


def periodo_de(fecha) -> str | None:
    return (info_fecha(fecha) or {}).get("periodo")
