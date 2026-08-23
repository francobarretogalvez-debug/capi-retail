"""
acciones_log.py — Registro estructurado de acciones de gestión (Gap G3).

Origen: auditoría integral 2026-08-23. Sin registro de acciones, ningún
delta de capital es atribuible ("eso fue el cambio de temporada"). Este log
es joinable contra los snapshots por (semana_iso, marca/sku).

Persistencia: acciones/acciones_log.csv versionado en git. En Streamlit
Cloud el filesystem es efímero: las acciones registradas en la nube deben
descargarse (botón en la vista Caso de Éxito) y commitearse en el flujo
semanal — igual que los snapshots.
"""

import os
from datetime import date, datetime

import pandas as pd

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "acciones")
RUTA_LOG = os.path.join(_DIR, "acciones_log.csv")

COLUMNAS = ["fecha_registro", "semana_iso", "tipo", "marca", "sku",
            "descripcion", "magnitud", "origen", "estado"]

TIPOS = ["Markdown / Precio", "Reposición / Empuje", "Transferencia",
         "Negociación Terceras", "Liquidación", "Exhibición / Tienda", "Otro"]
ORIGENES = ["Sugerida por Capi", "Manual"]
ESTADOS_ACCION = ["Ejecutada", "En curso", "Sugerida"]


def cargar() -> pd.DataFrame:
    """Log completo (CSV del repo). DataFrame vacío con esquema si no existe."""
    if os.path.exists(RUTA_LOG):
        try:
            df = pd.read_csv(RUTA_LOG, dtype=str).fillna("")
            for c in COLUMNAS:
                if c not in df.columns:
                    df[c] = ""
            return df[COLUMNAS]
        except Exception:
            pass
    return pd.DataFrame(columns=COLUMNAS)


def guardar(df: pd.DataFrame) -> None:
    os.makedirs(_DIR, exist_ok=True)
    df[COLUMNAS].to_csv(RUTA_LOG, index=False)


def agregar(semana_iso: str, tipo: str, marca: str, descripcion: str,
            magnitud: str = "", sku: str = "", origen: str = "Sugerida por Capi",
            estado: str = "Ejecutada") -> pd.DataFrame:
    """Agrega una acción y persiste. Devuelve el log actualizado."""
    df = cargar()
    # Validar formato YYYY-WW; cualquier otra cosa cae a la semana actual
    import re as _re
    _sem_ok = bool(semana_iso and _re.fullmatch(r"\d{4}-\d{2}", str(semana_iso).strip()))
    _sem_default = f"{date.today().isocalendar()[0]}-{date.today().isocalendar()[1]:02d}"
    fila = {
        "fecha_registro": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "semana_iso": str(semana_iso).strip() if _sem_ok else _sem_default,
        "tipo": tipo, "marca": marca, "sku": str(sku or ""),
        "descripcion": descripcion, "magnitud": str(magnitud or ""),
        "origen": origen, "estado": estado,
    }
    df = pd.concat([df, pd.DataFrame([fila])], ignore_index=True)
    guardar(df)
    return df


def acciones_de_semanas(desde: str = None, hasta: str = None) -> pd.DataFrame:
    df = cargar()
    if df.empty:
        return df
    if desde:
        df = df[df["semana_iso"] >= desde]
    if hasta:
        df = df[df["semana_iso"] <= hasta]
    return df
