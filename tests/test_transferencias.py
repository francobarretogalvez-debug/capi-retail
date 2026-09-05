"""Regresión del criterio económico de transferencias (fórmula Ripley 2026-08-24)."""
from motor_v2 import evaluar_transferencia


def test_replica_exacta_hoja_ripley():
    # 10 uds · dscto 60% · master 39.9 → vig 15.96 · costo 20 · flete 3.5 → −99.75
    contrib, flete, pot, esp = evaluar_transferencia(10, 15.96, 20, 3.5)
    assert abs(contrib - (-6.4746)) < 1e-3, contrib
    assert flete == 35.0 and abs(pot - (-99.75)) < 0.01, (flete, pot)
    assert esp == pot


def test_esperada_menor_que_potencial_si_destino_no_vende_todo():
    _, _, p2, e2 = evaluar_transferencia(40, 59.99, 23.9, 3.5, uds_vendibles=32)
    assert p2 > 0 and 0 < e2 < p2


def test_uds_vendibles_se_capea():
    _, _, p3, e3 = evaluar_transferencia(10, 59.99, 23.9, 3.5, uds_vendibles=99)
    assert e3 == p3


def test_guards_no_revientan():
    assert evaluar_transferencia(10, 0, 20, 3.5) == (None, None, None, None)
    assert evaluar_transferencia(10, 15.96, None, 3.5) == (None, None, None, None)
