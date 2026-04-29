"""
Capi — El Cockpit Semanal del Comprador
========================================
Lee la plantilla del cliente (4 pestañas) y muestra:
  - Vista 1: Reposición (quiebres, cobertura, pareto tiendas)
  - Vista 2: Sobrestock (real vs aparente, obsoletos, transferencias)
  - Vista 3: Marcas Terceras (margen, contribución, sell-through)
  - Alertas IA + Briefing Semanal + Chat IA
"""

import io
import os
import sys
import tempfile

import altair as alt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from openpyxl.utils import get_column_letter

# Fix para datasets grandes: aumentar límite de celdas del Pandas Styler
pd.set_option("styler.render.max_elements", 500_000)

# Motor v2 — forzar recarga para que cambios se reflejen sin reiniciar Streamlit
sys.path.insert(0, os.path.dirname(__file__))
import importlib
import motor_v2
importlib.reload(motor_v2)
import renderers_alertas_tienda as R_at
importlib.reload(R_at)
import transformar_profundidad as etl_profundidad
importlib.reload(etl_profundidad)

# Cargar .env antes de importar chat_engine
try:
    from dotenv import load_dotenv
    _env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    load_dotenv(_env_file)
except ImportError:
    # Si no hay python-dotenv, intentar leer .env manualmente
    _env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(_env_file):
        with open(_env_file) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ[_k.strip()] = _v.strip()

import chat_engine

# ══════════════════════════════════════════════════════════════
#  CONFIG DE PÁGINA
# ══════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Capi — Cockpit del Comprador",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Paleta de colores Capi ──
TEAL_600 = "#0D9488"
TEAL_700 = "#0F766E"
TEAL_50  = "#F0FDFA"
SLATE_900 = "#0F172A"
SLATE_800 = "#1E293B"
SLATE_700 = "#334155"
SLATE_500 = "#64748B"
SLATE_400 = "#94A3B8"
SLATE_200 = "#E2E8F0"
SLATE_100 = "#F1F5F9"
SLATE_50  = "#F8FAFC"
STATUS_CRITICO    = "#EF4444"
STATUS_PRECRITICO = "#F97316"
STATUS_OPTIMO     = "#10B981"
STATUS_ALTO       = "#F59E0B"
STATUS_SOBRESTOCK = "#E11D48"
STATUS_LIQUIDAR   = "#EC4899"
STATUS_NUEVO_SV   = "#94A3B8"
STATUS_DORMIDO    = "#78716C"
STATUS_MUERTO     = "#374151"

st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    /* ── Global ──────────────────────────────── */
    html, body, [class*="css"] {{
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    }}
    .main .block-container {{
        padding-top: 1.5rem;
        max-width: 1400px;
    }}
    h1, h2, h3, h4, h5 {{
        font-family: 'Inter', sans-serif;
        color: {SLATE_900};
    }}

    /* ── Header (Nansen-inspired, compact) ──── */
    .main-header {{
        background: {SLATE_900};
        padding: 1.2rem 2rem;
        border-radius: 14px;
        margin-bottom: 1.2rem;
        color: white;
        display: flex;
        align-items: center;
        justify-content: space-between;
        border: 1px solid rgba(255,255,255,0.06);
    }}
    .main-header h1 {{
        color: white; margin: 0; font-size: 1.4rem; font-weight: 700;
        letter-spacing: -0.02em;
    }}
    .main-header h1 span {{ color: {TEAL_600}; }}
    .main-header p {{
        color: rgba(255,255,255,0.5); margin: 0;
        font-size: 0.78rem; font-weight: 400;
    }}

    /* ── Chat input (Nansen AI-style, dark) ─── */
    [data-testid="stTextInput"] input[aria-label="Pregunta a Capi"] {{
        background: {SLATE_900} !important;
        border: 1px solid rgba(255,255,255,0.12) !important;
        border-radius: 16px !important;
        color: white !important;
        padding: 16px 20px !important;
        font-size: 0.95rem !important;
        height: auto !important;
    }}
    [data-testid="stTextInput"] input[aria-label="Pregunta a Capi"]::placeholder {{
        color: rgba(255,255,255,0.3) !important;
    }}
    [data-testid="stTextInput"] input[aria-label="Pregunta a Capi"]:focus {{
        border-color: {TEAL_600} !important;
        box-shadow: 0 0 0 2px rgba(13,148,136,0.2) !important;
    }}

    /* ── Right Chat Column — Nansen-style (scoped to chat only) ── */
    div[data-testid="stHorizontalBlock"]:has(.chat-panel-marker) {{
        align-items: flex-start !important;
    }}
    div[data-testid="stColumn"]:has(.chat-panel-marker) {{
        position: sticky;
        top: 0;
        max-height: 100vh;
        min-width: 380px;
        overflow-y: auto;
        background: #0B0F19 !important;
        border-left: 1px solid rgba(255,255,255,0.06);
        border-radius: 0;
        padding: 16px 20px !important;
    }}
    /* Input estilo Nansen — borde teal, fondo oscuro */
    div[data-testid="stColumn"]:has(.chat-panel-marker) .stTextInput input {{
        background: #0B0F19 !important;
        border: 1.5px solid rgba(13,148,136,0.5) !important;
        color: white !important;
        border-radius: 10px !important;
        padding: 12px 16px !important;
        font-size: 0.9rem !important;
    }}
    div[data-testid="stColumn"]:has(.chat-panel-marker) .stTextInput input::placeholder {{
        color: rgba(255,255,255,0.3) !important;
    }}
    div[data-testid="stColumn"]:has(.chat-panel-marker) .stTextInput input:focus {{
        border-color: {TEAL_600} !important;
        box-shadow: 0 0 0 2px rgba(13,148,136,0.15) !important;
    }}
    /* Botones chip estilo Nansen — fondo sutil, texto suave */
    div[data-testid="stColumn"]:has(.chat-panel-marker) .stButton > button {{
        background: rgba(255,255,255,0.04) !important;
        color: rgba(255,255,255,0.5) !important;
        border: 1px solid rgba(255,255,255,0.08) !important;
        font-size: 0.75em !important;
        padding: 4px 8px !important;
        border-radius: 6px !important;
        min-height: 0 !important;
    }}
    div[data-testid="stColumn"]:has(.chat-panel-marker) .stButton > button:hover {{
        background: rgba(255,255,255,0.08) !important;
        color: rgba(255,255,255,0.8) !important;
        border-color: rgba(13,148,136,0.3) !important;
    }}
    /* Expander en chat */
    div[data-testid="stColumn"]:has(.chat-panel-marker) .stExpander {{
        border-color: rgba(255,255,255,0.08) !important;
    }}
    div[data-testid="stColumn"]:has(.chat-panel-marker) .stExpander summary {{
        color: rgba(255,255,255,0.5) !important;
        font-size: 0.8rem !important;
    }}
    div[data-testid="stColumn"]:has(.chat-panel-marker) .stSpinner > div {{
        color: rgba(255,255,255,0.5) !important;
    }}
    /* Warning en chat */
    div[data-testid="stColumn"]:has(.chat-panel-marker) .stAlert {{
        background: rgba(255,255,255,0.04) !important;
        color: rgba(255,255,255,0.7) !important;
        border-color: rgba(255,255,255,0.08) !important;
    }}

    /* ── Chat Panel (Nansen style messages) ── */
    .nansen-chat-panel {{
        background: {SLATE_900};
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 16px;
        overflow: hidden;
        margin-top: 0.5rem;
        margin-bottom: 1rem;
    }}
    .nansen-chat-header {{
        background: rgba(255,255,255,0.04);
        border-bottom: 1px solid rgba(255,255,255,0.08);
        padding: 14px 20px;
        display: flex;
        align-items: center;
        gap: 10px;
    }}
    .nansen-chat-header .chat-logo {{
        width: 28px; height: 28px;
        background: {TEAL_600};
        border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        font-size: 0.8rem; color: white; font-weight: 700;
        flex-shrink: 0;
    }}
    .nansen-chat-header .chat-title {{
        font-size: 0.88rem; font-weight: 600; color: white;
    }}
    .nansen-chat-header .chat-badge {{
        background: rgba(13,148,136,0.2);
        color: {TEAL_600};
        font-size: 0.6rem; font-weight: 700;
        padding: 2px 8px; border-radius: 4px;
        letter-spacing: 0.05em;
        text-transform: uppercase;
    }}
    .nansen-chat-header .chat-query-preview {{
        color: rgba(255,255,255,0.4);
        font-size: 0.78rem;
        white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        max-width: 300px;
        margin-left: auto;
    }}
    .nansen-chat-body {{
        padding: 20px;
        max-height: 500px;
        overflow-y: auto;
    }}
    /* User message bubble (right-aligned, like Nansen) */
    .chat-msg-user {{
        display: flex;
        justify-content: flex-end;
        margin-bottom: 16px;
    }}
    .chat-msg-user .bubble {{
        background: rgba(255,255,255,0.08);
        color: rgba(255,255,255,0.9);
        padding: 10px 16px;
        border-radius: 16px 16px 4px 16px;
        font-size: 0.88rem;
        max-width: 80%;
    }}
    /* AI response (left-aligned) */
    .chat-msg-ai {{
        margin-bottom: 16px;
    }}
    .chat-msg-ai .ai-step {{
        display: inline-flex;
        align-items: center;
        gap: 8px;
        background: rgba(255,255,255,0.06);
        border-radius: 24px;
        padding: 5px 14px;
        font-size: 0.78rem;
        color: rgba(255,255,255,0.5);
        margin-bottom: 12px;
    }}
    .chat-msg-ai .ai-step .check {{
        color: {TEAL_600};
        font-size: 0.9rem;
    }}
    .chat-msg-ai h4 {{
        color: white;
        font-size: 1rem;
        font-weight: 700;
        margin: 0 0 10px 0;
    }}
    .chat-msg-ai p {{
        color: rgba(255,255,255,0.72);
        margin: 0 0 10px 0;
        font-size: 0.88rem;
        line-height: 1.7;
    }}
    .chat-msg-ai strong {{
        color: white;
    }}
    .chat-msg-ai .chat-insight {{
        border-top: 1px solid rgba(255,255,255,0.08);
        margin-top: 14px;
        padding-top: 12px;
    }}
    .nansen-chat-footer {{
        border-top: 1px solid rgba(255,255,255,0.08);
        padding: 10px 20px;
        text-align: center;
    }}
    .nansen-chat-footer span {{
        font-size: 0.72rem;
        color: rgba(255,255,255,0.3);
    }}

    /* ── Legacy chat-response (keep for backward compat) ── */
    .chat-response {{
        background: {SLATE_900};
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 18px;
        padding: 22px 26px;
        margin-bottom: 1rem;
        color: rgba(255,255,255,0.88);
        font-size: 0.92rem;
        line-height: 1.7;
    }}

    /* ── Live badge ─────────────────────────── */
    .live-badge {{
        display: inline-block;
        background: {TEAL_600};
        color: white;
        font-size: 0.65rem;
        font-weight: 700;
        padding: 2px 8px;
        border-radius: 4px;
        letter-spacing: 0.05em;
        margin-left: 8px;
        vertical-align: middle;
    }}

    /* ── Section headers (Nansen-style) ────── */
    .section-header {{
        display: flex;
        align-items: center;
        gap: 8px;
        margin: 1.2rem 0 0.6rem 0;
    }}
    .section-header h3 {{
        margin: 0;
        font-size: 1.05rem;
        font-weight: 600;
    }}

    /* ── KPI cards ───────────────────────────── */
    .kpi-card {{
        background: white;
        border-radius: 14px;
        padding: 1.1rem 1.3rem;
        border-left: 4px solid {TEAL_600};
        box-shadow: 0 1px 4px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
        margin-bottom: 0.6rem;
        transition: box-shadow 0.2s ease;
    }}
    .kpi-card:hover {{
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
    }}
    .kpi-val {{
        font-size: 1.9rem; font-weight: 700; color: {SLATE_900};
        line-height: 1; letter-spacing: -0.02em;
    }}
    .kpi-lbl {{
        font-size: 0.76rem; color: {SLATE_500}; margin-top: 0.3rem;
        font-weight: 500; text-transform: uppercase; letter-spacing: 0.03em;
    }}
    .kpi-card.red    {{ border-left-color: {STATUS_CRITICO}; }}
    .kpi-card.red    .kpi-val {{ color: {STATUS_CRITICO}; }}
    .kpi-card.green  {{ border-left-color: {STATUS_OPTIMO}; }}
    .kpi-card.green  .kpi-val {{ color: #059669; }}
    .kpi-card.yellow {{ border-left-color: {STATUS_ALTO}; }}
    .kpi-card.yellow .kpi-val {{ color: #D97706; }}
    .kpi-card.orange {{ border-left-color: {STATUS_SOBRESTOCK}; }}
    .kpi-card.orange .kpi-val {{ color: #EA580C; }}
    .kpi-card.darkred {{ border-left-color: {STATUS_LIQUIDAR}; }}
    .kpi-card.darkred .kpi-val {{ color: {STATUS_LIQUIDAR}; }}
    .kpi-card.blue   {{ border-left-color: {TEAL_600}; }}
    .kpi-card.blue   .kpi-val {{ color: {TEAL_700}; }}

    /* ── Sidebar (Nansen-style dark nav) ─────── */
    [data-testid="stSidebar"] {{
        background: #111318;
        border-right: 1px solid rgba(255,255,255,0.06);
    }}
    [data-testid="stSidebar"] [data-testid="stSidebarContent"] {{
        padding-top: 0.5rem;
    }}
    [data-testid="stSidebar"] * {{
        color: rgba(255,255,255,0.7) !important;
    }}
    [data-testid="stSidebar"] h1,
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3 {{
        color: white !important;
    }}
    [data-testid="stSidebar"] .stButton > button[kind="primary"] {{
        background: {TEAL_600};
        border: none;
        border-radius: 10px;
        font-weight: 600;
        padding: 0.6rem 1rem;
        transition: all 0.2s;
        width: 100%;
    }}
    [data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {{
        background: {TEAL_700};
        transform: translateY(-1px);
    }}
    [data-testid="stSidebar"] .stSlider label {{
        font-size: 0.82rem !important;
    }}
    /* Sidebar nav items styling */
    .sidebar-nav-item {{
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 8px 12px;
        border-radius: 8px;
        color: rgba(255,255,255,0.6);
        font-size: 0.85rem;
        font-weight: 500;
        transition: all 0.15s;
        cursor: default;
        margin-bottom: 2px;
    }}
    .sidebar-nav-item:hover {{
        background: rgba(255,255,255,0.06);
        color: white;
    }}
    .sidebar-nav-item.active {{
        background: rgba(13,148,136,0.15);
        color: {TEAL_600};
        font-weight: 600;
    }}
    .sidebar-nav-item .nav-icon {{
        font-size: 1rem;
        width: 20px;
        text-align: center;
    }}
    .sidebar-section-label {{
        font-size: 0.68rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: rgba(255,255,255,0.3) !important;
        padding: 16px 12px 6px 12px;
        font-weight: 600;
    }}
    /* Sidebar nav button styling — secondary (inactive) */
    [data-testid="stSidebar"] .stButton > button[kind="secondary"],
    [data-testid="stSidebar"] .stButton > button[data-testid="baseButton-secondary"] {{
        background: transparent !important;
        color: rgba(255,255,255,0.6) !important;
        border: none !important;
        text-align: left !important;
        justify-content: flex-start !important;
        padding: 8px 12px !important;
        border-radius: 8px !important;
        font-size: 0.85rem !important;
        font-weight: 500 !important;
        transition: all 0.15s !important;
        box-shadow: none !important;
    }}
    [data-testid="stSidebar"] .stButton > button[kind="secondary"]:hover,
    [data-testid="stSidebar"] .stButton > button[data-testid="baseButton-secondary"]:hover {{
        background: rgba(255,255,255,0.06) !important;
        color: white !important;
        border: none !important;
    }}
    /* Sidebar nav button — primary (active page) */
    [data-testid="stSidebar"] .stButton > button[kind="primary"],
    [data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"] {{
        background: rgba(13,148,136,0.15) !important;
        color: {TEAL_600} !important;
        font-weight: 600 !important;
        border: none !important;
        text-align: left !important;
        justify-content: flex-start !important;
        padding: 8px 12px !important;
        border-radius: 8px !important;
        font-size: 0.85rem !important;
        box-shadow: none !important;
    }}
    /* File uploader in dark sidebar */
    [data-testid="stSidebar"] [data-testid="stFileUploader"] {{
        border-color: rgba(255,255,255,0.1) !important;
    }}
    [data-testid="stSidebar"] [data-testid="stFileUploader"] section {{
        background: rgba(255,255,255,0.04) !important;
        border: 1px dashed rgba(255,255,255,0.15) !important;
        border-radius: 10px !important;
    }}
    [data-testid="stSidebar"] .stExpander {{
        border-color: rgba(255,255,255,0.08) !important;
    }}

    /* ── Ocultar footer y menú hamburguesa ──── */
    footer {{ visibility: hidden; }}
    #MainMenu {{ visibility: hidden; }}

    /* ── Tabs ────────────────────────────────── */
    .stTabs [data-baseweb="tab-list"] {{
        gap: 4px;
        background: {SLATE_50};
        padding: 4px;
        border-radius: 12px;
        border: 1px solid {SLATE_200};
    }}
    .stTabs [data-baseweb="tab"] {{
        padding: 8px 18px;
        border-radius: 8px;
        font-size: 0.82rem;
        font-weight: 500;
        color: {SLATE_500};
        border: none;
        background: transparent;
    }}
    .stTabs [data-baseweb="tab"][aria-selected="true"] {{
        background: white;
        color: {TEAL_700};
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        font-weight: 600;
    }}
    .stTabs [data-baseweb="tab-highlight"] {{
        display: none;
    }}
    .stTabs [data-baseweb="tab-border"] {{
        display: none;
    }}

    /* ── Dataframes / Tablas ─────────────────── */
    [data-testid="stDataFrame"] {{
        border-radius: 12px;
        overflow: hidden;
        border: 1px solid {SLATE_200};
    }}

    /* ── Expanders ───────────────────────────── */
    .streamlit-expanderHeader {{
        font-weight: 600;
        font-size: 0.9rem;
        color: {SLATE_700};
    }}

    /* ── Métricas ────────────────────────────── */
    [data-testid="stMetric"] {{
        background: white;
        padding: 1rem;
        border-radius: 12px;
        border: 1px solid {SLATE_200};
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }}
    [data-testid="stMetricLabel"] {{
        font-size: 0.78rem !important;
        color: {SLATE_500} !important;
        font-weight: 500 !important;
        text-transform: uppercase;
        letter-spacing: 0.03em;
    }}
    [data-testid="stMetricValue"] {{
        font-size: 1.6rem !important;
        font-weight: 700 !important;
        color: {SLATE_900} !important;
    }}

    /* ── Botones de descarga ─────────────────── */
    .stDownloadButton > button {{
        background: white;
        color: {TEAL_700};
        border: 1.5px solid {TEAL_600};
        border-radius: 10px;
        font-weight: 600;
        transition: all 0.2s;
    }}
    .stDownloadButton > button:hover {{
        background: {TEAL_50};
        border-color: {TEAL_700};
    }}

    /* ── Alertas / Dividers ──────────────────── */
    hr {{
        border: none;
        border-top: 1px solid {SLATE_200};
        margin: 1.8rem 0;
    }}

    /* ── Sección cards del briefing ──────────── */
    .briefing-card {{
        border-radius: 12px;
        padding: 14px 18px;
        margin-bottom: 6px;
        border-left: 4px solid;
        font-size: 0.92em;
    }}

    /* ── Nansen-style subtle card containers ── */
    .nansen-card {{
        background: white;
        border: 1px solid {SLATE_200};
        border-radius: 14px;
        padding: 18px 22px;
        margin-bottom: 12px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }}
    .nansen-card:hover {{
        box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    }}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
#  HELPERS DE COLOR
# ══════════════════════════════════════════════════════════════

BADGE_CSS = {
    "CRÍTICO":          f"background:{STATUS_CRITICO}; color:#FFFFFF",
    "PRE-CRÍTICO":      f"background:{STATUS_PRECRITICO}; color:#FFFFFF",
    "ÓPTIMO":           f"background:{STATUS_OPTIMO}; color:#FFFFFF",
    "ALTO":             f"background:{STATUS_ALTO}; color:#FFFFFF",
    "SOBRESTOCK":       f"background:{STATUS_SOBRESTOCK}; color:#FFFFFF",
    "LIQUIDAR":         f"background:{STATUS_LIQUIDAR}; color:#FFFFFF",
    "NUEVO SIN VENTA":  f"background:{STATUS_NUEVO_SV}; color:#FFFFFF",
    "DORMIDO":          f"background:{STATUS_DORMIDO}; color:#FFFFFF",
    "MUERTO":           f"background:{STATUS_MUERTO}; color:#FFFFFF",
}


def _badge(val):
    css = BADGE_CSS.get(str(val), "background:#E2E8F0; color:#334155")
    return f'<span style="display:inline-block;padding:3px 12px;border-radius:20px;font-size:0.73rem;font-weight:600;letter-spacing:0.02em;{css}">{val}</span>'


def color_estado(val):
    colors = {
        "CRÍTICO":          f"background-color:{STATUS_CRITICO}; color:#FFFFFF",
        "PRE-CRÍTICO":      f"background-color:{STATUS_PRECRITICO}; color:#FFFFFF",
        "ÓPTIMO":           f"background-color:{STATUS_OPTIMO}; color:#FFFFFF",
        "ALTO":             f"background-color:{STATUS_ALTO}; color:#FFFFFF",
        "SOBRESTOCK":       f"background-color:{STATUS_SOBRESTOCK}; color:#FFFFFF",
        "LIQUIDAR":         f"background-color:{STATUS_LIQUIDAR}; color:#FFFFFF",
        "NUEVO SIN VENTA":  f"background-color:{STATUS_NUEVO_SV}; color:#FFFFFF",
        "DORMIDO":          f"background-color:{STATUS_DORMIDO}; color:#FFFFFF",
        "MUERTO":           f"background-color:{STATUS_MUERTO}; color:#FFFFFF",
    }
    return colors.get(str(val), "")


def color_cobertura(val, params):
    try:
        v = float(val)
    except (TypeError, ValueError):
        return f"background-color:{STATUS_NUEVO_SV}; color:#FFFFFF"
    if v < params.get("umbral_critico", 4):
        return f"background-color:{STATUS_CRITICO}; color:#FFFFFF"
    elif v < params.get("umbral_precritico", 8):
        return f"background-color:{STATUS_PRECRITICO}; color:#FFFFFF"
    elif v < params.get("umbral_optimo", 12):
        return f"background-color:{STATUS_OPTIMO}; color:#FFFFFF"
    elif v < params.get("umbral_alto", 16):
        return f"background-color:{STATUS_ALTO}; color:#FFFFFF"
    else:
        return f"background-color:{STATUS_SOBRESTOCK}; color:#FFFFFF"


def color_dscto(val):
    try:
        v = float(val)
    except (TypeError, ValueError):
        return ""
    if v >= 0.40:
        return f"background-color:{STATUS_CRITICO}; color:#FFFFFF"
    elif v >= 0.25:
        return f"background-color:{STATUS_SOBRESTOCK}; color:#FFFFFF"
    elif v > 0:
        return f"background-color:{STATUS_ALTO}; color:#FFFFFF"
    return ""


def _kpi_html(valor, label, css_class="blue"):
    return f"""
    <div class="kpi-card {css_class}">
        <div class="kpi-val">{valor}</div>
        <div class="kpi-lbl">{label}</div>
    </div>"""


# ══════════════════════════════════════════════════════════════
#  ESTADO DE SESIÓN (inicializar ANTES del sidebar)
# ══════════════════════════════════════════════════════════════

if "results" not in st.session_state:
    st.session_state["results"] = None

# ══════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown(f"""
    <div style="text-align:center; padding: 0.6rem 0 0.8rem 0; border-bottom: 1px solid rgba(255,255,255,0.06); margin-bottom: 0.8rem;">
        <span style="font-size:1.5rem; font-weight:700; color:white; letter-spacing:-0.03em;">
            <span style="color:{TEAL_600};">Capi</span>
        </span>
        <span style="font-size:0.65rem; color:rgba(255,255,255,0.4); display:block; margin-top:2px; letter-spacing:0.05em;">EL COCKPIT DEL COMPRADOR</span>
    </div>
    """, unsafe_allow_html=True)

    # ── Navegación funcional con botones ──
    _has_results = st.session_state["results"] is not None

    if "nav_page" not in st.session_state:
        st.session_state["nav_page"] = "🏠 Dashboard"

    if _has_results:
        # Chat IA toggle — primer elemento del menú
        st.markdown('<div class="sidebar-section-label">ASISTENTE</div>', unsafe_allow_html=True)
        if "chat_open" not in st.session_state:
            st.session_state["chat_open"] = False
        _chat_toggle_label = "✕ Cerrar Chat" if st.session_state["chat_open"] else "💬 Chat IA"
        if st.button(
            _chat_toggle_label, key="nav_chat_toggle_top",
            use_container_width=True,
            type="primary" if st.session_state["chat_open"] else "secondary",
        ):
            st.session_state["chat_open"] = not st.session_state["chat_open"]
            st.rerun()

        # ── CAPI SEMANAL — 3 vistas principales ──
        st.markdown('<div class="sidebar-section-label">CAPI SEMANAL</div>', unsafe_allow_html=True)

        _NAV_CAPI = [
            ("🏠", "Dashboard"),
            ("📦", "Reposición"),
            ("📊", "Sobrestock"),
            ("🏷️", "Marcas Terceras"),
        ]

        for _icon, _label in _NAV_CAPI:
            _full = f"{_icon} {_label}"
            _is_active = st.session_state["nav_page"] == _full
            if st.button(
                _full, key=f"nav_{_label}",
                use_container_width=True,
                type="primary" if _is_active else "secondary",
            ):
                st.session_state["nav_page"] = _full
                st.rerun()

        # ── DETALLE — vistas de análisis granular ──
        st.markdown('<div class="sidebar-section-label">DETALLE</div>', unsafe_allow_html=True)

        _NAV_DETALLE = [
            ("📈", "Cobertura"),
            ("📊", "Gestión por Antigüedad"),
            ("🔄", "Transferencias"),
            ("💰", "Acciones Precio"),
            ("📲", "Briefing Semanal"),
            ("📦", "Ventana de Compra"),
            ("🚚", "Predistribución"),
            ("🤖", "Alertas IA"),
            ("🔮", "Simulador Predictivo"),
        ]

        for _icon, _label in _NAV_DETALLE:
            _full = f"{_icon} {_label}"
            _is_active = st.session_state["nav_page"] == _full
            if st.button(
                _full, key=f"nav_{_label}",
                use_container_width=True,
                type="primary" if _is_active else "secondary",
            ):
                st.session_state["nav_page"] = _full
                st.rerun()

        st.markdown('<div style="border-bottom:1px solid rgba(255,255,255,0.06); margin:4px 0 12px 0;"></div>', unsafe_allow_html=True)

    nav_page = st.session_state["nav_page"] if _has_results else None

    # ── Upload ──
    uploaded = st.file_uploader(
        "Sube tu archivo Excel",
        type=["xlsx"],
        help="Sube directamente la Base Profundidad de Ripley o una Plantilla Capi. Se detecta y transforma automáticamente.",
    )
    formato_input = 'auto'

    # ── Ejecutar ──
    run_btn = st.button("🚀 Ejecutar análisis", use_container_width=True, type="primary")

    # ── Paso 3: Parámetros avanzados (colapsados por defecto) ──
    with st.expander("⚙️ Configuración avanzada", expanded=False):
        st.markdown("**Umbrales de cobertura**")
        umbral_critico    = st.slider("CRÍTICO — menor a (sem)",      min_value=1,  max_value=8,  value=4,  step=1)
        umbral_precritico = st.slider("PRE-CRÍTICO — hasta (sem)",    min_value=4,  max_value=12, value=8,  step=1)
        umbral_optimo     = st.slider("ÓPTIMO — hasta (sem)",         min_value=6,  max_value=16, value=12, step=1)
        umbral_alto       = st.slider("ALTO — hasta (sem)",           min_value=8,  max_value=32, value=16, step=1)
        umbral_edad       = st.slider("LIQUIDAR — antigüedad (sem)",  min_value=12, max_value=52, value=26, step=2)

        st.markdown("**Parámetros de cálculo**")
        cob_target    = st.slider("Cobertura objetivo (sem)",       min_value=4,  max_value=16, value=12,  step=1)
        uds_min_trans = st.slider("Uds. mínimas por transferencia", min_value=1,  max_value=20, value=3,  step=1)
        margen_min    = st.slider("Margen mínimo para precio (%)",  min_value=5,  max_value=40, value=15, step=5) / 100

        st.markdown("**Alertas Sobrestock (Tiendas)**")
        alertas_tienda_cob_min = st.slider(
            "Cobertura mín. para alertar (sem)",
            min_value=8, max_value=32, value=16, step=2,
            help="SKUs con cobertura ≥ este valor disparan alerta de revisión a tienda (exhibición + precio)."
        )
        alertas_tienda_edad_min = st.slider(
            "Edad mín. para alertar (sem)",
            min_value=1, max_value=8, value=2, step=1,
            help="Leadtime de llegada/exhibición. Productos más nuevos no se alertan."
        )
        alertas_tienda_top_n = st.slider(
            "Máx. ítems por tienda (sobrestock)",
            min_value=10, max_value=50, value=30, step=5,
            help="Tope de SKUs por tienda, ordenados por capital parado. Menos = WhatsApp más corto."
        )

        st.markdown("**Regla de descuento (Reposición)**")
        excluir_descuento_alto = st.checkbox(
            "Excluir SKUs con descuento ≥40% del plan de reposición",
            value=True, key="cfg_excl_dscto",
            help="Regla Majo: no reponer productos que ya tienen descuento alto (≥40%). Se aplica en el motor de cálculo."
        )
        umbral_descuento_repo = st.slider(
            "Umbral de descuento (%)",
            min_value=20, max_value=60, value=40, step=5, key="cfg_umbral_dscto",
            help="SKUs con descuento ≥ este % se excluyen del plan de reposición."
        ) / 100

        st.markdown("**Alertas Venta Cero (Tiendas)**")
        alertas_vc_capital_min = st.slider(
            "Capital mín. para alertar (S/)",
            min_value=200, max_value=5000, value=1000, step=100,
            help="Solo alertar SKUs con stock a costo ≥ este valor."
        )
        alertas_vc_top_por_marca = st.slider(
            "Máx. SKUs por marca×tienda",
            min_value=5, max_value=30, value=15, step=5,
            help="Top N SKUs por marca dentro de cada tienda, ordenados por capital parado."
        )

    params_ui = {
        "umbral_critico":    umbral_critico,
        "umbral_precritico": umbral_precritico,
        "umbral_optimo":     umbral_optimo,
        "umbral_alto":       umbral_alto,
        "umbral_edad":       umbral_edad,
        "cob_target":        cob_target,
        "uds_min_trans":  uds_min_trans,
        "margen_min":     margen_min,
        "alertas_tienda_cob_min":  alertas_tienda_cob_min,
        "alertas_tienda_edad_min": alertas_tienda_edad_min,
        "alertas_tienda_top_n":    alertas_tienda_top_n,
        "alertas_vta_cero_capital_min":   alertas_vc_capital_min,
        "alertas_vta_cero_top_por_marca": alertas_vc_top_por_marca,
        "excluir_descuento_alto":  excluir_descuento_alto,
        "umbral_descuento_repo":   umbral_descuento_repo,
    }

    st.markdown(f"""
    <div style="border-top:1px solid rgba(255,255,255,0.06); margin-top:1.5rem; padding-top:0.8rem; text-align:center;">
        <div class="sidebar-nav-item" style="justify-content:center; margin-bottom:4px;">
            <span class="nav-icon">⚙️</span> Settings
        </div>
        <span style="font-size:0.65rem; color:rgba(255,255,255,0.25); letter-spacing:0.05em;">
            v2.7 · Powered by AI
        </span>
    </div>
    """, unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
#  HEADER
# ══════════════════════════════════════════════════════════════

st.markdown(f"""
<div class="main-header">
    <div>
        <h1><span>Retail</span>AI</h1>
        <p>Inventory Engine · Cobertura · Reposiciones · Transferencias · Alertas</p>
    </div>
    <div style="display:flex; align-items:center; gap:12px;">
        <span style="font-size:0.75rem; color:rgba(255,255,255,0.4);">v2.7</span>
        <span style="background:{TEAL_600}; color:white; padding:4px 12px; border-radius:8px; font-size:0.75rem; font-weight:600;">Powered by AI</span>
    </div>
</div>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
#  AUTO-DETECCIÓN DE FORMATO
# ══════════════════════════════════════════════════════════════

def _is_base_profundidad(path):
    """Detecta si el archivo es una Base Profundidad (formato wide de Ripley)
    vs una Plantilla Capi (4 pestañas procesadas)."""
    try:
        xl = pd.ExcelFile(path, engine="openpyxl")
        # La plantilla Capi tiene estas pestañas específicas
        if '1. Maestro Productos' in xl.sheet_names:
            return False
        # La Base Profundidad es una sola hoja con muchas columnas (200+)
        df_head = pd.read_excel(path, nrows=1)
        if len(df_head.columns) > 100:
            return True
        return False
    except Exception:
        return False


# ══════════════════════════════════════════════════════════════
#  EJECUCIÓN DEL ANÁLISIS
# ══════════════════════════════════════════════════════════════

if run_btn:
    if uploaded is None:
        st.warning("⚠️ Primero sube tu archivo Excel para continuar.")
    else:
        try:
            # Guardar archivo subido a temporal
            with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
                tmp.write(uploaded.getvalue())
                tmp_path = tmp.name

            # Auto-detectar formato
            if _is_base_profundidad(tmp_path):
                with st.spinner("Detectada Base Profundidad de Ripley. Transformando..."):
                    plantilla_path = tmp_path.replace(".xlsx", "_plantilla.xlsx")
                    etl_profundidad.transform(tmp_path, output_path=plantilla_path)
                    os.unlink(tmp_path)
                    tmp_path = plantilla_path
                    st.toast("Base Profundidad transformada a plantilla Capi")

            with st.spinner("Ejecutando análisis..."):
                results = motor_v2.run_analysis(tmp_path, params=params_ui, formato=formato_input)
                os.unlink(tmp_path)
                st.session_state["results"] = results
                st.rerun()  # Forzar rerun para que sidebar se re-renderice con nav

        except Exception as e:
            st.error(f"❌ Error al procesar el archivo: {e}")
            st.exception(e)


# ══════════════════════════════════════════════════════════════
#  PANTALLA DE BIENVENIDA (sin datos)
# ══════════════════════════════════════════════════════════════

if st.session_state["results"] is None:
    st.markdown(f"""
    <div style="text-align:center; padding:60px 20px;">
        <div style="font-size:2.2rem; font-weight:700; color:{SLATE_900}; margin-bottom:8px;">
            ¿Qué está pasando con tu inventario?
        </div>
        <p style="color:{SLATE_500}; font-size:1rem; margin-bottom:30px;">
            Sube tu Base Profundidad para desbloquear el análisis completo.
        </p>
        <div style="background:{SLATE_50}; border:1px solid {SLATE_200}; border-radius:14px; padding:24px; max-width:500px; margin:0 auto; text-align:left;">
            <div style="font-weight:600; color:{SLATE_900}; margin-bottom:12px;">Cómo empezar:</div>
            <div style="color:{SLATE_700}; font-size:0.9rem; line-height:1.8;">
                <span style="color:{TEAL_600}; font-weight:600;">1.</span> Sube tu archivo Excel en el sidebar<br>
                <span style="color:{TEAL_600}; font-weight:600;">2.</span> Ajusta umbrales si lo necesitas<br>
                <span style="color:{TEAL_600}; font-weight:600;">3.</span> Haz clic en <strong>Ejecutar análisis</strong>
            </div>
            <div style="margin-top:14px; padding:10px 14px; background:white; border-radius:8px; border:1px solid {SLATE_200};">
                <span style="font-size:0.82rem; color:{SLATE_500};">
                    Acepta <strong>Base Profundidad</strong> de Ripley o Plantilla Capi. Se detecta y transforma automáticamente.
                </span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    st.stop()


# ══════════════════════════════════════════════════════════════
#  RESULTADOS
# ══════════════════════════════════════════════════════════════

res    = st.session_state["results"]
s      = res["summary"]
params = res["params"]

df_cob       = res["cobertura"]
df_rep       = res["reposiciones"]
df_rep_pivot = res["reposiciones_pivot"]
df_trans     = res["transferencias"]
df_prec      = res["acciones_precio"]
df_alertas   = res["alertas"]
df_anomalias = res["anomalias_tienda"]
alertas_tienda_dict = res.get("alertas_tienda", {})
alertas_venta_cero_dict = res.get("alertas_venta_cero", {})
briefing     = res["briefing"]
briefing_items  = briefing['items']
briefing_tablas = briefing.get('tablas', {})
aging        = res.get("aging", {})
df_aging     = aging.get('df_aging', pd.DataFrame())
aging_kpis   = aging.get('kpis', {})
aging_top_ejemplos = aging.get('top_ejemplos', {})
embarque     = res.get("embarque") or {}
df_embarque  = embarque.get('df_embarque', pd.DataFrame())
por_ventana  = embarque.get('por_ventana', pd.DataFrame())
embarque_kpis = embarque.get('kpis', {})
embarque_recs = embarque.get('recomendaciones', {})
embarque_top  = embarque.get('top_problemas', {})
predist       = res.get("predistribucion") or {}
df_retenidos_cd   = predist.get('retenidos_cd', pd.DataFrame())
df_gaps_dist      = predist.get('gaps_distribucion', pd.DataFrame())
predist_kpis      = predist.get('kpis', {})

# Margen efectivo (Contribución / VtasMF)
_margen_global = s.get('margen_efectivo_global', None)
_vta_soles_total = s.get('vta_soles_4sem_total', 0)
_contrib_soles_total = s.get('contrib_soles_4sem_total', 0)
_margen_por_marca = s.get('margen_por_marca', [])

# ══════════════════════════════════════════════════════════════
#  FILTRO GLOBAL: Solo marcas vigentes en Ripley
# ══════════════════════════════════════════════════════════════
# Solo se analizan estas marcas. El resto se descarta antes de
# cualquier visualización para no ensuciar los datos.

_MARCAS_VIGENTES = {
    "MARQUIS", "NAVIGATA", "CACHAREL", "SPAVALDI",
    "OSCAR DE LA RENTA", "US POLO", "JOHN HOLDEN",
    "PIERRE CARDIN", "LACOSTE", "DOCKERS", "SILBON",
    "NORTON", "NAUTICA", "SELECTED",
}

def _filtrar_marcas(df, col="marca"):
    """Filtra un DataFrame para quedarse solo con marcas vigentes."""
    if df.empty or col not in df.columns:
        return df
    return df[df[col].str.upper().isin(_MARCAS_VIGENTES)].reset_index(drop=True)

# ── Helper: agregar columnas de precio + fórmula Nuevo Margen a Excel ──
def _add_pricing_cols(df_in, df_ref, sheet_name, writer):
    """
    Agrega precio_blanco, precio_vigente, costo al df, escribe a Excel,
    y luego agrega columnas 'Nuevo Precio' (vacía) y 'Nuevo Margen' (fórmula Excel).

    df_in: DataFrame a escribir
    df_ref: DataFrame de referencia para obtener precios (df_cob típicamente)
    sheet_name: nombre de la hoja Excel
    writer: pd.ExcelWriter activo
    """
    df_out = df_in.copy()
    # Agregar columnas de precio si no las tiene
    _precio_cols = ['precio_blanco', 'precio_vigente', 'costo']
    for _pc in _precio_cols:
        if _pc not in df_out.columns and _pc in df_ref.columns and 'sku' in df_out.columns:
            _map = df_ref.drop_duplicates('sku').set_index('sku')[_pc].to_dict()
            df_out[_pc] = df_out['sku'].map(_map)

    # Agregar columna vacía para Nuevo Precio
    df_out['nuevo_precio'] = np.nan
    # Placeholder para la fórmula (se sobreescribe después)
    df_out['nuevo_margen'] = np.nan

    df_out.to_excel(writer, sheet_name=sheet_name, index=False)

    # Escribir fórmulas Excel en la columna "nuevo_margen"
    ws = writer.sheets[sheet_name]
    # Encontrar índices de columnas
    headers = [cell.value for cell in ws[1]]
    _col_np = headers.index('nuevo_precio') + 1  # 1-based
    _col_nm = headers.index('nuevo_margen') + 1
    _col_costo = headers.index('costo') + 1 if 'costo' in headers else None

    if _col_costo:
        _ltr_np = get_column_letter(_col_np)
        _ltr_costo = get_column_letter(_col_costo)
        _ltr_nm = get_column_letter(_col_nm)
        for row in range(2, ws.max_row + 1):
            # Nuevo Margen = (NuevoPrecio/1.18 - Costo) / (NuevoPrecio/1.18)
            ws[f'{_ltr_nm}{row}'] = f'=IF({_ltr_np}{row}="","",({_ltr_np}{row}/1.18-{_ltr_costo}{row})/({_ltr_np}{row}/1.18))'
        # Formatear como porcentaje
        for row in range(2, ws.max_row + 1):
            ws[f'{_ltr_nm}{row}'].number_format = '0.0%'
        # Formato moneda para precio cols
        for _pc_name in ['precio_blanco', 'precio_vigente', 'costo', 'nuevo_precio']:
            if _pc_name in headers:
                _ltr = get_column_letter(headers.index(_pc_name) + 1)
                for row in range(2, ws.max_row + 1):
                    ws[f'{_ltr}{row}'].number_format = '#,##0.00'

    # Renombrar headers a español
    _rename = {'precio_blanco': 'Precio Blanco', 'precio_vigente': 'Precio Vigente',
               'costo': 'Costo', 'nuevo_precio': 'Nuevo Precio', 'nuevo_margen': 'Nuevo Margen'}
    for cell in ws[1]:
        if cell.value in _rename:
            cell.value = _rename[cell.value]

    return df_out


df_cob   = _filtrar_marcas(df_cob)
df_rep   = _filtrar_marcas(df_rep)
df_trans = _filtrar_marcas(df_trans)
df_prec  = _filtrar_marcas(df_prec)
df_alertas   = _filtrar_marcas(df_alertas)
df_anomalias = _filtrar_marcas(df_anomalias, col="marca" if "marca" in df_anomalias.columns else "___skip")

# Filtrar pivot de reposiciones (solo SKUs que sobrevivieron el filtro)
if not df_rep_pivot.empty and "marca" in df_rep_pivot.columns:
    df_rep_pivot = _filtrar_marcas(df_rep_pivot)
elif not df_rep_pivot.empty:
    _skus_rep_vivos = set(df_rep["sku"].unique()) if not df_rep.empty else set()
    df_rep_pivot = df_rep_pivot[df_rep_pivot["sku"].isin(_skus_rep_vivos)].reset_index(drop=True)

# Filtrar alertas por tienda: reconstruir solo con ítems de marcas vigentes
if alertas_tienda_dict:
    _at_filtrado = {}
    for _t_name, _t_payload in alertas_tienda_dict.items():
        _items = _t_payload.get('items', [])
        _items_ok = [it for it in _items
                     if str(it.get('marca', '')).upper() in _MARCAS_VIGENTES]
        if _items_ok:
            _new_payload = dict(_t_payload)
            _new_payload['items'] = _items_ok
            # Recalcular resumen
            _new_payload['resumen'] = {
                'n_items': len(_items_ok),
                'capital_parado_sol': sum(it.get('capital_parado', 0) for it in _items_ok),
                'n_con_descuento': sum(1 for it in _items_ok if it.get('pct_descuento', 0) > 0),
            }
            _at_filtrado[_t_name] = _new_payload
    alertas_tienda_dict = _at_filtrado

# Filtrar alertas venta cero: solo marcas vigentes
if alertas_venta_cero_dict:
    _vc_filtrado = {}
    for _t_name, _t_payload in alertas_venta_cero_dict.items():
        _pm = _t_payload.get('por_marca', {})
        _pm_ok = {m: v for m, v in _pm.items() if m.upper() in _MARCAS_VIGENTES}
        if _pm_ok:
            n_skus = sum(v['n_skus'] for v in _pm_ok.values())
            capital = sum(v['capital'] for v in _pm_ok.values())
            _vc_filtrado[_t_name] = {
                'tienda': _t_payload['tienda'],
                'fecha_corte': _t_payload.get('fecha_corte', ''),
                'resumen': {'n_skus': n_skus, 'n_marcas': len(_pm_ok), 'capital_parado_total': capital},
                'por_marca': _pm_ok,
            }
    alertas_venta_cero_dict = _vc_filtrado

# Recalcular summary con datos filtrados
s["total_combos"]      = len(df_cob)
s["n_critico"]         = int((df_cob["estado"] == "CRÍTICO").sum()) if not df_cob.empty else 0
s["n_precritico"]      = int((df_cob["estado"] == "PRE-CRÍTICO").sum()) if not df_cob.empty else 0
s["n_optimo"]          = int((df_cob["estado"] == "ÓPTIMO").sum()) if not df_cob.empty else 0
s["n_alto"]            = int((df_cob["estado"] == "ALTO").sum()) if not df_cob.empty else 0
s["n_sobrestock"]      = int((df_cob["estado"] == "SOBRESTOCK").sum()) if not df_cob.empty else 0
s["n_liquidar"]        = int((df_cob["estado"] == "LIQUIDAR").sum()) if not df_cob.empty else 0
s["n_nuevo_sv"]        = int((df_cob["estado"] == "NUEVO SIN VENTA").sum()) if not df_cob.empty else 0
s["n_dormido"]         = int((df_cob["estado"] == "DORMIDO").sum()) if not df_cob.empty else 0
s["n_muerto"]          = int((df_cob["estado"] == "MUERTO").sum()) if not df_cob.empty else 0
s["uds_reponer"]       = int(df_rep["a_reponer"].sum()) if not df_rep.empty else 0
s["uds_transferir"]    = int(df_trans["uds_transferir"].sum()) if not df_trans.empty else 0
s["n_acciones_precio"] = len(df_prec)

# Nota: marcas propias sin stock CD ya están excluidas por motor_v2.build_reposiciones()
# Marcas propias: MARQUIS, NAVIGATA, CACHAREL, SPAVALDI, OSCAR DE LA RENTA, US POLO

# ══════════════════════════════════════════════════════════════
#  LAYOUT: Columnas (main + chat panel derecho)
# ══════════════════════════════════════════════════════════════

if "chat_messages" not in st.session_state:
    st.session_state["chat_messages"] = []
if "chat_open" not in st.session_state:
    st.session_state["chat_open"] = False

_chat_is_open = st.session_state["chat_open"]

if _chat_is_open:
    _col_main, _col_chat = st.columns([3, 2])
    _col_main.__enter__()
else:
    _col_main = None
    _col_chat = None

# ══════════════════════════════════════════════════════════════
#  DASHBOARD VISUAL  (solo se renderiza en vista Dashboard)
# ══════════════════════════════════════════════════════════════

# ── Configuración global de Plotly (usada en múltiples vistas) ──
_plotly_layout = dict(
    font=dict(family="Inter, -apple-system, sans-serif", size=12, color=SLATE_700),
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=20, r=20, t=40, b=20),
    hoverlabel=dict(bgcolor="white", font_size=12, font_family="Inter"),
)

_estado_color_map = {
    "CRÍTICO":          STATUS_CRITICO,
    "PRE-CRÍTICO":      STATUS_PRECRITICO,
    "ÓPTIMO":           STATUS_OPTIMO,
    "ALTO":             STATUS_ALTO,
    "SOBRESTOCK":       STATUS_SOBRESTOCK,
    "LIQUIDAR":         STATUS_LIQUIDAR,
    "NUEVO SIN VENTA":  STATUS_NUEVO_SV,
    "DORMIDO":          STATUS_DORMIDO,
    "MUERTO":           STATUS_MUERTO,
}

if nav_page == "🏠 Dashboard":
    st.markdown(f'<div class="section-header"><h3>Dashboard</h3><span class="live-badge">LIVE</span></div>', unsafe_allow_html=True)

    # ── Filtros del dashboard ──
    st.markdown(f"""<div style="background:{SLATE_50}; border:1px solid {SLATE_200}; border-radius:12px; padding:12px 16px; margin-bottom:16px;">
    <span style="font-weight:600; color:{SLATE_900}; font-size:0.9rem;">Filtros del Dashboard</span>
    </div>""", unsafe_allow_html=True)

    has_temporada = "temporada" in df_cob.columns
    has_rango = "rango_antiguedad" in df_cob.columns
    fcol1, fcol2 = st.columns(2)

    with fcol1:
        if has_temporada:
            temps_disponibles = sorted([t for t in df_cob["temporada"].unique() if t and str(t).strip()])
            temps_opciones = ["Todas"] + temps_disponibles
            f_dash_temp = st.selectbox("Temporada", temps_opciones, key="dash_temporada")
        else:
            f_dash_temp = "Todas"
            st.caption("Sin columna Temporada en los datos")

    with fcol2:
        if has_rango:
            _rango_orden = {"RANGO 0": 0, "RANGO 0_3": 1, "RANGO 3_6": 2, "RANGO 6_9": 3,
                            "RANGO 9_12": 4, "RANGO 12_99": 5, "Sin Rango": 6}
            rangos_disponibles = sorted(
                [r for r in df_cob["rango_antiguedad"].unique() if r and str(r).strip()],
                key=lambda x: _rango_orden.get(x, 99)
            )
            rangos_opciones = ["Todos"] + rangos_disponibles
            f_dash_rango = st.selectbox("Rango de Antigüedad", rangos_opciones, key="dash_rango",
                                         help="Filtra por rango de antigüedad del reporte micro")
        else:
            f_dash_rango = "Todos"
            st.caption("Sin columna Rango Antigüedad en los datos")

    # Aplicar filtros al df_cob para el dashboard
    df_dash = df_cob.copy()
    if f_dash_temp != "Todas" and has_temporada:
        df_dash = df_dash[df_dash["temporada"] == f_dash_temp]
    if f_dash_rango != "Todos" and has_rango:
        df_dash = df_dash[df_dash["rango_antiguedad"] == f_dash_rango]

    st.caption(f"Mostrando {len(df_dash):,} de {len(df_cob):,} combos SKU×Tienda después de filtros")

    # ── Fila 1: Donut + Leyenda de estados ──
    _marcas_donut = ["Todas"] + sorted(df_dash["marca"].unique().tolist()) if "marca" in df_dash.columns else ["Todas"]
    _marca_donut_sel = st.selectbox("Filtrar por Marca", _marcas_donut, index=0, key="donut_marca_filter")
    _df_donut = df_dash if _marca_donut_sel == "Todas" else df_dash[df_dash["marca"] == _marca_donut_sel]

    dash_c1, dash_c2 = st.columns([1, 1])

    with dash_c1:
        estado_counts = _df_donut["estado"].value_counts().reset_index()
        estado_counts.columns = ["Estado", "Cantidad"]
        estado_order = [
            "CRÍTICO", "PRE-CRÍTICO", "ÓPTIMO", "ALTO", "SOBRESTOCK",
            "LIQUIDAR", "NUEVO SIN VENTA", "DORMIDO", "MUERTO",
        ]
        estado_counts["Estado"] = pd.Categorical(estado_counts["Estado"], categories=estado_order, ordered=True)
        estado_counts = estado_counts.sort_values("Estado").dropna(subset=["Estado"])

        _display_labels = {"NUEVO SIN VENTA": "SIN VENTA"}
        estado_counts["Label"] = estado_counts["Estado"].astype(str).map(lambda x: _display_labels.get(x, x))

        # Capital por estado para hover
        _capital_por_estado = _df_donut.groupby("estado")["stock_valor_costo"].sum()
        estado_counts["Capital"] = estado_counts["Estado"].astype(str).map(_capital_por_estado).fillna(0)

        _donut_title = f"Distribución por Estado — {_marca_donut_sel}" if _marca_donut_sel != "Todas" else "Distribución por Estado"
        fig_donut = go.Figure(data=[go.Pie(
            labels=estado_counts["Label"],
            values=estado_counts["Cantidad"],
            hole=0.55,
            marker=dict(colors=[_estado_color_map.get(e, "#CBD5E1") for e in estado_counts["Estado"]]),
            textinfo="label+percent",
            textfont=dict(size=11),
            customdata=estado_counts[["Capital"]].values,
            hovertemplate="<b>%{label}</b><br>%{value:,} combos<br>%{percent}<br>Capital: S/ %{customdata[0]:,.0f}<extra></extra>",
        )])
        fig_donut.update_layout(
            **_plotly_layout,
            title=dict(text=_donut_title, font=dict(size=14, color=SLATE_900)),
            showlegend=False,
            height=380,
        )
        total_donut = len(_df_donut)
        fig_donut.add_annotation(
            text=f"<b>{total_donut:,}</b><br><span style='font-size:10px;color:{SLATE_500}'>SKU×Tienda</span>",
            showarrow=False, font=dict(size=18, color=SLATE_900),
        )
        st.plotly_chart(fig_donut, use_container_width=True)

    with dash_c2:
        # ── Leyenda: criterio de cada estado ──
        _estado_criterios = {
            "CRÍTICO":         {"color": STATUS_CRITICO,    "icon": "🔴", "regla": "Cobertura < 4 semanas"},
            "PRE-CRÍTICO":     {"color": STATUS_PRECRITICO, "icon": "🟠", "regla": "Cobertura 4–8 semanas"},
            "ÓPTIMO":          {"color": STATUS_OPTIMO,     "icon": "🟢", "regla": "Cobertura 8–12 semanas"},
            "ALTO":            {"color": STATUS_ALTO,       "icon": "🟡", "regla": "Cobertura 12–16 semanas"},
            "SOBRESTOCK":      {"color": STATUS_SOBRESTOCK, "icon": "🔴", "regla": "Cobertura > 16 semanas"},
            "LIQUIDAR":        {"color": STATUS_LIQUIDAR,   "icon": "💀", "regla": "Edad > 26 semanas"},
            "NUEVO SIN VENTA": {"color": STATUS_NUEVO_SV,   "icon": "🆕", "regla": "Sin venta, edad 0–3 meses", "label": "SIN VENTA"},
            "DORMIDO":         {"color": STATUS_DORMIDO,    "icon": "😴", "regla": "Sin venta, edad 3–6 meses"},
            "MUERTO":          {"color": STATUS_MUERTO,     "icon": "⚫", "regla": "Sin venta, edad > 6 meses"},
        }

        st.markdown(f"""<div style="font-weight:600; color:{SLATE_900}; font-size:0.95rem; margin-bottom:10px;">
        Criterios de Clasificación
        </div>""", unsafe_allow_html=True)

        for _est_name, _est_info in _estado_criterios.items():
            # Contar combos en este estado
            _n_est = int((_df_donut["estado"] == _est_name).sum()) if not _df_donut.empty else 0
            _cap_est = _df_donut.loc[_df_donut["estado"] == _est_name, "stock_valor_costo"].sum() if _n_est > 0 else 0
            _pct_est = (_n_est / total_donut * 100) if total_donut > 0 else 0

            st.markdown(f"""<div style="display:flex; align-items:center; gap:10px; padding:5px 10px; margin-bottom:3px;
                border-left:3px solid {_est_info['color']}; border-radius:0 6px 6px 0;
                background:{'rgba(0,0,0,0.02)' if _n_est > 0 else 'transparent'};">
                <span style="font-size:0.85rem; min-width:18px;">{_est_info['icon']}</span>
                <div style="flex:1;">
                    <span style="font-weight:600; color:{SLATE_900}; font-size:0.82rem;">{_est_info.get('label', _est_name)}</span>
                    <span style="color:{SLATE_500}; font-size:0.78rem;"> — {_est_info['regla']}</span>
                </div>
                <div style="text-align:right; min-width:80px;">
                    <span style="font-weight:700; color:{_est_info['color']}; font-size:0.85rem;">{_n_est:,}</span>
                    <span style="color:{SLATE_500}; font-size:0.72rem;"> ({_pct_est:.0f}%)</span>
                </div>
            </div>""", unsafe_allow_html=True)

        st.markdown(f"""<div style="margin-top:10px; padding:8px 12px; background:{SLATE_50}; border-radius:8px; font-size:0.78rem; color:{SLATE_500};">
        Capital total: <strong style="color:{SLATE_900};">S/ {_df_donut['stock_valor_costo'].sum():,.0f}</strong> &nbsp;·&nbsp;
        {total_donut:,} combos SKU×Tienda
        </div>""", unsafe_allow_html=True)

    # ── Desglose por marca del estado seleccionado + descarga ──
    st.markdown("---")

    _est_display_map = {"NUEVO SIN VENTA": "SIN VENTA"}
    _est_opciones = [e for e in estado_order if e in _df_donut["estado"].values]
    _est_opciones_display = [_est_display_map.get(e, e) for e in _est_opciones]
    _est_default = _est_opciones.index("CRÍTICO") if "CRÍTICO" in _est_opciones else 0

    _det_c1, _det_c2 = st.columns([1, 2])
    with _det_c1:
        _est_sel_display = st.selectbox("Ver detalle por estado", _est_opciones_display, index=_est_default, key="donut_estado_sel")
        # Mapear de vuelta al nombre interno
        _est_reverse_map = {v: k for k, v in _est_display_map.items()}
        _est_sel = _est_reverse_map.get(_est_sel_display, _est_sel_display)

    _df_est = _df_donut[_df_donut["estado"] == _est_sel]

    if not _df_est.empty:
        # Resumen por marca
        if "marca" in _df_est.columns:
            _est_marca = _df_est.groupby("marca").agg(
                combos=("sku", "count"),
                skus=("sku", "nunique"),
                capital=("stock_valor_costo", "sum"),
                vta_sem=("prom_vta_uds", "sum"),
            ).sort_values("capital", ascending=False).reset_index()
            _est_marca["Participación"] = (_est_marca["combos"] / _est_marca["combos"].sum() * 100).round(1)
            _est_marca = _est_marca.rename(columns={
                "marca": "Marca", "combos": "Combos", "skus": "SKUs",
                "capital": "Capital S/", "vta_sem": "Vta Sem (uds)",
            })

            st.markdown(f"""<div style="background:{'#FEF2F2' if _est_sel in ('CRÍTICO','PRE-CRÍTICO') else SLATE_50};
            border-left:4px solid {_estado_color_map.get(_est_sel, SLATE_500)};
            padding:10px 14px; border-radius:10px; margin-bottom:10px;">
            <strong style="color:{_estado_color_map.get(_est_sel, SLATE_900)};">{_est_sel}</strong>
            <span style="color:{SLATE_500}; font-size:0.82em;"> &nbsp;·&nbsp; {len(_df_est):,} combos · {_df_est['sku'].nunique()} SKUs · S/ {_df_est['stock_valor_costo'].sum():,.0f}</span>
            </div>""", unsafe_allow_html=True)

            st.dataframe(
                _est_marca.style.format({
                    "Capital S/": "S/ {:,.0f}", "Participación": "{:.1f}%",
                    "Vta Sem (uds)": "{:,.0f}",
                }),
                use_container_width=True, hide_index=True, height=250,
            )

        # Detalle de SKUs expandible
        with st.expander(f"Ver {min(50, len(_df_est)):,} SKUs en estado {_est_sel}", expanded=False):
            _det_cols = ["sku", "nombre", "marca", "tienda", "stock_total", "prom_vta_uds",
                         "cobertura_sem", "stock_valor_costo", "edad_semanas"]
            if "pct_descuento" in _df_est.columns:
                _det_cols.append("pct_descuento")
            _det_cols = [c for c in _det_cols if c in _df_est.columns]
            _df_est_disp = _df_est[_det_cols].sort_values("stock_valor_costo", ascending=False).head(50)
            _df_est_disp = _df_est_disp.rename(columns={
                "sku": "SKU", "nombre": "Nombre", "marca": "Marca", "tienda": "Tienda",
                "stock_total": "Stock", "prom_vta_uds": "Vta Sem (uds)",
                "cobertura_sem": "Cob (sem)", "stock_valor_costo": "Capital S/",
                "edad_semanas": "Edad (sem)", "pct_descuento": "Dscto",
            })
            _det_fmt = {"Capital S/": "S/ {:,.0f}", "Cob (sem)": "{:.1f}", "Vta Sem (uds)": "{:.0f}"}
            if "Dscto" in _df_est_disp.columns:
                _det_fmt["Dscto"] = "{:.0%}"
            st.dataframe(
                _df_est_disp.style.format(_det_fmt, na_rep="—"),
                use_container_width=True, hide_index=True, height=350,
            )

        # Botón de descarga de TODOS los estados (para que el usuario filtre)
        _dl_cols = ["sku", "nombre", "marca", "temporada", "rango_antiguedad", "tienda",
                    "stock_total", "prom_vta_uds", "cobertura_sem", "stock_valor_costo",
                    "edad_semanas", "estado"]
        if "pct_descuento" in _df_donut.columns:
            _dl_cols.append("pct_descuento")
        _dl_cols = [c for c in _dl_cols if c in _df_donut.columns]
        _dl_buf = io.BytesIO()
        with pd.ExcelWriter(_dl_buf, engine="openpyxl") as _w_dl:
            _add_pricing_cols(
                _df_donut[_dl_cols].sort_values(["estado", "stock_valor_costo"], ascending=[True, False]),
                df_cob, "Todos los estados", _w_dl
            )
        _dl_buf.seek(0)
        st.download_button(
            f"📥 Descargar {len(_df_donut):,} SKUs — Todos los estados (.xlsx)",
            data=_dl_buf.getvalue(),
            file_name="Capi_todos_estados.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_all_estados",
        )

    # ── Capital + Venta a Costo + Cobertura por marca (stacked bar) ──
    st.markdown("---")

    group_col = "marca" if "marca" in df_dash.columns else "categoria"
    group_label = "Marca" if group_col == "marca" else "Categoría"

    # Calcular capital, venta a costo y cobertura por marca
    _has_vta = "vta_soles_4sem" in df_dash.columns and "contrib_soles_4sem" in df_dash.columns

    # Capital: sumar a nivel SKU×Tienda (cada fila es un combo, correcto)
    capital_grp = (
        df_dash.groupby(group_col)["stock_valor_costo"]
        .sum()
        .sort_values(ascending=False)
        .head(10)
        .reset_index()
    )
    capital_grp.columns = [group_label, "stock_valor_costo"]

    # Venta a costo: vta_soles_4sem y contrib_soles_4sem son a nivel SKU (no SKU×Tienda)
    # → deduplicar por SKU antes de sumar para no inflar por número de tiendas
    if _has_vta:
        _df_sku_vta = df_dash.drop_duplicates("sku")[[group_col, "vta_soles_4sem", "contrib_soles_4sem"]].copy()
        _vta_marca = _df_sku_vta.groupby(group_col).agg(
            vta_soles=("vta_soles_4sem", "sum"),
            contrib_soles=("contrib_soles_4sem", "sum"),
        ).reset_index()
        _vta_marca.columns = [group_label, "_vta_soles", "_contrib_soles"]
        _vta_marca["vta_costo"] = (_vta_marca["_vta_soles"] - _vta_marca["_contrib_soles"]).clip(lower=0)
        capital_grp = capital_grp.merge(_vta_marca[[group_label, "vta_costo"]], on=group_label, how="left")
        capital_grp["vta_costo"] = capital_grp["vta_costo"].fillna(0)
        capital_grp["cobertura_meses"] = capital_grp.apply(
            lambda r: round(r["stock_valor_costo"] / r["vta_costo"], 1) if r["vta_costo"] > 0 else None, axis=1
        )
    else:
        capital_grp["vta_costo"] = 0
        capital_grp["cobertura_meses"] = None

    capital_grp = capital_grp.sort_values("stock_valor_costo", ascending=True)

    _max_total = (capital_grp["stock_valor_costo"] + capital_grp["vta_costo"]).max()

    # ── Renderizar stacked bar con HTML (capital + venta a costo + badge cobertura) ──
    st.markdown(f"""<div style="margin-bottom:12px;">
    <div style="font-size:14px; font-weight:600; color:{SLATE_900}; margin-bottom:4px;">Inventario a Costo por {group_label} (Top 10)</div>
    <div style="display:flex; gap:16px; font-size:12px; color:{SLATE_500}; margin-bottom:12px;">
        <span style="display:flex; align-items:center; gap:4px;"><span style="width:10px; height:10px; border-radius:2px; background:{TEAL_700}; display:inline-block;"></span>Capital a costo</span>
        <span style="display:flex; align-items:center; gap:4px;"><span style="width:10px; height:10px; border-radius:2px; background:#5DCAA5; display:inline-block;"></span>Venta a costo (4 sem)</span>
        <span style="display:flex; align-items:center; gap:4px;"><span style="background:{SLATE_100}; border:1px solid {SLATE_200}; border-radius:4px; padding:0 5px; font-size:10px; color:{SLATE_700};">5.2</span>Cobertura (meses)</span>
    </div>
    </div>""", unsafe_allow_html=True)

    _bars_html = ""
    for _, _row in capital_grp.iloc[::-1].iterrows():
        _cap = _row["stock_valor_costo"]
        _vta = _row["vta_costo"]
        _cob = _row.get("cobertura_meses", None)
        _marca_name = _row[group_label]

        _total = _cap + _vta
        _bar_w_pct = max(5, _total / _max_total * 100) if _max_total > 0 else 5
        _cap_pct = (_cap / _total * 100) if _total > 0 else 100
        _vta_pct = 100 - _cap_pct

        # Texto dentro de barras
        _cap_txt = f"S/ {_cap/1e6:.1f}M" if _cap >= 1e6 else f"S/ {_cap:,.0f}"
        _vta_txt = f"S/ {_vta/1e6:.1f}M" if _vta >= 1e6 else f"S/ {_vta:,.0f}"

        # Semáforo cobertura: verde <3m, neutro 3-5m, rojo >5m
        if _cob is not None:
            if _cob <= 3:
                _cob_bg, _cob_color, _cob_border = "#ECFDF5", "#059669", "#A7F3D0"
            elif _cob <= 5:
                _cob_bg, _cob_color, _cob_border = SLATE_100, SLATE_700, SLATE_200
            else:
                _cob_bg, _cob_color, _cob_border = "#FEF2F2", "#DC2626", "#FECACA"
            _cob_html = f'<span style="background:{_cob_bg}; color:{_cob_color}; border:1px solid {_cob_border}; border-radius:6px; padding:2px 8px; font-size:11px; font-weight:600; min-width:50px; text-align:center; white-space:nowrap;">{_cob:.1f}</span>'
        else:
            _cob_html = f'<span style="background:{SLATE_100}; color:{SLATE_500}; border:1px solid {SLATE_200}; border-radius:6px; padding:2px 8px; font-size:11px; min-width:50px; text-align:center;">—</span>'

        _bars_html += f"""<div style="display:flex; align-items:center; gap:8px; margin-bottom:5px;">
            <span style="width:130px; font-size:12px; font-weight:500; color:{SLATE_800}; text-align:right; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; flex-shrink:0;">{_marca_name}</span>
            <div style="flex:1; display:flex; align-items:center; gap:6px;">
                <div style="width:{_bar_w_pct:.1f}%; display:flex; height:22px; border-radius:4px; overflow:hidden;">
                    <div style="width:{_cap_pct:.0f}%; background:{TEAL_700}; display:flex; align-items:center; justify-content:flex-end; padding-right:5px; min-width:40px;">
                        <span style="font-size:10px; color:white; font-weight:500; white-space:nowrap;">{_cap_txt}</span>
                    </div>
                    <div style="width:{_vta_pct:.0f}%; background:#5DCAA5; display:flex; align-items:center; padding-left:4px; min-width:{'40px' if _vta > 0 else '0'};">
                        <span style="font-size:10px; color:white; font-weight:500; white-space:nowrap;">{_vta_txt if _vta > 0 else ''}</span>
                    </div>
                </div>
                {_cob_html}
            </div>
        </div>"""

    st.markdown(_bars_html, unsafe_allow_html=True)

    # ── KPIs ───────────────────────────────────────────────────────

    st.markdown(f'<div class="section-header"><h3>KPIs de Inventario</h3><span class="live-badge">9 ESTADOS</span></div>', unsafe_allow_html=True)

    # Fila 1: estados con stock en movimiento
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.markdown(_kpi_html(s["n_critico"], "🔴 CRÍTICO", "red"), unsafe_allow_html=True)
    with c2:
        st.markdown(_kpi_html(s["n_precritico"], "🟠 PRE-CRÍTICO", "orange"), unsafe_allow_html=True)
    with c3:
        st.markdown(_kpi_html(s["n_optimo"], "🟢 ÓPTIMO", "green"), unsafe_allow_html=True)
    with c4:
        st.markdown(_kpi_html(s["n_alto"], "🟡 ALTO", "yellow"), unsafe_allow_html=True)
    with c5:
        st.markdown(_kpi_html(s["n_sobrestock"], "🟠 SOBRESTOCK", "darkred"), unsafe_allow_html=True)

    # Fila 2: estados sin venta + liquidar
    c6, c7, c8, c9, c10 = st.columns(5)
    with c6:
        st.markdown(_kpi_html(s["n_liquidar"], "💀 LIQUIDAR", "darkred"), unsafe_allow_html=True)
    with c7:
        st.markdown(_kpi_html(s["n_nuevo_sv"], "🆕 SIN VENTA"), unsafe_allow_html=True)
    with c8:
        st.markdown(_kpi_html(s["n_dormido"], "😴 DORMIDO"), unsafe_allow_html=True)
    with c9:
        st.markdown(_kpi_html(s["n_muerto"], "💀 MUERTO"), unsafe_allow_html=True)
    with c10:
        st.markdown(_kpi_html(s["total_combos"], "SKU×Tienda total", "blue"), unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    c11, c12, c13 = st.columns(3)
    with c11:
        st.markdown(_kpi_html(f"{s['uds_reponer']} uds", "📦 A Reponer"), unsafe_allow_html=True)
    with c12:
        st.markdown(_kpi_html(f"{s['uds_transferir']} uds", "🔄 A Transferir"), unsafe_allow_html=True)
    with c13:
        st.markdown(_kpi_html(s["n_acciones_precio"], "💰 Acciones Precio"), unsafe_allow_html=True)

    st.markdown("---")

    # ── Tabs ───────────────────────────────────────────────────────

    st.markdown("---")

    # Contar combos obsoletos (rangos 6_9, 9_12, 12_99)
    _RANGOS_OBSOLETOS_TAB = {"RANGO 6_9", "RANGO 9_12", "RANGO 12_99"}
    _n_obs = 0
    if "rango_antiguedad" in df_cob.columns:
        _n_obs = len(df_cob[df_cob["rango_antiguedad"].isin(_RANGOS_OBSOLETOS_TAB)])

# Navegación controlada por sidebar radio (nav_page)
# Si nav_page es None (antes del análisis), no se renderiza nada aquí


# ─── TAB 0: Acciones del Día ─────────────────────────────────
#  Resumen por MARCA con desplegable por TIENDA
#  Filtros de temporada y rango de antigüedad (obsoletos excluidos por defecto)

if nav_page == "🏠 Dashboard":

    # ═══════════════════════════════════════════════════════════════
    #  VENTANA DE MERCADERÍA — Aging del inventario (4 capas)
    # ═══════════════════════════════════════════════════════════════
    # ═══════════════════════════════════════════════════════════════
    #  RESUMEN AGING — Tarjeta compacta (detalle completo en "Gestión por Antigüedad")
    # ═══════════════════════════════════════════════════════════════
    if not df_aging.empty:
        import math as _math_mod
        _ak = aging_kpis
        _cap_viejo = _ak.get('capital_viejo', 0)
        _pct_viejo = _ak.get('pct_viejo', 0)
        _edad_prom = _ak.get('edad_prom_pond', 0)
        if _edad_prom is None or (isinstance(_edad_prom, float) and _math_mod.isnan(_edad_prom)):
            _edad_prom = 0
        _n_riesgo = _ak.get('n_zona_riesgo', 0)
        _cap_total = _ak.get('capital_total', 1)

        st.markdown(f'<div class="section-header"><h3>🪟 Ventana de Mercadería</h3><span class="live-badge">AGING</span></div>', unsafe_allow_html=True)

        _kc1, _kc2, _kc3 = st.columns(3)
        with _kc1:
            st.markdown(f"""<div style="background:#FEF2F2; border-radius:12px; padding:14px 18px; border-left:4px solid #ef4444;">
                <div style="font-size:0.72rem; color:{SLATE_500}; font-weight:500;">Capital viejo (>16 sem)</div>
                <div style="font-size:1.5rem; font-weight:700; color:#ef4444;">S/ {_cap_viejo:,.0f}</div>
                <div style="font-size:0.68rem; color:{SLATE_500};">{_pct_viejo:.0f}% del inventario</div>
            </div>""", unsafe_allow_html=True)
        with _kc2:
            st.markdown(f"""<div style="background:#FFFBEB; border-radius:12px; padding:14px 18px; border-left:4px solid #f59e0b;">
                <div style="font-size:0.72rem; color:{SLATE_500}; font-weight:500;">Edad prom. ponderada</div>
                <div style="font-size:1.5rem; font-weight:700; color:#f59e0b;">{_edad_prom:.1f} sem</div>
            </div>""", unsafe_allow_html=True)
        with _kc3:
            st.markdown(f"""<div style="background:#F0FDF4; border-radius:12px; padding:14px 18px; border-left:4px solid {TEAL_600};">
                <div style="font-size:0.72rem; color:{SLATE_500}; font-weight:500;">SKUs zona riesgo (8-16 sem)</div>
                <div style="font-size:1.5rem; font-weight:700; color:{TEAL_700};">{_n_riesgo:,}</div>
            </div>""", unsafe_allow_html=True)

        # Barra horizontal compacta
        _dist_edad = _ak.get('dist_edad', {})
        if _dist_edad and _cap_total > 0:
            _edad_colors = {'0-4 sem': '#10b981', '4-8 sem': '#84cc16', '8-16 sem': '#f59e0b', '16-26 sem': '#f97316', '26+ sem': '#ef4444'}
            _bar_parts = ""
            for _lbl in ['0-4 sem', '4-8 sem', '8-16 sem', '16-26 sem', '26+ sem']:
                _val = _dist_edad.get(_lbl, 0)
                _pct = _val / _cap_total * 100 if _cap_total > 0 else 0
                _clr = _edad_colors.get(_lbl, '#94A3B8')
                if _pct > 1:
                    _bar_parts += f'<div style="width:{_pct:.1f}%; background:{_clr}; height:100%; display:inline-block;" title="{_lbl}: S/{_val:,.0f} ({_pct:.0f}%)"></div>'
            st.markdown(f"""<div style="margin-top:12px; background:white; border-radius:8px; padding:10px 14px; border:1px solid {SLATE_200};">
                <div style="width:100%; height:16px; border-radius:6px; overflow:hidden; background:{SLATE_200}; display:flex;">{_bar_parts}</div>
            </div>""", unsafe_allow_html=True)

        st.caption("Drill-down completo, alertas por acción y reglas del motor → sección **Gestión por Antigüedad** en el menú lateral.")

    # ══════════════════════════════════════════════════════════
    #  MARGEN EFECTIVO — Contribución / VtasMF (4 semanas)
    # ══════════════════════════════════════════════════════════
    if _margen_global is not None and _vta_soles_total > 0:
        st.markdown("<div style='height:32px'></div>", unsafe_allow_html=True)
        st.markdown(f'<div class="section-header"><h3>💰 Margen Efectivo</h3><span class="live-badge">RENTABILIDAD</span></div>', unsafe_allow_html=True)
        st.caption("Contribución / Venta MF — últimas 4 semanas, ponderado por volumen de venta")

        # ── KPIs de margen ──
        _mk1, _mk2, _mk3 = st.columns(3)
        _mg_pct = _margen_global * 100
        _mg_color = "#10b981" if _mg_pct >= 35 else ("#f59e0b" if _mg_pct >= 25 else "#ef4444")
        _mg_bg = "#F0FDF4" if _mg_pct >= 35 else ("#FFFBEB" if _mg_pct >= 25 else "#FEF2F2")
        with _mk1:
            st.markdown(f"""<div style="background:{_mg_bg}; border-radius:12px; padding:16px 20px; border-left:4px solid {_mg_color};">
                <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">Margen efectivo global</div>
                <div style="font-size:1.8rem; font-weight:700; color:{_mg_color};">{_mg_pct:.1f}%</div>
                <div style="font-size:0.7rem; color:{SLATE_500};">Contribución / Venta (4 sem)</div>
            </div>""", unsafe_allow_html=True)
        with _mk2:
            st.markdown(f"""<div style="background:#EFF6FF; border-radius:12px; padding:16px 20px; border-left:4px solid #3b82f6;">
                <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">Venta total (4 sem)</div>
                <div style="font-size:1.8rem; font-weight:700; color:#3b82f6;">S/ {_vta_soles_total:,.0f}</div>
                <div style="font-size:0.7rem; color:{SLATE_500};">Sin IGV</div>
            </div>""", unsafe_allow_html=True)
        with _mk3:
            st.markdown(f"""<div style="background:#F0FDF4; border-radius:12px; padding:16px 20px; border-left:4px solid {TEAL_600};">
                <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">Contribución total (4 sem)</div>
                <div style="font-size:1.8rem; font-weight:700; color:{TEAL_700};">S/ {_contrib_soles_total:,.0f}</div>
                <div style="font-size:0.7rem; color:{SLATE_500};">Venta - Costo</div>
            </div>""", unsafe_allow_html=True)

        # ── Tabla de margen por marca ──
        if _margen_por_marca:
            st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
            st.markdown(f"<h4 style='color:{SLATE_800}; margin:0 0 8px 0;'>Margen por marca</h4>", unsafe_allow_html=True)

            _max_vta_marca = max(r['vta_soles'] for r in _margen_por_marca) if _margen_por_marca else 1
            _rows_html = ""
            for _mr in _margen_por_marca:
                _m_pct = _mr['margen_efectivo'] * 100
                _bar_w = max(2, int(_mr['vta_soles'] / _max_vta_marca * 100))
                _m_clr = "#10b981" if _m_pct >= 35 else ("#f59e0b" if _m_pct >= 25 else "#ef4444")
                _rows_html += f"""<tr>
                    <td style="padding:8px 12px; font-weight:500; white-space:nowrap;">{_mr['marca']}</td>
                    <td style="padding:8px 12px; text-align:right;">S/ {_mr['vta_soles']:,.0f}</td>
                    <td style="padding:8px 12px; text-align:right;">S/ {_mr['contrib_soles']:,.0f}</td>
                    <td style="padding:8px 12px; text-align:right; font-weight:600; color:{_m_clr};">{_m_pct:.1f}%</td>
                    <td style="padding:8px 12px; width:120px;">
                        <div style="background:#E2E8F0; border-radius:4px; height:14px; width:100%;">
                            <div style="background:{_m_clr}; border-radius:4px; height:14px; width:{_bar_w}%;"></div>
                        </div>
                    </td>
                </tr>"""

            st.markdown(f"""<div style="overflow-x:auto;">
            <table style="width:100%; border-collapse:collapse; font-size:0.85rem;">
                <thead>
                    <tr style="background:{SLATE_100}; border-bottom:2px solid {SLATE_200};">
                        <th style="padding:8px 12px; text-align:left;">Marca</th>
                        <th style="padding:8px 12px; text-align:right;">Venta S/</th>
                        <th style="padding:8px 12px; text-align:right;">Contribución S/</th>
                        <th style="padding:8px 12px; text-align:right;">Margen %</th>
                        <th style="padding:8px 12px; text-align:left;">Vol. relativo</th>
                    </tr>
                </thead>
                <tbody>{_rows_html}</tbody>
            </table></div>""", unsafe_allow_html=True)

    # (Sección "Acciones del Día" eliminada — info disponible en vistas del sidebar)


# ═══════════════════════════════════════════════════════════════
#  VISTA CAPI 1: REPOSICIÓN
#  KPIs: % SKUs críticos, # tiendas cob baja, capital atrapado
# ═══════════════════════════════════════════════════════════════

elif nav_page == "📦 Reposición":

    # ── KPIs específicos de la vista ──
    _n_critico = s.get('n_critico', 0)
    _n_total = s.get('total_combos', 1)
    _pct_critico = (_n_critico / _n_total * 100) if _n_total > 0 else 0
    _capital_critico = df_cob[df_cob['estado'] == 'CRÍTICO']['stock_valor_costo'].sum() if 'estado' in df_cob.columns else 0
    _tiendas_cob_baja = df_cob[df_cob['cobertura_sem'] < params['umbral_critico']]['tienda'].nunique() if 'tienda' in df_cob.columns else 0

    st.markdown(f'<div class="section-header"><h3>Vista Reposición</h3><span class="live-badge">CAPI SEMANAL</span></div>', unsafe_allow_html=True)

    _kpi_cols = st.columns(3)
    with _kpi_cols[0]:
        st.markdown(f"""
        <div style="background:{SLATE_50}; border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_CRITICO};">
            <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">% SKUs en estado crítico</div>
            <div style="font-size:1.6rem; font-weight:700; color:{STATUS_CRITICO};">{_pct_critico:.1f}%</div>
            <div style="font-size:0.7rem; color:{SLATE_500};">{_n_critico:,} de {_n_total:,} combos</div>
        </div>""", unsafe_allow_html=True)
    with _kpi_cols[1]:
        st.markdown(f"""
        <div style="background:{SLATE_50}; border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_PRECRITICO};">
            <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">Tiendas con cobertura baja</div>
            <div style="font-size:1.6rem; font-weight:700; color:{STATUS_PRECRITICO};">{_tiendas_cob_baja}</div>
            <div style="font-size:0.7rem; color:{SLATE_500};">< {params['umbral_critico']} semanas</div>
        </div>""", unsafe_allow_html=True)
    with _kpi_cols[2]:
        st.markdown(f"""
        <div style="background:{SLATE_50}; border-radius:12px; padding:16px 20px; border-left:4px solid {TEAL_600};">
            <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">Capital atrapado en críticos</div>
            <div style="font-size:1.6rem; font-weight:700; color:{TEAL_700};">S/ {_capital_critico:,.0f}</div>
            <div style="font-size:0.7rem; color:{SLATE_500};">SKUs en estado CRÍTICO</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    st.markdown("---")

    st.markdown(f"""<div style="background:#FEF2F2; border-left:4px solid {STATUS_CRITICO}; padding:10px 14px; border-radius:10px; margin-bottom:10px;">
    <strong style="color:{STATUS_CRITICO};">Reposición Pendiente por Marca</strong>
    <span style="color:{SLATE_500}; font-size:0.82em;"> &nbsp;·&nbsp; Unidades a reponer y costo total estimado por temporada</span>
    </div>""", unsafe_allow_html=True)

    if not df_rep.empty:
        df_rep_dash = df_rep.copy()
        # Merge temporada y rango_antiguedad desde df_cob si no están en df_rep
        _merge_cols = []
        for _mc in ["temporada", "rango_antiguedad"]:
            if _mc in df_cob.columns and _mc not in df_rep_dash.columns:
                _merge_cols.append(_mc)
        if _merge_cols:
            _map = df_cob[["sku"] + _merge_cols].drop_duplicates(subset=["sku"])
            df_rep_dash = df_rep_dash.merge(_map, on="sku", how="left")

        # ── Filtros dedicados para esta sección ──
        _rep_has_temp = "temporada" in df_rep_dash.columns
        _rep_has_rango = "rango_antiguedad" in df_rep_dash.columns

        _fcol1, _fcol2 = st.columns(2)
        with _fcol1:
            if _rep_has_temp:
                _temps_disp = sorted(df_rep_dash["temporada"].dropna().astype(str).unique().tolist())
                _temps_disp = [t for t in _temps_disp if t.strip() != ""]
                f_rep_temps = st.multiselect(
                    "Temporada", _temps_disp, default=_temps_disp,
                    key="repo_vista_rep_marca_temporada",
                    help="Filtra la reposición por temporada (OI / PV / TT)"
                )
            else:
                f_rep_temps = []
        with _fcol2:
            if _rep_has_rango:
                _rangos_disp = sorted(df_rep_dash["rango_antiguedad"].dropna().astype(str).unique().tolist())
                _rangos_disp = [r for r in _rangos_disp if r.strip() != "" and r != "Sin Rango"]
                _rangos_disp_all = ["Todos"] + _rangos_disp
                f_rep_rango = st.selectbox(
                    "Rango de Antigüedad", _rangos_disp_all,
                    key="repo_vista_rep_marca_rango",
                    help="Excluye productos antiguos que ensucian la reposición"
                )
            else:
                f_rep_rango = "Todos"

        # Aplicar filtros dedicados
        if _rep_has_temp and f_rep_temps:
            df_rep_dash = df_rep_dash[df_rep_dash["temporada"].astype(str).isin(f_rep_temps)]
        if f_rep_rango != "Todos" and _rep_has_rango:
            df_rep_dash = df_rep_dash[df_rep_dash["rango_antiguedad"].astype(str) == f_rep_rango]

        # Traer costo unitario desde df_cob (fuente confiable)
        if "costo" not in df_rep_dash.columns and "costo" in df_cob.columns:
            _costo_map = df_cob.drop_duplicates("sku").set_index("sku")["costo"]
            df_rep_dash["costo"] = df_rep_dash["sku"].map(_costo_map)
        # Calcular costo reposición por SKU
        if "costo" in df_rep_dash.columns:
            df_rep_dash["costo_reposicion"] = df_rep_dash["a_reponer"] * df_rep_dash["costo"].fillna(0)
        elif "precio_vigente" in df_rep_dash.columns:
            df_rep_dash["costo_reposicion"] = df_rep_dash["a_reponer"] * df_rep_dash["precio_vigente"].fillna(0) * 0.5
        else:
            df_rep_dash["costo_reposicion"] = 0

        # Normalizar temporada para display
        if _rep_has_temp:
            df_rep_dash["_temp_display"] = df_rep_dash["temporada"].fillna("Sin Temp").astype(str).str.strip()
            df_rep_dash.loc[df_rep_dash["_temp_display"] == "", "_temp_display"] = "Sin Temp"
        else:
            df_rep_dash["_temp_display"] = "Total"

        # Determinar columna de marca
        _marca_col_rep = None
        for _mc in ["marca", "Marca", "brand"]:
            if _mc in df_rep_dash.columns:
                _marca_col_rep = _mc
                break

        if _marca_col_rep:
            # ── KPIs resumen (datos filtrados) ──
            _total_uds = df_rep_dash["a_reponer"].sum()
            _total_costo = df_rep_dash["costo_reposicion"].sum()
            _n_marcas = df_rep_dash[_marca_col_rep].nunique()

            if _total_uds > 0:
                _k1, _k2, _k3 = st.columns(3)
                with _k1:
                    st.metric("Marcas con Reposición", f"{_n_marcas}")
                with _k2:
                    st.metric("Total Uds a Reponer", f"{int(_total_uds):,}")
                with _k3:
                    st.metric("Costo Total Estimado", f"S/ {_total_costo:,.0f}")

                # ── Chart: Barras agrupadas por temporada dentro de cada marca ──
                _TEMP_COLORS = {"OI": "#3B82F6", "PV": "#F59E0B", "TT": "#10B981", "Sin Temp": "#94A3B8"}

                repo_marca_temp = (
                    df_rep_dash.groupby([_marca_col_rep, "_temp_display"])
                    .agg(uds_reponer=("a_reponer", "sum"), costo_total=("costo_reposicion", "sum"))
                    .reset_index()
                )
                repo_marca_temp = repo_marca_temp[repo_marca_temp["uds_reponer"] > 0]

                # Orden de marcas por total descendente
                _marca_order = (
                    repo_marca_temp.groupby(_marca_col_rep)["uds_reponer"].sum()
                    .sort_values(ascending=True)
                )
                _top_marcas = _marca_order.tail(20).index.tolist()
                repo_marca_temp = repo_marca_temp[repo_marca_temp[_marca_col_rep].isin(_top_marcas)]

                # Tabs: Unidades / Costo
                _tab_uds, _tab_costo = st.tabs(["📦 Unidades a Reponer", "💰 Costo a Reponer"])

                for _tab, _val_col, _val_label, _prefix in [
                    (_tab_uds, "uds_reponer", "Uds", ""),
                    (_tab_costo, "costo_total", "Costo S/", "S/ "),
                ]:
                    with _tab:
                        fig_repo_marca = go.Figure()
                        _temps_in_data = sorted(repo_marca_temp["_temp_display"].unique().tolist())

                        for _temp in _temps_in_data:
                            _df_t = repo_marca_temp[repo_marca_temp["_temp_display"] == _temp]
                            _df_t = _df_t.set_index(_marca_col_rep).reindex(_top_marcas).fillna(0).reset_index()
                            _color = _TEMP_COLORS.get(_temp, "#6B7280")

                            if _prefix:
                                _text_vals = [f"S/ {v:,.0f}" if v > 0 else "" for v in _df_t[_val_col]]
                            else:
                                _text_vals = [f"{int(v):,}" if v > 0 else "" for v in _df_t[_val_col]]

                            fig_repo_marca.add_trace(go.Bar(
                                x=_df_t[_val_col],
                                y=_df_t[_marca_col_rep],
                                orientation="h",
                                name=_temp,
                                marker=dict(color=_color, cornerradius=4),
                                hovertemplate=f"<b>%{{y}}</b> · {_temp}<br>{_val_label}: %{{x:,.0f}}<extra></extra>",
                                text=_text_vals,
                                textposition="inside",
                                textfont=dict(size=9, color="white"),
                            ))

                        fig_repo_marca.update_layout(
                            **_plotly_layout,
                            barmode="stack",
                            xaxis=dict(showgrid=False, showticklabels=False),
                            yaxis=dict(showgrid=False, tickfont=dict(size=10), categoryorder="array", categoryarray=_top_marcas),
                            height=max(380, len(_top_marcas) * 32 + 80),
                            legend=dict(
                                orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5,
                                font=dict(size=11),
                            ),
                        )
                        st.plotly_chart(fig_repo_marca, use_container_width=True)

                # ── Tabla detalle por marca y temporada ──
                with st.expander(f"📋 Ver tabla detalle por marca y temporada ({_n_marcas} marcas)"):
                    _tbl = repo_marca_temp.copy()
                    _tbl["% del Total"] = (_tbl["uds_reponer"] / _total_uds * 100).round(1)
                    _tbl = _tbl.sort_values(["uds_reponer"], ascending=False)
                    _tbl_display = _tbl[[_marca_col_rep, "_temp_display", "uds_reponer", "costo_total", "% del Total"]].copy()
                    _tbl_display.columns = ["Marca", "Temporada", "Uds a Reponer", "Costo Total S/", "% del Total"]
                    _tbl_display["Uds a Reponer"] = _tbl_display["Uds a Reponer"].apply(lambda x: f"{int(x):,}")
                    _tbl_display["Costo Total S/"] = _tbl_display["Costo Total S/"].apply(lambda x: f"S/ {x:,.0f}")
                    _tbl_display["% del Total"] = _tbl_display["% del Total"].apply(lambda x: f"{x}%")
                    st.dataframe(_tbl_display, use_container_width=True, hide_index=True)
            else:
                st.success("Sin reposiciones pendientes con los filtros seleccionados")
        else:
            st.info("No se encontró columna de Marca en los datos de reposición")
    else:
        st.success("Sin reposiciones pendientes")


    st.markdown("---")

    # ── Tabla de reposiciones — vista pivot SKU × Tienda ──
    if df_rep.empty:
        st.success("✅ No hay SKUs bajo cobertura objetivo. Sin reposiciones necesarias.")
    elif not df_rep_pivot.empty:
        st.markdown(f"##### SKUs que requieren reposición — {len(df_rep)} ítems · **{s['uds_reponer']} uds**")
        st.caption("Filas = SKUs · Columnas = Tiendas · Celdas en rojo = unidades a reponer")

        _df_pv_r = df_rep_pivot.copy()
        if "temporada" not in _df_pv_r.columns and "temporada" in df_cob.columns:
            _temp_map_pv = df_cob[["sku", "temporada"]].drop_duplicates(subset=["sku"])
            _df_pv_r = _df_pv_r.merge(_temp_map_pv, on="sku", how="left")
            _df_pv_r["temporada"] = _df_pv_r["temporada"].fillna("Sin Temp").astype(str).str.strip()
            _df_pv_r.loc[_df_pv_r["temporada"] == "", "temporada"] = "Sin Temp"

        # Filtros
        _rpv_f1, _rpv_f2, _rpv_f3 = st.columns(3)
        with _rpv_f1:
            _marcas_rpv = ["Todas"] + sorted(_df_pv_r['marca'].dropna().unique().tolist()) if 'marca' in _df_pv_r.columns else ["Todas"]
            _f_marca_rpv = st.selectbox("Marca", _marcas_rpv, key="rpv_marca")
        with _rpv_f2:
            if "temporada" in _df_pv_r.columns:
                _temps_pv = sorted([t for t in _df_pv_r["temporada"].dropna().unique().tolist() if str(t).strip() != ""])
                _f_temp_rpv = st.selectbox("Temporada", ["Todas"] + _temps_pv, key="rpv_temp")
            else:
                _f_temp_rpv = "Todas"
        with _rpv_f3:
            _cats_rpv = ["Todas"] + sorted(_df_pv_r["categoria"].dropna().unique().tolist())
            _f_cat_rpv = st.selectbox("Categoría", _cats_rpv, key="rpv_cat")

        if _f_marca_rpv != "Todas" and 'marca' in _df_pv_r.columns:
            _df_pv_r = _df_pv_r[_df_pv_r['marca'] == _f_marca_rpv]
        if _f_temp_rpv != "Todas" and "temporada" in _df_pv_r.columns:
            _df_pv_r = _df_pv_r[_df_pv_r["temporada"] == _f_temp_rpv]
        if _f_cat_rpv != "Todas":
            _df_pv_r = _df_pv_r[_df_pv_r["categoria"] == _f_cat_rpv]

        if "temporada" in _df_pv_r.columns:
            _df_pv_r = _df_pv_r.drop(columns=["temporada"])

        # Ordenar por Stock CD descendente
        if "stock_cd" in _df_pv_r.columns:
            _df_pv_r = _df_pv_r.sort_values("stock_cd", ascending=False).reset_index(drop=True)

        _ren_rpv = {"sku": "SKU", "nombre": "Nombre", "categoria": "Categoría", "marca": "Marca"}
        if "stock_cd" in _df_pv_r.columns:
            _ren_rpv["stock_cd"] = "Stock CD"
        _df_pv_r = _df_pv_r.rename(columns=_ren_rpv)

        _rpv_fijas = [c for c in ["SKU", "Nombre", "Categoría", "Marca", "Stock CD"] if c in _df_pv_r.columns]
        _rpv_tiendas = [c for c in _df_pv_r.columns if c not in _rpv_fijas]

        def _hl_repo(val):
            if isinstance(val, (int, float)) and val > 0:
                return f"background-color:{STATUS_CRITICO}; color:#FFF; font-weight:bold; border-radius:4px"
            return ""

        st.dataframe(
            _df_pv_r.style.map(_hl_repo, subset=_rpv_tiendas) if _rpv_tiendas else _df_pv_r,
            use_container_width=True, height=500, hide_index=True,
        )
        st.caption(f"Mostrando {len(_df_pv_r):,} SKUs · ✅ Regla 40%: SKUs con ≥40% dscto excluidos desde motor")

        # Detalle flat en expander (para quienes necesiten la vista lineal)
        with st.expander("📋 Vista detalle por línea SKU × Tienda", expanded=False):
            _df_rep_v1 = df_rep.copy()
            if _f_marca_rpv != "Todas" and 'marca' in _df_rep_v1.columns:
                _df_rep_v1 = _df_rep_v1[_df_rep_v1['marca'] == _f_marca_rpv]
            if _f_temp_rpv != "Todas" and 'temporada' in _df_rep_v1.columns:
                _df_rep_v1 = _df_rep_v1[_df_rep_v1['temporada'] == _f_temp_rpv]
            _rep_show_cols = [c for c in ['marca', 'sku', 'nombre', 'tienda', 'stock_actual', 'cobertura_actual',
                                           'prom_vta_sem', 'a_reponer', 'stock_cd', 'pct_descuento', 'urgencia'] if c in _df_rep_v1.columns]
            _df_rep_disp = _df_rep_v1[_rep_show_cols].rename(columns={
                'marca': 'Marca', 'sku': 'SKU', 'nombre': 'Producto', 'tienda': 'Tienda',
                'stock_actual': 'Stock', 'cobertura_actual': 'Cob (sem)', 'prom_vta_sem': 'Vta Sem',
                'a_reponer': 'A Reponer', 'stock_cd': 'Stock CD', 'pct_descuento': 'Dscto',
                'urgencia': 'Urgencia',
            })
            if 'A Reponer' in _df_rep_disp.columns:
                _df_rep_disp = _df_rep_disp.sort_values('A Reponer', ascending=False)
            _rep_fmt = {}
            if 'Cob (sem)' in _df_rep_disp.columns: _rep_fmt['Cob (sem)'] = '{:.1f}'
            if 'Dscto' in _df_rep_disp.columns: _rep_fmt['Dscto'] = '{:.0%}'
            st.dataframe(_df_rep_disp.style.format(_rep_fmt, na_rep="—"),
                         use_container_width=True, hide_index=True, height=420)
    else:
        # Fallback: si no hay pivot disponible, mostrar tabla flat
        st.markdown(f"##### SKUs que requieren reposición — {len(df_rep)} ítems · **{s['uds_reponer']} uds**")
        _rep_show_cols = [c for c in ['marca', 'sku', 'nombre', 'tienda', 'stock_actual', 'cobertura_actual',
                                       'prom_vta_sem', 'a_reponer', 'stock_cd', 'pct_descuento', 'urgencia'] if c in df_rep.columns]
        _df_rep_disp = df_rep[_rep_show_cols].rename(columns={
            'marca': 'Marca', 'sku': 'SKU', 'nombre': 'Producto', 'tienda': 'Tienda',
            'stock_actual': 'Stock', 'cobertura_actual': 'Cob (sem)', 'prom_vta_sem': 'Vta Sem',
            'a_reponer': 'A Reponer', 'stock_cd': 'Stock CD', 'pct_descuento': 'Dscto',
            'urgencia': 'Urgencia',
        }).sort_values('A Reponer', ascending=False)
        st.dataframe(_df_rep_disp, use_container_width=True, hide_index=True, height=500)

    # ── Pareto de tiendas con más SKUs críticos ──
    if not df_rep.empty and 'tienda' in df_rep.columns:
        st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
        st.markdown("##### Tiendas con mayor necesidad de reposición")
        _pareto_tienda = df_rep.groupby('tienda').agg(
            n_skus=('sku', 'nunique'),
            uds_reponer=('a_reponer', 'sum'),
            capital=('stock_valor_costo', 'sum') if 'stock_valor_costo' in df_rep.columns else ('a_reponer', 'count'),
        ).reset_index().sort_values('uds_reponer', ascending=False).head(15)
        _pareto_tienda = _pareto_tienda.rename(columns={
            'tienda': 'Tienda', 'n_skus': 'SKUs a reponer',
            'uds_reponer': 'Uds a reponer', 'capital': 'Capital S/',
        })
        _pareto_fmt = {'Uds a reponer': '{:,.0f}'}
        if 'Capital S/' in _pareto_tienda.columns:
            _pareto_fmt['Capital S/'] = 'S/ {:,.0f}'
        st.dataframe(_pareto_tienda.style.format(_pareto_fmt, na_rep="—"),
                     use_container_width=True, hide_index=True, height=min(400, 50 + len(_pareto_tienda) * 35))
        st.caption("Top 15 tiendas por unidades a reponer. Priorizar empuje y validación de exhibición.")

    # ── Descarga Excel — misma vista pivot que se muestra en pantalla ──
    if not df_rep.empty and not df_rep_pivot.empty:
        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

        # Preparar pivot con los mismos filtros aplicados en pantalla
        _pv_xl = df_rep_pivot.copy()
        if "temporada" not in _pv_xl.columns and "temporada" in df_cob.columns:
            _temp_map_xl = df_cob[["sku", "temporada"]].drop_duplicates(subset=["sku"])
            _pv_xl = _pv_xl.merge(_temp_map_xl, on="sku", how="left")
        if _f_marca_rpv != "Todas" and 'marca' in _pv_xl.columns:
            _pv_xl = _pv_xl[_pv_xl['marca'] == _f_marca_rpv]
        if _f_temp_rpv != "Todas" and "temporada" in _pv_xl.columns:
            _pv_xl = _pv_xl[_pv_xl["temporada"] == _f_temp_rpv]
        if _f_cat_rpv != "Todas" and "categoria" in _pv_xl.columns:
            _pv_xl = _pv_xl[_pv_xl["categoria"] == _f_cat_rpv]
        # Eliminar columna temporada del pivot (ya filtrada)
        if "temporada" in _pv_xl.columns:
            _pv_xl = _pv_xl.drop(columns=["temporada"])
        # Ordenar por Stock CD descendente
        if "stock_cd" in _pv_xl.columns:
            _pv_xl = _pv_xl.sort_values("stock_cd", ascending=False).reset_index(drop=True)

        _rep_xl_buf = io.BytesIO()
        with pd.ExcelWriter(_rep_xl_buf, engine='openpyxl') as _w:
            # Pestaña 1: Vista pivot (la que se manda a inventarios)
            _pv_xl.to_excel(_w, sheet_name='Reposición Pivot', index=False)
            # Colorear celdas con unidades > 0 en columnas de tienda
            _ws_pv = _w.sheets['Reposición Pivot']
            from openpyxl.styles import PatternFill, Font as XlFont
            _pv_headers = [cell.value for cell in _ws_pv[1]]
            _pv_fijas = {'sku', 'nombre', 'categoria', 'marca', 'stock_cd'}
            _pv_tienda_idxs = [i + 1 for i, h in enumerate(_pv_headers) if h and str(h) not in _pv_fijas]
            _red_fill = PatternFill(start_color='EF4444', end_color='EF4444', fill_type='solid')
            _white_font = XlFont(color='FFFFFF', bold=True)
            for row in range(2, _ws_pv.max_row + 1):
                for col_idx in _pv_tienda_idxs:
                    cell = _ws_pv.cell(row=row, column=col_idx)
                    if cell.value and isinstance(cell.value, (int, float)) and cell.value > 0:
                        cell.fill = _red_fill
                        cell.font = _white_font
            # Pestaña 2: Detalle flat con precios
            _rep_xl_cols = [c for c in ['marca', 'sku', 'nombre', 'categoria', 'tienda', 'stock_actual',
                                         'stock_cd', 'cobertura_actual', 'prom_vta_sem', 'a_reponer',
                                         'cob_post_rep', 'pct_descuento', 'urgencia'] if c in df_rep.columns]
            _add_pricing_cols(
                df_rep[_rep_xl_cols].sort_values('stock_cd', ascending=False),
                df_cob, 'Detalle SKU×Tienda', _w
            )
        _rep_xl_buf.seek(0)
        _n_pv_xl = len(_pv_xl)
        st.download_button(
            f"📥 Descargar reposición — {_n_pv_xl:,} SKUs (.xlsx)",
            data=_rep_xl_buf.getvalue(),
            file_name="Capi_Reposicion.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_reposicion_vista",
        )

    # ── Alertas y recomendaciones de reposición ──
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    st.markdown("##### Recomendaciones de gestión")
    _alertas_repo = [a for a in briefing_items if any(kw in a.get('titulo', '').lower() for kw in ['cobertura', 'quiebre', 'reposición', 'repo', 'fast', 'agotamiento'])]
    if _alertas_repo:
        for _ar in _alertas_repo[:5]:
            with st.expander(f"{_ar.get('icono', '⚠️')} {_ar['titulo']}", expanded=False):
                st.markdown(_ar['mensaje'])
    else:
        st.info("Sin alertas de reposición activas.")


# ═══════════════════════════════════════════════════════════════
#  VISTA CAPI 2: SOBRESTOCK
#  KPIs: capital en sobrestock, % aparente, SKUs >40 sem cob
# ═══════════════════════════════════════════════════════════════

elif nav_page == "📊 Sobrestock":

    # ── KPIs específicos de la vista (usando columnas del motor) ──
    _n_sobrestock = s.get('n_sobrestock', 0) + s.get('n_liquidar', 0)
    _capital_sobre = s.get('capital_sobrestock', 0)
    _df_sobre = df_cob[df_cob['estado'].isin(['SOBRESTOCK', 'LIQUIDAR', 'ALTO'])].copy() if 'estado' in df_cob.columns else pd.DataFrame()
    _n_aparente = s.get('n_sobrestock_aparente', 0)
    _pct_aparente = (_n_aparente / len(_df_sobre) * 100) if len(_df_sobre) > 0 else 0
    _skus_40sem = (df_cob['cobertura_sem'] > 40).sum() if 'cobertura_sem' in df_cob.columns else 0

    st.markdown(f'<div class="section-header"><h3>Vista Sobrestock</h3><span class="live-badge">CAPI SEMANAL</span></div>', unsafe_allow_html=True)

    _kpi_cols2 = st.columns(3)
    with _kpi_cols2[0]:
        st.markdown(f"""
        <div style="background:{SLATE_50}; border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_SOBRESTOCK};">
            <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">Capital en sobrestock</div>
            <div style="font-size:1.6rem; font-weight:700; color:{STATUS_SOBRESTOCK};">S/ {_capital_sobre:,.0f}</div>
            <div style="font-size:0.7rem; color:{SLATE_500};">{_n_sobrestock:,} combos SKU×Tienda</div>
        </div>""", unsafe_allow_html=True)
    with _kpi_cols2[1]:
        st.markdown(f"""
        <div style="background:{SLATE_50}; border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_ALTO};">
            <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">% sobrestock aparente</div>
            <div style="font-size:1.6rem; font-weight:700; color:{STATUS_ALTO};">{_pct_aparente:.0f}%</div>
            <div style="font-size:0.7rem; color:{SLATE_500};">{_n_aparente} combos con >60% stock en CD</div>
        </div>""", unsafe_allow_html=True)
    with _kpi_cols2[2]:
        st.markdown(f"""
        <div style="background:{SLATE_50}; border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_MUERTO};">
            <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">SKUs con >40 sem cobertura</div>
            <div style="font-size:1.6rem; font-weight:700; color:{STATUS_MUERTO};">{_skus_40sem:,}</div>
            <div style="font-size:0.7rem; color:{SLATE_500};">Candidatos a markdown</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    # ── Tabs: Real vs Aparente ──
    _sobre_tab1, _sobre_tab2, _sobre_tab3 = st.tabs(["Sobrestock Real", "Sobrestock Aparente (Empuje)", "Obsoletos"])

    with _sobre_tab1:
        st.markdown("##### Sobrestock real — acción: markdown o transferencia")
        _df_real = _df_sobre[~_df_sobre['sobrestock_aparente']].copy() if 'sobrestock_aparente' in _df_sobre.columns else _df_sobre.copy()
        if _df_real.empty:
            st.info("No hay sobrestock real detectado con los filtros actuales.")
        else:
            _real_cols = [c for c in ['marca', 'sku', 'nombre', 'tienda', 'stock_total', 'cobertura_sem',
                                       'edad_semanas', 'pct_descuento', 'stock_valor_costo'] if c in _df_real.columns]
            _df_real_d = _df_real[_real_cols].rename(columns={
                'marca': 'Marca', 'sku': 'SKU', 'nombre': 'Producto', 'tienda': 'Tienda',
                'stock_total': 'Stock', 'cobertura_sem': 'Cob (sem)', 'edad_semanas': 'Edad (sem)',
                'pct_descuento': 'Dscto', 'stock_valor_costo': 'Capital S/',
            }).sort_values('Capital S/', ascending=False) if 'stock_valor_costo' in _df_real.columns else _df_real[_real_cols]
            st.dataframe(_df_real_d.head(200).style.format({
                'Cob (sem)': '{:.1f}', 'Dscto': '{:.0%}', 'Capital S/': 'S/ {:,.0f}',
            }, na_rep="—"), use_container_width=True, hide_index=True, height=400)
            st.caption(f"{len(_df_real):,} combos en sobrestock real")

    with _sobre_tab2:
        st.markdown("##### Sobrestock aparente — acción: empuje a piso")
        st.caption("SKUs con >60% del stock concentrado en CD. Probablemente no salieron a piso de venta.")
        _df_aparente = _df_sobre[_df_sobre['sobrestock_aparente']].copy() if 'sobrestock_aparente' in _df_sobre.columns else pd.DataFrame()
        if _df_aparente.empty:
            if 'stock_cd' not in df_cob.columns:
                st.warning("No hay datos de Stock CD disponibles para detectar sobrestock aparente. Asegurar que la Base Profundidad incluya esta columna.")
            else:
                st.info("No hay sobrestock aparente detectado.")
        else:
            _apar_cols = [c for c in ['marca', 'sku', 'nombre', 'tienda', 'stock_total', 'stock_cd',
                                       'ratio_cd', 'cobertura_sem', 'stock_valor_costo'] if c in _df_aparente.columns]
            _df_apar_d = _df_aparente[_apar_cols].rename(columns={
                'marca': 'Marca', 'sku': 'SKU', 'nombre': 'Producto', 'tienda': 'Tienda',
                'stock_total': 'Stock Total', 'stock_cd': 'Stock CD', 'ratio_cd': '% en CD',
                'cobertura_sem': 'Cob (sem)', 'stock_valor_costo': 'Capital S/',
            }).sort_values('Capital S/', ascending=False) if 'stock_valor_costo' in _df_aparente.columns else _df_aparente[_apar_cols]
            st.dataframe(_df_apar_d.head(200).style.format({
                '% en CD': '{:.0%}', 'Cob (sem)': '{:.1f}', 'Capital S/': 'S/ {:,.0f}',
            }, na_rep="—"), use_container_width=True, hide_index=True, height=400)
            st.caption(f"{len(_df_aparente):,} combos — instruir empuje a piso")

    with _sobre_tab3:
        st.markdown("##### Obsoletos por antigüedad")
        _df_obs_tab = df_cob[df_cob['estado'].isin(['DORMIDO', 'MUERTO', 'LIQUIDAR'])].copy() if 'estado' in df_cob.columns else pd.DataFrame()
        if _df_obs_tab.empty:
            st.info("No hay mercadería obsoleta detectada.")
        else:
            _obs_cols = [c for c in ['marca', 'sku', 'nombre', 'tienda', 'stock_total', 'edad_semanas',
                                      'cobertura_sem', 'pct_descuento', 'stock_valor_costo'] if c in _df_obs_tab.columns]
            _df_obs_d = _df_obs_tab[_obs_cols].rename(columns={
                'marca': 'Marca', 'sku': 'SKU', 'nombre': 'Producto', 'tienda': 'Tienda',
                'stock_total': 'Stock', 'edad_semanas': 'Edad (sem)', 'cobertura_sem': 'Cob (sem)',
                'pct_descuento': 'Dscto', 'stock_valor_costo': 'Capital S/',
            }).sort_values('Capital S/', ascending=False) if 'stock_valor_costo' in _df_obs_tab.columns else _df_obs_tab[_obs_cols]
            st.dataframe(_df_obs_d.head(300).style.format({
                'Cob (sem)': '{:.1f}', 'Dscto': '{:.0%}', 'Capital S/': 'S/ {:,.0f}',
            }, na_rep="—"), use_container_width=True, hide_index=True, height=400)
            st.caption(f"{len(_df_obs_tab):,} combos en estados DORMIDO/MUERTO/LIQUIDAR · Capital: S/ {_df_obs_tab['stock_valor_costo'].sum():,.0f}")

    # ── Descarga Excel Sobrestock ──
    if not _df_sobre.empty:
        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
        _sobre_xl_cols = [c for c in ['marca', 'sku', 'nombre', 'categoria', 'tienda', 'estado',
                                       'stock_total', 'stock_cd', 'cobertura_sem', 'edad_semanas',
                                       'pct_descuento', 'stock_valor_costo', 'sobrestock_aparente'] if c in _df_sobre.columns]
        _sobre_xl_buf = io.BytesIO()
        with pd.ExcelWriter(_sobre_xl_buf, engine='openpyxl') as _w:
            _df_real_xl = _df_sobre[~_df_sobre.get('sobrestock_aparente', pd.Series(False, index=_df_sobre.index))].copy() if 'sobrestock_aparente' in _df_sobre.columns else _df_sobre.copy()
            _df_apar_xl = _df_sobre[_df_sobre['sobrestock_aparente']].copy() if 'sobrestock_aparente' in _df_sobre.columns else pd.DataFrame()
            if not _df_real_xl.empty:
                _add_pricing_cols(_df_real_xl[_sobre_xl_cols].sort_values('stock_valor_costo', ascending=False), df_cob, 'Sobrestock Real', _w)
            if not _df_apar_xl.empty:
                _add_pricing_cols(_df_apar_xl[_sobre_xl_cols].sort_values('stock_valor_costo', ascending=False), df_cob, 'Empuje a Piso', _w)
        _sobre_xl_buf.seek(0)
        st.download_button(
            f"📥 Descargar sobrestock — {len(_df_sobre):,} combos (.xlsx)",
            data=_sobre_xl_buf.getvalue(),
            file_name="Capi_Sobrestock.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_sobrestock_vista",
        )

    # ── Alertas y recomendaciones de sobrestock ──
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    st.markdown("##### Recomendaciones de gestión")
    _alertas_sobre = [a for a in briefing_items if any(kw in a.get('titulo', '').lower() for kw in ['sobrestock', 'obsolet', 'dormido', 'muerto', 'liquidar', 'antigüedad', 'edad'])]
    if _alertas_sobre:
        for _as in _alertas_sobre[:5]:
            with st.expander(f"{_as.get('icono', '⚠️')} {_as['titulo']}", expanded=False):
                st.markdown(_as['mensaje'])
    else:
        st.info("Sin alertas de sobrestock activas.")


# ═══════════════════════════════════════════════════════════════
#  VISTA CAPI 3: MARCAS TERCERAS
#  KPIs: margen efectivo, cobertura por marca, capital invertido
# ═══════════════════════════════════════════════════════════════

elif nav_page == "🏷️ Marcas Terceras":

    st.markdown(f'<div class="section-header"><h3>Vista Marcas Terceras</h3><span class="live-badge">CAPI SEMANAL</span></div>', unsafe_allow_html=True)

    # ── Construir resumen por marca tercera ──
    # Marcas propias a excluir
    _MARCAS_PROPIAS = {'MARQUIS', 'NAVIGATA', 'CACHAREL', 'SPAVALDI', 'OSCAR DE LA RENTA', 'US POLO', 'NAUTICA'}
    _df_terceras = df_cob[~df_cob['marca'].str.upper().isin(_MARCAS_PROPIAS)].copy() if 'marca' in df_cob.columns else df_cob.copy()

    if _df_terceras.empty:
        st.info("No hay datos de marcas terceras disponibles.")
    else:
        _IGV = 1.18
        _t_marca = _df_terceras.groupby('marca').agg(
            capital=('stock_valor_costo', 'sum'),
            stock_uds=('stock_total', 'sum'),
            cob_prom=('cobertura_sem', 'mean'),
            n_skus=('sku', 'nunique'),
            vta_sem=('prom_vta_uds', 'sum'),
        ).reset_index()

        # Calcular venta a costo y margen por marca (misma fórmula que Dashboard)
        # vta_soles_4sem y contrib_soles_4sem son a nivel SKU → deduplicar antes de sumar
        _has_vta_terc = 'vta_soles_4sem' in _df_terceras.columns and 'contrib_soles_4sem' in _df_terceras.columns
        if _has_vta_terc:
            _df_sku_terc = _df_terceras.drop_duplicates('sku')[['marca', 'vta_soles_4sem', 'contrib_soles_4sem']].copy()
            _vta_marca_terc = _df_sku_terc.groupby('marca').agg(
                vta_soles=('vta_soles_4sem', 'sum'),
                contrib_soles=('contrib_soles_4sem', 'sum'),
            ).reset_index()
            _vta_marca_terc['vta_costo'] = (_vta_marca_terc['vta_soles'] - _vta_marca_terc['contrib_soles']).clip(lower=0)
            _vta_marca_terc['margen_efectivo'] = np.where(
                _vta_marca_terc['vta_soles'] > 0,
                (_vta_marca_terc['contrib_soles'] / _vta_marca_terc['vta_soles'] * 100).round(1),
                0
            )
            _t_marca = pd.merge(_t_marca, _vta_marca_terc[['marca', 'vta_soles', 'contrib_soles', 'vta_costo', 'margen_efectivo']], on='marca', how='left')
            _t_marca['vta_costo'] = _t_marca['vta_costo'].fillna(0)
        else:
            _t_marca['margen_efectivo'] = np.nan
            _t_marca['vta_soles'] = 0
            _t_marca['contrib_soles'] = 0
            _t_marca['vta_costo'] = 0

        # Sell-through por marca
        _st_data = _df_terceras.groupby('marca').agg(
            vta_4sem=('prom_vta_uds', 'sum'),  # Aprox 1 semana × marca
            stock_sum=('stock_total', 'sum'),
        ).reset_index()
        _st_data['sell_through'] = np.where(
            (_st_data['vta_4sem'] + _st_data['stock_sum']) > 0,
            (_st_data['vta_4sem'] / (_st_data['vta_4sem'] + _st_data['stock_sum']) * 100).round(1),
            0
        )
        _t_marca = pd.merge(_t_marca, _st_data[['marca', 'sell_through']], on='marca', how='left')

        _t_marca = _t_marca.sort_values('capital', ascending=False)

        # ── KPIs ──
        _margen_prom = _t_marca['margen_efectivo'].mean() if 'margen_efectivo' in _t_marca.columns else 0
        _cob_prom_terc = _t_marca['cob_prom'].mean()
        _capital_total_terc = _t_marca['capital'].sum()

        _kpi_cols3 = st.columns(3)
        with _kpi_cols3[0]:
            st.markdown(f"""
            <div style="background:{SLATE_50}; border-radius:12px; padding:16px 20px; border-left:4px solid {TEAL_600};">
                <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">Margen efectivo promedio</div>
                <div style="font-size:1.6rem; font-weight:700; color:{TEAL_700};">{_margen_prom:.1f}%</div>
                <div style="font-size:0.7rem; color:{SLATE_500};">Contribución / Vta Soles 4sem</div>
            </div>""", unsafe_allow_html=True)
        with _kpi_cols3[1]:
            st.markdown(f"""
            <div style="background:{SLATE_50}; border-radius:12px; padding:16px 20px; border-left:4px solid {SLATE_700};">
                <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">Cobertura promedio</div>
                <div style="font-size:1.6rem; font-weight:700; color:{SLATE_700};">{_cob_prom_terc:.1f} sem</div>
                <div style="font-size:0.7rem; color:{SLATE_500};">{len(_t_marca)} marcas terceras</div>
            </div>""", unsafe_allow_html=True)
        with _kpi_cols3[2]:
            st.markdown(f"""
            <div style="background:{SLATE_50}; border-radius:12px; padding:16px 20px; border-left:4px solid {SLATE_900};">
                <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">Capital invertido</div>
                <div style="font-size:1.6rem; font-weight:700; color:{SLATE_900};">S/ {_capital_total_terc:,.0f}</div>
                <div style="font-size:0.7rem; color:{SLATE_500};">Top 10 concentran {(_t_marca.head(10)['capital'].sum() / _capital_total_terc * 100):.0f}%</div>
            </div>""", unsafe_allow_html=True)

        st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

        # ── Tabla performance por marca tercera ──
        st.markdown("##### Performance por marca tercera")
        _t_disp = _t_marca[['marca', 'capital', 'vta_costo', 'margen_efectivo', 'cob_prom', 'sell_through',
                             'vta_sem', 'stock_uds', 'n_skus']].rename(columns={
            'marca': 'Marca', 'capital': 'Capital S/', 'vta_costo': 'Vta Costo S/',
            'margen_efectivo': 'Margen %',
            'cob_prom': 'Cob (sem)', 'sell_through': 'ST %', 'vta_sem': 'Vta Sem',
            'stock_uds': 'Stock Uds', 'n_skus': 'SKUs',
        })
        # Gradient manual para Margen % (sin necesitar matplotlib)
        def _margen_color(val):
            try:
                v = float(val)
            except (TypeError, ValueError):
                return ''
            # RdYlGn: rojo(-10) → amarillo(25) → verde(60)
            if v <= -10:
                r, g = 215, 48
            elif v <= 25:
                ratio = (v + 10) / 35
                r = int(215 + (255 - 215) * ratio) if ratio < 0.5 else int(255 - (255 - 76) * (ratio - 0.5) * 2)
                g = int(48 + (255 - 48) * ratio) if ratio < 0.5 else int(255 - (255 - 153) * (ratio - 0.5) * 2)
            else:
                ratio = min((v - 25) / 35, 1.0)
                r, g = int(76 - 76 * ratio), int(153 + (100) * ratio)
            return f'background-color: rgba({r},{g},50,0.25); color: inherit;'

        _t_styled = _t_disp.style.format({
            'Capital S/': 'S/ {:,.0f}', 'Vta Costo S/': 'S/ {:,.0f}',
            'Margen %': '{:.1f}%', 'Cob (sem)': '{:.1f}',
            'ST %': '{:.1f}%', 'Vta Sem': '{:,.0f}', 'Stock Uds': '{:,.0f}',
        }, na_rep="—").map(_margen_color, subset=['Margen %'])
        st.dataframe(_t_styled, use_container_width=True, hide_index=True, height=500)

    # ── Descarga Excel Terceras ──
    if not _df_terceras.empty and '_t_marca' in locals():
        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
        _terc_xl_buf = io.BytesIO()
        with pd.ExcelWriter(_terc_xl_buf, engine='openpyxl') as _w:
            _t_marca.to_excel(_w, sheet_name='Resumen por Marca', index=False)
            _terc_det_cols = [c for c in ['marca', 'sku', 'nombre', 'categoria', 'tienda', 'estado',
                                           'stock_total', 'cobertura_sem', 'prom_vta_uds', 'edad_semanas',
                                           'pct_descuento', 'stock_valor_costo'] if c in _df_terceras.columns]
            _add_pricing_cols(
                _df_terceras[_terc_det_cols].sort_values(['marca', 'stock_valor_costo'], ascending=[True, False]),
                df_cob, 'Detalle SKU×Tienda', _w
            )
        _terc_xl_buf.seek(0)
        st.download_button(
            f"📥 Descargar marcas terceras — {len(_t_marca)} marcas (.xlsx)",
            data=_terc_xl_buf.getvalue(),
            file_name="Capi_Marcas_Terceras.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_terceras_vista",
        )

    # ── Alertas y recomendaciones de terceras ──
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    st.markdown("##### Recomendaciones de gestión")
    # Filtrar alertas relevantes a terceras, excluyendo las que mencionan marcas propias
    _alertas_terc_raw = [a for a in briefing_items if any(kw in a.get('titulo', '').lower() for kw in ['marca', 'contribución', 'sell-through', 'tercera', 'venta', 'top'])]
    _alertas_terc = [a for a in _alertas_terc_raw
                     if not any(mp.lower() in a.get('mensaje', '').lower()
                                for mp in _MARCAS_PROPIAS)]
    if _alertas_terc:
        for _at in _alertas_terc[:5]:
            with st.expander(f"{_at.get('icono', '⚠️')} {_at['titulo']}", expanded=False):
                st.markdown(_at['mensaje'])
    else:
        st.info("Sin alertas de marcas terceras activas.")


# ═══════════════════════════════════════════════════════════════
#  DETALLE — Vistas de análisis granular
# ═══════════════════════════════════════════════════════════════

# ─── TAB 1: Cobertura ─────────────────────────────────────────
#  Resumen por MARCA con desplegable por TIENDA

elif nav_page == "📈 Cobertura" or nav_page == "📊 Cobertura":
    st.markdown("#### 📊 Cobertura por Marca")
    st.caption("Resumen por marca con desglose por tienda. Cobertura = Stock Total / Vta Semanal.")

    has_marca_cob = "marca" in df_cob.columns

    # Filtros rápidos
    _cob_fcol1, _cob_fcol2 = st.columns(2)
    with _cob_fcol1:
        _cob_estados = ["Todos"] + list(motor_v2.ESTADO_ORDEN.keys())
        _f_cob_est = st.selectbox("Filtrar por Estado", _cob_estados, key="cob_resumen_est")
    with _cob_fcol2:
        _cob_cats = ["Todas"] + sorted(df_cob["categoria"].dropna().unique().tolist())
        _f_cob_cat = st.selectbox("Filtrar por Categoría", _cob_cats, key="cob_resumen_cat")

    df_cob_filt = df_cob.copy()
    if _f_cob_est != "Todos":
        df_cob_filt = df_cob_filt[df_cob_filt["estado"] == _f_cob_est]
    if _f_cob_cat != "Todas":
        df_cob_filt = df_cob_filt[df_cob_filt["categoria"] == _f_cob_cat]

    if has_marca_cob:
        # Resumen por marca
        _cob_marca = df_cob_filt.groupby("marca").agg(
            stock_total=("stock_total", "sum"),
            vta_semanal=("prom_vta_uds", "sum"),
            capital=("stock_valor_costo", "sum"),
            n_combos=("sku", "count"),
            n_skus=("sku", "nunique"),
            n_tiendas=("tienda", "nunique"),
        ).reset_index()
        _cob_marca["cobertura"] = _cob_marca.apply(
            lambda r: round(r["stock_total"] / r["vta_semanal"], 1) if r["vta_semanal"] > 0 else None, axis=1
        )
        _cob_marca = _cob_marca.sort_values("capital", ascending=False)

        # Tabla resumen
        _cob_marca_disp = _cob_marca.rename(columns={
            "marca": "Marca", "stock_total": "Stock Uds", "capital": "Capital S/",
            "cobertura": "Cob (sem)", "n_skus": "SKUs", "n_tiendas": "Tiendas",
        })
        st.dataframe(
            _cob_marca_disp[["Marca", "Stock Uds", "Capital S/", "Cob (sem)", "SKUs", "Tiendas"]].style.format({
                "Stock Uds": "{:,.0f}", "Capital S/": "S/ {:,.0f}", "Cob (sem)": "{:.1f}",
            }, na_rep="—"),
            use_container_width=True, hide_index=True,
        )

        st.markdown("---")
        st.caption("Expande cada marca para ver el desglose por tienda:")

        for _, cm in _cob_marca.iterrows():
            _cob_label = f"{cm['cobertura']:.1f} sem" if pd.notna(cm['cobertura']) else "—"
            with st.expander(f"📊 {cm['marca']} — Cob: {_cob_label} · Capital: S/ {cm['capital']:,.0f} · {int(cm['n_skus'])} SKUs"):
                _t_grp = df_cob_filt[df_cob_filt["marca"] == cm["marca"]].groupby("tienda").agg(
                    stock_uds=("stock_total", "sum"),
                    vta_sem=("prom_vta_uds", "sum"),
                    capital=("stock_valor_costo", "sum"),
                    n_skus=("sku", "nunique"),
                ).reset_index()
                _t_grp["cobertura"] = _t_grp.apply(
                    lambda r: round(r["stock_uds"] / r["vta_sem"], 1) if r["vta_sem"] > 0 else None, axis=1
                )
                _t_grp = _t_grp.sort_values("capital", ascending=False)
                _t_grp_disp = _t_grp.rename(columns={
                    "tienda": "Tienda", "stock_uds": "Stock Uds", "vta_sem": "Vta Sem (uds)",
                    "capital": "Capital S/", "cobertura": "Cob (sem)", "n_skus": "SKUs",
                })
                st.dataframe(
                    _t_grp_disp[["Tienda", "Stock Uds", "Vta Sem (uds)", "Capital S/", "Cob (sem)", "SKUs"]].style.format({
                        "Stock Uds": "{:,.0f}", "Vta Sem (uds)": "{:.0f}",
                        "Capital S/": "S/ {:,.0f}", "Cob (sem)": "{:.1f}",
                    }, na_rep="—"),
                    use_container_width=True, hide_index=True,
                )
    else:
        # Sin marca: solo por tienda
        _cob_tienda = df_cob_filt.groupby("tienda").agg(
            stock_total=("stock_total", "sum"),
            vta_semanal=("prom_vta_uds", "sum"),
            capital=("stock_valor_costo", "sum"),
            n_skus=("sku", "nunique"),
        ).reset_index()
        _cob_tienda["cobertura"] = _cob_tienda.apply(
            lambda r: round(r["stock_total"] / r["vta_semanal"], 1) if r["vta_semanal"] > 0 else None, axis=1
        )
        _cob_tienda = _cob_tienda.sort_values("capital", ascending=False)
        _cob_tienda.columns = ["Tienda", "Stock Uds", "Vta Sem (uds)", "Capital S/", "SKUs", "Cob (sem)"]
        st.dataframe(_cob_tienda.style.format({
            "Stock Uds": "{:,.0f}", "Vta Sem (uds)": "{:.0f}",
            "Capital S/": "S/ {:,.0f}", "Cob (sem)": "{:.1f}",
        }, na_rep="—"), use_container_width=True, hide_index=True)

    st.caption(f"{len(df_cob_filt):,} combinaciones SKU×Tienda después de filtros")
    st.markdown("---")

    st.markdown("---")

    _tienda_agg = df_cob_filt.groupby("tienda").agg(
        stock_total=("stock_total", "sum"),
        vta_semanal=("prom_vta_uds", "sum"),
        stock_valor_costo=("stock_valor_costo", "sum"),
    ).reset_index()
    _tienda_agg["cobertura_tienda"] = _tienda_agg.apply(
        lambda r: round(r["stock_total"] / r["vta_semanal"], 1) if r["vta_semanal"] > 0 else None, axis=1
    )
    _tienda_agg = _tienda_agg.dropna(subset=["cobertura_tienda"])

    top5_menor = _tienda_agg.nsmallest(5, "cobertura_tienda")
    top5_mayor = _tienda_agg.nlargest(5, "cobertura_tienda")

    dash_t1, dash_t2 = st.columns(2)

    with dash_t1:
        st.markdown(f"""<div style="background:#FEF2F2; border-left:4px solid {STATUS_CRITICO}; padding:10px 14px; border-radius:10px; margin-bottom:10px;">
        <strong style="color:{STATUS_CRITICO};">Top 5 Tiendas — Menor Cobertura</strong>
        <span style="color:{SLATE_500}; font-size:0.82em;"> &nbsp;·&nbsp; Oportunidad de reposición</span>
        </div>""", unsafe_allow_html=True)

        for _, row_t in top5_menor.iterrows():
            tienda_name = row_t["tienda"]
            cob_val = row_t["cobertura_tienda"]
            capital_val = row_t["stock_valor_costo"]

            st.markdown(f"""<div style="background:white; border:1px solid {SLATE_200}; border-radius:10px; padding:10px 14px; margin-bottom:4px;">
            <span style="font-weight:600; color:{SLATE_900};">{tienda_name}</span>
            &nbsp;·&nbsp; <span style="color:{STATUS_CRITICO}; font-weight:700;">{cob_val:.1f} sem</span>
            &nbsp;·&nbsp; <span style="color:{SLATE_500}; font-size:0.85em;">Capital: S/ {capital_val:,.0f}</span>
            </div>""", unsafe_allow_html=True)

            with st.expander(f"Ver detalle por marca — {tienda_name}", expanded=False):
                marca_det = df_cob_filt[df_cob_filt["tienda"] == tienda_name].copy()
                if "marca" in marca_det.columns:
                    grp = marca_det.groupby("marca").agg(
                        stock_costo=("stock_valor_costo", "sum"),
                        vta_sem_uds=("prom_vta_uds", "sum"),
                        stock_uds=("stock_total", "sum"),
                    ).reset_index()
                    grp["vta_sem_costo"] = 0.0
                    marca_vta_costo = marca_det.groupby("marca").apply(
                        lambda g: (g["prom_vta_uds"] * g["costo"]).sum()
                    ).reset_index(name="vta_sem_costo")
                    grp = grp.merge(marca_vta_costo, on="marca", how="left", suffixes=("_x", ""))
                    grp = grp.drop(columns=["vta_sem_costo_x"], errors="ignore")
                    grp["cobertura"] = grp.apply(
                        lambda r: round(r["stock_uds"] / r["vta_sem_uds"], 1) if r["vta_sem_uds"] > 0 else None, axis=1
                    )
                    grp = grp.sort_values("stock_costo", ascending=False)
                    grp_disp = grp.rename(columns={
                        "marca": "Marca", "stock_costo": "Stock (S/ costo)",
                        "vta_sem_costo": "Vta Sem (S/ costo)", "cobertura": "Cobertura (sem)",
                    })
                    st.dataframe(
                        grp_disp[["Marca", "Stock (S/ costo)", "Vta Sem (S/ costo)", "Cobertura (sem)"]].style.format({
                            "Stock (S/ costo)": "S/ {:,.0f}",
                            "Vta Sem (S/ costo)": "S/ {:,.0f}",
                            "Cobertura (sem)": "{:.1f}",
                        }, na_rep="—"),
                        use_container_width=True, hide_index=True,
                    )
                else:
                    st.caption("Sin columna Marca en los datos")

    with dash_t2:
        st.markdown(f"""<div style="background:#FFF7ED; border-left:4px solid {STATUS_SOBRESTOCK}; padding:10px 14px; border-radius:10px; margin-bottom:10px;">
        <strong style="color:{STATUS_SOBRESTOCK};">Top 5 Tiendas — Mayor Cobertura</strong>
        <span style="color:{SLATE_500}; font-size:0.82em;"> &nbsp;·&nbsp; Requieren acción</span>
        </div>""", unsafe_allow_html=True)

        for _, row_t in top5_mayor.iterrows():
            tienda_name = row_t["tienda"]
            cob_val = row_t["cobertura_tienda"]
            capital_val = row_t["stock_valor_costo"]

            st.markdown(f"""<div style="background:white; border:1px solid {SLATE_200}; border-radius:10px; padding:10px 14px; margin-bottom:4px;">
            <span style="font-weight:600; color:{SLATE_900};">{tienda_name}</span>
            &nbsp;·&nbsp; <span style="color:{STATUS_SOBRESTOCK}; font-weight:700;">{cob_val:.1f} sem</span>
            &nbsp;·&nbsp; <span style="color:{SLATE_500}; font-size:0.85em;">Capital: S/ {capital_val:,.0f}</span>
            </div>""", unsafe_allow_html=True)

            with st.expander(f"Ver detalle por marca — {tienda_name}", expanded=False):
                marca_det = df_cob_filt[df_cob_filt["tienda"] == tienda_name].copy()
                if "marca" in marca_det.columns:
                    grp = marca_det.groupby("marca").agg(
                        stock_costo=("stock_valor_costo", "sum"),
                        vta_sem_uds=("prom_vta_uds", "sum"),
                        stock_uds=("stock_total", "sum"),
                    ).reset_index()
                    marca_vta_costo = marca_det.groupby("marca").apply(
                        lambda g: (g["prom_vta_uds"] * g["costo"]).sum()
                    ).reset_index(name="vta_sem_costo")
                    grp = grp.merge(marca_vta_costo, on="marca", how="left")
                    grp["cobertura"] = grp.apply(
                        lambda r: round(r["stock_uds"] / r["vta_sem_uds"], 1) if r["vta_sem_uds"] > 0 else None, axis=1
                    )
                    grp = grp.sort_values("stock_costo", ascending=False)
                    grp_disp = grp.rename(columns={
                        "marca": "Marca", "stock_costo": "Stock (S/ costo)",
                        "vta_sem_costo": "Vta Sem (S/ costo)", "cobertura": "Cobertura (sem)",
                    })
                    st.dataframe(
                        grp_disp[["Marca", "Stock (S/ costo)", "Vta Sem (S/ costo)", "Cobertura (sem)"]].style.format({
                            "Stock (S/ costo)": "S/ {:,.0f}",
                            "Vta Sem (S/ costo)": "S/ {:,.0f}",
                            "Cobertura (sem)": "{:.1f}",
                        }, na_rep="—"),
                        use_container_width=True, hide_index=True,
                    )
                else:
                    st.caption("Sin columna Marca en los datos")




# ─── TAB 2: Gestión por Antigüedad ─────────────────────────────────
#  Ventana de Mercadería completa (4 capas) + Obsolescencia detallada
#  Sección dedicada con tabs

elif nav_page == "📊 Gestión por Antigüedad":
    st.markdown(f'<div class="section-header"><h3>📊 Gestión por Antigüedad</h3><span class="live-badge">MERCADERÍA</span></div>', unsafe_allow_html=True)
    st.caption("Análisis completo del envejecimiento del inventario — Ventana de Mercadería + Obsoletos detallados")

    _aging_tab1, _aging_tab2 = st.tabs(["🪟 Ventana de Mercadería", "⏳ Obsolescencia Detallada"])

    # ══════════════════════════════════════════════════════════
    #  TAB 1 — VENTANA DE MERCADERÍA (4 capas)
    # ══════════════════════════════════════════════════════════
    with _aging_tab1:
        if df_aging.empty:
            st.info("ℹ️ No hay datos de aging disponibles. Sube archivos con información de antigüedad.")
        else:
            import math as _math_mod

            # ────────────────────────────────────────────────────
            #  CAPA 1 — Resumen ejecutivo (KPIs + barra horizontal)
            # ────────────────────────────────────────────────────
            _ak = aging_kpis
            _cap_viejo = _ak.get('capital_viejo', 0)
            _pct_viejo = _ak.get('pct_viejo', 0)
            _edad_prom = _ak.get('edad_prom_pond', 0)
            if _edad_prom is None or (isinstance(_edad_prom, float) and _math_mod.isnan(_edad_prom)):
                _edad_prom = 0
            _n_riesgo = _ak.get('n_zona_riesgo', 0)
            _cap_total = _ak.get('capital_total', 1)

            _kc1, _kc2, _kc3 = st.columns(3)
            with _kc1:
                st.markdown(f"""<div style="background:#FEF2F2; border-radius:12px; padding:16px 20px; border-left:4px solid #ef4444;">
                    <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">Capital en mercadería vieja (>16 sem)</div>
                    <div style="font-size:1.8rem; font-weight:700; color:#ef4444;">S/ {_cap_viejo:,.0f}</div>
                    <div style="font-size:0.7rem; color:{SLATE_500};">{_pct_viejo:.0f}% del inventario total</div>
                </div>""", unsafe_allow_html=True)
            with _kc2:
                st.markdown(f"""<div style="background:#FFFBEB; border-radius:12px; padding:16px 20px; border-left:4px solid #f59e0b;">
                    <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">Edad promedio ponderada</div>
                    <div style="font-size:1.8rem; font-weight:700; color:#f59e0b;">{_edad_prom:.1f} sem</div>
                    <div style="font-size:0.7rem; color:{SLATE_500};">Ponderada por capital invertido</div>
                </div>""", unsafe_allow_html=True)
            with _kc3:
                st.markdown(f"""<div style="background:#F0FDF4; border-radius:12px; padding:16px 20px; border-left:4px solid {TEAL_600};">
                    <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">SKUs en zona de riesgo (8-16 sem)</div>
                    <div style="font-size:1.8rem; font-weight:700; color:{TEAL_700};">{_n_riesgo:,}</div>
                    <div style="font-size:0.7rem; color:{SLATE_500};">Aún rescatables con empuje a piso</div>
                </div>""", unsafe_allow_html=True)

            # Barra horizontal de distribución de capital por edad
            _dist_edad = _ak.get('dist_edad', {})
            if _dist_edad and _cap_total > 0:
                _edad_colors = {'0-4 sem': '#10b981', '4-8 sem': '#84cc16', '8-16 sem': '#f59e0b', '16-26 sem': '#f97316', '26+ sem': '#ef4444'}
                _bar_parts = ""
                _legend_parts = ""
                for _lbl in ['0-4 sem', '4-8 sem', '8-16 sem', '16-26 sem', '26+ sem']:
                    _val = _dist_edad.get(_lbl, 0)
                    _pct = _val / _cap_total * 100 if _cap_total > 0 else 0
                    _clr = _edad_colors.get(_lbl, '#94A3B8')
                    if _pct > 1:
                        _bar_parts += f'<div style="width:{_pct:.1f}%; background:{_clr}; height:100%; display:inline-block;" title="{_lbl}: S/{_val:,.0f} ({_pct:.0f}%)"></div>'
                    _legend_parts += f'<span style="display:inline-flex; align-items:center; gap:4px; margin-right:12px;"><span style="width:10px; height:10px; border-radius:2px; background:{_clr}; display:inline-block;"></span><span style="font-size:0.7rem; color:{SLATE_500};">{_lbl}</span></span>'
                st.markdown(f"""<div style="margin-top:16px; background:white; border-radius:8px; padding:12px 16px; border:1px solid {SLATE_200};">
                    <div style="font-size:0.8rem; font-weight:600; color:{SLATE_700}; margin-bottom:8px;">Distribución de capital por edad</div>
                    <div style="width:100%; height:20px; border-radius:6px; overflow:hidden; background:{SLATE_200}; display:flex;">{_bar_parts}</div>
                    <div style="margin-top:6px;">{_legend_parts}</div>
                </div>""", unsafe_allow_html=True)

            st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

            # ────────────────────────────────────────────────────
            #  CAPA 2 — Stacked bar por categoría (click → drill a marca)
            # ────────────────────────────────────────────────────
            st.markdown(f"<h4 style='color:{SLATE_800}; margin:0 0 4px 0;'>Capital por categoría y antigüedad</h4>", unsafe_allow_html=True)
            st.caption("Cada barra = una categoría. Colores = rango de edad. Mientras más rojo, más vieja la mercadería.")

            # Filtros
            _aging_fcols = st.columns(4)
            with _aging_fcols[0]:
                _temps_aging = ["Todas"] + sorted(df_aging['temporada'].dropna().unique().tolist()) if 'temporada' in df_aging.columns else ["Todas"]
                _f_temp_aging = st.selectbox("Temporada", _temps_aging, key="aging_inv_temp")
            with _aging_fcols[1]:
                _f_tipo_marca = st.selectbox("Tipo marca", ["Todas", "Marca Tercera", "Marca Propia"], key="aging_inv_tipo_marca")
            with _aging_fcols[2]:
                _marcas_aging = ["Todas"] + sorted(df_aging['marca'].dropna().unique().tolist()) if 'marca' in df_aging.columns else ["Todas"]
                _f_marca_aging = st.selectbox("Marca", _marcas_aging, key="aging_inv_marca")
            with _aging_fcols[3]:
                _f_rango_dscto = st.selectbox("Descuento", ["Todos", "Sin descuento", "1-20%", "20-40%", "40%+"], key="aging_inv_dscto")

            # Aplicar filtros
            _df_ag = df_aging.copy()
            if _f_temp_aging != "Todas" and 'temporada' in _df_ag.columns:
                _df_ag = _df_ag[_df_ag['temporada'] == _f_temp_aging]
            if _f_tipo_marca != "Todas" and 'tipo_marca' in _df_ag.columns:
                _df_ag = _df_ag[_df_ag['tipo_marca'] == _f_tipo_marca]
            if _f_marca_aging != "Todas" and 'marca' in _df_ag.columns:
                _df_ag = _df_ag[_df_ag['marca'] == _f_marca_aging]
            if _f_rango_dscto != "Todos" and 'rango_descuento' in _df_ag.columns:
                _df_ag = _df_ag[_df_ag['rango_descuento'] == _f_rango_dscto]

            st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

            if 'categoria' in _df_ag.columns and 'rango_edad_aging' in _df_ag.columns:
                _chart_data = _df_ag.groupby(
                    ['categoria', 'rango_edad_aging'], observed=True
                ).agg(capital=('stock_valor_costo', 'sum')).reset_index()
                _chart_data['rango_edad_aging'] = _chart_data['rango_edad_aging'].astype(str)

                _pivot = _chart_data.pivot_table(index='categoria', columns='rango_edad_aging', values='capital', fill_value=0)
                _orden_rangos = [r for r in ['0-4 sem', '4-8 sem', '8-16 sem', '16-26 sem', '26+ sem'] if r in _pivot.columns]
                _pivot = _pivot[_orden_rangos]
                _pivot['_total'] = _pivot.sum(axis=1)
                _pivot = _pivot.sort_values('_total', ascending=False).drop(columns='_total').head(8)

                _colores_rango = {'0-4 sem': '#10b981', '4-8 sem': '#84cc16', '8-16 sem': '#f59e0b', '16-26 sem': '#f97316', '26+ sem': '#ef4444'}

                # Plotly stacked bar con tooltips formateados
                _totales_cat = _pivot.sum(axis=1)
                _fig_aging = go.Figure()
                for _rango in _pivot.columns:
                    _vals = _pivot[_rango]
                    _pcts = (_vals / _totales_cat * 100).round(1)
                    _custom = np.column_stack([_pcts.values])
                    _fig_aging.add_trace(go.Bar(
                        name=_rango,
                        x=_pivot.index,
                        y=_vals,
                        marker_color=_colores_rango.get(_rango, '#94A3B8'),
                        customdata=_custom,
                        hovertemplate=(
                            '<b>%{x}</b><br>'
                            f'<b>{_rango}</b><br>'
                            'Capital: S/ %{y:,.0f}<br>'
                            'Peso: %{customdata[0]:.1f}%'
                            '<extra></extra>'
                        ),
                    ))
                _fig_aging.update_layout(
                    barmode='stack',
                    height=380,
                    margin=dict(l=0, r=0, t=10, b=0),
                    legend=dict(orientation='h', yanchor='bottom', y=-0.25, xanchor='center', x=0.5,
                                font=dict(size=12)),
                    xaxis=dict(tickangle=-45, tickfont=dict(size=11)),
                    yaxis=dict(tickformat=',', tickfont=dict(size=11)),
                    plot_bgcolor='rgba(0,0,0,0)',
                    paper_bgcolor='rgba(0,0,0,0)',
                    hoverlabel=dict(bgcolor='white', font_size=13, font_color='#1e293b', bordercolor='#e2e8f0'),
                )
                st.plotly_chart(_fig_aging, use_container_width=True, config={'displayModeBar': False})

                # Drill-down: seleccionar categoría para ver marcas
                _cats_list = _pivot.index.tolist()
                _sel_cat = st.selectbox("Drill-down: seleccionar categoría", ["—"] + _cats_list, key="aging_inv_cat_drill")
                if _sel_cat != "—":
                    _df_cat = _df_ag[_df_ag['categoria'] == _sel_cat]
                    _drill_marca = _df_cat.groupby('marca').agg(
                        capital=('stock_valor_costo', 'sum'),
                        n_skus=('sku', 'nunique'),
                        edad_prom=('edad_semanas', 'mean'),
                    ).reset_index().sort_values('capital', ascending=False).head(8)

                    st.markdown(f"<div style='background:white; border:1px solid {SLATE_200}; border-radius:8px; padding:16px; margin-top:8px;'>", unsafe_allow_html=True)
                    st.markdown(f"<div style='font-weight:600; color:{SLATE_800}; margin-bottom:8px;'>Drill-down: {_sel_cat}</div>", unsafe_allow_html=True)
                    st.caption("Top marcas por capital viejo (>16 sem)")

                    _max_cap_drill = _drill_marca['capital'].max() if not _drill_marca.empty else 1
                    for _, _dr in _drill_marca.iterrows():
                        _bar_w_d = max(5, int(_dr['capital'] / _max_cap_drill * 100))
                        _dr_color = '#ef4444' if _dr['edad_prom'] > 26 else ('#f97316' if _dr['edad_prom'] > 16 else ('#f59e0b' if _dr['edad_prom'] > 8 else '#10b981'))
                        st.markdown(f"""<div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
                            <span style="width:120px; font-size:0.8rem; font-weight:500; color:{SLATE_700}; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{_dr['marca']}</span>
                            <div style="flex:1; background:{SLATE_200}; border-radius:4px; height:16px;">
                                <div style="background:{_dr_color}; border-radius:4px; height:16px; width:{_bar_w_d}%;"></div>
                            </div>
                            <span style="font-size:0.75rem; color:{SLATE_500}; white-space:nowrap;">S/{_dr['capital']:,.0f}</span>
                        </div>""", unsafe_allow_html=True)

                    # Nivel 3: click marca → ver SKUs
                    _marcas_drill = ["—"] + _drill_marca['marca'].tolist()
                    _sel_marca_drill = st.selectbox("Click marca → ver SKUs", _marcas_drill, key="aging_inv_marca_drill")
                    if _sel_marca_drill != "—":
                        _df_sku_drill = _df_cat[_df_cat['marca'] == _sel_marca_drill].sort_values('stock_valor_costo', ascending=False).head(10)
                        _sku_rows = ""
                        for _, _sr in _df_sku_drill.iterrows():
                            _sku_rows += f"""<tr>
                                <td style="padding:6px 8px; font-size:0.75rem;">{str(_sr.get('nombre',''))[:35]}</td>
                                <td style="padding:6px 8px; font-size:0.75rem;">{_sr.get('tienda','')}</td>
                                <td style="padding:6px 8px; font-size:0.75rem; text-align:right;">{int(_sr.get('edad_semanas',0))} sem</td>
                                <td style="padding:6px 8px; font-size:0.75rem; text-align:right;">S/{_sr.get('stock_valor_costo',0):,.0f}</td>
                                <td style="padding:6px 8px; font-size:0.75rem; text-align:center;">{_sr.get('accion_aging','')}</td>
                            </tr>"""
                        st.markdown(f"""<table style="width:100%; border-collapse:collapse; margin-top:8px;">
                            <thead><tr style="background:{SLATE_100}; border-bottom:1px solid {SLATE_200};">
                                <th style="padding:6px 8px; text-align:left; font-size:0.7rem;">Modelo</th>
                                <th style="padding:6px 8px; text-align:left; font-size:0.7rem;">Tienda</th>
                                <th style="padding:6px 8px; text-align:right; font-size:0.7rem;">Edad</th>
                                <th style="padding:6px 8px; text-align:right; font-size:0.7rem;">Capital</th>
                                <th style="padding:6px 8px; text-align:center; font-size:0.7rem;">Acción</th>
                            </tr></thead>
                            <tbody>{_sku_rows}</tbody>
                        </table>""", unsafe_allow_html=True)
                    st.markdown("</div>", unsafe_allow_html=True)

            st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

            # ────────────────────────────────────────────────────
            #  CAPA 3 — Alertas inteligentes por tipo de acción
            # ────────────────────────────────────────────────────
            st.markdown(f"<h4 style='color:{SLATE_800}; margin:0 0 4px 0;'>Alertas inteligentes por tipo de acción</h4>", unsafe_allow_html=True)
            st.caption("El motor clasifica cada SKU según edad + sell-through + descuento → sugiere la acción correcta.")

            _acc_configs = [
                {'key': 'EMPUJE', 'title': 'EMPUJE A PISO', 'icon': '🟢', 'color': '#84cc16', 'bg': '#F7FEE7', 'border': '#84cc16',
                 'criteria': 'Edad 8-16 sem · ST >5% · Sin dscto',
                 'n_label': f"{_ak.get('n_skus_empuje', 0)} SKUs · S/{_ak.get('capital_empuje', 0):,.0f}",
                 'accion': 'Acción: instruir tiendas'},
                {'key': 'MARKDOWN', 'title': 'MARKDOWN PROGRESIVO', 'icon': '🟡', 'color': '#f59e0b', 'bg': '#FFFBEB', 'border': '#f59e0b',
                 'criteria': 'Edad 16-26 sem · ST 2-5% · Dscto <40%',
                 'n_label': f"{_ak.get('n_skus_markdown', 0)} SKUs · S/{_ak.get('capital_markdown', 0):,.0f}",
                 'accion': 'Acción: ajustar precio'},
                {'key': 'NEGOCIAR', 'title': 'NEGOCIAR PROVEEDOR', 'icon': '🟠', 'color': '#f97316', 'bg': '#FFF7ED', 'border': '#f97316',
                 'criteria': 'Marca tercera · >16 sem · Capital >S/50K',
                 'n_label': f"{_ak.get('n_marcas_negociar', 0)} marcas · S/{_ak.get('capital_negociar', 0):,.0f}",
                 'accion': 'Acción: reunión proveedor'},
                {'key': 'LIQUIDAR', 'title': 'LIQUIDAR', 'icon': '🔴', 'color': '#ef4444', 'bg': '#FEF2F2', 'border': '#ef4444',
                 'criteria': 'Edad >26 sem · ST <2% · Ya con dscto',
                 'n_label': f"{_ak.get('n_skus_liquidar', 0)} SKUs · S/{_ak.get('capital_liquidar', 0):,.0f}",
                 'accion': 'Acción: sacar del sistema'},
            ]

            _acc_c1, _acc_c2, _acc_c3, _acc_c4 = st.columns(4)
            for _col_ui, _cfg in zip([_acc_c1, _acc_c2, _acc_c3, _acc_c4], _acc_configs):
                with _col_ui:
                    _ejemplos_acc = aging_top_ejemplos.get(_cfg['key'], [])
                    _ej_html = ""
                    for _ej in _ejemplos_acc[:2]:
                        _ej_html += f"""<div style="margin-bottom:8px; padding:6px 8px; background:white; border-radius:6px; border:1px solid {SLATE_200};">
                            <div style="font-size:0.72rem; font-weight:600; color:{SLATE_800};">• {_ej['nombre'][:45]}</div>
                            <div style="font-size:0.68rem; color:{SLATE_500}; margin-top:2px;">{_ej['detalle']}</div>
                            <div style="font-size:0.68rem; color:{_cfg['color']}; margin-top:2px;">→ {_ej['sugerencia'][:60]}</div>
                        </div>"""
                    if not _ej_html:
                        _ej_html = f'<div style="font-size:0.72rem; color:{SLATE_400}; padding:8px;">Sin casos detectados</div>'

                    st.markdown(f"""<div style="background:{_cfg['bg']}; border:2px solid {_cfg['border']}; border-radius:12px; padding:14px; height:100%;">
                        <div style="font-weight:700; font-size:0.85rem; color:{_cfg['color']}; margin-bottom:2px;">{_cfg['icon']} {_cfg['title']}</div>
                        <div style="font-size:0.68rem; color:{SLATE_500}; margin-bottom:10px;">{_cfg['criteria']}</div>
                        {_ej_html}
                        <div style="background:{_cfg['color']}; color:white; padding:6px 10px; border-radius:6px; font-size:0.72rem; font-weight:600; text-align:center; margin-top:8px;">{_cfg['n_label']}</div>
                        <div style="font-size:0.68rem; color:{SLATE_500}; text-align:center; margin-top:4px;">{_cfg['accion']}</div>
                    </div>""", unsafe_allow_html=True)

            st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

            # ────────────────────────────────────────────────────
            #  CAPA 4 — Reglas del motor de clasificación
            # ────────────────────────────────────────────────────
            st.markdown(f"<h4 style='color:{SLATE_800}; margin:0 0 4px 0;'>Reglas del motor de clasificación</h4>", unsafe_allow_html=True)
            st.caption("Cada SKU se clasifica automáticamente según esta matriz de decisión:")

            _reglas_html = f"""<div style="overflow-x:auto;">
            <table style="width:100%; border-collapse:collapse; font-size:0.8rem;">
                <thead>
                    <tr style="background:{SLATE_100}; border-bottom:2px solid {SLATE_200};">
                        <th style="padding:8px 12px; text-align:left; color:{SLATE_700};">Criterio</th>
                        <th style="padding:8px 12px; text-align:center; color:#84cc16;">🟢 Empuje</th>
                        <th style="padding:8px 12px; text-align:center; color:#f59e0b;">🟡 Markdown</th>
                        <th style="padding:8px 12px; text-align:center; color:#f97316;">🟠 Negociar</th>
                        <th style="padding:8px 12px; text-align:center; color:#ef4444;">🔴 Liquidar</th>
                    </tr>
                </thead>
                <tbody>
                    <tr style="border-bottom:1px solid {SLATE_200};">
                        <td style="padding:8px 12px; font-weight:500;">Edad (sem)</td>
                        <td style="padding:8px 12px; text-align:center;">8 – 16</td>
                        <td style="padding:8px 12px; text-align:center;">16 – 26</td>
                        <td style="padding:8px 12px; text-align:center;">16+ (terceras)</td>
                        <td style="padding:8px 12px; text-align:center;">>26</td>
                    </tr>
                    <tr style="border-bottom:1px solid {SLATE_200};">
                        <td style="padding:8px 12px; font-weight:500;">Sell-through</td>
                        <td style="padding:8px 12px; text-align:center;">>5%</td>
                        <td style="padding:8px 12px; text-align:center;">2% – 5%</td>
                        <td style="padding:8px 12px; text-align:center;">cualquiera</td>
                        <td style="padding:8px 12px; text-align:center;"><2%</td>
                    </tr>
                    <tr style="border-bottom:1px solid {SLATE_200};">
                        <td style="padding:8px 12px; font-weight:500;">Descuento actual</td>
                        <td style="padding:8px 12px; text-align:center;"><10% o ninguno</td>
                        <td style="padding:8px 12px; text-align:center;"><40%</td>
                        <td style="padding:8px 12px; text-align:center;">cualquiera</td>
                        <td style="padding:8px 12px; text-align:center;">ya con 30%+</td>
                    </tr>
                    <tr style="border-bottom:1px solid {SLATE_200};">
                        <td style="padding:8px 12px; font-weight:500;">Tipo marca</td>
                        <td style="padding:8px 12px; text-align:center;">cualquiera</td>
                        <td style="padding:8px 12px; text-align:center;">cualquiera</td>
                        <td style="padding:8px 12px; text-align:center;">solo terceras</td>
                        <td style="padding:8px 12px; text-align:center;">cualquiera</td>
                    </tr>
                    <tr style="border-bottom:1px solid {SLATE_200};">
                        <td style="padding:8px 12px; font-weight:500;">Capital mín.</td>
                        <td style="padding:8px 12px; text-align:center;">S/10K por SKU</td>
                        <td style="padding:8px 12px; text-align:center;">S/5K por SKU</td>
                        <td style="padding:8px 12px; text-align:center;">S/50K por marca</td>
                        <td style="padding:8px 12px; text-align:center;">cualquiera</td>
                    </tr>
                    <tr>
                        <td style="padding:8px 12px; font-weight:700;">Output</td>
                        <td style="padding:8px 12px; text-align:center; font-weight:600;">WhatsApp a tienda</td>
                        <td style="padding:8px 12px; text-align:center; font-weight:600;">Sugerencia de precio</td>
                        <td style="padding:8px 12px; text-align:center; font-weight:600;">Brief para reunión</td>
                        <td style="padding:8px 12px; text-align:center; font-weight:600;">Lista de corte</td>
                    </tr>
                </tbody>
            </table></div>"""
            st.markdown(_reglas_html, unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════
    #  TAB 2 — OBSOLESCENCIA DETALLADA (por modelo + drill tienda)
    # ══════════════════════════════════════════════════════════
    with _aging_tab2:
        st.markdown(f"<h4 style='color:{SLATE_800}; margin:0 0 4px 0;'>Mercadería Obsoleta y Preobsoleta</h4>", unsafe_allow_html=True)
        st.caption("Productos con más de 6 meses de antigüedad. Analiza por modelo para tomar acciones de liquidación.")

        st.markdown("---")

        _RANGOS_OBS_DASH = {"RANGO 6_9", "RANGO 9_12", "RANGO 12_99"}

        if "rango_antiguedad" in df_cob.columns:
            df_obs = df_cob[df_cob["rango_antiguedad"].isin(_RANGOS_OBS_DASH)].copy()

            if not df_obs.empty:
                st.markdown(f"""<div style="background:#FDF4FF; border-left:4px solid {STATUS_LIQUIDAR}; padding:10px 14px; border-radius:10px; margin-bottom:10px;">
                <strong style="color:{STATUS_LIQUIDAR};">Inventario Obsoleto (>6 meses)</strong>
                <span style="color:{SLATE_500}; font-size:0.82em;"> &nbsp;·&nbsp; {len(df_obs):,} combos · S/ {df_obs['stock_valor_costo'].sum():,.0f} en capital</span>
                </div>""", unsafe_allow_html=True)

                _obs_marcas = ["Todas"] + sorted(df_obs["marca"].unique().tolist()) if "marca" in df_obs.columns else ["Todas"]
                _obs_marca_sel = st.selectbox("Filtrar por Marca", _obs_marcas, index=0, key="aging_obs_marca_filter")
                if _obs_marca_sel != "Todas":
                    df_obs = df_obs[df_obs["marca"] == _obs_marca_sel]

                obs_c1, obs_c2 = st.columns(2)

                # ── DONUT: % de inventario obsoleto por rango ──
                with obs_c1:
                    obs_rango = df_obs.groupby("rango_antiguedad").agg(
                        capital=("stock_valor_costo", "sum"),
                        n_combos=("sku", "count"),
                    ).reset_index()
                    obs_rango_order = {"RANGO 6_9": 0, "RANGO 9_12": 1, "RANGO 12_99": 2}
                    obs_rango = obs_rango.sort_values("rango_antiguedad", key=lambda x: x.map(obs_rango_order))

                    _obs_colors = {"RANGO 6_9": "#F59E0B", "RANGO 9_12": "#F97316", "RANGO 12_99": "#EF4444"}
                    _obs_labels = {"RANGO 6_9": "6-9 meses", "RANGO 9_12": "9-12 meses", "RANGO 12_99": ">12 meses"}
                    obs_rango["label"] = obs_rango["rango_antiguedad"].map(_obs_labels)

                    fig_obs = go.Figure(data=[go.Pie(
                        labels=obs_rango["label"],
                        values=obs_rango["capital"],
                        hole=0.55,
                        marker=dict(colors=[_obs_colors.get(r, "#CBD5E1") for r in obs_rango["rango_antiguedad"]]),
                        textinfo="label+percent",
                        textfont=dict(size=11),
                        hovertemplate="<b>%{label}</b><br>S/ %{value:,.0f}<br>%{percent}<extra></extra>",
                    )])
                    fig_obs.update_layout(
                        **_plotly_layout,
                        title=dict(text="Capital Obsoleto por Antigüedad", font=dict(size=14, color=SLATE_900)),
                        showlegend=False,
                        height=340,
                    )
                    _obs_total = obs_rango["capital"].sum()
                    _obs_pct_total = (_obs_total / df_cob["stock_valor_costo"].sum() * 100) if df_cob["stock_valor_costo"].sum() > 0 else 0
                    fig_obs.add_annotation(
                        text=f"<b>{_obs_pct_total:.1f}%</b><br><span style='font-size:10px;color:{SLATE_500}'>del inventario</span>",
                        showarrow=False, font=dict(size=18, color=SLATE_900),
                    )
                    st.plotly_chart(fig_obs, use_container_width=True)

                # ── Tabla: stock, venta, cobertura, dscto por marca ──
                with obs_c2:
                    st.markdown(f"**Métricas por Marca** — mercadería >6 meses")
                    _obs_marca = df_obs.groupby("marca" if "marca" in df_obs.columns else "categoria").agg(
                        stock_uds=("stock_total", "sum"),
                        capital=("stock_valor_costo", "sum"),
                        vta_sem=("prom_vta_uds", "sum"),
                        n_skus=("sku", "nunique"),
                    ).reset_index()
                    _grp_label = "marca" if "marca" in df_obs.columns else "categoria"

                    # Calcular venta a costo
                    if "marca" in df_obs.columns and "costo" in df_obs.columns:
                        _obs_vta_costo = df_obs.groupby("marca").apply(
                            lambda g: (g["prom_vta_uds"] * g["costo"]).sum()
                        ).reset_index(name="vta_sem_costo")
                        _obs_marca = _obs_marca.merge(_obs_vta_costo, on="marca", how="left")
                    else:
                        _obs_marca["vta_sem_costo"] = 0

                    _obs_marca["cobertura"] = _obs_marca.apply(
                        lambda r: round(r["stock_uds"] / r["vta_sem"], 1) if r["vta_sem"] > 0 else None, axis=1
                    )

                    # Descuento promedio por marca
                    if "pct_descuento" in df_obs.columns:
                        _obs_dscto = df_obs.groupby(_grp_label)["pct_descuento"].mean().reset_index(name="dscto_prom")
                        _obs_marca = _obs_marca.merge(_obs_dscto, on=_grp_label, how="left")
                    else:
                        _obs_marca["dscto_prom"] = None

                    _obs_marca = _obs_marca.sort_values("capital", ascending=False)
                    _obs_disp = _obs_marca.rename(columns={
                        _grp_label: "Marca", "capital": "Capital S/", "stock_uds": "Stock Uds",
                        "vta_sem_costo": "Vta Sem S/", "cobertura": "Cob (sem)", "dscto_prom": "Dscto Prom",
                        "n_skus": "SKUs",
                    })
                    st.dataframe(
                        _obs_disp[["Marca", "Capital S/", "Stock Uds", "Vta Sem S/", "Cob (sem)", "Dscto Prom", "SKUs"]].style.format({
                            "Capital S/": "S/ {:,.0f}", "Stock Uds": "{:,.0f}",
                            "Vta Sem S/": "S/ {:,.0f}", "Cob (sem)": "{:.1f}", "Dscto Prom": "{:.0%}",
                        }, na_rep="—"),
                        use_container_width=True, hide_index=True, height=300,
                    )

                    # Expander: detalle por marca
                    with st.expander("Ver detalle por marca", expanded=False):
                        _sel_marca_obs = st.selectbox(
                            "Marca", sorted(df_obs[_grp_label].unique().tolist()), key="aging_obs_marca_sel"
                        )
                        _obs_det = df_obs[df_obs[_grp_label] == _sel_marca_obs].copy()
                        _obs_det_grp = _obs_det.groupby("rango_antiguedad").agg(
                            stock_uds=("stock_total", "sum"),
                            capital=("stock_valor_costo", "sum"),
                            n_skus=("sku", "nunique"),
                        ).reset_index()
                        if "pct_descuento" in _obs_det.columns:
                            _obs_det_dscto = _obs_det.groupby("rango_antiguedad")["pct_descuento"].mean().reset_index(name="dscto_prom")
                            _obs_det_grp = _obs_det_grp.merge(_obs_det_dscto, on="rango_antiguedad", how="left")
                        else:
                            _obs_det_grp["dscto_prom"] = None
                        _obs_det_grp["label"] = _obs_det_grp["rango_antiguedad"].map(_obs_labels).fillna(_obs_det_grp["rango_antiguedad"])
                        _obs_det_grp = _obs_det_grp.rename(columns={
                            "label": "Rango", "capital": "Capital S/", "stock_uds": "Stock Uds",
                            "dscto_prom": "Dscto Prom", "n_skus": "SKUs",
                        })
                        st.dataframe(
                            _obs_det_grp[["Rango", "Capital S/", "Stock Uds", "Dscto Prom", "SKUs"]].style.format({
                                "Capital S/": "S/ {:,.0f}", "Stock Uds": "{:,.0f}", "Dscto Prom": "{:.0%}",
                            }, na_rep="—"),
                            use_container_width=True, hide_index=True,
                        )

                    # ── Botón de descarga: detalle completo de obsoletos ──
                    _obs_dl_cols = ["sku", "nombre", "marca", "categoria", "tienda",
                                    "rango_antiguedad", "stock_total", "stock_valor_costo",
                                    "prom_vta_uds", "cobertura_sem", "edad_semanas"]
                    if "pct_descuento" in df_obs.columns:
                        _obs_dl_cols.append("pct_descuento")
                    _obs_dl_cols = [c for c in _obs_dl_cols if c in df_obs.columns]
                    _obs_dl = df_obs[_obs_dl_cols].copy()
                    _obs_dl = _obs_dl.sort_values(["marca", "stock_valor_costo"], ascending=[True, False])
                    _obs_buf = io.BytesIO()
                    with pd.ExcelWriter(_obs_buf, engine="openpyxl") as _w_obs:
                        _add_pricing_cols(_obs_dl, df_cob, 'Obsoletos', _w_obs)
                    _obs_buf.seek(0)
                    st.download_button(
                        "📥 Descargar detalle de obsoletos (.xlsx)",
                        data=_obs_buf.getvalue(),
                        file_name="detalle_obsoletos.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                    )


        st.markdown("---")
        st.markdown("##### Detalle por Modelo")


        _OBS_RANGOS = {"RANGO 6_9", "RANGO 9_12", "RANGO 12_99"}
        _OBS_LABELS = {"RANGO 6_9": "Pre-obsoleto (6-9m)", "RANGO 9_12": "Obsoleto (9-12m)", "RANGO 12_99": "Crítico (>12m)"}

        if "rango_antiguedad" not in df_cob.columns:
            st.warning("No hay columna de rango de antigüedad en los datos. Sube un archivo con esta información.")
        else:
            df_obs_tab = df_cob[df_cob["rango_antiguedad"].isin(_OBS_RANGOS)].copy()

            if df_obs_tab.empty:
                st.success("✅ No hay mercadería obsoleta en el inventario actual.")
            else:
                # KPIs rápidos
                _obs_k1, _obs_k2, _obs_k3, _obs_k4 = st.columns(4)
                _obs_k1.metric("SKUs Obsoletos", f"{df_obs_tab['sku'].nunique():,}")
                _obs_k2.metric("Capital Parado", f"S/ {df_obs_tab['stock_valor_costo'].sum():,.0f}")
                _obs_k3.metric("Stock (uds)", f"{int(df_obs_tab['stock_total'].sum()):,}")
                _obs_pct_inv = (df_obs_tab['stock_valor_costo'].sum() / df_cob['stock_valor_costo'].sum() * 100) if df_cob['stock_valor_costo'].sum() > 0 else 0
                _obs_k4.metric("% del Inventario", f"{_obs_pct_inv:.1f}%")

                st.markdown("---")

                # Filtros
                _obs_f1, _obs_f2, _obs_f3 = st.columns(3)
                with _obs_f1:
                    _obs_rango_opts = sorted(df_obs_tab["rango_antiguedad"].unique().tolist(),
                                              key=lambda x: {"RANGO 6_9": 0, "RANGO 9_12": 1, "RANGO 12_99": 2}.get(x, 99))
                    _f_obs_rango = st.multiselect("Rango", _obs_rango_opts, default=_obs_rango_opts, key="obs_tab_rango",
                                                   format_func=lambda x: _OBS_LABELS.get(x, x))
                with _obs_f2:
                    if "marca" in df_obs_tab.columns:
                        _obs_marcas = ["Todas"] + sorted(df_obs_tab["marca"].dropna().unique().tolist())
                        _f_obs_marca = st.selectbox("Marca", _obs_marcas, key="obs_tab_marca")
                    else:
                        _f_obs_marca = "Todas"
                with _obs_f3:
                    _obs_sort = st.selectbox("Ordenar por", ["Capital S/", "Stock Uds", "Cobertura (sem)", "Dscto Prom"], key="obs_tab_sort")

                # Aplicar filtros
                df_obs_f = df_obs_tab.copy()
                if _f_obs_rango:
                    df_obs_f = df_obs_f[df_obs_f["rango_antiguedad"].isin(_f_obs_rango)]
                if _f_obs_marca != "Todas" and "marca" in df_obs_f.columns:
                    df_obs_f = df_obs_f[df_obs_f["marca"] == _f_obs_marca]

                if df_obs_f.empty:
                    st.info("Sin resultados con los filtros seleccionados.")
                else:
                    # Agrupar por modelo (sku)
                    _grp_col = "marca" if "marca" in df_obs_f.columns else "categoria"

                    _obs_modelo = df_obs_f.groupby(["sku", "nombre"]).agg(
                        marca=(_grp_col, "first"),
                        rango=("rango_antiguedad", "first"),
                        stock_uds=("stock_total", "sum"),
                        capital=("stock_valor_costo", "sum"),
                        vta_sem=("prom_vta_uds", "sum"),
                        n_tiendas=("tienda", "nunique"),
                    ).reset_index()

                    # Venta a costo
                    if "costo" in df_obs_f.columns:
                        _obs_vta_costo = df_obs_f.groupby("sku").apply(
                            lambda g: (g["prom_vta_uds"] * g["costo"]).sum()
                        ).reset_index(name="vta_costo")
                        _obs_modelo = _obs_modelo.merge(_obs_vta_costo, on="sku", how="left")
                    else:
                        _obs_modelo["vta_costo"] = 0

                    _obs_modelo["cobertura"] = _obs_modelo.apply(
                        lambda r: round(r["stock_uds"] / r["vta_sem"], 1) if r["vta_sem"] > 0 else None, axis=1
                    )

                    # Descuento promedio
                    if "pct_descuento" in df_obs_f.columns:
                        _obs_dscto = df_obs_f.groupby("sku")["pct_descuento"].mean().reset_index(name="dscto_prom")
                        _obs_modelo = _obs_modelo.merge(_obs_dscto, on="sku", how="left")
                    else:
                        _obs_modelo["dscto_prom"] = None

                    _obs_modelo["rango_label"] = _obs_modelo["rango"].map(_OBS_LABELS).fillna(_obs_modelo["rango"])

                    # Ordenar
                    _sort_map = {"Capital S/": "capital", "Stock Uds": "stock_uds",
                                 "Cobertura (sem)": "cobertura", "Dscto Prom": "dscto_prom"}
                    _sort_col = _sort_map.get(_obs_sort, "capital")
                    _obs_modelo = _obs_modelo.sort_values(_sort_col, ascending=False, na_position="last")

                    # Tabla resumen
                    _obs_disp = _obs_modelo.rename(columns={
                        "sku": "SKU", "nombre": "Nombre", "marca": "Marca",
                        "rango_label": "Antigüedad", "stock_uds": "Stock Uds",
                        "capital": "Capital S/", "vta_costo": "Vta Sem S/",
                        "cobertura": "Cob (sem)", "dscto_prom": "Dscto Prom",
                        "n_tiendas": "Tiendas",
                    })

                    st.dataframe(
                        _obs_disp[["SKU", "Nombre", "Marca", "Antigüedad", "Stock Uds", "Capital S/",
                                   "Vta Sem S/", "Cob (sem)", "Dscto Prom", "Tiendas"]].style.format({
                            "Stock Uds": "{:,.0f}", "Capital S/": "S/ {:,.0f}",
                            "Vta Sem S/": "S/ {:,.0f}", "Cob (sem)": "{:.1f}",
                            "Dscto Prom": "{:.0%}",
                        }, na_rep="—"),
                        use_container_width=True, hide_index=True, height=400,
                    )

                    st.caption(f"{len(_obs_modelo)} modelos obsoletos mostrados")

                    # Expander por modelo → detalle tienda
                    st.markdown("---")
                    st.caption("Selecciona un modelo para ver el desglose por tienda:")

                    _obs_sku_sel = st.selectbox(
                        "Modelo",
                        _obs_modelo["sku"].tolist(),
                        format_func=lambda x: f"{x} — {_obs_modelo[_obs_modelo['sku']==x]['nombre'].iloc[0][:40]}",
                        key="obs_sku_sel",
                    )

                    if _obs_sku_sel:
                        _obs_det = df_obs_f[df_obs_f["sku"] == _obs_sku_sel].copy()
                        _obs_det_cols = ["tienda", "stock_total", "stock_valor_costo", "prom_vta_uds",
                                         "cobertura_sem", "estado"]
                        if "pct_descuento" in _obs_det.columns:
                            _obs_det_cols.append("pct_descuento")
                        _obs_det_disp = _obs_det[_obs_det_cols].rename(columns={
                            "tienda": "Tienda", "stock_total": "Stock", "stock_valor_costo": "Capital S/",
                            "prom_vta_uds": "Vta Sem (uds)", "cobertura_sem": "Cob (sem)",
                            "estado": "Estado", "pct_descuento": "Dscto",
                        }).sort_values("Capital S/", ascending=False)

                        _det_fmt = {"Capital S/": "S/ {:,.0f}", "Vta Sem (uds)": "{:.1f}", "Cob (sem)": "{:.1f}"}
                        if "Dscto" in _obs_det_disp.columns:
                            _det_fmt["Dscto"] = "{:.0%}"

                        st.dataframe(
                            _obs_det_disp.style.format(_det_fmt, na_rep="—"),
                            use_container_width=True, hide_index=True,
                        )


# ─── TAB 3: Transferencias ────────────────────────────────────

elif nav_page == "🔄 Transferencias":
    if df_trans.empty:
        st.info("ℹ️ No hay transferencias sugeridas. Esto ocurre cuando no hay simultáneamente tiendas en SOBRESTOCK y CRÍTICO del mismo SKU.")
    else:
        st.markdown(f"#### Transferencias sugeridas — {len(df_trans)} movimientos · **{s['uds_transferir']} unidades**")

        col_f1, col_f2 = st.columns(2)
        with col_f1:
            skus_t = ["Todos"] + sorted(df_trans["sku"].unique().tolist())
            f_sku_t = st.selectbox("SKU", skus_t, key="trans_sku")
        with col_f2:
            origen_t = ["Todas"] + sorted(df_trans["tienda_origen"].unique().tolist())
            f_origen  = st.selectbox("Tienda Origen", origen_t, key="trans_orig")

        df_t = df_trans.copy()
        if f_sku_t  != "Todos":  df_t = df_t[df_t["sku"] == f_sku_t]
        if f_origen != "Todas":  df_t = df_t[df_t["tienda_origen"] == f_origen]

        df_t_disp = df_t[[
            "sku", "nombre", "tienda_origen", "tienda_destino",
            "uds_transferir", "cob_origen_pre", "cob_destino_pre",
            "cob_origen_post", "cob_destino_post", "motivo"
        ]].rename(columns={
            "sku": "SKU", "nombre": "Nombre",
            "tienda_origen": "Tienda Origen", "tienda_destino": "Tienda Destino",
            "uds_transferir": "Uds a Transferir",
            "cob_origen_pre":   "Cob. Origen (pre)",
            "cob_destino_pre":  "Cob. Destino (pre)",
            "cob_origen_post":  "Cob. Origen (post)",
            "cob_destino_post": "Cob. Destino (post)",
            "motivo": "Motivo",
        })

        st.dataframe(
            df_t_disp.style.format({
                "Cob. Origen (pre)": "{:.1f}", "Cob. Destino (pre)": "{:.1f}",
                "Cob. Origen (post)": "{:.1f}", "Cob. Destino (post)": "{:.1f}",
            }, na_rep="—"),
            use_container_width=True, height=420
        )


# ─── TAB 5: Acciones de Precio ────────────────────────────────

elif nav_page == "💰 Acciones Precio":
    if df_prec.empty:
        st.success("✅ No hay productos en sobrestock que requieran acción de precio.")
    else:
        st.markdown(f"#### Acciones de precio sugeridas — {len(df_prec)} productos")

        col_f1, col_f2 = st.columns(2)
        with col_f1:
            cats_p  = ["Todas"] + sorted(df_prec["categoria"].dropna().unique().tolist())
            f_cat_p = st.selectbox("Categoría", cats_p, key="prec_cat")
        with col_f2:
            est_p  = ["Todos"] + sorted(df_prec["estado"].unique().tolist())
            f_est_p = st.selectbox("Estado", est_p, key="prec_est")

        df_p = df_prec.copy()
        if f_cat_p != "Todas": df_p = df_p[df_p["categoria"] == f_cat_p]
        if f_est_p != "Todos": df_p = df_p[df_p["estado"] == f_est_p]

        df_p_disp = df_p[[
            "sku", "nombre", "categoria", "tienda", "estado",
            "stock_total", "cobertura_actual", "edad_semanas",
            "precio_vigente", "precio_sugerido", "dscto_sugerido",
            "margen_post", "motivo"
        ]].rename(columns={
            "sku": "SKU", "nombre": "Nombre", "categoria": "Categoría",
            "tienda": "Tienda", "estado": "Estado",
            "stock_total": "Stock",
            "cobertura_actual": "Cobertura (sem)", "edad_semanas": "Edad (sem)",
            "precio_vigente": "Precio Actual (S/)", "precio_sugerido": "Precio Sug. (S/)",
            "dscto_sugerido": "Dscto Sug.", "margen_post": "Margen Post",
            "motivo": "Motivo",
        })

        def _style_prec(row):
            styles = [""] * len(row)
            idx = row.index.tolist()
            if "Estado" in idx:
                estado_colors = {
                    "CRÍTICO": STATUS_CRITICO, "PRE-CRÍTICO": STATUS_PRECRITICO,
                    "ÓPTIMO": STATUS_OPTIMO, "ALTO": STATUS_ALTO,
                    "SOBRESTOCK": STATUS_SOBRESTOCK, "LIQUIDAR": STATUS_LIQUIDAR,
                    "NUEVO SIN VENTA": STATUS_NUEVO_SV, "DORMIDO": STATUS_DORMIDO,
                    "MUERTO": STATUS_MUERTO,
                }
                bg = estado_colors.get(row["Estado"], "")
                if bg:
                    styles[idx.index("Estado")] = f"background-color:{bg}; color:#FFFFFF; border-radius:4px"
            if "Dscto Sug." in idx:
                try:
                    v = float(row["Dscto Sug."])
                    if v >= 0.40:
                        styles[idx.index("Dscto Sug.")] = f"background-color:{STATUS_CRITICO};color:#FFFFFF"
                    elif v >= 0.25:
                        styles[idx.index("Dscto Sug.")] = f"background-color:{STATUS_SOBRESTOCK};color:#FFFFFF"
                    elif v > 0:
                        styles[idx.index("Dscto Sug.")] = f"background-color:{STATUS_ALTO};color:#FFFFFF"
                except (TypeError, ValueError):
                    pass
            return styles

        st.dataframe(
            df_p_disp.style.apply(_style_prec, axis=1).format({
                "Cobertura (sem)": "{:.1f}",
                "Precio Actual (S/)": "S/ {:.2f}",
                "Precio Sug. (S/)": "S/ {:.2f}",
                "Dscto Sug.": "{:.1%}",
                "Margen Post": "{:.1%}",
            }, na_rep="—"),
            use_container_width=True, height=420
        )

        # Alerta si descuento = precio mínimo
        capped = df_p[df_p["precio_sugerido"] == df_p["precio_minimo"]]
        if not capped.empty:
            st.warning(
                f"⚠️ {len(capped)} producto(s) llegaron al precio mínimo (margen {params['margen_min']*100:.0f}%). "
                "El descuento sugerido fue reducido para proteger el margen."
            )

        # ── Descarga Excel Acciones Precio ──
        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
        _prec_xl_cols = [c for c in ['marca', 'sku', 'nombre', 'categoria', 'tienda', 'estado',
                                      'stock_total', 'cobertura_actual', 'edad_semanas',
                                      'precio_sugerido', 'dscto_sugerido',
                                      'margen_post', 'motivo'] if c in df_p.columns]
        _prec_xl_buf = io.BytesIO()
        with pd.ExcelWriter(_prec_xl_buf, engine='openpyxl') as _w_prec:
            _add_pricing_cols(
                df_p[_prec_xl_cols].sort_values('dscto_sugerido', ascending=False),
                df_cob, 'Acciones Precio', _w_prec
            )
        _prec_xl_buf.seek(0)
        st.download_button(
            f"📥 Descargar acciones de precio — {len(df_p):,} productos (.xlsx)",
            data=_prec_xl_buf.getvalue(),
            file_name="Capi_Acciones_Precio.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_precio_vista",
        )


# ─── TAB 6: Alertas IA ───────────────────────────────────────

elif nav_page == "🤖 Alertas IA":
    if df_alertas.empty and df_anomalias.empty:
        st.success("✅ No se detectaron alertas ni anomalías en los datos actuales.")
    else:
        _n_alertas_total_ia = len(df_alertas) + len(df_anomalias)
        st.markdown(f"#### 🤖 Alertas Inteligentes — {_n_alertas_total_ia} hallazgos")
        st.caption("Análisis consolidado a nivel SKU con filtros de materialidad. Ventas de las últimas 4 semanas.")

        # ── KPIs rápidos ──
        if not df_alertas.empty:
            _kpi_cols = st.columns(5)
            _tipos_kpi = [
                ("⚠️ Se Detuvo",    "SE DETUVO",    "#FEF2F2", "#DC2626"),
                ("🔴 Frenando",     "FRENANDO",     "#FFF7ED", "#EA580C"),
                ("🟢 Acelerando",   "ACELERANDO",   "#ECFDF5", "#059669"),
                ("🆕 Sin Tracción", "SIN TRACCIÓN", TEAL_50,   TEAL_600),
                ("🔮 Riesgo Crít.", "RIESGO CRÍTICO", "#FDF4FF", "#9333EA"),
            ]
            for col, (label, keyword, bg, fg) in zip(_kpi_cols, _tipos_kpi):
                _count = int(df_alertas["tipo_alerta"].str.contains(keyword, na=False).sum())
                _cap_sum = df_alertas.loc[df_alertas["tipo_alerta"].str.contains(keyword, na=False), "capital_stock"].sum()
                col.markdown(
                    f'<div style="background:{bg}; border-radius:12px; padding:12px 14px; text-align:center;">'
                    f'<div style="font-size:1.6em; font-weight:700; color:{fg};">{_count}</div>'
                    f'<div style="font-size:0.75em; color:{SLATE_500};">{label}</div>'
                    f'<div style="font-size:0.7em; color:{SLATE_500};">S/ {_cap_sum:,.0f}</div>'
                    f'</div>',
                    unsafe_allow_html=True
                )

            # Capital total en riesgo
            _capital_total_riesgo = df_alertas[
                df_alertas["tipo_alerta"].str.contains("SE DETUVO|FRENANDO|RIESGO CRÍTICO", na=False)
            ]["capital_stock"].sum()
            if _capital_total_riesgo > 0:
                st.markdown(
                    f'<div style="background:linear-gradient(135deg,#FEF2F2,#FFF7ED); border-radius:12px; padding:10px 16px; margin-top:8px; text-align:center;">'
                    f'<span style="font-size:0.85em; color:{SLATE_700};">Capital en riesgo (detuvo + frenando + riesgo crítico): </span>'
                    f'<strong style="font-size:1.1em; color:#DC2626;">S/ {_capital_total_riesgo:,.0f}</strong>'
                    f'</div>',
                    unsafe_allow_html=True
                )

            st.markdown("")

            # ── Filtros ──
            st.markdown("##### Tendencias y Riesgos")
            _fcol1, _fcol2, _fcol3 = st.columns(3)
            with _fcol1:
                _tipos_disp = ["Todas"] + sorted(df_alertas["tipo_alerta"].unique().tolist())
                f_tipo = st.selectbox("Tipo de alerta", _tipos_disp, key="alerta_tipo_v3")
            with _fcol2:
                _marcas_disp = ["Todas"] + sorted(df_alertas["marca"].dropna().unique().tolist())
                _marcas_disp = [m for m in _marcas_disp if m.strip() != ""]
                if len(_marcas_disp) <= 1:
                    _marcas_disp = ["Todas"]
                f_marca = st.selectbox("Marca", _marcas_disp, key="alerta_marca_v3")
            with _fcol3:
                _cats_disp = ["Todas"] + sorted(df_alertas["categoria"].dropna().unique().tolist())
                f_cat = st.selectbox("Categoría", _cats_disp, key="alerta_cat_v3")

            df_al = df_alertas.copy()
            if f_tipo != "Todas":
                df_al = df_al[df_al["tipo_alerta"] == f_tipo]
            if f_marca != "Todas":
                df_al = df_al[df_al["marca"] == f_marca]
            if f_cat != "Todas":
                df_al = df_al[df_al["categoria"] == f_cat]

            st.caption(f"Mostrando {len(df_al)} de {len(df_alertas)} alertas")

            # ── Alertas agrupadas por marca (desplegables) ──
            _COLOR_MAP = {
                "SE DETUVO": ("#FEF2F2", "#DC2626"), "FRENANDO": ("#FFF7ED", "#EA580C"),
                "ACELERANDO": ("#ECFDF5", "#059669"), "SIN TRACCIÓN": (TEAL_50, TEAL_600),
                "RIESGO CRÍTICO": ("#FDF4FF", "#9333EA"),
            }

            # Agrupar alertas por marca
            _marcas_alertas = (
                df_al.groupby("marca").agg(
                    n_alertas=("sku", "count"),
                    capital=("capital_stock", "sum"),
                    tipos=("tipo_alerta", lambda x: list(x.value_counts().items())),
                ).sort_values("capital", ascending=False)
            )

            for _marca_name, _marca_agg in _marcas_alertas.iterrows():
                _n = int(_marca_agg["n_alertas"])
                _cap = _marca_agg["capital"]
                # Mini badges de tipos para el header del expander
                _tipo_badges = ""
                for _t_name, _t_count in _marca_agg["tipos"]:
                    _t_bg, _t_fg = SLATE_50, SLATE_500
                    for kw, (bg_c, bd_c) in _COLOR_MAP.items():
                        if kw in _t_name:
                            _t_bg, _t_fg = bg_c, bd_c
                            break
                    _tipo_badges += f"{_t_name}: {_t_count}  ·  "
                _tipo_badges = _tipo_badges.rstrip("  ·  ")

                _exp_label = f"**{_marca_name}** — {_n} alertas · S/ {_cap:,.0f} capital · {_tipo_badges}"
                with st.expander(_exp_label, expanded=False):
                    _df_marca = df_al[df_al["marca"] == _marca_name].sort_values("capital_stock", ascending=False)
                    for _, r in _df_marca.iterrows():
                        tipo = r["tipo_alerta"]
                        _bg, _border = SLATE_50, SLATE_500
                        for kw, (bg_c, bd_c) in _COLOR_MAP.items():
                            if kw in tipo:
                                _bg, _border = bg_c, bd_c
                                break

                        _temp_tag = f' · <span style="background:rgba(13,148,136,0.1); color:{TEAL_600}; padding:1px 6px; border-radius:4px; font-size:0.78em;">{r["temporada"]}</span>' if r.get("temporada") else ""
                        _cob_txt = f'{r["cobertura_sem"]:.1f} sem' if r.get("cobertura_sem") is not None else "—"

                        st.markdown(
                            f'<div style="background-color:{_bg}; padding:14px 18px; border-radius:12px; margin-bottom:8px; border-left:4px solid {_border};">'
                            f'<div style="display:flex; justify-content:space-between; align-items:center;">'
                            f'  <div><strong style="color:{SLATE_900};">{tipo}</strong>{_temp_tag}</div>'
                            f'  <div style="text-align:right; font-size:0.82em; color:{SLATE_500};">S/ {r["capital_stock"]:,.0f} en stock</div>'
                            f'</div>'
                            f'<div style="margin-top:4px;">'
                            f'  <code style="background:rgba(0,0,0,0.06); padding:2px 6px; border-radius:4px; font-size:0.82em;">{r["sku"]}</code>'
                            f'  &nbsp;·&nbsp; {str(r["nombre"])[:35]}'
                            f'</div>'
                            f'<div style="margin-top:4px; font-size:0.82em; color:{SLATE_500};">'
                            f'  Estado: {r["estado_actual"]} &nbsp;·&nbsp; Stock: {int(r["stock_total"])} uds ({int(r["n_tiendas"])} tiendas)'
                            f'  &nbsp;·&nbsp; Cob: {_cob_txt} &nbsp;·&nbsp; Edad: {int(r["edad_semanas"])} sem'
                            f'</div>'
                            f'<div style="margin-top:6px; font-size:0.88em; color:{SLATE_700};">{r["detalle"]}</div>'
                            f'</div>',
                            unsafe_allow_html=True
                        )

            # ── Resumen por tipo (tabla) ──
            st.markdown("---")
            st.markdown("**Resumen por tipo**")
            resumen_al = df_alertas.groupby("tipo_alerta").agg(
                Cantidad=("sku", "count"),
                SKUs_únicos=("sku", "nunique"),
                Capital_total=("capital_stock", "sum"),
            ).reset_index()
            resumen_al.columns = ["Tipo de Alerta", "Alertas", "SKUs", "Capital (S/)"]
            resumen_al["Capital (S/)"] = resumen_al["Capital (S/)"].apply(lambda x: f"S/ {x:,.0f}")
            st.dataframe(resumen_al, use_container_width=True, hide_index=True)

            # ── Tabla descargable ──
            with st.expander("📋 Ver tabla completa de alertas (sin filtros)"):
                _cols_show = ["sku", "nombre", "marca", "categoria", "temporada", "tipo_alerta",
                              "estado_actual", "stock_total", "n_tiendas", "capital_stock",
                              "cobertura_sem", "edad_semanas", "sem1_total", "prom_sem2_4",
                              "variacion_pct", "tendencia_pct", "severidad"]
                _cols_avail = [c for c in _cols_show if c in df_alertas.columns]
                st.dataframe(df_alertas[_cols_avail], use_container_width=True, hide_index=True)

        # ── Anomalías por tienda ──
        if not df_anomalias.empty:
            st.markdown("---")
            st.markdown("##### Anomalías por Tienda")
            st.caption("SKUs con comportamiento anómalo en una tienda específica vs. el resto de tiendas.")

            for _, r in df_anomalias.iterrows():
                color = "#FEF2F2" if any(sv in r["tipo"] for sv in ("SIN VENTA", "DORMIDO", "MUERTO")) else "#FEFCE8"
                _border_anom = "#DC2626" if "SIN VENTA" in r["tipo"] else "#EAB308"
                st.markdown(
                    f'<div style="background-color:{color}; padding:14px 18px; border-radius:12px; margin-bottom:8px; border-left:4px solid {_border_anom};">'
                    f'<strong style="color:{SLATE_900};">{r["tipo"]}</strong> &nbsp;·&nbsp; '
                    f'<code style="background:rgba(0,0,0,0.06); padding:2px 6px; border-radius:4px; font-size:0.82em;">{r["sku"]}</code> '
                    f'&nbsp;·&nbsp; {str(r["nombre"])[:30]} &nbsp;·&nbsp; Tienda: {r["tienda_anomala"]}<br>'
                    f'<span style="font-size:0.88em; color:{SLATE_700};">{r["detalle"]}</span>'
                    f'</div>',
                    unsafe_allow_html=True
                )

            with st.expander("📋 Ver tabla de anomalías"):
                st.dataframe(df_anomalias, use_container_width=True, hide_index=True)

        elif not df_alertas.empty:
            st.info("ℹ️ Anomalías por tienda: no aplica (todos los SKUs están en una sola tienda en este dataset).")


# ─── TAB 7: Simulador Predictivo ────────────────────────────

elif nav_page == "🔮 Simulador Predictivo":
    st.markdown(f'<div class="section-header"><h3>🔮 Simulador Predictivo de Margen</h3></div>', unsafe_allow_html=True)
    st.caption("Conecta acciones reales del buyer con su impacto proyectado en margen, capital y tiempo.")

    # ── Datos base desde el motor ──
    _SIM_PROPIAS_SET = {'MARQUIS', 'NAVIGATA', 'CACHAREL', 'SPAVALDI', 'OSCAR DE LA RENTA', 'US POLO'}

    if 'marca' in df_cob.columns and 'vta_soles_4sem' in df_cob.columns:
        _df_sku_sim = df_cob.drop_duplicates('sku')[['sku', 'marca', 'vta_soles_4sem', 'contrib_soles_4sem', 'stock_valor_costo']].copy()
        _df_sku_sim['tipo_marca'] = _df_sku_sim['marca'].str.upper().str.strip().apply(
            lambda m: 'propia' if m in _SIM_PROPIAS_SET else 'tercera'
        )
        _sim_by_marca = _df_sku_sim.groupby(['marca', 'tipo_marca']).agg(
            vta=('vta_soles_4sem', 'sum'),
            contrib=('contrib_soles_4sem', 'sum'),
            capital=('stock_valor_costo', 'sum'),
        ).reset_index()
        _sim_by_marca['margen'] = _sim_by_marca.apply(
            lambda r: r['contrib'] / r['vta'] if r['vta'] > 0 else 0, axis=1
        )
    elif _margen_por_marca:
        _sim_by_marca = pd.DataFrame(_margen_por_marca)
        _sim_by_marca['tipo_marca'] = _sim_by_marca['marca'].str.upper().str.strip().apply(
            lambda m: 'propia' if m in _SIM_PROPIAS_SET else 'tercera'
        )
        _sim_by_marca['margen'] = _sim_by_marca.get('margen_efectivo', 0)
        _sim_by_marca['capital'] = _sim_by_marca.get('stock_valor_costo', 0)
    else:
        _sim_by_marca = pd.DataFrame()

    if _sim_by_marca.empty:
        st.warning("No hay datos de margen por marca disponibles. Ejecuta el análisis con un archivo que incluya Vta S/ y Contribución S/.")
    else:
        _sim_propias = _sim_by_marca[_sim_by_marca['tipo_marca'] == 'propia']
        _sim_terceras = _sim_by_marca[_sim_by_marca['tipo_marca'] == 'tercera']

        _sim_vta_p = float(_sim_propias['vta'].sum())
        _sim_vta_t = float(_sim_terceras['vta'].sum())
        _sim_vta_total = _sim_vta_p + _sim_vta_t
        _sim_contrib_p = float(_sim_propias['contrib'].sum())
        _sim_contrib_t = float(_sim_terceras['contrib'].sum())
        _sim_cap_t = float(_sim_terceras['capital'].sum())

        _sim_mg_global = (_sim_contrib_p + _sim_contrib_t) / _sim_vta_total if _sim_vta_total > 0 else 0
        _sim_mg_p = _sim_contrib_p / _sim_vta_p if _sim_vta_p > 0 else 0
        _sim_mg_t = _sim_contrib_t / _sim_vta_t if _sim_vta_t > 0 else 0
        _sim_gap = _sim_mg_p - _sim_mg_t

        # ── Sección 1: Donde estamos hoy ──
        st.markdown("##### Donde estamos hoy")
        st.markdown('<span style="font-size:0.82em; color:#6c757d;">Punto de partida. Estos son tu baseline — el resultado de no hacer nada.</span>', unsafe_allow_html=True)

        _kpi_sim = st.columns(5)
        with _kpi_sim[0]:
            st.metric("Margen global", f"{_sim_mg_global*100:.1f}%")
        with _kpi_sim[1]:
            st.metric("Propias", f"{_sim_mg_p*100:.1f}%", help=f"Venta: S/{_sim_vta_p:,.0f}")
        with _kpi_sim[2]:
            st.metric("Terceras", f"{_sim_mg_t*100:.1f}%", help=f"Venta: S/{_sim_vta_t:,.0f}")
        with _kpi_sim[3]:
            st.metric("Gap", f"{_sim_gap*100:.1f}pp")
        with _kpi_sim[4]:
            st.metric("Capital parado", f"S/{_sim_cap_t:,.0f}", help=f"{len(_sim_terceras)} marcas terceras")

        st.markdown("---")

        # ── Sección 2: Acciones del buyer ──
        st.markdown("##### Que acciones puedes tomar")
        st.markdown('<span style="font-size:0.82em; color:#6c757d;">Activa las palancas que planeas ejecutar y ajusta los parámetros.</span>', unsafe_allow_html=True)

        # Initialize session state for toggles
        for _sim_key in ['sim_markdown', 'sim_negociar', 'sim_otb', 'sim_push', 'sim_precio']:
            if _sim_key not in st.session_state:
                st.session_state[_sim_key] = False

        _sim_col1, _sim_col2 = st.columns(2)

        # ── Acción 1: Markdown en terceras envejecidas ──
        with _sim_col1:
            with st.expander("📉 Markdown en terceras envejecidas", expanded=st.session_state.get('sim_markdown', False)):
                st.markdown(
                    '<span style="font-size:0.82em; color:#6c757d;">'
                    'Descuento progresivo a terceras >16 sem. Sacrificas margen pero recuperas capital.'
                    '</span>', unsafe_allow_html=True
                )
                _sim_md_on = st.checkbox("Activar", key="sim_markdown")
                _sim_md_pct = st.slider("Descuento adicional (%)", 5, 40, 20, 5, key="sim_md_pct")
                _sim_md_n = st.slider("Marcas afectadas", 1, max(1, len(_sim_terceras)), min(4, len(_sim_terceras)), 1, key="sim_md_n")

                st.markdown(
                    f'<span class="tag" style="background:#E1F5EE;color:#085041;padding:2px 8px;border-radius:4px;font-size:0.75em;">Fácil</span> '
                    f'<span class="tag" style="background:#E6F1FB;color:#0C447C;padding:2px 8px;border-radius:4px;font-size:0.75em;">2-4 sem</span>',
                    unsafe_allow_html=True
                )

        # ── Acción 2: Negociar devolución ──
            with st.expander("🤝 Negociar devolución con proveedores", expanded=st.session_state.get('sim_negociar', False)):
                st.markdown(
                    '<span style="font-size:0.82em; color:#6c757d;">'
                    'Devolver inventario o negociar swap. Capital libre sin costo de margen.'
                    '</span>', unsafe_allow_html=True
                )
                _sim_neg_on = st.checkbox("Activar", key="sim_negociar")
                _sim_neg_pct = st.slider("Tasa éxito negociación (%)", 10, 60, 30, 5, key="sim_neg_pct")
                _sim_neg_n = st.slider("Marcas a negociar", 1, max(1, len(_sim_terceras)), min(3, len(_sim_terceras)), 1, key="sim_neg_n")

                st.markdown(
                    f'<span style="background:#FAEEDA;color:#633806;padding:2px 8px;border-radius:4px;font-size:0.75em;">Requiere negociación</span> '
                    f'<span style="background:#FAEEDA;color:#633806;padding:2px 8px;border-radius:4px;font-size:0.75em;">4-8 sem</span>',
                    unsafe_allow_html=True
                )

        with _sim_col2:
        # ── Acción 3: Redirigir OTB ──
            with st.expander("🔄 Redirigir compra futura a propias", expanded=st.session_state.get('sim_otb', False)):
                st.markdown(
                    '<span style="font-size:0.82em; color:#6c757d;">'
                    'Mover % del presupuesto de compra de terceras a propias. Efecto gradual 8-12 sem.'
                    '</span>', unsafe_allow_html=True
                )
                _sim_otb_on = st.checkbox("Activar", key="sim_otb")
                _sim_otb_pct = st.slider("Redirigir del OTB (%)", 5, 35, 15, 5, key="sim_otb_pct")

                st.markdown(
                    f'<span style="background:#FAEEDA;color:#633806;padding:2px 8px;border-radius:4px;font-size:0.75em;">Requiere planificación</span> '
                    f'<span style="background:#FAEEDA;color:#633806;padding:2px 8px;border-radius:4px;font-size:0.75em;">8-12 sem</span>',
                    unsafe_allow_html=True
                )

        # ── Acción 4: Empujar propias a piso ──
            with st.expander("🚀 Empujar propias a piso", expanded=st.session_state.get('sim_push', False)):
                st.markdown(
                    '<span style="font-size:0.82em; color:#6c757d;">'
                    'Más exhibición de propias = más venta con mejor margen. Rápido y fácil.'
                    '</span>', unsafe_allow_html=True
                )
                _sim_push_on = st.checkbox("Activar", key="sim_push")
                _sim_push_boost = st.slider("Incremento sell-through (%)", 5, 25, 10, 5, key="sim_push_boost")

                st.markdown(
                    f'<span style="background:#E1F5EE;color:#085041;padding:2px 8px;border-radius:4px;font-size:0.75em;">Fácil</span> '
                    f'<span style="background:#E6F1FB;color:#0C447C;padding:2px 8px;border-radius:4px;font-size:0.75em;">1-3 sem</span>',
                    unsafe_allow_html=True
                )

        st.markdown("---")

        # ── Sección 3: Cálculo de proyección ──
        _sim_margen_delta = 0.0
        _sim_capital_libre = 0.0
        _sim_actions_active = 0
        _sim_max_weeks = 0

        # Markdown
        _sim_margen_delta_md = 0.0
        if st.session_state.get('sim_markdown', False):
            _sim_actions_active += 1
            _top_terceras = _sim_terceras.sort_values('capital', ascending=False).head(_sim_md_n)
            _cap_affected = float(_top_terceras['capital'].sum())
            _vta_affected = float(_top_terceras['vta'].sum())
            _sell_boost = min(0.6, (_sim_md_pct / 100) * 1.8)
            _cap_recovered = _cap_affected * _sell_boost
            _margin_loss = _vta_affected * (_sim_md_pct / 100) * 0.4
            _sim_margen_delta_md = -_margin_loss / _sim_vta_total
            _sim_margen_delta += _sim_margen_delta_md
            _sim_capital_libre += _cap_recovered
            _sim_max_weeks = max(_sim_max_weeks, 3)

        # Negociar
        if st.session_state.get('sim_negociar', False):
            _sim_actions_active += 1
            _top_neg = _sim_terceras.sort_values('capital', ascending=False).head(_sim_neg_n)
            _cap_neg = float(_top_neg['capital'].sum())
            _sim_capital_libre += _cap_neg * (_sim_neg_pct / 100)
            _sim_max_weeks = max(_sim_max_weeks, 6)

        # Redirigir OTB
        if st.session_state.get('sim_otb', False):
            _sim_actions_active += 1
            _shifted_vta = _sim_vta_t * (_sim_otb_pct / 100)
            _new_vta_p = _sim_vta_p + _shifted_vta
            _new_vta_t = _sim_vta_t - _shifted_vta
            _new_contrib_p = _new_vta_p * _sim_mg_p
            _new_contrib_t = _new_vta_t * _sim_mg_t
            _new_global = (_new_contrib_p + _new_contrib_t) / _sim_vta_total
            _sim_margen_delta += _new_global - _sim_mg_global
            _sim_max_weeks = max(_sim_max_weeks, 10)

        # Empujar propias
        if st.session_state.get('sim_push', False):
            _sim_actions_active += 1
            _extra_vta = _sim_vta_p * (_sim_push_boost / 100)
            _extra_contrib = _extra_vta * _sim_mg_p
            _new_total = _sim_vta_total + _extra_vta
            _new_mg = (_sim_contrib_p + _sim_contrib_t + _extra_contrib) / _new_total
            _sim_margen_delta += _new_mg - _sim_mg_global
            _sim_max_weeks = max(_sim_max_weeks, 2)

        _sim_new_global = _sim_mg_global + _sim_margen_delta
        _sim_extra_contrib = _sim_margen_delta * _sim_vta_total
        _sim_new_cap = _sim_cap_t - _sim_capital_libre

        # ── Resultado proyectado ──
        st.markdown("##### Proyección de resultado")

        if _sim_actions_active == 0:
            st.info("👆 Activa al menos una acción arriba para ver la proyección.")
        else:
            _proj_cols = st.columns([1, 1, 1, 2])
            with _proj_cols[0]:
                st.metric(
                    "Margen hoy", f"{_sim_mg_global*100:.1f}%",
                )
            with _proj_cols[1]:
                st.metric(
                    "Margen proyectado",
                    f"{_sim_new_global*100:.1f}%",
                    delta=f"{_sim_margen_delta*100:+.1f}pp",
                )
            with _proj_cols[2]:
                st.metric(
                    "Capital liberado",
                    f"S/{_sim_capital_libre:,.0f}" if _sim_capital_libre > 0 else "—",
                )
            with _proj_cols[3]:
                st.markdown(
                    f'<div style="background:{TEAL_50};padding:12px 16px;border-radius:10px;border-left:4px solid {TEAL_600};">'
                    f'<div style="font-size:0.82em;color:{SLATE_500};">Contribución extra mensual</div>'
                    f'<div style="font-size:1.3em;font-weight:700;color:{TEAL_600};">S/{abs(_sim_extra_contrib):,.0f}</div>'
                    f'<div style="font-size:0.78em;color:{SLATE_500};">'
                    f'{_sim_actions_active} acciones · impacto en {_sim_max_weeks} sem · '
                    f'capital parado remanente: S/{_sim_new_cap:,.0f}</div>'
                    f'</div>',
                    unsafe_allow_html=True
                )

            st.markdown("")

            # ── Timeline chart ──
            _weeks_range = list(range(0, 13))
            _timeline_data = []

            for w in _weeks_range:
                # Baseline
                _timeline_data.append({
                    'Semana': w, 'Margen': round(_sim_mg_global * 100, 2), 'Escenario': 'Sin cambios'
                })
                # Con acciones (eased ramp-up)
                _delta_w = 0.0
                if st.session_state.get('sim_markdown', False):
                    _md_weeks = 3
                    _md_delta = _sim_margen_delta_md  # pre-calculated above
                    if w >= _md_weeks:
                        _delta_w += _md_delta
                    elif w > 0:
                        _delta_w += _md_delta * (w / _md_weeks) ** 2
                if st.session_state.get('sim_otb', False):
                    _otb_weeks = 10
                    _otb_delta = ((_sim_vta_p + _sim_vta_t * (_sim_otb_pct / 100)) * _sim_mg_p + (_sim_vta_t * (1 - _sim_otb_pct / 100)) * _sim_mg_t) / _sim_vta_total - _sim_mg_global
                    if w >= _otb_weeks:
                        _delta_w += _otb_delta
                    elif w > 0:
                        _delta_w += _otb_delta * (w / _otb_weeks) ** 2
                if st.session_state.get('sim_push', False):
                    _push_weeks = 2
                    _push_extra = _sim_vta_p * (_sim_push_boost / 100)
                    _push_delta = ((_sim_contrib_p + _sim_contrib_t + _push_extra * _sim_mg_p) / (_sim_vta_total + _push_extra)) - _sim_mg_global
                    if w >= _push_weeks:
                        _delta_w += _push_delta
                    elif w > 0:
                        _delta_w += _push_delta * (w / _push_weeks) ** 2

                _timeline_data.append({
                    'Semana': w,
                    'Margen': round((_sim_mg_global + _delta_w) * 100, 2),
                    'Escenario': 'Con acciones'
                })

                # Costo inacción
                _deprec = _sim_cap_t * 0.005 * w
                _adj_contrib = (_sim_contrib_p + _sim_contrib_t) - _deprec * 0.1
                _timeline_data.append({
                    'Semana': w,
                    'Margen': round(_adj_contrib / _sim_vta_total * 100, 2),
                    'Escenario': 'Costo inacción'
                })

            _df_timeline = pd.DataFrame(_timeline_data)

            _color_scale = alt.Scale(
                domain=['Con acciones', 'Sin cambios', 'Costo inacción'],
                range=[TEAL_600, SLATE_400, '#E24B4A']
            )
            _dash_scale = alt.Scale(
                domain=['Con acciones', 'Sin cambios', 'Costo inacción'],
                range=[[1, 0], [6, 4], [3, 3]]
            )

            _chart_timeline = alt.Chart(_df_timeline).mark_line(
                strokeWidth=2, point=alt.OverlayMarkDef(size=20, filled=True)
            ).encode(
                x=alt.X('Semana:Q', title='Semana', axis=alt.Axis(tickCount=13)),
                y=alt.Y('Margen:Q', title='Margen efectivo (%)',
                        scale=alt.Scale(zero=False)),
                color=alt.Color('Escenario:N', scale=_color_scale,
                               legend=alt.Legend(orient='bottom', title=None)),
                strokeDash=alt.StrokeDash('Escenario:N', scale=_dash_scale, legend=None),
                tooltip=['Semana', 'Escenario', alt.Tooltip('Margen:Q', format='.1f')]
            ).properties(height=300, title='Proyección de margen a 12 semanas')

            st.altair_chart(_chart_timeline, use_container_width=True)

        st.markdown("---")

        # ── Sección 4: Trade-offs ──
        st.markdown("##### Trade-offs a considerar")
        st.markdown('<span style="font-size:0.82em; color:#6c757d;">Toda acción tiene un costo. No hay decisión sin sacrificio.</span>', unsafe_allow_html=True)

        _tf_col1, _tf_col2 = st.columns(2)
        with _tf_col1:
            st.markdown(
                f'<div style="background:#fff;padding:14px 18px;border-radius:10px;border:1px solid #e9ecef;">'
                f'<strong>Margen vs velocidad de recuperación</strong>'
                f'<div style="font-size:0.82em;color:{SLATE_500};margin-top:6px;">'
                f'Markdown acelera la recuperación de capital pero destruye margen. '
                f'La pregunta no es "cuánto margen pierdo" sino "cuánto vale tener ese capital libre 8 semanas antes".'
                f'</div></div>', unsafe_allow_html=True
            )
        with _tf_col2:
            st.markdown(
                f'<div style="background:#fff;padding:14px 18px;border-radius:10px;border:1px solid #e9ecef;">'
                f'<strong>Diversidad vs rentabilidad</strong>'
                f'<div style="font-size:0.82em;color:{SLATE_500};margin-top:6px;">'
                f'Concentrar en propias sube el margen pero reduce opciones. '
                f'Los clientes que buscan Lacoste también compran Marquis — el tráfico de marca importa.'
                f'</div></div>', unsafe_allow_html=True
            )

        st.markdown("")

        # ── Sección 5: Tabla detallada por marca ──
        st.markdown("##### Detalle por marca")
        _sim_by_marca_display = _sim_by_marca.copy()
        _sim_by_marca_display['margen_pct'] = (_sim_by_marca_display['margen'] * 100).round(1)
        _sim_by_marca_display = _sim_by_marca_display.sort_values('vta', ascending=False)
        _sim_display_cols = {
            'marca': 'Marca',
            'tipo_marca': 'Tipo',
            'vta': 'Venta 4sem (S/)',
            'contrib': 'Contribución (S/)',
            'margen_pct': 'Margen %',
            'capital': 'Capital (S/)',
        }
        _cols_avail_sim = [c for c in _sim_display_cols.keys() if c in _sim_by_marca_display.columns]
        _df_show_sim = _sim_by_marca_display[_cols_avail_sim].rename(columns=_sim_display_cols)
        for _sc in ['Venta 4sem (S/)', 'Contribución (S/)', 'Capital (S/)']:
            if _sc in _df_show_sim.columns:
                _df_show_sim[_sc] = _df_show_sim[_sc].apply(lambda x: f"S/{x:,.0f}")
        st.dataframe(_df_show_sim, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════
#  DESCARGA DE EXCEL
# ══════════════════════════════════════════════════════════════

st.markdown("---")
st.markdown(f'<div class="section-header"><h3>📥 Descargar resultados</h3></div>', unsafe_allow_html=True)

@st.cache_data
def _build_excel(cob_json, rep_pivot_json, rep_json, trans_json, prec_json, alertas_json, anomalias_json):
    """Genera el Excel de resultados en memoria con columnas de precio y nuevo margen."""
    buf = io.BytesIO()
    _df_cob_ref = pd.read_json(io.StringIO(cob_json))
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        _add_pricing_cols(_df_cob_ref, _df_cob_ref, "Cobertura", writer)
        pd.read_json(io.StringIO(rep_pivot_json)).to_excel(writer, sheet_name="Reposiciones", index=False)
        _df_rep_xl = pd.read_json(io.StringIO(rep_json))
        if not _df_rep_xl.empty:
            _add_pricing_cols(_df_rep_xl, _df_cob_ref, "Reposiciones Detalle", writer)
        else:
            _df_rep_xl.to_excel(writer, sheet_name="Reposiciones Detalle", index=False)
        pd.read_json(io.StringIO(trans_json)).to_excel(writer, sheet_name="Transferencias", index=False)
        _df_prec_xl = pd.read_json(io.StringIO(prec_json))
        if not _df_prec_xl.empty:
            _add_pricing_cols(_df_prec_xl, _df_cob_ref, "Acciones Precio", writer)
        else:
            _df_prec_xl.to_excel(writer, sheet_name="Acciones Precio", index=False)
        pd.read_json(io.StringIO(alertas_json)).to_excel(writer, sheet_name="Alertas IA", index=False)
        pd.read_json(io.StringIO(anomalias_json)).to_excel(writer, sheet_name="Anomalías Tienda", index=False)
    buf.seek(0)
    return buf.read()


# Combinar alertas + anomalías para Excel
_alertas_excel = df_alertas if not df_alertas.empty else pd.DataFrame()
_anomalias_excel = df_anomalias if not df_anomalias.empty else pd.DataFrame()

excel_bytes = _build_excel(
    df_cob.to_json(),
    df_rep_pivot.to_json() if not df_rep_pivot.empty else pd.DataFrame().to_json(),
    df_rep.to_json() if not df_rep.empty else pd.DataFrame().to_json(),
    df_trans.to_json() if not df_trans.empty else pd.DataFrame().to_json(),
    df_prec.to_json() if not df_prec.empty else pd.DataFrame().to_json(),
    _alertas_excel.to_json() if not _alertas_excel.empty else pd.DataFrame().to_json(),
    _anomalias_excel.to_json() if not _anomalias_excel.empty else pd.DataFrame().to_json(),
)

st.download_button(
    label="📥 Descargar análisis completo (.xlsx)",
    data=excel_bytes,
    file_name="Capi_Analisis.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    use_container_width=True,
)

# ══════════════════════════════════════════════════════════════
#  SNAPSHOT & COMPARATIVO SEMANAL
# ══════════════════════════════════════════════════════════════

st.markdown("---")
st.markdown(f'<div class="section-header"><h3>📸 Medición de resultados</h3></div>', unsafe_allow_html=True)

_snap_col1, _snap_col2 = st.columns(2)
with _snap_col1:
    _snap_label = st.text_input("Etiqueta del snapshot", value="semana_0", key="snap_label",
                                 help="Ej: semana_0 (baseline), semana_1, semana_2...")
with _snap_col2:
    st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    if st.button("📸 Guardar snapshot semanal", key="btn_snapshot", use_container_width=True):
        _snap = motor_v2.snapshot_kpis(st.session_state["results"], semana_label=_snap_label)
        st.success(f"✅ Snapshot '{_snap_label}' guardado — {_snap['total_combos']:,} combos, ST {_snap['sell_through_pct']:.1f}%")

# Mostrar comparativo si hay snapshots
_all_snaps = motor_v2.load_snapshots()
if len(_all_snaps) >= 2:
    with st.expander(f"📊 Comparativo semanal — {len(_all_snaps)} snapshots disponibles", expanded=False):
        _snap_options = [s_item.get('semana', '?') for s_item in _all_snaps]
        _sc1, _sc2 = st.columns(2)
        with _sc1:
            _base_idx = st.selectbox("Baseline", range(len(_snap_options)),
                                      format_func=lambda i: _snap_options[i], key="comp_base")
        with _sc2:
            _curr_idx = st.selectbox("Actual", range(len(_snap_options)),
                                      index=len(_snap_options)-1,
                                      format_func=lambda i: _snap_options[i], key="comp_curr")

        _comp = motor_v2.comparativo_semanal(_all_snaps[_curr_idx], _all_snaps[_base_idx])
        if _comp:
            # KPI resumen
            _cm1, _cm2, _cm3 = st.columns(3)
            _cm1.metric("Mejoras", f"{_comp['n_mejoras']}", delta=f"{_comp['score']} KPIs")
            _cm2.metric("Empeoramientos", f"{_comp['n_peores']}")
            _cm3.metric("Período", f"{_comp['baseline_semana']} → {_comp['actual_semana']}")

            # Tabla de deltas
            _comp_rows = []
            for kpi, d in _comp['deltas'].items():
                if d['delta'] is not None:
                    _comp_rows.append({
                        'KPI': d['label'],
                        'Baseline': d['baseline'],
                        'Actual': d['actual'],
                        'Delta': d['delta_fmt'],
                        'Estado': d['icono'],
                    })
            if _comp_rows:
                _df_comp = pd.DataFrame(_comp_rows)
                st.dataframe(_df_comp, use_container_width=True, hide_index=True, height=min(500, 40 + len(_comp_rows) * 35))
elif len(_all_snaps) == 1:
    st.caption(f"📌 Baseline guardado: {_all_snaps[0].get('semana', '?')} ({_all_snaps[0].get('timestamp', '?')[:10]}). "
               "Guarda un segundo snapshot para ver el comparativo.")
else:
    st.caption("Guarda tu primer snapshot (baseline) para comenzar a medir el impacto de Capi.")


# ══════════════════════════════════════════════════════════════
#  CHAT IA — Panel lateral derecho (estilo Nansen)
# ══════════════════════════════════════════════════════════════

# Cerrar el contexto de la columna principal
if _chat_is_open and _col_main is not None:
    _col_main.__exit__(None, None, None)

# Renderizar panel de chat en la columna derecha (estilo Nansen AI)
if _chat_is_open and _col_chat is not None:
    import re as _re_chat

    with _col_chat:
        # Marcador para CSS scoping
        st.markdown('<div class="chat-panel-marker"></div>', unsafe_allow_html=True)

        # ── Header estilo Nansen: logo + "Capi" + badge + preview query + X ──
        _last_query_preview = ""
        for _m in st.session_state["chat_messages"]:
            if _m["role"] == "user":
                _last_query_preview = _m["question"]
        _preview_txt = (_last_query_preview[:40] + "...") if len(_last_query_preview) > 40 else _last_query_preview

        st.markdown(
            f'<div style="display:flex; align-items:center; gap:8px; padding:12px 0; '
            f'border-bottom:1px solid rgba(255,255,255,0.08); margin-bottom:16px;">'
            f'<div style="width:28px; height:28px; background:{TEAL_600}; border-radius:50%; '
            f'display:flex; align-items:center; justify-content:center; color:white; font-weight:700; '
            f'font-size:0.75rem; flex-shrink:0;">C</div>'
            f'<span style="font-weight:600; color:white; font-size:0.88rem;">Capi</span>'
            f'<span style="background:rgba(13,148,136,0.25); color:{TEAL_600}; font-size:0.58rem; font-weight:700; '
            f'padding:2px 7px; border-radius:3px; letter-spacing:0.06em; text-transform:uppercase;">AI</span>'
            f'<span style="color:rgba(255,255,255,0.35); font-size:0.78rem; margin-left:auto; '
            f'white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:180px;">{_preview_txt}</span>'
            f'</div>',
            unsafe_allow_html=True
        )

        # Botón cerrar panel (X)
        if st.button("✕", key="close_chat_x", help="Cerrar chat"):
            st.session_state["chat_open"] = False
            st.rerun()

        # ── Historial de conversación ──
        for _msg_idx, _msg in enumerate(st.session_state["chat_messages"]):
            if _msg["role"] == "user":
                # Burbuja usuario — alineada a la derecha, fondo sutil
                st.markdown(
                    f'<div style="display:flex; justify-content:flex-end; margin:16px 0 12px 0;">'
                    f'<div style="background:rgba(255,255,255,0.06); color:rgba(255,255,255,0.88); '
                    f'padding:10px 16px; border-radius:16px 16px 4px 16px; max-width:88%; '
                    f'font-size:0.88rem; line-height:1.5;">{_msg["question"]}</div></div>',
                    unsafe_allow_html=True
                )
            elif _msg["role"] == "ai":
                # Respuesta AI — texto directo estilo Nansen
                _conv = _msg["conversacion"].replace("\n\n", "<br><br>").replace("\n", "<br>")
                _conv = _re_chat.sub(r'\*\*(.+?)\*\*', r'<strong style="color:white;">\1</strong>', _conv)

                # Step verde con check (estilo Nansen "Checking Smart Money positions >")
                _step_html = (
                    f'<div style="display:inline-flex; align-items:center; gap:6px; margin:8px 0 14px 0;">'
                    f'<span style="color:{TEAL_600}; font-size:0.9rem;">●</span>'
                    f'<span style="color:rgba(255,255,255,0.55); font-size:0.82rem;">'
                    f'Analizando inventario — {_msg.get("n_combos", 0):,} combos</span>'
                    f'<span style="color:rgba(255,255,255,0.35); font-size:0.82rem;">›</span>'
                    f'</div>'
                )

                st.markdown(
                    f'<div style="margin:4px 0 20px 0;">'
                    f'{_step_html}'
                    f'<div style="color:rgba(255,255,255,0.78); font-size:0.88rem; line-height:1.7;">{_conv}</div>'
                    f'</div>',
                    unsafe_allow_html=True
                )

                # Tabla de datos (colapsable)
                if _msg.get("resultado_json"):
                    try:
                        _rdf = pd.read_json(io.StringIO(_msg["resultado_json"]))
                        if not _rdf.empty:
                            with st.expander(f"📊 Ver datos ({len(_rdf)} filas)", expanded=False):
                                st.dataframe(_rdf, use_container_width=True, hide_index=True, height=200)
                    except Exception:
                        pass

                # Separador sutil entre respuestas
                st.markdown(
                    '<div style="border-bottom:1px solid rgba(255,255,255,0.06); margin:4px 0 8px 0;"></div>',
                    unsafe_allow_html=True
                )

        # ── Procesar pregunta pendiente (con memoria conversacional) ──
        if (st.session_state["chat_messages"] and
                st.session_state["chat_messages"][-1]["role"] == "user"):
            _pending_q = st.session_state["chat_messages"][-1]["question"]

            # Construir historial para memoria conversacional
            _chat_history = []
            _msgs = st.session_state["chat_messages"][:-1]  # excluir pregunta actual
            i = 0
            while i < len(_msgs):
                if _msgs[i]["role"] == "user" and i + 1 < len(_msgs) and _msgs[i + 1]["role"] == "ai":
                    _ai_msg = _msgs[i + 1]
                    _chat_history.append({
                        "user_question": _msgs[i]["question"],
                        "titulo": _ai_msg.get("titulo", ""),
                        "result_summary": _ai_msg.get("result_summary", ""),
                        "conversation": _ai_msg.get("conversacion", ""),
                    })
                    i += 2
                else:
                    i += 1

            # Spinner estilo Nansen
            with st.spinner("Analizando datos de inventario..."):
                _chat_result = chat_engine.ask(
                    question=_pending_q,
                    df=df_cob,
                    history=_chat_history if _chat_history else None,
                )
            if _chat_result["error"]:
                st.warning(f"⚠️ {_chat_result['error']}")
                st.session_state["chat_messages"].pop()
            else:
                _res_json = None
                if _chat_result["resultado"] is not None and not _chat_result["resultado"].empty:
                    _res_json = _chat_result["resultado"].to_json()
                st.session_state["chat_messages"].append({
                    "role": "ai",
                    "titulo": _chat_result["titulo"],
                    "conversacion": _chat_result["conversacion"],
                    "resultado_json": _res_json,
                    "result_summary": _chat_result.get("result_summary", ""),
                    "n_combos": len(df_cob),
                })
                st.rerun()

        # ── Input estilo Nansen: "Pregunta a Capi" con borde teal ──
        def _submit_chat_msg():
            val = st.session_state.get("right_panel_q", "").strip()
            if val:
                st.session_state["chat_messages"].append({"role": "user", "question": val})
                st.session_state["right_panel_q"] = ""  # limpiar input tras enviar

        st.text_input("Pregunta a Capi", placeholder="Pregunta a Capi",
                      key="right_panel_q", label_visibility="collapsed",
                      on_change=_submit_chat_msg)

        # ── Chips de sugerencia debajo del input (estilo Nansen) ──
        _CHAT_SUGGESTIONS = [
            "Top vendidos",
            "Capital parado",
            "Peor cobertura",
            "Sobrestock",
        ]
        _chip_html = '<div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:8px;">'
        for _idx, _s_label in enumerate(_CHAT_SUGGESTIONS):
            _chip_html += (
                f'<span class="nansen-chip" style="background:rgba(255,255,255,0.06); '
                f'color:rgba(255,255,255,0.55); font-size:0.72rem; padding:4px 10px; '
                f'border-radius:6px; border:1px solid rgba(255,255,255,0.08); '
                f'cursor:default;">{_s_label}</span>'
            )
        _chip_html += '</div>'
        st.markdown(_chip_html, unsafe_allow_html=True)

        # Botones funcionales para los chips (hidden behind the HTML)
        _chip_button_cols = st.columns(len(_CHAT_SUGGESTIONS))
        for _idx, _s_label in enumerate(_CHAT_SUGGESTIONS):
            with _chip_button_cols[_idx]:
                if st.button(_s_label, key=f"rchip_{_idx}", use_container_width=True):
                    st.session_state["chat_messages"].append({"role": "user", "question": _s_label})
                    st.rerun()

        # Limpiar + disclaimer
        _bot_cols = st.columns([3, 1])
        with _bot_cols[0]:
            st.markdown(
                '<span style="font-size:0.68rem; color:rgba(255,255,255,0.25);">AI-generated. Verify independently.</span>',
                unsafe_allow_html=True
            )
        with _bot_cols[1]:
            if st.session_state["chat_messages"]:
                if st.button("🗑️", key="clear_right_chat", help="Limpiar chat"):
                    st.session_state["chat_messages"] = []
                    st.rerun()

elif nav_page == "📲 Briefing Semanal":
    st.markdown(f'<div class="section-header"><h3>📲 Briefing Semanal — Alertas para Tiendas</h3><span class="live-badge">SEMANAL</span></div>', unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════
    #  RESUMEN EJECUTIVO PARA GERENCIA
    # ══════════════════════════════════════════════════════════════

    # Calcular totales para el resumen
    _vc_n_tiendas = len(alertas_venta_cero_dict)
    _vc_total_skus = sum(p['resumen']['n_skus'] for p in alertas_venta_cero_dict.values())
    _vc_total_capital = sum(p['resumen']['capital_parado_total'] for p in alertas_venta_cero_dict.values())
    _at_n_tiendas = len(alertas_tienda_dict)
    _at_total_items = sum(p['resumen']['n_items'] for p in alertas_tienda_dict.values())
    _at_total_capital = sum(p['resumen']['capital_parado_sol'] for p in alertas_tienda_dict.values())

    st.markdown(f"""<div style="background:{SLATE_50}; border:1px solid {SLATE_200}; border-radius:14px; padding:18px 22px; margin-bottom:16px;">
    <span style="font-weight:700; color:{SLATE_900}; font-size:1rem;">Resumen Ejecutivo — Criterios de alertas a tiendas</span>
    <p style="color:{SLATE_700}; font-size:0.88rem; margin:10px 0 6px 0;">
    Se generan <strong>dos tipos de alerta</strong> semanales para el personal de piso:
    </p>
    <div style="display:flex; gap:16px; flex-wrap:wrap;">
    <div style="flex:1; min-width:280px; background:white; border-radius:10px; padding:14px; border-left:4px solid {STATUS_MUERTO};">
    <strong style="color:{SLATE_900};">1. Productos con venta cero</strong><br>
    <span style="font-size:0.84rem; color:{SLATE_700};">
    SKUs con stock a costo &ge; S/ 1,000 que no vendieron la semana pasada.<br>
    <strong>Acción:</strong> Revisar exhibición del producto y comunicación de precio (si tiene descuento).<br>
    <strong>Alcance:</strong> {_vc_n_tiendas} tiendas · {_vc_total_skus:,} alertas · S/ {_vc_total_capital:,.0f} capital parado.<br>
    Top 15 SKUs por marca, ordenados por capital parado.
    </span>
    </div>
    <div style="flex:1; min-width:280px; background:white; border-radius:10px; padding:14px; border-left:4px solid {STATUS_SOBRESTOCK};">
    <strong style="color:{SLATE_900};">2. Productos con sobrestock</strong><br>
    <span style="font-size:0.84rem; color:{SLATE_700};">
    SKUs con cobertura &ge; {params.get('alertas_tienda_cob_min', 16)} semanas y edad &ge; {params.get('alertas_tienda_edad_min', 2)} semanas.<br>
    <strong>Acción:</strong> Revisar exhibición + comunicación de precio según tipo descuento (MD1/PTR).<br>
    <strong>Alcance:</strong> {_at_n_tiendas} tiendas · {_at_total_items:,} alertas · S/ {_at_total_capital:,.0f} capital inmovilizado.<br>
    Top {params.get('alertas_tienda_top_n', 30)} SKUs por tienda, ordenados por capital parado.
    </span>
    </div>
    </div>
    </div>""", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════
    #  ALERTA 1: VENTA CERO
    # ══════════════════════════════════════════════════════════════

    st.markdown("#### 🔴 Productos con Venta Cero la Semana Pasada")
    st.caption("SKUs con stock a costo ≥ S/ 1,000 que no vendieron. Ordenados por capital parado. Top 15 por marca×tienda.")

    if alertas_venta_cero_dict:
        # Tabla resumen por tienda
        _vc_rows = []
        for _t_name, _t_payload in alertas_venta_cero_dict.items():
            _r = _t_payload['resumen']
            _marcas_top = list(_t_payload.get('por_marca', {}).keys())[:5]
            _vc_rows.append({
                "Tienda": _t_name,
                "SKUs": _r['n_skus'],
                "Marcas": _r['n_marcas'],
                "Capital Parado S/": _r['capital_parado_total'],
                "Marcas Principales": ", ".join(_marcas_top) + ("..." if len(_t_payload.get('por_marca', {})) > 5 else ""),
            })
        _vc_df = pd.DataFrame(_vc_rows).sort_values("Capital Parado S/", ascending=False)

        st.markdown(f"""<div style="background:#FEF2F2; border-left:4px solid {STATUS_CRITICO}; padding:10px 14px; border-radius:10px; margin-bottom:10px;">
        <strong style="color:{STATUS_CRITICO};">⚠️ {_vc_n_tiendas} tiendas con SKUs sin venta</strong>
        <span style="color:{SLATE_500}; font-size:0.82em;"> &nbsp;·&nbsp; Capital parado total: S/ {_vc_total_capital:,.0f}</span>
        </div>""", unsafe_allow_html=True)

        st.dataframe(
            _vc_df.style.format({"Capital Parado S/": "S/ {:,.0f}"}),
            use_container_width=True, hide_index=True,
        )

        # Botón de descarga del detalle completo
        _vc_all_rows = []
        for _t_name, _t_payload in alertas_venta_cero_dict.items():
            for _marca, _mdata in _t_payload.get('por_marca', {}).items():
                for _it in _mdata.get('items', []):
                    _it_copy = dict(_it)
                    _it_copy['tienda'] = _t_name
                    _vc_all_rows.append(_it_copy)
        if _vc_all_rows:
            _vc_all_df = pd.DataFrame(_vc_all_rows)
            _vc_export_cols = ["tienda", "sku", "nombre", "marca", "categoria", "stock_total",
                               "stock_valor_costo", "edad_semanas", "estado", "pct_descuento", "mensaje"]
            _vc_export_cols = [c for c in _vc_export_cols if c in _vc_all_df.columns]
            _vc_csv = _vc_all_df[_vc_export_cols].to_csv(index=False).encode("utf-8")
            st.download_button(
                "📥 Descargar detalle venta cero (CSV)",
                data=_vc_csv,
                file_name="detalle_venta_cero.csv",
                mime="text/csv",
                key="download_venta_cero",
            )

        # ── WhatsApp venta cero ──
        _vc_tiendas_con_items = [t for t, p in alertas_venta_cero_dict.items() if p.get('resumen', {}).get('n_skus', 0) > 0]
        if _vc_tiendas_con_items:
            with st.expander(f"💬 Mensajes WhatsApp — Venta Cero ({len(_vc_tiendas_con_items)} tiendas)", expanded=False):
                _vc_tienda_wa = st.selectbox(
                    "Selecciona tienda",
                    options=sorted(_vc_tiendas_con_items),
                    key="briefing_vc_wa_tienda",
                )
                _vc_wa_txt = R_at.render_whatsapp_venta_cero(alertas_venta_cero_dict[_vc_tienda_wa])
                st.code(_vc_wa_txt, language=None)

                _vc_b1, _vc_b2 = st.columns(2)
                with _vc_b1:
                    _vc_fecha_slug = pd.Timestamp.now().strftime('%Y-%m-%d')
                    _vc_tienda_slug = (_vc_tienda_wa.replace(' ', '_').replace('/', '_').replace('ñ', 'n'))
                    st.download_button(
                        "💬 Descargar WhatsApp (.txt)",
                        data=_vc_wa_txt,
                        file_name=f"{_vc_fecha_slug}_VentaCero_{_vc_tienda_slug}.txt",
                        mime="text/plain",
                        use_container_width=True,
                        key="download_vc_wa_txt",
                    )
                with _vc_b2:
                    _vc_zip = R_at.render_batch_zip_venta_cero(alertas_venta_cero_dict)
                    st.download_button(
                        f"📦 ZIP todas las tiendas ({len(_vc_tiendas_con_items)})",
                        data=_vc_zip,
                        file_name=f"{_vc_fecha_slug}_VentaCero_Todas.zip",
                        mime="application/zip",
                        use_container_width=True,
                        key="download_vc_wa_zip",
                    )
    else:
        st.success("✅ No hay SKUs relevantes con venta cero esta semana.")

    st.markdown("---")

    # ══════════════════════════════════════════════════════════════
    #  ALERTA 2: COBERTURA ALTA (sobrestock)
    # ══════════════════════════════════════════════════════════════

    st.markdown("#### 🟠 Productos con Sobrestock / Cobertura Alta")
    st.caption("Productos con cobertura alta que requieren revisión de exhibición y/o precio.")

    if alertas_tienda_dict:
        _at_rows = []
        for _t_name, _t_payload in alertas_tienda_dict.items():
            _t_res = _t_payload['resumen']
            _t_items = _t_payload.get('items', [])
            _t_marcas = set()
            for _it in _t_items:
                _m = _it.get('marca', '')
                if _m:
                    _t_marcas.add(_m)
            _at_rows.append({
                "Tienda": _t_name,
                "Productos": _t_res['n_items'],
                "Capital Parado S/": _t_res['capital_parado_sol'],
                "Con Dscto": _t_res['n_con_descuento'],
                "Marcas": ", ".join(sorted(_t_marcas)[:5]) + ("..." if len(_t_marcas) > 5 else ""),
            })
        _at_df = pd.DataFrame(_at_rows).sort_values("Capital Parado S/", ascending=False)

        st.markdown(f"""<div style="background:#FFF7ED; border-left:4px solid {STATUS_SOBRESTOCK}; padding:10px 14px; border-radius:10px; margin-bottom:10px;">
        <strong style="color:{STATUS_SOBRESTOCK};">📲 {_at_n_tiendas} tiendas con alertas de sobrestock</strong>
        <span style="color:{SLATE_500}; font-size:0.82em;"> &nbsp;·&nbsp; Capital inmovilizado total: S/ {_at_total_capital:,.0f}</span>
        </div>""", unsafe_allow_html=True)

        st.dataframe(
            _at_df.style.format({"Capital Parado S/": "S/ {:,.0f}"}),
            use_container_width=True, hide_index=True,
        )

        # Botón de descarga del detalle completo
        _at_all_rows = []
        for _t_name, _t_payload in alertas_tienda_dict.items():
            for _it in _t_payload.get('items', []):
                _it_copy = dict(_it)
                _it_copy['tienda'] = _t_name
                _at_all_rows.append(_it_copy)
        if _at_all_rows:
            _at_all_df = pd.DataFrame(_at_all_rows)
            _at_export_cols = ["tienda", "sku", "nombre", "marca", "categoria",
                               "stock_actual", "cobertura_sem", "capital_parado_sol", "pct_descuento"]
            _at_export_cols = [c for c in _at_export_cols if c in _at_all_df.columns]
            _at_csv = _at_all_df[_at_export_cols].to_csv(index=False).encode("utf-8")
            st.download_button(
                "📥 Descargar detalle sobrestock (CSV)",
                data=_at_csv,
                file_name="detalle_sobrestock.csv",
                mime="text/csv",
                key="download_sobrestock",
            )

        # ── WhatsApp sobrestock ──
        _at_tiendas_con_items = [t for t, p in alertas_tienda_dict.items() if p.get('resumen', {}).get('n_items', 0) > 0]
        if _at_tiendas_con_items:
            with st.expander(f"💬 Mensajes WhatsApp — Sobrestock ({len(_at_tiendas_con_items)} tiendas)", expanded=False):
                _at_tienda_wa = st.selectbox(
                    "Selecciona tienda",
                    options=sorted(_at_tiendas_con_items),
                    key="briefing_at_wa_tienda",
                )
                _at_wa_txt = R_at.render_whatsapp(alertas_tienda_dict[_at_tienda_wa])
                st.code(_at_wa_txt, language=None)

                _at_b1, _at_b2 = st.columns(2)
                with _at_b1:
                    _at_fecha_slug = pd.Timestamp.now().strftime('%Y-%m-%d')
                    _at_tienda_slug = (_at_tienda_wa.replace(' ', '_').replace('/', '_').replace('ñ', 'n'))
                    st.download_button(
                        "💬 Descargar WhatsApp (.txt)",
                        data=_at_wa_txt,
                        file_name=f"{_at_fecha_slug}_Sobrestock_{_at_tienda_slug}.txt",
                        mime="text/plain",
                        use_container_width=True,
                        key="download_at_wa_txt",
                    )
                with _at_b2:
                    _at_zip = R_at.render_batch_zip(alertas_tienda_dict)
                    st.download_button(
                        f"📦 ZIP todas las tiendas ({len(_at_tiendas_con_items)})",
                        data=_at_zip,
                        file_name=f"{_at_fecha_slug}_Sobrestock_Todas.zip",
                        mime="application/zip",
                        use_container_width=True,
                        key="download_at_wa_zip",
                    )
    else:
        st.info("No se generaron alertas de sobrestock para tiendas en este análisis.")

    # ── Briefing analítico (expandible) ──
    with st.expander("📊 Ver briefing analítico completo", expanded=False):
        def _briefing_color(prioridad):
            if prioridad <= 1: return "#FEF2F2", STATUS_CRITICO
            if prioridad <= 2: return "#FFF7ED", STATUS_SOBRESTOCK
            if prioridad <= 3: return TEAL_50, TEAL_600
            if prioridad <= 5: return SLATE_50, SLATE_500
            return "#ECFDF5", STATUS_OPTIMO

        for idx, item in enumerate(briefing_items):
            bg, border = _briefing_color(item['prioridad'])

            st.markdown(
                f"""<div style="background:{bg}; border-left:4px solid {border}; padding:10px 14px; border-radius:6px; margin-bottom:2px;">
                <strong style="color:{border};">{item['icono']} {item['titulo']}</strong><br>
                <span style="font-size:0.92em; color:#333333;">{item['mensaje']}</span>
                </div>""",
                unsafe_allow_html=True
            )

        # Tablas del briefing dentro del expander
        if 'tiendas_cobertura' in briefing_tablas:
            st.markdown("**Top tiendas por cobertura:**")
            st.dataframe(briefing_tablas['tiendas_cobertura'],
                         use_container_width=True, hide_index=True)

        if 'sell_through_marca' in briefing_tablas:
            st.markdown("**Sell-through por marca:**")
            st.dataframe(
                briefing_tablas['sell_through_marca'].style.format({
                    'ST %': '{:.1f}%', 'Vta S/ 4sem': 'S/ {:,.0f}',
                }, na_rep='—'),
                use_container_width=True, hide_index=True,
            )

        if 'pareto_top20' in briefing_tablas:
            st.markdown("**Top 20 SKUs (80% de venta):**")
            st.dataframe(
                briefing_tablas['pareto_top20'].style.format({
                    'Vta S/ 4sem': 'S/ {:,.0f}', '% Acum': '{:.1f}%',
                }, na_rep='—'),
                use_container_width=True, hide_index=True,
            )




elif nav_page == "📦 Ventana de Compra":
    st.markdown(f'<div class="section-header"><h3>📦 Ventana de Compra</h3><span class="live-badge">EMBARQUES</span></div>', unsafe_allow_html=True)
    st.caption("Análisis de performance por ventana de compra (embarques). Sell-through, cobertura y recomendaciones por ventana.")

    if por_ventana.empty:
        st.info("No hay datos de embarques/ventana de compra. Sube un archivo con la columna Embarque en Base Profundidad.")
    else:
        # ── KPIs ──
        _ek = embarque_kpis
        _ek_c1, _ek_c2, _ek_c3, _ek_c4 = st.columns(4)
        with _ek_c1:
            st.markdown(f"""<div style="background:{SLATE_50}; border-radius:12px; padding:16px 20px; border-left:4px solid {TEAL_600};">
                <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">Ventanas activas</div>
                <div style="font-size:1.6rem; font-weight:700; color:{TEAL_700};">{_ek.get('n_ventanas', 0)}</div>
            </div>""", unsafe_allow_html=True)
        with _ek_c2:
            st.markdown(f"""<div style="background:{SLATE_50}; border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_OPTIMO};">
                <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">Mejor ventana</div>
                <div style="font-size:1.6rem; font-weight:700; color:{STATUS_OPTIMO};">{_ek.get('mejor_ventana', '—')}</div>
                <div style="font-size:0.7rem; color:{SLATE_500};">Retorno/sem: {_ek.get('mejor_retorno', 0):.2%}</div>
            </div>""", unsafe_allow_html=True)
        with _ek_c3:
            st.markdown(f"""<div style="background:{SLATE_50}; border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_CRITICO};">
                <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">Peor ventana</div>
                <div style="font-size:1.6rem; font-weight:700; color:{STATUS_CRITICO};">{_ek.get('peor_ventana', '—')}</div>
                <div style="font-size:0.7rem; color:{SLATE_500};">Retorno/sem: {_ek.get('peor_retorno', 0):.2%}</div>
            </div>""", unsafe_allow_html=True)
        with _ek_c4:
            st.markdown(f"""<div style="background:{SLATE_50}; border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_PRECRITICO};">
                <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">Ventanas en rojo</div>
                <div style="font-size:1.6rem; font-weight:700; color:{STATUS_PRECRITICO};">{_ek.get('n_rojos', 0)}</div>
            </div>""", unsafe_allow_html=True)

        st.markdown("---")

        # ── Tabla resumen por ventana ──
        _sem_colors = {"verde": STATUS_OPTIMO, "amarillo": STATUS_ALTO, "rojo": STATUS_CRITICO}
        _sem_icons = {"verde": "🟢", "amarillo": "🟡", "rojo": "🔴"}

        for _, vrow in por_ventana.iterrows():
            _v = vrow["ventana"]
            _sem = vrow.get("semaforo", "verde")
            _icon = _sem_icons.get(_sem, "⚪")
            _border_color = _sem_colors.get(_sem, SLATE_400)

            st.markdown(f"""<div style="background:white; border:1px solid {SLATE_200}; border-left:4px solid {_border_color}; border-radius:10px; padding:12px 16px; margin-bottom:6px;">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap;">
                <div>
                    <strong style="color:{SLATE_900}; font-size:1rem;">{_icon} Ventana {_v}</strong>
                    <span style="color:{SLATE_500}; font-size:0.82em;"> &nbsp;·&nbsp; {vrow['label']}</span>
                </div>
                <div style="display:flex; gap:20px; font-size:0.85em;">
                    <span><strong>ST:</strong> {vrow['sell_through']:.0%}</span>
                    <span><strong>Cob:</strong> {vrow['cobertura_prom']:.1f} sem</span>
                    <span><strong>Capital:</strong> S/ {vrow['capital']:,.0f}</span>
                    <span><strong>SKUs:</strong> {vrow['n_skus']:,}</span>
                    <span><strong>Dscto:</strong> {vrow['descuento_prom']:.0%}</span>
                </div>
            </div>
            </div>""", unsafe_allow_html=True)

            # Recomendación
            _rec = embarque_recs.get(_v, "")
            if _rec:
                with st.expander(f"Ver recomendación — Ventana {_v}", expanded=False):
                    st.markdown(_rec)

            # Top 5 SKUs problemáticos
            _top_df = embarque_top.get(_v, pd.DataFrame())
            if not _top_df.empty:
                with st.expander(f"Top 5 SKUs problemáticos — Ventana {_v}", expanded=False):
                    _top_disp = _top_df.rename(columns={
                        "sku": "SKU", "nombre": "Producto", "marca": "Marca", "tienda": "Tienda",
                        "stock_total": "Stock Uds", "stock_valor_costo": "Capital S/",
                        "sell_through": "ST %", "cobertura_sem": "Cob (sem)",
                        "pct_descuento": "Dscto", "edad_semanas": "Edad (sem)",
                    })
                    _top_show = [c for c in ["SKU", "Producto", "Marca", "Tienda", "Stock Uds", "Capital S/", "ST %", "Cob (sem)", "Dscto", "Edad (sem)"] if c in _top_disp.columns]
                    _top_fmt = {}
                    if "Capital S/" in _top_show: _top_fmt["Capital S/"] = "S/ {:,.0f}"
                    if "ST %" in _top_show: _top_fmt["ST %"] = "{:.0%}"
                    if "Cob (sem)" in _top_show: _top_fmt["Cob (sem)"] = "{:.1f}"
                    if "Dscto" in _top_show: _top_fmt["Dscto"] = "{:.0%}"
                    st.dataframe(
                        _top_disp[_top_show].style.format(_top_fmt, na_rep="—"),
                        use_container_width=True, hide_index=True,
                    )

        # ── Drill-down: detalle completo por ventana ──
        st.markdown("---")
        st.markdown("##### Detalle por Ventana de Compra")

        if not df_embarque.empty:
            _vc_ventanas = sorted(df_embarque["ventana_compra"].unique().tolist())
            _vc_sel = st.selectbox("Seleccionar ventana", _vc_ventanas, key="vc_drill_ventana")
            _vc_det = df_embarque[df_embarque["ventana_compra"] == _vc_sel].copy()

            _vc_show_cols = [c for c in ["sku", "nombre", "marca", "tienda", "stock_total",
                                          "stock_valor_costo", "sell_through", "cobertura_sem",
                                          "pct_descuento", "edad_semanas"] if c in _vc_det.columns]
            _vc_det_disp = _vc_det[_vc_show_cols].sort_values("stock_valor_costo", ascending=False).head(50)
            _vc_det_disp = _vc_det_disp.rename(columns={
                "sku": "SKU", "nombre": "Producto", "marca": "Marca", "tienda": "Tienda",
                "stock_total": "Stock Uds", "stock_valor_costo": "Capital S/",
                "sell_through": "ST %", "cobertura_sem": "Cob (sem)",
                "pct_descuento": "Dscto", "edad_semanas": "Edad (sem)",
            })
            _vc_fmt = {}
            if "Capital S/" in _vc_det_disp.columns: _vc_fmt["Capital S/"] = "S/ {:,.0f}"
            if "ST %" in _vc_det_disp.columns: _vc_fmt["ST %"] = "{:.0%}"
            if "Cob (sem)" in _vc_det_disp.columns: _vc_fmt["Cob (sem)"] = "{:.1f}"
            if "Dscto" in _vc_det_disp.columns: _vc_fmt["Dscto"] = "{:.0%}"
            st.dataframe(
                _vc_det_disp.style.format(_vc_fmt, na_rep="—"),
                use_container_width=True, hide_index=True, height=500,
            )
            st.caption(f"Top 50 combos por capital invertido en ventana {_vc_sel}. Total: {len(_vc_det):,} combos.")


# ══════════════════════════════════════════════════════════════
#  PREDISTRIBUCIÓN (Marcas Propias)
# ══════════════════════════════════════════════════════════════

elif nav_page == "🚚 Predistribución":
    st.markdown(f'<div class="section-header"><h3>🚚 Monitor de Predistribución</h3><span class="live-badge">MARCAS PROPIAS</span></div>', unsafe_allow_html=True)
    st.caption("Detecta productos propios retenidos en CD sin distribuir y gaps de distribución vs la matriz configurable de tiendas por línea.")

    # ── Tab layout ──
    _pd_tab1, _pd_tab2, _pd_tab3 = st.tabs([
        "🔴 Retenidos en CD",
        "🟡 Gaps de Distribución",
        "⚙️ Configurar Matriz",
    ])

    # ══════════════ TAB 1: Retenidos en CD ══════════════
    with _pd_tab1:
        st.markdown("#### Productos 100% en CD — sin stock ni tránsito en ninguna tienda")
        st.caption("SKUs de marcas propias con todo el inventario en Centro de Distribución y nada enviado a tiendas. Requieren predistribución urgente.")

        _n_ret = predist_kpis.get('n_retenidos', 0)
        _uds_ret = predist_kpis.get('uds_retenidas_cd', 0)
        _cap_ret = predist_kpis.get('capital_retenido', 0)

        # KPIs
        _r_c1, _r_c2, _r_c3 = st.columns(3)
        with _r_c1:
            _ret_color = STATUS_CRITICO if _n_ret > 0 else STATUS_OPTIMO
            st.markdown(f"""<div style="background:{SLATE_50}; border-radius:12px; padding:16px 20px; border-left:4px solid {_ret_color};">
                <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">SKUs retenidos en CD</div>
                <div style="font-size:1.6rem; font-weight:700; color:{_ret_color};">{_n_ret:,}</div>
            </div>""", unsafe_allow_html=True)
        with _r_c2:
            st.markdown(f"""<div style="background:{SLATE_50}; border-radius:12px; padding:16px 20px; border-left:4px solid {TEAL_600};">
                <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">Unidades retenidas</div>
                <div style="font-size:1.6rem; font-weight:700; color:{TEAL_700};">{_uds_ret:,}</div>
            </div>""", unsafe_allow_html=True)
        with _r_c3:
            st.markdown(f"""<div style="background:{SLATE_50}; border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_ALTO};">
                <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">Capital retenido</div>
                <div style="font-size:1.6rem; font-weight:700; color:{STATUS_ALTO};">S/ {_cap_ret:,.0f}</div>
            </div>""", unsafe_allow_html=True)

        st.markdown("")

        if df_retenidos_cd.empty:
            st.success("No hay productos 100% retenidos en CD. Todos los SKUs propios tienen alguna distribución a tiendas.")
        else:
            # Tabla de retenidos
            _ret_disp = df_retenidos_cd.copy()
            _ret_disp = _ret_disp.rename(columns={
                'sku': 'SKU', 'nombre': 'Producto', 'marca': 'Marca',
                'categoria': 'Línea', 'stock_cd': 'Stock CD',
                'costo': 'Costo Unit', 'precio_vigente': 'P.Vigente',
                'capital_retenido': 'Capital Retenido S/',
            })
            _ret_fmt = {
                'Costo Unit': 'S/ {:.2f}',
                'P.Vigente': 'S/ {:.2f}',
                'Capital Retenido S/': 'S/ {:,.0f}',
            }
            st.dataframe(
                _ret_disp.style.format(_ret_fmt, na_rep="—"),
                use_container_width=True, hide_index=True, height=400,
            )
            st.caption(f"{_n_ret} SKUs propios con 100% del stock en CD. Capital total: S/ {_cap_ret:,.0f}")

            # Excel download
            _ret_xl_buf = io.BytesIO()
            with pd.ExcelWriter(_ret_xl_buf, engine='openpyxl') as _w:
                df_retenidos_cd.to_excel(_w, sheet_name='Retenidos CD', index=False)
            st.download_button(
                "📥 Descargar Retenidos CD (.xlsx)",
                data=_ret_xl_buf.getvalue(),
                file_name="predist_retenidos_cd.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_retenidos_cd",
            )

    # ══════════════ TAB 2: Gaps de Distribución ══════════════
    with _pd_tab2:
        st.markdown("#### Gaps de distribución vs matriz de tiendas")
        st.caption("SKUs propios que están en algunas tiendas pero faltan en otras según la segmentación configurada por línea de producto.")

        _n_gaps = predist_kpis.get('n_gaps', 0)
        _n_gaps_cd = predist_kpis.get('n_gaps_con_cd', 0)
        _prom_cob = predist_kpis.get('prom_cobertura_dist', 1.0)

        # KPIs
        _g_c1, _g_c2, _g_c3 = st.columns(3)
        with _g_c1:
            _gap_color = STATUS_ALTO if _n_gaps > 0 else STATUS_OPTIMO
            st.markdown(f"""<div style="background:{SLATE_50}; border-radius:12px; padding:16px 20px; border-left:4px solid {_gap_color};">
                <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">SKUs con gaps</div>
                <div style="font-size:1.6rem; font-weight:700; color:{_gap_color};">{_n_gaps:,}</div>
            </div>""", unsafe_allow_html=True)
        with _g_c2:
            st.markdown(f"""<div style="background:{SLATE_50}; border-radius:12px; padding:16px 20px; border-left:4px solid {TEAL_600};">
                <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">Gaps con stock CD</div>
                <div style="font-size:1.6rem; font-weight:700; color:{TEAL_700};">{_n_gaps_cd:,}</div>
                <div style="font-size:0.7rem; color:{SLATE_500};">Accionables (hay stock para enviar)</div>
            </div>""", unsafe_allow_html=True)
        with _g_c3:
            _cob_color = STATUS_OPTIMO if _prom_cob >= 0.8 else (STATUS_ALTO if _prom_cob >= 0.5 else STATUS_CRITICO)
            st.markdown(f"""<div style="background:{SLATE_50}; border-radius:12px; padding:16px 20px; border-left:4px solid {_cob_color};">
                <div style="font-size:0.75rem; color:{SLATE_500}; font-weight:500;">Cobertura distribución prom.</div>
                <div style="font-size:1.6rem; font-weight:700; color:{_cob_color};">{_prom_cob:.0%}</div>
            </div>""", unsafe_allow_html=True)

        st.markdown("")

        if df_gaps_dist.empty:
            st.success("No hay gaps de distribución. Todos los SKUs propios están en las tiendas esperadas según la matriz.")
        else:
            # Filtros
            _gf_c1, _gf_c2, _gf_c3, _gf_c4 = st.columns(4)
            with _gf_c1:
                _gap_lineas = ['Todas'] + sorted(df_gaps_dist['categoria'].unique().tolist())
                _gap_linea_sel = st.selectbox("Filtrar por Línea", _gap_lineas, key="gap_linea_filter")
            with _gf_c2:
                _gap_marcas = ['Todas'] + sorted(df_gaps_dist['marca'].unique().tolist())
                _gap_marca_sel = st.selectbox("Filtrar por Marca", _gap_marcas, key="gap_marca_filter")
            with _gf_c3:
                _gap_solo_cd = st.checkbox("Solo con stock CD > 0", value=True, key="gap_solo_cd")
            with _gf_c4:
                _gap_solo_nuevos = st.checkbox("Solo 0-3 meses", value=True, key="gap_solo_nuevos",
                                               help="Filtrar solo SKUs con edad ≤ 12 semanas (0-3 meses)")

            _gaps_filtered = df_gaps_dist.copy()
            if _gap_linea_sel != 'Todas':
                _gaps_filtered = _gaps_filtered[_gaps_filtered['categoria'] == _gap_linea_sel]
            if _gap_marca_sel != 'Todas':
                _gaps_filtered = _gaps_filtered[_gaps_filtered['marca'] == _gap_marca_sel]
            if _gap_solo_cd:
                _gaps_filtered = _gaps_filtered[_gaps_filtered['stock_cd'] > 0]
            if _gap_solo_nuevos and 'edad_semanas' in _gaps_filtered.columns:
                _gaps_filtered = _gaps_filtered[_gaps_filtered['edad_semanas'] <= 12]

            if _gaps_filtered.empty:
                st.info("No hay gaps con los filtros seleccionados.")
            else:
                # Ordenar por Stock CD descendente
                _gaps_filtered = _gaps_filtered.sort_values('stock_cd', ascending=False)

                _gaps_disp = _gaps_filtered.rename(columns={
                    'sku': 'SKU', 'nombre': 'Producto', 'marca': 'Marca',
                    'categoria': 'Línea', 'edad_semanas': 'Edad (sem)',
                    'stock_cd': 'Stock CD',
                    'costo': 'Costo Unit', 'precio_vigente': 'P.Vigente',
                    'n_tiendas_esperadas': 'Tiendas Esperadas',
                    'n_tiendas_presentes': 'Tiendas Presentes',
                    'n_tiendas_faltantes': 'Tiendas Faltantes',
                    'pct_cobertura_dist': '% Cobertura',
                    'tiendas_faltantes': 'Detalle Tiendas Faltantes',
                })
                _gaps_fmt = {
                    'Costo Unit': 'S/ {:.2f}',
                    'P.Vigente': 'S/ {:.2f}',
                    '% Cobertura': '{:.0%}',
                }
                st.dataframe(
                    _gaps_disp.style.format(_gaps_fmt, na_rep="—"),
                    use_container_width=True, hide_index=True, height=500,
                )
                st.caption(f"{len(_gaps_filtered):,} SKUs con gaps ({_gaps_filtered[_gaps_filtered['stock_cd'] > 0].shape[0]} con stock CD disponible)")

                # Excel download
                _gaps_xl_buf = io.BytesIO()
                with pd.ExcelWriter(_gaps_xl_buf, engine='openpyxl') as _w:
                    _gaps_filtered.to_excel(_w, sheet_name='Gaps Distribución', index=False)
                st.download_button(
                    "📥 Descargar Gaps de Distribución (.xlsx)",
                    data=_gaps_xl_buf.getvalue(),
                    file_name="predist_gaps_distribucion.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_gaps_dist",
                )

    # ══════════════ TAB 3: Configurar Matriz ══════════════
    with _pd_tab3:
        st.markdown("#### Matriz de tiendas por marca")
        st.caption("Define qué tiendas deberían recibir cada marca. Generada desde el archivo de distribución marca×tienda que pasó Franco. Editable manualmente.")

        import json as _json_mod
        _matriz_marca_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config_marca_tiendas.json')

        if os.path.exists(_matriz_marca_path):
            with open(_matriz_marca_path, 'r', encoding='utf-8') as _mf:
                _matriz_marca_actual = _json_mod.load(_mf)
        else:
            _matriz_marca_actual = {}
            st.warning("No se encontró config_marca_tiendas.json. Sube el archivo de distribución marca×tienda para generar la matriz.")

        if _matriz_marca_actual:
            # Resumen de la matriz
            _mat_rows = []
            for _marca, _info in sorted(_matriz_marca_actual.items()):
                _vta = _info.get('venta_total', 0)
                _mat_rows.append({
                    'Marca': _marca,
                    'N° Tiendas': _info.get('n_tiendas', len(_info.get('tiendas', []))),
                    'Venta S/': _vta,
                    'Tiendas': ', '.join(_info.get('tiendas', [])[:5]) + ('...' if len(_info.get('tiendas', [])) > 5 else ''),
                })
            _df_mat = pd.DataFrame(_mat_rows)
            _mat_fmt = {'Venta S/': 'S/ {:,.0f}'}
            st.dataframe(
                _df_mat.style.format(_mat_fmt, na_rep="—"),
                use_container_width=True, hide_index=True,
            )

            # Detalle editable por marca
            st.markdown("---")
            st.markdown("##### Editar tiendas por marca")
            _marcas_disponibles = sorted(_matriz_marca_actual.keys())
            _marca_editar = st.selectbox("Seleccionar marca", _marcas_disponibles, key="mat_editar_marca")

            if _marca_editar:
                _tiendas_marca = _matriz_marca_actual[_marca_editar].get('tiendas', [])
                st.markdown(f"**{_marca_editar}** — {len(_tiendas_marca)} tiendas activas:")

                # Mostrar todas las tiendas posibles y marcar las activas
                from transformar_profundidad import STORE_NAMES as _ALL_STORES
                _todas_tiendas = sorted(set(_ALL_STORES.values()) - {
                    'Tienda Virtual', 'Tienda Virtual PI', 'Ventas Corporativas',
                    'FSF Mac LO', 'Boutique BBB', 'Asia',
                    'Estacional Chiclayo', 'Estacional Trujillo', 'Estacional VES',
                    'Mac Larco', 'Mac Plaza Norte',
                    'Outlet Plaza Norte', 'Outlet San Isidro',
                })

                _tiendas_set = set(_tiendas_marca)
                _nuevas_tiendas = st.multiselect(
                    f"Tiendas para {_marca_editar}",
                    options=_todas_tiendas,
                    default=[t for t in _todas_tiendas if t in _tiendas_set],
                    key=f"mat_tiendas_{_marca_editar}",
                )

                if st.button("💾 Guardar cambios en matriz", key="mat_guardar"):
                    # Reconstruir tiendas_code desde STORE_NAMES inverso
                    _inv_store = {v: k for k, v in _ALL_STORES.items()}
                    _nuevos_codes = sorted([_inv_store[t] for t in _nuevas_tiendas if t in _inv_store])
                    _matriz_marca_actual[_marca_editar] = {
                        'tiendas': sorted(_nuevas_tiendas),
                        'tiendas_code': _nuevos_codes,
                        'n_tiendas': len(_nuevas_tiendas),
                        'venta_total': _matriz_marca_actual[_marca_editar].get('venta_total', 0),
                    }
                    with open(_matriz_marca_path, 'w', encoding='utf-8') as _mf:
                        _json_mod.dump(_matriz_marca_actual, _mf, ensure_ascii=False, indent=2)
                    st.success(f"Matriz actualizada: {_marca_editar} → {len(_nuevas_tiendas)} tiendas. Re-corre el análisis para ver el efecto.")

        # ── Editor por LÍNEA (fallback) ──
        st.markdown("---")
        st.markdown("#### Matriz de tiendas por línea (fallback)")
        st.caption("Se usa cuando una marca no tiene asignación propia. Define qué tiendas deberían recibir cada línea de producto.")

        _matriz_linea_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config_matriz_tiendas.json')

        if os.path.exists(_matriz_linea_path):
            with open(_matriz_linea_path, 'r', encoding='utf-8') as _mlf:
                _matriz_linea_actual = _json_mod.load(_mlf)
        else:
            _matriz_linea_actual = {}
            st.warning("No se encontró config_matriz_tiendas.json.")

        if _matriz_linea_actual:
            # Resumen de la matriz por línea
            _mat_lin_rows = []
            for _linea_k, _info_l in sorted(_matriz_linea_actual.items()):
                _mat_lin_rows.append({
                    'Línea': _linea_k,
                    'N° Tiendas': _info_l.get('n_tiendas', len(_info_l.get('tiendas', []))),
                    'Tiendas': ', '.join(_info_l.get('tiendas', [])[:5]) + ('...' if len(_info_l.get('tiendas', [])) > 5 else ''),
                })
            _df_mat_lin = pd.DataFrame(_mat_lin_rows)
            st.dataframe(_df_mat_lin, use_container_width=True, hide_index=True)

            # Detalle editable por línea
            st.markdown("---")
            st.markdown("##### Editar tiendas por línea")
            _lineas_disponibles = sorted(_matriz_linea_actual.keys())
            _linea_editar = st.selectbox("Seleccionar línea", _lineas_disponibles, key="mat_editar_linea")

            if _linea_editar:
                _tiendas_linea = _matriz_linea_actual[_linea_editar].get('tiendas', [])
                st.markdown(f"**{_linea_editar}** — {len(_tiendas_linea)} tiendas activas:")

                from transformar_profundidad import STORE_NAMES as _ALL_STORES_L
                _todas_tiendas_l = sorted(set(_ALL_STORES_L.values()) - {
                    'Tienda Virtual', 'Tienda Virtual PI', 'Ventas Corporativas',
                    'FSF Mac LO', 'Boutique BBB', 'Asia',
                    'Estacional Chiclayo', 'Estacional Trujillo', 'Estacional VES',
                    'Mac Larco', 'Mac Plaza Norte',
                    'Outlet Plaza Norte', 'Outlet San Isidro',
                })

                _tiendas_lin_set = set(_tiendas_linea)
                _nuevas_tiendas_l = st.multiselect(
                    f"Tiendas para {_linea_editar}",
                    options=_todas_tiendas_l,
                    default=[t for t in _todas_tiendas_l if t in _tiendas_lin_set],
                    key=f"mat_tiendas_linea_{_linea_editar}",
                )

                if st.button("💾 Guardar cambios en matriz de líneas", key="mat_guardar_linea"):
                    _inv_store_l = {v: k for k, v in _ALL_STORES_L.items()}
                    _nuevos_codes_l = sorted([_inv_store_l[t] for t in _nuevas_tiendas_l if t in _inv_store_l])
                    _matriz_linea_actual[_linea_editar] = {
                        'tiendas': sorted(_nuevas_tiendas_l),
                        'tiendas_code': _nuevos_codes_l,
                        'n_tiendas': len(_nuevas_tiendas_l),
                    }
                    with open(_matriz_linea_path, 'w', encoding='utf-8') as _mlf:
                        _json_mod.dump(_matriz_linea_actual, _mlf, ensure_ascii=False, indent=2)
                    st.success(f"Matriz actualizada: {_linea_editar} → {len(_nuevas_tiendas_l)} tiendas. Re-corre el análisis para ver el efecto.")


