"""Genera los fixtures FICTICIOS del módulo PV en Tránsito (ningún dato real de Ripley).

  detalle_comex_mini.xlsx   — forma del DETALLE de comex (hoja 'Export', OC × SKU, 20 columnas)
  llegadas_franco_mini.xlsx — forma de las hojas 'LLEGADAS RETRASOS COMEX' de Franco
                              (encabezado en filas distintas, fechas como serial Excel,
                              filas 'Total general' y '(en blanco)', ventana solo por hoja)

Cada OC prueba una regla (ver tests/test_pv_transito.py). Correr desde la raíz:
    python3 tests/fixtures/make_detalle_mini.py
"""
import os
from datetime import date

import pandas as pd

AQUI = os.path.dirname(os.path.abspath(__file__))


def _serial(d: date | None) -> int | None:
    return None if d is None else (d - date(1899, 12, 30)).days


def detalle() -> pd.DataFrame:
    cols = ["OC", "EMBARQUE", "SKU", "COD PADRE", "TEMP", "VENTANA", "DIVISION", "DEPARTAMENTO", "LINEA",
            "SUBLINEA", "MARCA", "PROVEEDOR", "PAIS", "ETA", "ETD", "PROYECTADO INGRESO CD3",
            "ESTADO_EMBARQUE_FINAL", "UND", "MONTO_FOB", "MODELO", "INGRESOCD", "MOTIVO_RETRASO"]
    H, M = "HOMBRE", "MUJER"
    filas = [
        # OC 1001: recibida (Almacenado + INGRESOCD), 2 SKU → 200 u, FOB 1000
        (1001, 60001, "A1", "M1", "PV 26/27", "E", H, "MARQUIS HOMBRE", "CAMISAS M/C", "CAM MC", "MARQUIS", "PROV UNO", "China",
         date(2026, 8, 25), date(2026, 7, 20), date(2026, 9, 1), "1.Almacenado", 100, 500.0, "CAM MC UNO MQS", date(2026, 9, 2), ""),
        (1001, 60001, "A2", "M1", "PV 26/27", "E", H, "MARQUIS HOMBRE", "CAMISAS M/C", "CAM MC", "MARQUIS", "PROV UNO", "China",
         date(2026, 8, 25), date(2026, 7, 20), date(2026, 9, 1), "1.Almacenado", 100, 500.0, "CAM MC UNO MQS", date(2026, 9, 2), ""),
        # OC 1002: en tránsito, ETA futura 15/10 → W202635
        (1002, 60002, "B1", "M2", "PV 26/27", "E", H, "MARQUIS HOMBRE", "POLOS M/C", "POL MC", "MARQUIS", "PROV UNO", "China",
         date(2026, 10, 10), date(2026, 9, 5), date(2026, 10, 15), "5.En Transito", 200, 800.0, "POL MC DOS MQS", None, "Retraso de Naviera"),
        # OC 1003: por embarcar con ETA VENCIDA (20/09 < hoy 04/10) → atrasada
        (1003, None, "C1", "M3", "PV 26/27", "F", H, "MARQUIS HOMBRE", "PANTALONES", "PANT", "MARQUIS", "PROV DOS", "Bangladesh",
         None, None, date(2026, 9, 20), "6.Por embarcar", 100, 600.0, "PANT TRES MQS", None, "CRD con retraso"),
        # OC 1004: línea partida (mismo OC×SKU en 2 filas) → se suma 300+50 = 350; ETA 22/10 → W202636
        (1004, 60004, "D1", "M4", "PV 26/27", "E", H, "NAVIGATA HOMBRE", "CAMISAS M/L", "CAM ML", "NAVIGATA", "PROV TRES", "India",
         date(2026, 10, 18), date(2026, 9, 10), date(2026, 10, 22), "5.En Transito", 300, 900.0, "CAM ML CUATRO RGT", None, ""),
        (1004, 60004, "D1", "M4", "PV 26/27", "E", H, "NAVIGATA HOMBRE", "CAMISAS M/L", "CAM ML", "NAVIGATA", "PROV TRES", "India",
         date(2026, 10, 18), date(2026, 9, 10), date(2026, 10, 22), "5.En Transito", 50, 150.0, "CAM ML CUATRO RGT", None, ""),
        # OC 1005: recibida vía estado 2.En CD
        (1005, 60005, "E1", "M5", "PV 26/27", "F", H, "NAVIGATA HOMBRE", "SHORTS", "SHORT", "NAVIGATA", "PROV TRES", "India",
         date(2026, 9, 22), date(2026, 8, 15), date(2026, 9, 25), "2.En CD", 150, 450.0, "SHORT CINCO RGT", date(2026, 9, 28), ""),
        # OC 1006: alias de marca (U.S. POLO ASSN. → US POLO) y SIN ETA → atrasada
        (1006, None, "F1", "M6", "PV 26/27", None, H, "MARCAS CASUAL INTERNACIONAL HOMBRE", "POLOS M/C", "POL MC", "U.S. Polo Assn.", "PROV CUATRO", "China",
         None, None, None, "5.En Transito", 80, 400.0, "POL MC USP", None, ""),
        # OC 1007: SIN MARCA → hallazgo, fuera del resumen
        (1007, 60007, "G1", "M7", "PV 26/27", "E", H, "MARQUIS HOMBRE", "JEANS", "JEAN", "", "PROV UNO", "China",
         date(2026, 10, 30), date(2026, 9, 25), date(2026, 11, 5), "5.En Transito", 60, 300.0, "JEAN SIETE", None, ""),
        # OC 1008: OI 27 → fuera del filtro PV
        (1008, None, "H1", "M8", "OI 27", "A", H, "MARQUIS HOMBRE", "CHOMPAS", "SW", "MARQUIS", "PROV UNO", "China",
         None, None, date(2027, 1, 10), "6.Por embarcar", 500, 2500.0, "SW OCHO MQS", None, ""),
        # OC 1009: MUJER → fuera del filtro de división
        (1009, 60009, "I1", "M9", "PV 26/27", "E", M, "MARQUIS MUJER", "BLUSAS", "BLUSA", "MARQUIS", "PROV UNO", "China",
         date(2026, 10, 5), date(2026, 9, 1), date(2026, 10, 10), "5.En Transito", 999, 999.0, "BLUSA NUEVE", None, ""),
        # OC 1010: FOB cero → hallazgo COSTO_CERO; ETA 20/11 → W202640
        (1010, None, "J1", "M10", "PV 26/27", "F", H, "MARQUIS HOMBRE", "CAMISAS M/C", "CAM MC", "MARQUIS", "PROV DOS", "Bangladesh",
         None, None, date(2026, 11, 20), "6.Por embarcar", 120, 0.0, "CAM MC DIEZ MQS", None, ""),
    ]
    df = pd.DataFrame(filas, columns=cols)
    # fechas como serial Excel, como las exporta comex (y como las lee pyxlsb)
    for c in ("ETA", "ETD", "PROYECTADO INGRESO CD3", "INGRESOCD"):
        df[c] = df[c].map(_serial)
    return df


def llegadas() -> dict[str, pd.DataFrame]:
    """Tres hojas al estilo de Franco. Las fechas van como serial; el encabezado NO está en la fila 0."""
    cols = ["OC", "EMBARQUE", "ESTADO_EMBARQUE_FINAL", "LSD", "LINEA", "MARCA", "MODELO", "STATUS", "ETD1",
            "ETA CALLAO", "PROYECTADO INGRESO CD3", "MOTIVO_RETRASO", "UNIDADES", "ETD-LSD"]
    s = _serial
    e = [
        (2001, 70001, "5.En Transito", s(date(2026, 8, 20)), "CAMISAS M/L", "SPAVALDI", "CAM ML A SP", "Shipped", s(date(2026, 9, 5)), s(date(2026, 10, 17)), s(date(2026, 10, 25)), "Retraso de Naviera", 800, 16),
        (2002, 70002, "1.Almacenado", s(date(2026, 8, 1)), "POLOS M/C", "SPAVALDI", "POL MC B SP", "Arrived", s(date(2026, 8, 10)), s(date(2026, 9, 23)), s(date(2026, 9, 29)), "CRD con retraso", 2000, 9),
        (2003, 70003, "5.En Transito", s(date(2026, 8, 20)), "CAMISAS M/C", "MARQUIS", "CAM MC C MQS", "Shipped", 0, 0, s(date(2026, 9, 28)), "", 1100, -46285),
        ("(en blanco)", 70004, "5.En Transito", s(date(2026, 8, 20)), "#N/D", "NAVIGATA", "#N/D", "Shipped", s(date(2026, 9, 5)), s(date(2026, 10, 1)), s(date(2026, 10, 7)), "", 160, 29),
        ("Total general", None, None, None, None, None, None, None, None, None, None, None, 4060, 0),
    ]
    f = [
        (2004, None, "6.Por embarcar", s(date(2026, 9, 10)), "PANTALONES", "CACHAREL", "PANT D CAH", "Booked", s(date(2026, 9, 28)), s(date(2026, 11, 9)), s(date(2026, 11, 15)), "", 1800, 18),
        ("Total general", None, None, None, None, None, None, None, None, None, None, None, 1800, 0),
    ]
    a = [
        (2005, None, "6.Por embarcar", s(date(2026, 9, 10)), "POLOS M/C", "NAVIGATA", "POL MC E RGT", "Booked", s(date(2026, 9, 28)), s(date(2026, 12, 9)), s(date(2026, 12, 15)), "", 6000, 18),
        ("Total general", None, None, None, None, None, None, None, None, None, None, None, 6000, 0),
    ]
    hoja_e = pd.DataFrame([["fecha CD china ", s(date(2026, 9, 16))] + [None] * 12,
                           ["fecha CD bangla", s(date(2026, 9, 10))] + [None] * 12,
                           [None] * 14, cols] + [list(r) for r in e])
    hoja_f = pd.DataFrame([["fecha CD china ", s(date(2026, 10, 16))] + [None] * 12,
                           ["fecha CD bangla", s(date(2026, 10, 10))] + [None] * 12,
                           [None] * 14, cols] + [list(r) for r in f])
    cons_rows = [r for r in e + f + a if isinstance(r[0], int)]
    hoja_c = pd.DataFrame([[None] * 15, [None] + cols] + [[None] + list(r) for r in cons_rows])
    hoja_a = pd.DataFrame([cols] + [list(r) for r in a])
    return {"VENTANA E FRANCO": hoja_e, "VENTANA F FRANCO": hoja_f, "CONSOLIDADO": hoja_c, "VENTANA OI27 A FRANCO": hoja_a}


def main():
    p1 = os.path.join(AQUI, "detalle_comex_mini.xlsx")
    with pd.ExcelWriter(p1, engine="openpyxl") as w:
        pd.DataFrame({"x": ["hoja de portada sin tabla"]}).to_excel(w, sheet_name="Hoja1", index=False)
        detalle().to_excel(w, sheet_name="Export", index=False)
    p2 = os.path.join(AQUI, "llegadas_franco_mini.xlsx")
    with pd.ExcelWriter(p2, engine="openpyxl") as w:
        for h, df in llegadas().items():
            df.to_excel(w, sheet_name=h, index=False, header=False)
    print("fixtures:", p1, p2)


if __name__ == "__main__":
    main()
