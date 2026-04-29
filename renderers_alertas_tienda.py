"""
renderers_alertas_tienda.py — Exportador WhatsApp para alertas por tienda.

Consume el payload producido por `motor_v2.build_alertas_tienda()` y genera
texto plano en formato WhatsApp (UTF-8, con emojis) + batch ZIP para las
30+ tiendas.

Diseño post-abril-2026 (Franco):
  • Solo WhatsApp texto — PDF y HTML eliminados.
  • Cada SKU disparado (cobertura ≥16 sem + edad ≥2 sem) muestra dos bloques
    de revisión: exhibición (siempre) y precio (si hay descuento, según
    tipo MD1/PTR). MTR no genera bloque de precio.
"""

from __future__ import annotations
import io
import zipfile
from typing import Dict


# ─────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────

def _fmt_soles(v: float) -> str:
    """Formatea soles estilo peruano: 'S/ 29,170'."""
    try:
        return f"S/ {v:,.0f}"
    except Exception:
        return "S/ 0"


def _safe_filename(name: str) -> str:
    """Convierte nombre de tienda a filename ASCII seguro."""
    return (name.replace(' ', '_')
                .replace('/', '_')
                .replace('ñ', 'n')
                .replace('Ñ', 'N')
                .encode('ascii', 'ignore').decode('ascii'))


# ─────────────────────────────────────────────────────────────
#  Render WhatsApp
# ─────────────────────────────────────────────────────────────

def render_whatsapp(payload: Dict) -> str:
    """
    Genera un mensaje WhatsApp (texto plano, emojis Unicode).

    Estructura:
      📍 *<TIENDA> — <fecha>*
      Resumen: N productos con cobertura alta en tienda.
      Capital inmovilizado: S/ X.

      1. *<Marca> — <Nombre>*
         Stock: N uds | Edad: X sem | Cob: Y sem
         🔎 Exhibición: <mensaje>
         💲 Precio: <mensaje>   ← solo si aplica

      2. ...

      _Generado por Capi_
    """
    tienda      = payload.get('tienda', '')
    fecha_corte = payload.get('fecha_corte', '')
    resumen     = payload.get('resumen', {})
    items       = payload.get('items', [])

    n_items   = resumen.get('n_items', 0)
    capital   = resumen.get('capital_parado_sol', 0.0)
    n_dscto   = resumen.get('n_con_descuento', 0)

    lines = []
    lines.append(f"📍 *{tienda.upper()} — {fecha_corte}*")

    if n_items == 0:
        lines.append("")
        lines.append("✅ No hay productos que requieran revisión hoy.")
        lines.append("")
        lines.append("_Generado por Capi_")
        return "\n".join(lines)

    lines.append(f"Revisión de *{n_items} productos* con cobertura alta.")
    lines.append(f"💰 Capital inmovilizado: *{_fmt_soles(capital)}*")
    if n_dscto:
        lines.append(f"🏷️ {n_dscto} con descuento vigente (revisar comunicación de precio).")
    lines.append("")
    lines.append("Revisa cada producto: exhibición + precio (si tiene descuento).")
    lines.append("")

    for idx, it in enumerate(items, start=1):
        lines.append(f"*{idx}. {it['producto']}*")
        lines.append(
            f"   Stock: {it['stock_actual']} uds | "
            f"Edad: {it['edad_semanas']} sem | "
            f"Cob: {it['cobertura_sem']:.0f} sem | "
            f"{_fmt_soles(it['capital_parado_sol'])} parado"
        )
        lines.append(f"   🔎 {it['mensaje_exhibicion']}")
        if it.get('mensaje_precio'):
            lines.append(f"   💲 {it['mensaje_precio']}")
        lines.append("")

    lines.append("_Generado por Capi_")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
#  Render WhatsApp — Venta Cero
# ─────────────────────────────────────────────────────────────

def render_whatsapp_venta_cero(payload: Dict) -> str:
    """
    Genera un mensaje WhatsApp para alertas de venta cero por tienda.

    Estructura:
      🔴 *<TIENDA> — VENTA CERO — <fecha>*
      N productos con venta 0 y capital relevante.
      Capital parado: S/ X.

      ▸ *<Marca>* (N SKUs · S/ X parado)
        1. <Nombre> | Stock: N | Edad: X sem | S/ Y parado
           → <mensaje>
        ...

      _Generado por Capi_
    """
    tienda      = payload.get('tienda', '')
    fecha_corte = payload.get('fecha_corte', '')
    resumen     = payload.get('resumen', {})
    por_marca   = payload.get('por_marca', {})

    n_skus    = resumen.get('n_skus', 0)
    n_marcas  = resumen.get('n_marcas', 0)
    capital   = resumen.get('capital_parado_total', 0.0)

    lines = []
    lines.append(f"🔴 *{tienda.upper()} — VENTA CERO — {fecha_corte}*")

    if n_skus == 0:
        lines.append("")
        lines.append("✅ No hay productos con venta cero relevante esta semana.")
        lines.append("")
        lines.append("_Generado por Capi_")
        return "\n".join(lines)

    lines.append(f"*{n_skus} productos* sin venta la semana pasada en {n_marcas} marcas.")
    lines.append(f"💰 Capital parado: *{_fmt_soles(capital)}*")
    lines.append("")

    for marca, mdata in por_marca.items():
        m_items = mdata.get('items', [])
        m_capital = mdata.get('capital', 0)
        lines.append(f"▸ *{marca}* ({len(m_items)} SKUs · {_fmt_soles(m_capital)} parado)")

        for idx, it in enumerate(m_items, start=1):
            nombre = it.get('nombre', it.get('sku', ''))
            stock = it.get('stock_total', 0)
            edad = it.get('edad_semanas', 0)
            cap = it.get('stock_valor_costo', 0)
            lines.append(
                f"   {idx}. {nombre} | Stock: {stock} | "
                f"Edad: {edad} sem | {_fmt_soles(cap)} parado"
            )
            msg = it.get('mensaje', '')
            if msg:
                lines.append(f"      → {msg}")

        lines.append("")

    lines.append("_Generado por Capi_")
    return "\n".join(lines)


def render_batch_zip_venta_cero(alertas_venta_cero: Dict[str, Dict]) -> bytes:
    """
    Empaqueta un .txt WhatsApp por tienda (venta cero) dentro de un ZIP.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for tienda, payload in alertas_venta_cero.items():
            if payload.get('resumen', {}).get('n_skus', 0) == 0:
                continue
            fecha = payload.get('fecha_corte', '')
            safe  = _safe_filename(tienda)
            fname = f"{fecha}_VentaCero_{safe}.txt"
            zf.writestr(fname, render_whatsapp_venta_cero(payload))
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────
#  ZIP batch para todas las tiendas (sobrestock)
# ─────────────────────────────────────────────────────────────

def render_batch_zip(alertas_tienda: Dict[str, Dict]) -> bytes:
    """
    Empaqueta un .txt WhatsApp por tienda dentro de un ZIP.

    Retorna bytes del ZIP (para st.download_button).
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for tienda, payload in alertas_tienda.items():
            fecha = payload.get('fecha_corte', '')
            safe  = _safe_filename(tienda)
            fname = f"{fecha}_Alertas_{safe}.txt"
            zf.writestr(fname, render_whatsapp(payload))
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────
#  CLI test
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import motor_v2 as m
    path = sys.argv[1] if len(sys.argv) > 1 else \
        '/Users/francobarreto/Downloads/Base Profundidad al 08.04.xlsx'
    res = m.run_analysis(path, formato='ripley')
    at = res['alertas_tienda']
    # Mostrar WhatsApp de la primera tienda con items
    for t, p in at.items():
        if p['items']:
            print(render_whatsapp(p))
            print("\n---")
            print(f"Total chars: {len(render_whatsapp(p))}")
            break
