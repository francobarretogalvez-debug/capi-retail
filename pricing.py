"""
pricing.py — Lógica de precios unificada de Capi.

Decisión Franco 2026-08-26 (ficha #19, opción C): una sola fuente de verdad
para sugerir precios en toda la herramienta. Tres reglas, en este orden:

  1. PIRÁMIDE por antigüedad (tabla oficial de Franco, igual para todo el
     surtido): define el descuento objetivo según semanas de vida del SKU.
     Antes de la semana 8 NO se toca precio (recién llegado).
  2. PISO por margen mínimo: el precio sugerido nunca perfora
     costo / (1 − margen_min), llevado a base con IGV.
  3. NUNCA SUBIR: si el piso deja el sugerido en o sobre el vigente,
     la acción es mantener (fix 2026-08-17).

Consumidores: motor_v2.build_acciones_precio, agente_terceras (re-export de
descuento_sugerido), afinidad_engine (liquidación localizada), reportes_marcas.
"""

IGV = 1.18  # precios de venta incluyen IGV; costo viene ex-IGV


def descuento_sugerido(edad_sem: float) -> tuple:
    """Pirámide de descuentos por antigüedad. Devuelve (descuento_fraccion, tipo).
    'Eventual' = evento de precio temporal (sem 8-18); 'Fijo' = markdown
    permanente (sem 19+)."""
    e = edad_sem or 0
    if e < 8:    return 0.0, ""
    if e < 12:   return 0.20, "Eventual"
    if e < 19:   return 0.30, "Eventual"
    if e < 30:   return 0.30, "Fijo"
    if e < 35:   return 0.40, "Fijo"
    if e < 39:   return 0.50, "Fijo"
    if e < 44:   return 0.60, "Fijo"
    if e < 48:   return 0.70, "Fijo"
    return 0.80, "Fijo"


def precio_piso(costo: float, margen_min: float) -> float:
    """Precio mínimo (con IGV) que respeta el margen mínimo sobre costo ex-IGV."""
    base = costo / (1 - margen_min) if (1 - margen_min) > 0 else costo * 1.01
    return round(base * IGV, 2)


def sugerir_precio(precio_vigente: float, costo: float, edad_sem: float,
                   margen_min: float) -> dict:
    """Aplica pirámide + piso + nunca-subir sobre un SKU. Devuelve dict con:
    dscto_piramide, tipo ('Eventual'/'Fijo'/''), precio_minimo, precio_sugerido,
    dscto_real (vs vigente), margen_post (ex-IGV) y en_piso (True si el vigente
    ya está en/bajo el piso → mantener)."""
    dscto, tipo = descuento_sugerido(edad_sem)
    piso = precio_piso(costo, margen_min)
    precio_sug = max(round(precio_vigente * (1 - dscto), 2), piso)
    en_piso = False
    if precio_sug >= precio_vigente:
        precio_sug = precio_vigente
        en_piso = True
    dscto_real = round(1 - precio_sug / precio_vigente, 3) if precio_vigente > 0 else 0.0
    pv_exigv = precio_sug / IGV
    margen_post = round((pv_exigv - costo) / pv_exigv, 3) if pv_exigv > 0 else 0.0
    return {
        "dscto_piramide": dscto,
        "tipo": tipo,
        "precio_minimo": piso,
        "precio_sugerido": precio_sug,
        "dscto_real": dscto_real,
        "margen_post": margen_post,
        "en_piso": en_piso,
    }
