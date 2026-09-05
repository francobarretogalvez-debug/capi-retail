"""
test_meta_cuadres.py — La regla de los cuadres, hecha verificable.

REGLA (Franco, 2026-09-02): todo test cuyo nombre contenga "cuadre" DEBE
invocar el motor y comparar su salida contra una referencia externa. Un cuadre
que no llama al motor no es un cuadre.

Por qué existe esta regla: la primera versión de `test_cuadre_fl.py` verificaba
que los números del Excel de Planificación fueran consistentes entre sí. Todos
los asserts pasaban y el reporte decía "el motor reproduce la hoja FL" — pero
3 de 4 tests no ejecutaban una sola línea de `flujo_engine`. Se estaba
probando el Excel contra sí mismo.

Este test recorre el AST de los archivos de cuadre y falla si alguna función
de test no invoca ningún módulo del motor. Es la regla como código, no como
buena intención.

    python3 tests/test_meta_cuadres.py
"""

import ast
import glob
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(TESTS_DIR)

# Módulos que cuentan como "el motor". Ampliar cuando se agreguen nuevos.
MODULOS_MOTOR = {
    "flujo_engine", "flujo_ingesta", "motor_v2", "pricing", "taxonomia",
    "cobertura", "otb_engine", "curva_estacional", "alertas_plan",
    "noos_engine", "variance_bridge", "rendimiento_tienda",
    "analisis_estados", "calendario_ripley", "snapshots_engine",
}

# Funciones que miden una diferencia esperada o son utilitarias, no cuadres.
EXENTAS = {"main"}


def _alias_de_modulos_motor(tree):
    """Nombres locales que apuntan a un módulo del motor (incluye alias)."""
    alias = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] in MODULOS_MOTOR:
                    alias.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            raiz = (n.module or "").split(".")[0]
            if raiz in MODULOS_MOTOR:
                for a in n.names:
                    alias.add(a.asname or a.name)
    return alias


def _invoca_motor(fn, alias):
    """¿La función llama a algo del motor?"""
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in alias:
            return True
        if isinstance(f, ast.Name) and f.id in alias:
            return True
    return False


def test_los_cuadres_invocan_el_motor():
    archivos = [p for p in sorted(glob.glob(os.path.join(TESTS_DIR, "*cuadre*.py")))
                if os.path.basename(p) != os.path.basename(__file__)]
    assert archivos, "no se encontró ningún archivo de cuadre"

    incumplen = []
    for path in archivos:
        tree = ast.parse(open(path, encoding="utf-8").read())
        alias = _alias_de_modulos_motor(tree)
        nombre = os.path.basename(path)
        assert alias, f"{nombre}: no importa ningún módulo del motor"

        # Funciones auxiliares del propio archivo que sí tocan el motor: si un
        # test las usa, cuenta como invocación indirecta.
        helpers = {f.name for f in ast.walk(tree)
                   if isinstance(f, ast.FunctionDef) and _invoca_motor(f, alias)}
        alias_ext = alias | helpers

        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            if not fn.name.startswith("test_") or fn.name in EXENTAS:
                continue
            if not _invoca_motor(fn, alias_ext):
                incumplen.append(f"{nombre}::{fn.name}")
            else:
                print(f"  OK {nombre}::{fn.name}")

    assert not incumplen, (
        "estos tests dicen ser cuadres pero NO invocan el motor "
        "(prueban la referencia contra sí misma): " + ", ".join(incumplen))
    print(f"\nOK: todos los tests de cuadre invocan el motor "
          f"({len(archivos)} archivo(s))")


def main():
    print("— test_los_cuadres_invocan_el_motor —")
    test_los_cuadres_invocan_el_motor()
    return 0


if __name__ == "__main__":
    sys.exit(main())
