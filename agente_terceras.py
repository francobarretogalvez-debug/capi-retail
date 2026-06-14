"""
agente_terceras.py — Primer agente de Capi.

Detecta oportunidades con marcas TERCERAS y genera BORRADORES de correo a
proveedores que Franco aprueba antes de enviar. Nunca envía solo: el flujo
seguro es generar texto → dejar borrador en Gmail → Franco revisa y envía.
(Fase 1 de inducción supervisada del diseño original.)

Dos tipos de oportunidad:
  - capital_parado: marca tercera con capital inmovilizado alto + sell-through
    bajo → correo pidiendo rebate / apoyo de markdown / devolución.
  - quiebre: marca tercera agotada que vendía bien (requiere_proveedor) →
    correo pidiendo reorder.

Reusa el patrón de llamada a Claude de chat_engine, con system prompt comercial.
"""
from __future__ import annotations

import json
import os
from typing import Optional

import numpy as np
import pandas as pd

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

# ── Config ──
MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 1200
TEMPERATURE = 0.3  # algo de variación: es redacción, no análisis

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROVEEDORES_PATH = os.path.join(_BASE_DIR, "config_proveedores.json")

_MARCAS_PROPIAS = {'MARQUIS', 'NAVIGATA', 'CACHAREL', 'SPAVALDI',
                   'OSCAR DE LA RENTA', 'US POLO', 'NAUTICA'}

# Universo EXPLÍCITO del agente (definido por Franco 2026-06-12). No es "todo lo
# que no es propia": es esta lista blanca de marcas con presencia real en Ripley.
# Incluye Oscar de la Renta, US Polo y Nautica (que son propias en clasificación,
# pero Franco las gestiona vía este flujo). Excluye marcas sin presencia ya
# (Psycho Bunny, Dockers2, Arrow, Givenchy, Van Heusen).
MARCAS_AGENTE = {
    'JOHN HOLDEN', 'PIERRE CARDIN', 'DOCKERS', 'LACOSTE', 'SELECTED',
    'SILBON', 'NORTON', 'OSCAR DE LA RENTA', 'US POLO', 'NAUTICA',
}

# Universo de marcas PROPIAS (las 7). Oscar de la Renta, US Polo y Nautica
# están también en MARCAS_AGENTE (Franco las gestiona en ambos contextos).
MARCAS_PROPIAS_SET = {
    'MARQUIS', 'NAVIGATA', 'CACHAREL', 'SPAVALDI',
    'OSCAR DE LA RENTA', 'US POLO', 'NAUTICA',
}

SYSTEM_PROMPT_CORREO = """Eres el asistente de un Senior Fashion Buyer de Ripley (retail de moda, Perú).
Redactas correos comerciales a proveedores y representantes de marca: profesionales,
directos, cordiales, en español peruano de negocios. Sin relleno ni adjetivos vacíos.
El buyer revisará y enviará el correo, así que escribe en su voz (primera persona).
Estructura: saludo breve, contexto con los datos duros, pedido concreto, cierre cordial.
Nunca inventes cifras: usa SOLO los datos que se te dan. Devuelve EXACTAMENTE este formato:
ASUNTO: <línea de asunto>
---
<cuerpo del correo>"""


# ══════════════════════════════════════════════════════════════
#  CONTACTOS
# ══════════════════════════════════════════════════════════════

def cargar_proveedores(path: str = None) -> dict:
    """Carga el mapa marca -> contacto. Ignora claves que empiezan con '_'."""
    p = path or _PROVEEDORES_PATH
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {k.upper(): v for k, v in raw.items()
            if not k.startswith("_") and isinstance(v, dict) and v.get("contacto")}


# ══════════════════════════════════════════════════════════════
#  DETECCIÓN DE OPORTUNIDADES
# ══════════════════════════════════════════════════════════════

def detectar_capital_parado(df_cob: pd.DataFrame, min_capital: float = 50000,
                            max_sell_through: float = 15.0) -> pd.DataFrame:
    """Marcas terceras con capital inmovilizado alto y sell-through bajo.
    Candidatas a pedir rebate / apoyo de markdown / devolución."""
    _req = {'marca', 'sku', 'stock_valor_costo', 'stock_total', 'cobertura_sem', 'prom_vta_uds'}
    if df_cob.empty or not _req.issubset(df_cob.columns):
        return pd.DataFrame()

    terc = df_cob[df_cob['marca'].str.upper().str.strip().isin(MARCAS_AGENTE)].copy()
    if terc.empty:
        return pd.DataFrame()

    g = terc.groupby('marca').agg(
        capital=('stock_valor_costo', 'sum'),
        stock_uds=('stock_total', 'sum'),
        cob_prom=('cobertura_sem', 'mean'),
        n_skus=('sku', 'nunique'),
        vta_sem_uds=('prom_vta_uds', 'sum'),
    ).reset_index()

    # Sell-through aprox (1 semana de venta vs stock)
    g['sell_through'] = np.where(
        (g['vta_sem_uds'] + g['stock_uds']) > 0,
        (g['vta_sem_uds'] / (g['vta_sem_uds'] + g['stock_uds']) * 100).round(1),
        0.0,
    )

    # Margen efectivo por marca (dedup SKU)
    if {'vta_soles_4sem', 'contrib_soles_4sem'}.issubset(terc.columns):
        sku = terc.drop_duplicates('sku')[['marca', 'vta_soles_4sem', 'contrib_soles_4sem']]
        m = sku.groupby('marca').agg(vta=('vta_soles_4sem', 'sum'),
                                     contrib=('contrib_soles_4sem', 'sum')).reset_index()
        m['margen_efectivo'] = np.where(m['vta'] > 0,
                                        (m['contrib'] / m['vta'] * 100).round(1), 0.0)
        g = g.merge(m[['marca', 'margen_efectivo']], on='marca', how='left')
    else:
        g['margen_efectivo'] = np.nan

    op = g[(g['capital'] >= min_capital) & (g['sell_through'] <= max_sell_through)].copy()
    return op.sort_values('capital', ascending=False).reset_index(drop=True)


def top_skus_marca(df_cob: pd.DataFrame, marca: str, n: int = 5) -> pd.DataFrame:
    """Top SKUs por capital parado de una marca (consolidado a SKU único,
    sumando todas las tiendas), para adjuntar al correo."""
    sub = df_cob[df_cob['marca'].str.upper().str.strip() == marca.upper().strip()].copy()
    if sub.empty:
        return sub
    agg = {}
    if 'nombre' in sub.columns:           agg['nombre'] = ('nombre', 'first')
    if 'stock_total' in sub.columns:      agg['stock_total'] = ('stock_total', 'sum')
    if 'cobertura_sem' in sub.columns:    agg['cobertura_sem'] = ('cobertura_sem', 'mean')
    if 'pct_descuento' in sub.columns:    agg['pct_descuento'] = ('pct_descuento', 'max')
    if 'stock_valor_costo' in sub.columns: agg['stock_valor_costo'] = ('stock_valor_costo', 'sum')
    g = sub.groupby('sku').agg(**agg).reset_index()
    if 'stock_valor_costo' in g.columns:
        g = g.sort_values('stock_valor_costo', ascending=False)
    return g.head(n).reset_index(drop=True)


def top5_por_marca_linea(df_cob: pd.DataFrame, top_n: int = 5,
                         min_cobertura: float = 16.0, min_edad: float = 3.0) -> pd.DataFrame:
    """Top N SKUs a revisar por cada marca×línea (terceras): capital parado +
    alta cobertura. Filtros del buyer (Franco):
      - solo cobertura > min_cobertura (sobrestock real, no ÓPTIMO)
      - solo edad >= min_edad semanas (los recién ingresados tienen cobertura
        alta artificial por poca venta acumulada, no por sobrestock)
    Devuelve filas ordenadas por marca, línea y score (capital × cobertura)."""
    if df_cob.empty or 'marca' not in df_cob.columns or 'categoria' not in df_cob.columns:
        return pd.DataFrame()
    terc = df_cob[df_cob['marca'].str.upper().str.strip().isin(MARCAS_AGENTE)].copy()
    if terc.empty:
        return pd.DataFrame()

    agg = {}
    if 'nombre' in terc.columns:            agg['nombre'] = ('nombre', 'first')
    if 'edad_semanas' in terc.columns:      agg['edad'] = ('edad_semanas', 'max')
    if 'stock_total' in terc.columns:       agg['stock'] = ('stock_total', 'sum')
    if 'cobertura_sem' in terc.columns:     agg['cobertura'] = ('cobertura_sem', 'mean')
    if 'pct_descuento' in terc.columns:     agg['dscto'] = ('pct_descuento', 'max')
    if 'stock_valor_costo' in terc.columns: agg['capital'] = ('stock_valor_costo', 'sum')
    if 'prom_vta_uds' in terc.columns:      agg['vta_sem'] = ('prom_vta_uds', 'sum')
    if 'margen_efectivo' in terc.columns:   agg['margen'] = ('margen_efectivo', 'first')
    if not agg:
        return pd.DataFrame()

    # Consolidar a SKU primero (cobertura = promedio real de todas las tiendas,
    # edad = antigüedad del producto), y filtrar DESPUÉS para no sesgar promedios.
    g = terc.groupby(['marca', 'categoria', 'sku']).agg(**agg).reset_index()
    g['cobertura'] = g.get('cobertura', pd.Series(0, index=g.index)).fillna(0)
    g['capital'] = g.get('capital', pd.Series(0, index=g.index)).fillna(0)
    if 'edad' in g.columns:
        g = g[g['edad'].fillna(0) >= min_edad]  # excluir ingresos recientes (cobertura artificial)
    g = g[g['cobertura'] > min_cobertura]  # sobrestock real
    if g.empty:
        return g
    g['score'] = (g['capital'] * g['cobertura']).round(0)
    # Métricas comerciales para el proveedor + redondeo limpio
    if 'vta_sem' in g.columns and 'stock' in g.columns:
        _den = g['vta_sem'] + g['stock']
        g['sell_through'] = np.where(_den > 0, (g['vta_sem'] / _den * 100).round(1), 0.0)
    if 'margen' in g.columns:
        g['margen_efectivo'] = (g['margen'].fillna(0) * 100).round(1)
    g['cobertura'] = g['cobertura'].round(1)
    g['capital'] = g['capital'].round(0)
    g = g.sort_values(['marca', 'categoria', 'score'], ascending=[True, True, False])
    g = g.groupby(['marca', 'categoria'], sort=True).head(top_n).reset_index(drop=True)

    # Criticidad relativa dentro de cada marca×línea (tercios del ranking).
    # NO usar groupby.apply: en pandas 3.0 elimina las columnas de agrupación
    # (marca/categoria) del resultado. cumcount + transform preservan todo.
    _pos = g.groupby(['marca', 'categoria']).cumcount()
    _n = g.groupby(['marca', 'categoria'])['sku'].transform('size')
    g['criticidad'] = np.where(
        _pos < _n / 3, "🔴 Crítico",
        np.where(_pos < 2 * _n / 3, "🟠 Alto", "🟡 Medio"),
    )
    return g.reset_index(drop=True)


def descuento_sugerido(edad_sem: float) -> tuple:
    """Pirámide de descuentos por antigüedad (tabla de Franco, igual para todas
    las terceras). Devuelve (descuento_fraccion, tipo). 'Eventual' = evento de
    precio temporal (sem 8-18); 'Fijo' = markdown permanente (sem 19+)."""
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


def sugerencias_precio_terceras(df_cob: pd.DataFrame, marcas: set = None) -> pd.DataFrame:
    """Para cada SKU del universo `marcas`: descuento sugerido por su antigüedad
    (misma pirámide) vs descuento actual → acción de precio. Nivel SKU (pricing
    omnicanal). `marcas` por defecto = terceras; pasar MARCAS_PROPIAS_SET para propias."""
    marcas = marcas if marcas is not None else MARCAS_AGENTE
    _req = {'marca', 'sku', 'edad_semanas', 'pct_descuento'}
    if df_cob.empty or not _req.issubset(df_cob.columns):
        return pd.DataFrame()
    terc = df_cob[df_cob['marca'].str.upper().str.strip().isin(marcas)].copy()
    if terc.empty:
        return pd.DataFrame()

    agg = {'marca': ('marca', 'first'), 'edad': ('edad_semanas', 'max'),
           'dscto_actual': ('pct_descuento', 'max')}
    if 'nombre' in terc.columns:            agg['nombre'] = ('nombre', 'first')
    if 'categoria' in terc.columns:         agg['categoria'] = ('categoria', 'first')
    if 'stock_valor_costo' in terc.columns: agg['capital'] = ('stock_valor_costo', 'sum')
    if 'cobertura_sem' in terc.columns:     agg['cobertura'] = ('cobertura_sem', 'mean')
    g = terc.groupby('sku').agg(**agg).reset_index()

    _sug = g['edad'].apply(descuento_sugerido)
    g['dscto_sugerido'] = _sug.apply(lambda t: t[0])
    g['tipo'] = _sug.apply(lambda t: t[1])
    g['dscto_actual'] = g['dscto_actual'].fillna(0)
    g['gap'] = (g['dscto_sugerido'] - g['dscto_actual']).round(2)

    def _accion(r):
        if r['gap'] >= 0.05:
            return f"⬆️ Subir a {r['dscto_sugerido']:.0%}"
        if r['gap'] <= -0.05:
            return f"⬇️ Sobre-descuento ({r['dscto_actual']:.0%} vs {r['dscto_sugerido']:.0%} sugerido)"
        return "✓ Alineado"
    g['accion'] = g.apply(_accion, axis=1)
    # Orden: primero los que requieren subir, por capital parado
    g['_prio'] = (g['gap'] >= 0.05).astype(int)
    g = g.sort_values(['_prio', 'capital' if 'capital' in g.columns else 'gap'],
                      ascending=[False, False]).drop(columns='_prio')
    return g.reset_index(drop=True)


def detectar_quiebre_tercera(df_cob: pd.DataFrame, min_vta: float = 1.0) -> pd.DataFrame:
    """Marcas terceras con SKUs en quiebre que vendían bien (requiere reorder)."""
    if df_cob.empty or 'marca' not in df_cob.columns or 'estado' not in df_cob.columns:
        return pd.DataFrame()
    terc = df_cob[df_cob['marca'].str.upper().str.strip().isin(MARCAS_AGENTE)].copy()
    q = terc[(terc['estado'] == 'QUIEBRE') & (terc['prom_vta_uds'].fillna(0) >= min_vta)].copy()
    if q.empty:
        return pd.DataFrame()
    q['venta_riesgo'] = (q['prom_vta_uds'] * q.get('precio_vigente', 0).fillna(0)).round(0)
    g = q.groupby('marca').agg(
        n_skus_quiebre=('sku', 'nunique'),
        venta_riesgo_sem=('venta_riesgo', 'sum'),
        vta_sem_uds=('prom_vta_uds', 'sum'),
    ).reset_index()
    return g.sort_values('venta_riesgo_sem', ascending=False).reset_index(drop=True)


# ══════════════════════════════════════════════════════════════
#  GENERACIÓN DEL BORRADOR
# ══════════════════════════════════════════════════════════════

def _call_claude(prompt: str) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("No se encontró ANTHROPIC_API_KEY. Configúrala en .env")
    if Anthropic is None:
        raise ImportError("Instala el SDK: pip install anthropic")
    client = Anthropic(api_key=api_key, timeout=30.0, max_retries=1)
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, temperature=TEMPERATURE,
            system=SYSTEM_PROMPT_CORREO, messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        _n = type(e).__name__
        if "RateLimit" in _n or "Overloaded" in str(e):
            raise ValueError("El asistente está saturado. Intenta en unos segundos.")
        if "Timeout" in _n or "Connection" in _n:
            raise ValueError("El asistente tardó demasiado. Reintenta.")
        raise
    return resp.content[0].text


def _parse_correo(texto: str) -> dict:
    """Separa ASUNTO y cuerpo del formato devuelto por Claude."""
    asunto, cuerpo = "", texto.strip()
    if "ASUNTO:" in texto:
        resto = texto.split("ASUNTO:", 1)[1]
        if "---" in resto:
            asunto, cuerpo = resto.split("---", 1)
        else:
            partes = resto.split("\n", 1)
            asunto = partes[0]
            cuerpo = partes[1] if len(partes) > 1 else ""
    return {"asunto": asunto.strip(), "cuerpo": cuerpo.strip()}


def generar_correo_capital_parado(marca_row: pd.Series, proveedor: dict = None,
                                  top_skus: pd.DataFrame = None, buyer: str = "Franco Barreto",
                                  detalle_lineas: pd.DataFrame = None) -> dict:
    """Borrador pidiendo rebate / apoyo de markdown por capital parado.
    proveedor es opcional. Si se pasa detalle_lineas (top SKUs por línea de la
    marca), el correo comunica el desglose por línea, no solo el agregado."""
    proveedor = proveedor or {}
    top_skus = top_skus if top_skus is not None else pd.DataFrame()

    # Desglose por línea (si está disponible) — lo que Franco pidió comunicar
    if detalle_lineas is not None and not detalle_lineas.empty:
        bloques = []
        for _linea, _grp in detalle_lineas.groupby('categoria', sort=False):
            _cap_lin = _grp['capital'].sum() if 'capital' in _grp else 0
            _items = "\n".join(
                f"    · {r.get('nombre', r.get('sku'))}: {int(r.get('stock', 0))} uds, "
                f"cobertura {r.get('cobertura', 0):.0f} sem, S/ {r.get('capital', 0):,.0f}"
                for _, r in _grp.head(5).iterrows()
            )
            bloques.append(f"  {_linea} (S/ {_cap_lin:,.0f} en total):\n{_items}")
        skus_txt = "\n".join(bloques)
    else:
        skus_txt = "\n".join(
            f"  - {r.get('nombre', r.get('sku'))}: {int(r.get('stock_total', 0))} uds, "
            f"cobertura {r.get('cobertura_sem', 0):.0f} sem, "
            f"S/ {r.get('stock_valor_costo', 0):,.0f} a costo"
            for _, r in top_skus.iterrows()
        )
    _dest = (f"{proveedor.get('contacto','')} ({proveedor.get('empresa','')})"
             if proveedor.get('contacto') else f"representante comercial de la marca {marca_row['marca']}")
    prompt = f"""Redacta un correo al representante de la marca {marca_row['marca']} en Ripley.

DESTINATARIO: {_dest}
REMITENTE: {buyer}, Senior Fashion Buyer - Moda Masculina, Ripley.

SITUACIÓN (datos reales del inventario):
- Capital inmovilizado en la marca: S/ {marca_row['capital']:,.0f} (a costo)
- {int(marca_row['n_skus'])} SKUs, {int(marca_row['stock_uds']):,} unidades en stock
- Cobertura promedio: {marca_row['cob_prom']:.0f} semanas (muy por encima del objetivo de 12)
- Sell-through: {marca_row['sell_through']:.0f}% (bajo)
- Margen efectivo actual: {marca_row.get('margen_efectivo', 0):.0f}%

Detalle por línea de producto (SKUs prioritarios a revisar):
{skus_txt}

PEDIDO: solicitar apoyo comercial para liquidar este stock — rebate (descuento
post-compra), apoyo de markdown (que la marca cofinancie la rebaja de precio),
o devolución parcial. Menciona que el detalle está desglosado POR LÍNEA de
producto para facilitar la conversación. Propón una reunión para revisar
opciones línea por línea. Tono colaborativo: es una relación de largo plazo."""
    return {**_parse_correo(_call_claude(prompt)),
            "para": proveedor.get("email", ""), "marca": marca_row['marca'],
            "tipo": "capital_parado"}


def generar_correo_reorder(marca_row: pd.Series, proveedor: dict = None,
                           buyer: str = "Franco Barreto") -> dict:
    """Borrador pidiendo reorder por quiebre de marca tercera con venta.
    proveedor es opcional."""
    proveedor = proveedor or {}
    _dest = (f"{proveedor.get('contacto','')} ({proveedor.get('empresa','')})"
             if proveedor.get('contacto') else f"proveedor/representante de la marca {marca_row['marca']}")
    prompt = f"""Redacta un correo al proveedor/fabricante de la marca {marca_row['marca']}.

DESTINATARIO: {_dest}
REMITENTE: {buyer}, Senior Fashion Buyer - Moda Masculina, Ripley.

SITUACIÓN (datos reales):
- {int(marca_row['n_skus_quiebre'])} SKUs de la marca están en QUIEBRE de stock
- Estos productos venden {marca_row['vta_sem_uds']:.0f} unidades/semana en conjunto
- Venta en riesgo por el quiebre: S/ {marca_row['venta_riesgo_sem']:,.0f} por semana

PEDIDO: solicitar reorder urgente de estos productos, confirmar disponibilidad y
plazo de entrega. Tono de urgencia comercial pero cordial."""
    return {**_parse_correo(_call_claude(prompt)),
            "para": proveedor.get("email", ""), "marca": marca_row['marca'],
            "tipo": "reorder"}
