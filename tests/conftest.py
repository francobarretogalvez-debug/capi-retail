"""Config común de la suite: el repo en sys.path y rutas de data local con skip limpio."""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

BASES_DIR = os.path.join(REPO, "data2", "bases antiguas")
SNAPSHOTS_DIR = os.path.join(REPO, "snapshots")


def base_local(nombre: str) -> str | None:
    """Ruta a una base Ripley local (gitignored) o None si no está."""
    for carpeta in (BASES_DIR, os.path.expanduser("~/Downloads")):
        p = os.path.join(carpeta, nombre)
        if os.path.exists(p):
            return p
    return None


def requiere_archivo(path: str | None, motivo: str):
    if not path or not os.path.exists(path):
        pytest.skip(f"SKIP: {motivo} (data local no disponible)")
    return path
