"""
Capi — Herramienta de Gestión Retail
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

# Snapshots Engine — histórico semanal (Prompt B)
try:
    import snapshots_engine
    _HAS_SNAPSHOTS = True
except ImportError:
    _HAS_SNAPSHOTS = False

# Auto-cargar bases históricas como snapshots en cold start
if _HAS_SNAPSHOTS and not st.session_state.get("_snapshots_initialized"):
    try:
        _existing_weeks = snapshots_engine.list_available_weeks()
        if len(_existing_weeks) < 2:
            _hist_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data2", "bases antiguas")
            if os.path.isdir(_hist_dir):
                snapshots_engine.loader.load_all_bases_antiguas(_hist_dir, force=False)
        st.session_state["_snapshots_initialized"] = True
    except Exception:
        st.session_state["_snapshots_initialized"] = True

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

# En Streamlit Cloud la API key vive en st.secrets, no en variables de entorno.
# La propagamos a os.environ para que chat_engine y agente_terceras (que usan
# os.getenv) la encuentren igual que en local con .env.
try:
    if "ANTHROPIC_API_KEY" in st.secrets and not os.getenv("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = str(st.secrets["ANTHROPIC_API_KEY"])
except Exception:
    pass

import chat_engine
import agente_terceras

# ══════════════════════════════════════════════════════════════
#  CONFIG DE PÁGINA
# ══════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Capi — Gestión Retail",
    page_icon="📦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Paleta de colores Capi (Clean Corporate: navy + light) ──
TEAL_600 = "#1B4F72"     # Navy-600 — primary accent
TEAL_700 = "#154360"     # Navy-700 — hover/active
TEAL_50  = "#EBF2FA"     # Navy-50  — light accent bg
SLATE_900 = "#1F2937"    # Gray-800 — headings, strong text
SLATE_800 = "#374151"    # Gray-700 — secondary strong
SLATE_700 = "#4B5563"    # Gray-600 — medium text
SLATE_500 = "#6B7280"    # Gray-500 — muted text
SLATE_400 = "#9CA3AF"    # Gray-400 — tertiary text
SLATE_200 = "#E5E7EB"    # Gray-200 — borders
SLATE_100 = "#F3F4F6"    # Gray-100 — surface
SLATE_50  = "#F9FAFB"    # Near-white — page bg
STATUS_CRITICO    = "#EF4444"
STATUS_PRECRITICO = "#F97316"
STATUS_OPTIMO     = "#10B981"
STATUS_ALTO       = "#F59E0B"
STATUS_SOBRESTOCK = "#E11D48"
STATUS_LIQUIDAR   = "#EC4899"
STATUS_NUEVO_SV   = "#94A3B8"
STATUS_DORMIDO    = "#78716C"
STATUS_MUERTO     = "#374151"
# Sprint 1 Capi — nombres nuevos de la taxonomía (Prompt A1)
STATUS_QUIEBRE     = STATUS_CRITICO       # alias semántico
STATUS_BAJA        = STATUS_PRECRITICO    # alias semántico
STATUS_ESTANCADO   = "#475569"            # gris medio (entre SOBRESTOCK y MUERTO)
STATUS_LANZAMIENTO = "#60A5FA"            # azul claro (positivo, "está en rampa")

# ── Tema (corporate light — sin toggle) ──
if "theme_mode" not in st.session_state:
    st.session_state["theme_mode"] = "light"

_IS_LIGHT = True  # Corporate: siempre light

# Variables Python para contextos donde CSS vars no funcionan (Plotly, etc.)
TH_TEXT_PY = SLATE_900
TH_TEXT2_PY = SLATE_500
TH_BG_CARD_PY = "#FFFFFF"
TH_BG_SURFACE_PY = SLATE_50
TH_BORDER_PY = SLATE_200
TH_PLOT_BG = "rgba(0,0,0,0)"
TH_PLOT_GRID = "#E5E7EB"

# CSS custom properties — corporate light
_CSS_VARS = f"""
    :root {{
        --capi-bg: #F9FAFB;
        --capi-bg-card: #FFFFFF;
        --capi-bg-surface: #F3F4F6;
        --capi-text: #1F2937;
        --capi-text2: #6B7280;
        --capi-text3: #9CA3AF;
        --capi-border: #E5E7EB;
        --capi-shadow: rgba(0,0,0,0.04);
        --capi-hover-shadow: rgba(0,0,0,0.08);
        --capi-tab-bg: #F3F4F6;
        --capi-tab-active: #FFFFFF;
        --capi-tab-text: #6B7280;
        --capi-tab-active-text: {TEAL_600};
        --capi-accent: {TEAL_600};
        --capi-accent-light: {TEAL_50};
    }}
"""

# Dark mode override no longer needed — corporate light only
_DARK_BG_OVERRIDE = ""

st.markdown(f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap');

    {_CSS_VARS}

    /* ── Global ──────────────────────────────── */
    html, body, [class*="css"] {{
        font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    }}
    .stApp, .main, [data-testid="stAppViewContainer"] {{
        background-color: #F9FAFB !important;
    }}
    [data-testid="stHeader"] {{
        background-color: #F9FAFB !important;
    }}
    .main .block-container {{
        padding-top: 1.5rem;
        max-width: 1400px;
    }}
    h1, h2, h3, h4, h5 {{
        font-family: 'Plus Jakarta Sans', sans-serif;
        color: var(--capi-text);
    }}

    /* ── Header (clean corporate) ──────────── */
    .main-header {{
        background: #FFFFFF;
        padding: 1.2rem 2rem;
        border-radius: 10px;
        margin-bottom: 1.2rem;
        display: flex;
        align-items: center;
        justify-content: space-between;
        border: 1px solid {SLATE_200};
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }}
    .main-header h1 {{
        color: {SLATE_900}; margin: 0; font-size: 1.3rem; font-weight: 700;
        letter-spacing: -0.02em;
    }}
    .main-header h1 span {{ color: {TEAL_600}; }}
    .main-header p {{
        color: {SLATE_500}; margin: 0;
        font-size: 0.78rem; font-weight: 400;
    }}

    /* ── Chat input (clean corporate) ───────── */
    [data-testid="stTextInput"] input[aria-label="Pregunta a Capi"] {{
        background: #FFFFFF !important;
        border: 1px solid {SLATE_200} !important;
        border-radius: 10px !important;
        color: {SLATE_900} !important;
        padding: 14px 18px !important;
        font-size: 0.92rem !important;
        height: auto !important;
    }}
    [data-testid="stTextInput"] input[aria-label="Pregunta a Capi"]::placeholder {{
        color: {SLATE_400} !important;
    }}
    [data-testid="stTextInput"] input[aria-label="Pregunta a Capi"]:focus {{
        border-color: {TEAL_600} !important;
        box-shadow: 0 0 0 2px rgba(27,79,114,0.12) !important;
    }}

    /* ── Right Chat Column (corporate) ──────── */
    div[data-testid="stHorizontalBlock"]:has(.chat-panel-marker) {{
        align-items: flex-start !important;
    }}
    div[data-testid="stColumn"]:has(.chat-panel-marker) {{
        position: sticky;
        top: 0;
        max-height: 100vh;
        min-width: 380px;
        overflow-y: auto;
        background: #FFFFFF !important;
        border-left: 1px solid {SLATE_200};
        border-radius: 0;
        padding: 16px 20px !important;
    }}
    div[data-testid="stColumn"]:has(.chat-panel-marker) .stTextInput input {{
        background: {SLATE_50} !important;
        border: 1px solid {SLATE_200} !important;
        color: {SLATE_900} !important;
        border-radius: 8px !important;
        padding: 12px 16px !important;
        font-size: 0.9rem !important;
    }}
    div[data-testid="stColumn"]:has(.chat-panel-marker) .stTextInput input::placeholder {{
        color: {SLATE_400} !important;
    }}
    div[data-testid="stColumn"]:has(.chat-panel-marker) .stTextInput input:focus {{
        border-color: {TEAL_600} !important;
        box-shadow: 0 0 0 2px rgba(27,79,114,0.1) !important;
    }}
    div[data-testid="stColumn"]:has(.chat-panel-marker) .stButton > button {{
        background: {SLATE_50} !important;
        color: {SLATE_500} !important;
        border: 1px solid {SLATE_200} !important;
        font-size: 0.75em !important;
        padding: 4px 8px !important;
        border-radius: 6px !important;
        min-height: 0 !important;
    }}
    div[data-testid="stColumn"]:has(.chat-panel-marker) .stButton > button:hover {{
        background: {TEAL_50} !important;
        color: {TEAL_600} !important;
        border-color: {TEAL_600} !important;
    }}
    div[data-testid="stColumn"]:has(.chat-panel-marker) .stExpander {{
        border-color: {SLATE_200} !important;
    }}
    div[data-testid="stColumn"]:has(.chat-panel-marker) .stExpander summary {{
        color: {SLATE_500} !important;
        font-size: 0.8rem !important;
    }}
    div[data-testid="stColumn"]:has(.chat-panel-marker) .stSpinner > div {{
        color: {SLATE_500} !important;
    }}
    div[data-testid="stColumn"]:has(.chat-panel-marker) .stAlert {{
        background: {SLATE_50} !important;
        color: {SLATE_700} !important;
        border-color: {SLATE_200} !important;
    }}

    /* ── Chat Panel (corporate light) ────────── */
    .nansen-chat-panel {{
        background: #FFFFFF;
        border: 1px solid {SLATE_200};
        border-radius: 10px;
        overflow: hidden;
        margin-top: 0.5rem;
        margin-bottom: 1rem;
    }}
    .nansen-chat-header {{
        background: {SLATE_50};
        border-bottom: 1px solid {SLATE_200};
        padding: 14px 20px;
        display: flex;
        align-items: center;
        gap: 10px;
    }}
    .nansen-chat-header .chat-logo {{
        width: 28px; height: 28px;
        background: {TEAL_600};
        border-radius: 6px;
        display: flex; align-items: center; justify-content: center;
        font-size: 0.8rem; color: white; font-weight: 700;
        flex-shrink: 0;
    }}
    .nansen-chat-header .chat-title {{
        font-size: 0.88rem; font-weight: 600; color: {SLATE_900};
    }}
    .nansen-chat-header .chat-badge {{
        background: {TEAL_50};
        color: {TEAL_600};
        font-size: 0.6rem; font-weight: 700;
        padding: 2px 8px; border-radius: 4px;
        letter-spacing: 0.05em;
        text-transform: uppercase;
    }}
    .nansen-chat-header .chat-query-preview {{
        color: {SLATE_400};
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
    .chat-msg-user {{
        display: flex;
        justify-content: flex-end;
        margin-bottom: 16px;
    }}
    .chat-msg-user .bubble {{
        background: {TEAL_50};
        color: {TEAL_700};
        padding: 10px 16px;
        border-radius: 12px 12px 4px 12px;
        font-size: 0.88rem;
        max-width: 80%;
    }}
    .chat-msg-ai {{
        margin-bottom: 16px;
    }}
    .chat-msg-ai .ai-step {{
        display: inline-flex;
        align-items: center;
        gap: 8px;
        background: {SLATE_100};
        border-radius: 20px;
        padding: 5px 14px;
        font-size: 0.78rem;
        color: {SLATE_500};
        margin-bottom: 12px;
    }}
    .chat-msg-ai .ai-step .check {{
        color: #059669;
        font-size: 0.9rem;
    }}
    .chat-msg-ai h4 {{
        color: {SLATE_900};
        font-size: 1rem;
        font-weight: 700;
        margin: 0 0 10px 0;
    }}
    .chat-msg-ai p {{
        color: {SLATE_700};
        margin: 0 0 10px 0;
        font-size: 0.88rem;
        line-height: 1.7;
    }}
    .chat-msg-ai strong {{
        color: {SLATE_900};
    }}
    .chat-msg-ai .chat-insight {{
        border-top: 1px solid {SLATE_200};
        margin-top: 14px;
        padding-top: 12px;
    }}
    .nansen-chat-footer {{
        border-top: 1px solid {SLATE_200};
        padding: 10px 20px;
        text-align: center;
    }}
    .nansen-chat-footer span {{
        font-size: 0.72rem;
        color: {SLATE_400};
    }}

    /* ── Legacy chat-response ──────────────── */
    .chat-response {{
        background: #FFFFFF;
        border: 1px solid {SLATE_200};
        border-radius: 10px;
        padding: 22px 26px;
        margin-bottom: 1rem;
        color: {SLATE_700};
        font-size: 0.92rem;
        line-height: 1.7;
    }}

    /* ── Live badge ─────────────────────────── */
    .live-badge {{
        display: inline-block;
        background: {TEAL_50};
        color: {TEAL_600};
        font-size: 0.65rem;
        font-weight: 700;
        padding: 2px 8px;
        border-radius: 4px;
        letter-spacing: 0.05em;
        margin-left: 8px;
        vertical-align: middle;
    }}

    /* ── Section headers ──────────────────── */
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
        background: #FFFFFF;
        border-radius: 8px;
        padding: 1.1rem 1.3rem;
        border-left: 3px solid {TEAL_600};
        border: 1px solid {SLATE_200};
        border-left: 3px solid {TEAL_600};
        box-shadow: 0 1px 2px var(--capi-shadow);
        margin-bottom: 0.6rem;
        transition: box-shadow 0.2s ease;
    }}
    .kpi-card:hover {{
        box-shadow: 0 2px 6px var(--capi-hover-shadow);
    }}
    .kpi-val {{
        font-size: 1.8rem; font-weight: 700; color: var(--capi-text);
        line-height: 1; letter-spacing: -0.02em;
    }}
    .kpi-lbl {{
        font-size: 0.74rem; color: var(--capi-text2); margin-top: 0.3rem;
        font-weight: 500; text-transform: uppercase; letter-spacing: 0.03em;
    }}
    .kpi-card.red    {{ border-left-color: {STATUS_CRITICO}; }}
    .kpi-card.red    .kpi-val {{ color: {STATUS_CRITICO}; }}
    .kpi-card.green  {{ border-left-color: #059669; }}
    .kpi-card.green  .kpi-val {{ color: #059669; }}
    .kpi-card.yellow {{ border-left-color: {STATUS_ALTO}; }}
    .kpi-card.yellow .kpi-val {{ color: #B45309; }}
    .kpi-card.orange {{ border-left-color: {STATUS_SOBRESTOCK}; }}
    .kpi-card.orange .kpi-val {{ color: #C2410C; }}
    .kpi-card.darkred {{ border-left-color: {STATUS_LIQUIDAR}; }}
    .kpi-card.darkred .kpi-val {{ color: {STATUS_LIQUIDAR}; }}
    .kpi-card.blue   {{ border-left-color: {TEAL_600}; }}
    .kpi-card.blue   .kpi-val {{ color: {TEAL_600}; }}

    /* ── Sidebar (clean corporate — light) ─── */
    [data-testid="stSidebar"] {{
        background: #FAFBFD;
        border-right: 1px solid {SLATE_200};
    }}
    [data-testid="stSidebar"] [data-testid="stSidebarContent"] {{
        padding-top: 0.5rem;
    }}
    [data-testid="stSidebar"] * {{
        color: {SLATE_500} !important;
    }}
    [data-testid="stSidebar"] h1,
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3 {{
        color: {SLATE_900} !important;
    }}
    [data-testid="stSidebar"] .stButton > button[kind="primary"] {{
        background: {TEAL_600};
        color: white !important;
        border: none;
        border-radius: 8px;
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
    .sidebar-nav-item {{
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 8px 12px;
        border-radius: 6px;
        color: {SLATE_500};
        font-size: 0.85rem;
        font-weight: 500;
        transition: all 0.15s;
        cursor: default;
        margin-bottom: 2px;
    }}
    .sidebar-nav-item:hover {{
        background: {SLATE_100};
        color: {SLATE_900};
    }}
    .sidebar-nav-item.active {{
        background: {TEAL_50};
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
        color: {SLATE_400} !important;
        padding: 16px 12px 6px 12px;
        font-weight: 600;
    }}
    /* Sidebar nav — secondary (inactive) */
    [data-testid="stSidebar"] .stButton > button[kind="secondary"],
    [data-testid="stSidebar"] .stButton > button[data-testid="baseButton-secondary"] {{
        background: transparent !important;
        color: {SLATE_500} !important;
        border: none !important;
        text-align: left !important;
        justify-content: flex-start !important;
        padding: 8px 12px !important;
        border-radius: 6px !important;
        font-size: 0.85rem !important;
        font-weight: 500 !important;
        transition: all 0.15s !important;
        box-shadow: none !important;
    }}
    [data-testid="stSidebar"] .stButton > button[kind="secondary"]:hover,
    [data-testid="stSidebar"] .stButton > button[data-testid="baseButton-secondary"]:hover {{
        background: {SLATE_100} !important;
        color: {SLATE_900} !important;
        border: none !important;
    }}
    /* Sidebar nav — primary (active page) */
    [data-testid="stSidebar"] .stButton > button[kind="primary"],
    [data-testid="stSidebar"] .stButton > button[data-testid="baseButton-primary"] {{
        background: {TEAL_50} !important;
        color: {TEAL_600} !important;
        font-weight: 600 !important;
        border: none !important;
        text-align: left !important;
        justify-content: flex-start !important;
        padding: 8px 12px !important;
        border-radius: 6px !important;
        font-size: 0.85rem !important;
        box-shadow: none !important;
    }}
    /* File uploader in sidebar */
    [data-testid="stSidebar"] [data-testid="stFileUploader"] {{
        border-color: {SLATE_200} !important;
    }}
    [data-testid="stSidebar"] [data-testid="stFileUploader"] section {{
        background: #FFFFFF !important;
        border: 1px dashed {SLATE_200} !important;
        border-radius: 8px !important;
    }}
    [data-testid="stSidebar"] .stExpander {{
        border-color: {SLATE_200} !important;
    }}

    /* ── Ocultar footer y menú hamburguesa ──── */
    footer {{ visibility: hidden; }}
    #MainMenu {{ visibility: hidden; }}

    /* ── Tabs ────────────────────────────────── */
    .stTabs [data-baseweb="tab-list"] {{
        gap: 4px;
        background: {SLATE_100};
        padding: 4px;
        border-radius: 8px;
        border: 1px solid {SLATE_200};
    }}
    .stTabs [data-baseweb="tab"] {{
        padding: 8px 18px;
        border-radius: 6px;
        font-size: 0.82rem;
        font-weight: 500;
        color: {SLATE_500};
        border: none;
        background: transparent;
    }}
    .stTabs [data-baseweb="tab"][aria-selected="true"] {{
        background: #FFFFFF;
        color: {TEAL_600};
        box-shadow: 0 1px 2px rgba(0,0,0,0.04);
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
        border-radius: 8px;
        overflow: hidden;
        border: 1px solid {SLATE_200};
    }}

    /* ── Expanders ───────────────────────────── */
    .streamlit-expanderHeader {{
        font-weight: 600;
        font-size: 0.9rem;
        color: {SLATE_900};
    }}
    [data-testid="stExpander"] {{
        border-color: {SLATE_200} !important;
    }}

    /* ── Métricas ────────────────────────────── */
    [data-testid="stMetric"] {{
        background: #FFFFFF;
        padding: 1rem;
        border-radius: 8px;
        border: 1px solid {SLATE_200};
        box-shadow: 0 1px 2px rgba(0,0,0,0.04);
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
        background: #FFFFFF;
        color: {TEAL_600};
        border: 1px solid {TEAL_600};
        border-radius: 6px;
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

    /* ── Briefing cards ──────────────────────── */
    .briefing-card {{
        border-radius: 8px;
        padding: 14px 18px;
        margin-bottom: 6px;
        border-left: 3px solid;
        font-size: 0.92em;
        background: #FFFFFF;
    }}

    /* ── Card containers ──────────────────── */
    .nansen-card {{
        background: #FFFFFF;
        border: 1px solid {SLATE_200};
        border-radius: 8px;
        padding: 18px 22px;
        margin-bottom: 12px;
        box-shadow: 0 1px 2px rgba(0,0,0,0.04);
    }}
    .nansen-card:hover {{
        box-shadow: 0 2px 6px rgba(0,0,0,0.06);
    }}

    /* ── Selectboxes y inputs (corporate light) ── */
    .stSelectbox [data-baseweb="select"] {{
        background-color: #FFFFFF !important;
        border-color: {SLATE_200} !important;
    }}
    .stMultiSelect [data-baseweb="select"] {{
        background-color: #FFFFFF !important;
        border-color: {SLATE_200} !important;
    }}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
#  HELPERS DE COLOR
# ══════════════════════════════════════════════════════════════

BADGE_CSS = {
    "QUIEBRE":          f"background:{STATUS_QUIEBRE}; color:#FFFFFF",
    "PRE-QUIEBRE":      f"background:{STATUS_BAJA}; color:#FFFFFF",
    "ÓPTIMO":           f"background:{STATUS_OPTIMO}; color:#FFFFFF",
    "ALTO":             f"background:{STATUS_ALTO}; color:#FFFFFF",
    "SOBRESTOCK":       f"background:{STATUS_SOBRESTOCK}; color:#FFFFFF",
    "LIQUIDAR":         f"background:{STATUS_LIQUIDAR}; color:#FFFFFF",
    "NUEVO SIN VENTA":  f"background:{STATUS_NUEVO_SV}; color:#FFFFFF",
    "DORMIDO":          f"background:{STATUS_DORMIDO}; color:#FFFFFF",
    "MUERTO":           f"background:{STATUS_MUERTO}; color:#FFFFFF",
    "ESTANCADO":        f"background:{STATUS_ESTANCADO}; color:#FFFFFF",
}


def _badge(val):
    css = BADGE_CSS.get(str(val), "background:#E2E8F0; color:#334155")
    return f'<span style="display:inline-block;padding:3px 12px;border-radius:20px;font-size:0.73rem;font-weight:600;letter-spacing:0.02em;{css}">{val}</span>'


def color_estado(val):
    colors = {
        "QUIEBRE":          f"background-color:{STATUS_QUIEBRE}; color:#FFFFFF",
        "PRE-QUIEBRE":      f"background-color:{STATUS_BAJA}; color:#FFFFFF",
        "ÓPTIMO":           f"background-color:{STATUS_OPTIMO}; color:#FFFFFF",
        "ALTO":             f"background-color:{STATUS_ALTO}; color:#FFFFFF",
        "SOBRESTOCK":       f"background-color:{STATUS_SOBRESTOCK}; color:#FFFFFF",
        "LIQUIDAR":         f"background-color:{STATUS_LIQUIDAR}; color:#FFFFFF",
        "NUEVO SIN VENTA":  f"background-color:{STATUS_NUEVO_SV}; color:#FFFFFF",
        "DORMIDO":          f"background-color:{STATUS_DORMIDO}; color:#FFFFFF",
        "MUERTO":           f"background-color:{STATUS_MUERTO}; color:#FFFFFF",
        "ESTANCADO":        f"background-color:{STATUS_ESTANCADO}; color:#FFFFFF",
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

# ── Modo demo (?demo=1 en la URL): nav simplificada + auto-carga de base ──
try:
    _DEMO_MODE = st.query_params.get("demo") == "1"
except Exception:
    _DEMO_MODE = False

# ══════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════

with st.sidebar:
    # ── Toggle día/noche + logo ──
    _sb_top1, _sb_top2 = st.columns([3, 1])
    with _sb_top1:
        st.markdown(f"""
        <div style="padding: 0.6rem 0 0.4rem 0; display:flex; align-items:center; gap:8px;">
            <div style="width:28px; height:28px; background:{TEAL_600}; border-radius:6px; display:flex; align-items:center; justify-content:center; color:white; font-size:12px; font-weight:700;">C</div>
            <div>
                <span style="font-size:1.2rem; font-weight:700; color:{SLATE_900}; letter-spacing:-0.03em;">Capi</span>
                <span style="font-size:0.6rem; color:{SLATE_400}; display:block; margin-top:1px; letter-spacing:0.05em;">GESTIÓN RETAIL</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
    with _sb_top2:
        _theme_icon = "☀️" if st.session_state["theme_mode"] == "dark" else "🌙"
        if st.button(_theme_icon, key="theme_toggle", help="Cambiar entre modo día y noche"):
            st.session_state["theme_mode"] = "light" if st.session_state["theme_mode"] == "dark" else "dark"
            st.rerun()
    st.markdown(f'<div style="border-bottom: 1px solid {SLATE_200}; margin-bottom: 0.8rem;"></div>', unsafe_allow_html=True)

    if _DEMO_MODE:
        st.caption("🎬 Modo demo activo")

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

        # ── VISIÓN GENERAL ──
        st.markdown('<div class="sidebar-section-label">VISIÓN GENERAL</div>', unsafe_allow_html=True)

        _NAV_VISION = [
            ("🏠", "Dashboard"),
            ("🩺", "Salud del Stock"),
            ("📲", "Briefing Semanal"),
            ("📝", "Diario de Gestión"),
            ("📊", "Gestión por Antigüedad"),
        ]
        if _DEMO_MODE:
            # Demo: solo las vistas protagonistas del guion de 3 minutos
            _NAV_VISION = [
                ("🏠", "Dashboard"),
                ("🩺", "Salud del Stock"),
                ("📲", "Briefing Semanal"),
            ]

        for _icon, _label in _NAV_VISION:
            _full = f"{_icon} {_label}"
            _is_active = st.session_state["nav_page"] == _full
            if st.button(
                _full, key=f"nav_{_label}",
                use_container_width=True,
                type="primary" if _is_active else "secondary",
            ):
                st.session_state["nav_page"] = _full
                st.rerun()

        # ── GESTIÓN DE MARCAS TERCERAS ──
        # Cluster end-to-end de terceras: detectar (Agente) → reponer/transferir
        # (filtrado a las 10 marcas) → pricing por pirámide de antigüedad.
        if not _DEMO_MODE:
            st.markdown('<div class="sidebar-section-label">GESTIÓN DE MARCAS TERCERAS</div>', unsafe_allow_html=True)
            _NAV_TERCERAS = [
                ("🤝", "Agente Terceras"),
                ("📦", "Reposición Terceras"),
                ("🔄", "Transferencias Terceras"),
                ("💰", "Gestión de Precios Terceras"),
            ]
            for _icon, _label in _NAV_TERCERAS:
                _full = f"{_icon} {_label}"
                _is_active = st.session_state["nav_page"] == _full
                if st.button(
                    _full, key=f"nav_{_label}",
                    use_container_width=True,
                    type="primary" if _is_active else "secondary",
                ):
                    st.session_state["nav_page"] = _full
                    st.rerun()

        # ── GESTIÓN DE MARCAS PROPIAS ──
        # Mismo cluster que terceras, filtrado a las 7 marcas propias.
        if not _DEMO_MODE:
            st.markdown('<div class="sidebar-section-label">GESTIÓN DE MARCAS PROPIAS</div>', unsafe_allow_html=True)
            _NAV_PROPIAS = [
                ("📦", "Reposición Propias"),
                ("🔄", "Transferencias Propias"),
                ("💰", "Gestión de Precios Propias"),
                ("🚚", "Predistribución Propias"),
            ]
            for _icon, _label in _NAV_PROPIAS:
                _full = f"{_icon} {_label}"
                _is_active = st.session_state["nav_page"] == _full
                if st.button(
                    _full, key=f"nav_{_label}",
                    use_container_width=True,
                    type="primary" if _is_active else "secondary",
                ):
                    st.session_state["nav_page"] = _full
                    st.rerun()

        # ── GESTIÓN DE STOCK ──
        # (Reposición, Transferencias y Predistribución se movieron a las
        #  secciones de marca; aquí queda la cobertura general. Sobrestock y
        #  Acciones de Stock siguen calculándose en el motor, sin vista.)
        _NAV_STOCK = [
            ("📈", "Cobertura"),
        ]
        if _DEMO_MODE:
            _NAV_STOCK = []

        if _NAV_STOCK:
            st.markdown('<div class="sidebar-section-label">GESTIÓN DE STOCK</div>', unsafe_allow_html=True)
        for _icon, _label in _NAV_STOCK:
            _full = f"{_icon} {_label}"
            _is_active = st.session_state["nav_page"] == _full
            if st.button(
                _full, key=f"nav_{_label}",
                use_container_width=True,
                type="primary" if _is_active else "secondary",
            ):
                st.session_state["nav_page"] = _full
                st.rerun()

        if not _DEMO_MODE:
            # ── GESTIÓN COMERCIAL ──
            st.markdown('<div class="sidebar-section-label">GESTIÓN COMERCIAL</div>', unsafe_allow_html=True)

            _NAV_COMERCIAL = [
                ("💰", "Acciones Precio"),
            ]

            for _icon, _label in _NAV_COMERCIAL:
                _full = f"{_icon} {_label}"
                _is_active = st.session_state["nav_page"] == _full
                if st.button(
                    _full, key=f"nav_{_label}",
                    use_container_width=True,
                    type="primary" if _is_active else "secondary",
                ):
                    st.session_state["nav_page"] = _full
                    st.rerun()

            # ── ANÁLISIS PREDICTIVO ──
            st.markdown('<div class="sidebar-section-label">ANÁLISIS PREDICTIVO</div>', unsafe_allow_html=True)

            _NAV_PREDICTIVO = [
                ("🏪", "Afinidad Producto×Plaza"),
                ("🤖", "Alertas IA"),
            ]

            for _icon, _label in _NAV_PREDICTIVO:
                _full = f"{_icon} {_label}"
                _is_active = st.session_state["nav_page"] == _full
                if st.button(
                    _full, key=f"nav_{_label}",
                    use_container_width=True,
                    type="primary" if _is_active else "secondary",
                ):
                    st.session_state["nav_page"] = _full
                    st.rerun()

        st.markdown(f'<div style="border-bottom:1px solid {SLATE_200}; margin:4px 0 12px 0;"></div>', unsafe_allow_html=True)

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
        umbral_critico    = st.slider("QUIEBRE — menor a (sem)",       min_value=1,  max_value=8,  value=4,  step=1)
        umbral_precritico = st.slider("PRE-QUIEBRE — hasta (sem)",   min_value=4,  max_value=12, value=8,  step=1)
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

        st.markdown("**Confiabilidad del Stock CD (ATP)**")
        cd_prometible_pct = st.slider(
            "Stock CD prometible (%)",
            min_value=10, max_value=100, value=60, step=5,
            help="El reporte de CD no es tiempo real y el stock varía entre cortes. "
                 "Solo este % del CD reportado se considera disponible para prometer despachos. "
                 "Calibración Franco 2026-06-12: 60%."
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
    <div style="border-top:1px solid {SLATE_200}; margin-top:1.5rem; padding-top:0.8rem; text-align:center;">
        <div class="sidebar-nav-item" style="justify-content:center; margin-bottom:4px;">
            <span class="nav-icon">⚙️</span> Settings
        </div>
        <span style="font-size:0.65rem; color:{SLATE_400}; letter-spacing:0.05em;">
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
        <span style="font-size:0.75rem; color:{SLATE_400};">v2.7</span>
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
                    # Guardar copia en data2/bases antiguas/ para Afinidad y snapshots
                    _bases_dir = os.path.join(os.path.dirname(__file__), "data2", "bases antiguas")
                    os.makedirs(_bases_dir, exist_ok=True)
                    _base_copy_name = uploaded.name if uploaded.name else "Base_subida.xlsx"
                    _base_copy_path = os.path.join(_bases_dir, _base_copy_name)
                    import shutil as _shutil_cp
                    _shutil_cp.copy2(tmp_path, _base_copy_path)
                    st.session_state["_base_profundidad_path"] = _base_copy_path

                    # Guardar snapshot ANTES de transformar (columnas originales Ripley)
                    if _HAS_SNAPSHOTS:
                        try:
                            snapshots_engine.process_micro_profundidad(_base_copy_path, force=True)
                        except Exception:
                            pass  # Silencioso — no bloquear el flujo

                    plantilla_path = tmp_path.replace(".xlsx", "_plantilla.xlsx")
                    etl_profundidad.transform(tmp_path, output_path=plantilla_path)
                    os.unlink(tmp_path)
                    tmp_path = plantilla_path
                    st.toast("Base Profundidad transformada a plantilla Capi")

            with st.spinner("Ejecutando análisis..."):
                results = motor_v2.run_analysis(tmp_path, params=params_ui, formato=formato_input)

                # Snapshot para formato plantilla (non-profundidad uploads)
                if _HAS_SNAPSHOTS and not st.session_state.get("_base_profundidad_path"):
                    try:
                        snapshots_engine.process_micro_profundidad(tmp_path, force=True)
                    except Exception:
                        pass

                os.unlink(tmp_path)
                st.session_state["results"] = results
                st.rerun()  # Forzar rerun para que sidebar se re-renderice con nav

        except Exception as e:
            st.error(f"❌ Error al procesar el archivo: {e}")
            st.exception(e)


# ══════════════════════════════════════════════════════════════
#  MODO DEMO: AUTO-CARGA DE LA ÚLTIMA BASE DISPONIBLE
#  Con ?demo=1 la app arranca con datos sin pedir upload —
#  crítico para que la demo muestre insights en segundos.
# ══════════════════════════════════════════════════════════════

if _DEMO_MODE and st.session_state["results"] is None and not st.session_state.get("_demo_autoload_done"):
    st.session_state["_demo_autoload_done"] = True
    import re as _re_demo

    _demo_dir = os.path.join(os.path.dirname(__file__), "data2", "bases antiguas")

    def _demo_fecha_archivo(nombre):
        """Extrae (año, mes, día) de nombres tipo 'Base al 01.06.26.xlsx' para elegir la más reciente."""
        _m = _re_demo.search(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2}))?", nombre)
        if not _m:
            return (0, 0, 0)
        _yy = int(_m.group(3)) if _m.group(3) else 26
        return (_yy, int(_m.group(2)), int(_m.group(1)))

    try:
        _demo_bases = [f for f in os.listdir(_demo_dir)
                       if f.lower().endswith(".xlsx") and not f.startswith("~$")]
    except OSError:
        _demo_bases = []

    if _demo_bases:
        _demo_base = max(_demo_bases, key=_demo_fecha_archivo)
        _demo_path = os.path.join(_demo_dir, _demo_base)
        try:
            with st.spinner(f"Modo demo: cargando {_demo_base}…"):
                if _is_base_profundidad(_demo_path):
                    _demo_plantilla = os.path.join(tempfile.gettempdir(), "capi_demo_plantilla.xlsx")
                    etl_profundidad.transform(_demo_path, output_path=_demo_plantilla)
                    _demo_input = _demo_plantilla
                else:
                    _demo_input = _demo_path
                st.session_state["results"] = motor_v2.run_analysis(_demo_input, params=params_ui, formato=formato_input)
            st.rerun()
        except Exception as _demo_err:
            st.warning(f"Modo demo: no se pudo auto-cargar la base ({_demo_err}). Sube un archivo manualmente.")


# ══════════════════════════════════════════════════════════════
#  PANTALLA DE BIENVENIDA (sin datos)
# ══════════════════════════════════════════════════════════════

if st.session_state["results"] is None:
    st.markdown(f"""
    <div style="text-align:center; padding:60px 20px;">
        <div style="font-size:2.2rem; font-weight:700; color:var(--capi-text); margin-bottom:8px;">
            ¿Qué está pasando con tu inventario?
        </div>
        <p style="color:var(--capi-text2); font-size:1rem; margin-bottom:30px;">
            Sube tu Base Profundidad para desbloquear el análisis completo.
        </p>
        <div style="background:var(--capi-bg-surface); border:1px solid var(--capi-border); border-radius:14px; padding:24px; max-width:500px; margin:0 auto; text-align:left;">
            <div style="font-weight:600; color:var(--capi-text); margin-bottom:12px;">Cómo empezar:</div>
            <div style="color:var(--capi-text); font-size:0.9rem; line-height:1.8;">
                <span style="color:{TEAL_600}; font-weight:600;">1.</span> Sube tu archivo Excel en el sidebar<br>
                <span style="color:{TEAL_600}; font-weight:600;">2.</span> Ajusta umbrales si lo necesitas<br>
                <span style="color:{TEAL_600}; font-weight:600;">3.</span> Haz clic en <strong>Ejecutar análisis</strong>
            </div>
            <div style="margin-top:14px; padding:10px 14px; background:var(--capi-bg-card); border-radius:8px; border:1px solid var(--capi-border);">
                <span style="font-size:0.82rem; color:var(--capi-text2);">
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
s      = res.get("summary", {})
params = res["params"]

df_cob       = res.get("cobertura", pd.DataFrame())
df_rep       = res.get("reposiciones", pd.DataFrame())
df_rep_pivot = res.get("reposiciones_pivot", pd.DataFrame())
df_trans     = res.get("transferencias", pd.DataFrame())
df_prec      = res.get("acciones_precio", pd.DataFrame())
df_alertas   = res.get("alertas", pd.DataFrame())
df_anomalias = res.get("anomalias_tienda", pd.DataFrame())
alertas_tienda_dict = res.get("alertas_tienda", {})
alertas_venta_cero_dict = res.get("alertas_venta_cero", {})
briefing     = res.get("briefing") or {}
briefing_items  = briefing.get('items', [])
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

# LY Comparison + Ticket Promedio
ly_comparison = res.get('ly_comparison', {})
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
s["n_critico"]         = int((df_cob["estado"] == "QUIEBRE").sum()) if not df_cob.empty else 0
s["n_precritico"]      = int((df_cob["estado"] == "PRE-QUIEBRE").sum()) if not df_cob.empty else 0
s["n_optimo"]          = int((df_cob["estado"] == "ÓPTIMO").sum()) if not df_cob.empty else 0
s["n_alto"]            = int((df_cob["estado"] == "ALTO").sum()) if not df_cob.empty else 0
s["n_sobrestock"]      = int((df_cob["estado"] == "SOBRESTOCK").sum()) if not df_cob.empty else 0
s["n_liquidar"]        = int((df_cob["estado"] == "LIQUIDAR").sum()) if not df_cob.empty else 0
s["n_nuevo_sv"]        = int((df_cob["estado"] == "NUEVO SIN VENTA").sum()) if not df_cob.empty else 0
s["n_dormido"]         = int((df_cob["estado"] == "DORMIDO").sum()) if not df_cob.empty else 0
s["n_muerto"]          = int((df_cob["estado"] == "MUERTO").sum()) if not df_cob.empty else 0
s["n_estancado"]       = int((df_cob["estado"] == "ESTANCADO").sum()) if not df_cob.empty else 0
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
    font=dict(family="Inter, -apple-system, sans-serif", size=12, color=TH_TEXT_PY),
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(l=20, r=20, t=40, b=20),
    hoverlabel=dict(bgcolor="white", font_size=12, font_family="Inter"),
)

_estado_color_map = {
    "QUIEBRE":      STATUS_QUIEBRE,
    "PRE-QUIEBRE":  STATUS_BAJA,
    "ÓPTIMO":       STATUS_OPTIMO,
    "ALTO":         STATUS_ALTO,
    "SOBRESTOCK":   STATUS_SOBRESTOCK,
    "ESTANCADO":    STATUS_ESTANCADO,
    "LIQUIDAR":     STATUS_LIQUIDAR,
    "NUEVO SIN VENTA":  STATUS_LANZAMIENTO,
    "DORMIDO":      STATUS_DORMIDO,
    "MUERTO":       STATUS_MUERTO,
}


def _build_excel_terceras(_dfc, _dfr, _dft):
    """Excel-paquete de Marcas Terceras: una pestaña por componente de la
    sección (Capital Parado, SKUs Críticos, Quiebres, Reposición,
    Transferencias, Precios). Sin cache: se genera fresco al descargar."""
    _M = agente_terceras.MARCAS_AGENTE
    _buf = io.BytesIO()
    with pd.ExcelWriter(_buf, engine="openpyxl") as _w:
        # 1. SKUs críticos por marca×línea (con criticidad)
        _crit = agente_terceras.top5_por_marca_linea(_dfc, top_n=5)
        (_crit if not _crit.empty else pd.DataFrame({"sin datos": []})).to_excel(_w, sheet_name="SKUs Criticos", index=False)
        # 2. Reposición terceras (con margen + edad cruzados)
        _rep = _dfr[_dfr['marca'].str.upper().str.strip().isin(_M)].copy() if not _dfr.empty and 'marca' in _dfr.columns else pd.DataFrame()
        if not _rep.empty:
            _rep = _rep[_rep['a_reponer'] > 0] if 'a_reponer' in _rep.columns else _rep
            if 'sku' in _dfc.columns and 'margen_efectivo' in _dfc.columns:
                _mg = _dfc.drop_duplicates('sku').set_index('sku')['margen_efectivo']
                _rep['margen_efectivo_pct'] = (_rep['sku'].map(_mg).fillna(0) * 100).round(1)
            if 'sku' in _dfc.columns and 'edad_semanas' in _dfc.columns:
                _rep['edad_sem'] = _rep['sku'].map(_dfc.groupby('sku')['edad_semanas'].max())
        (_rep if not _rep.empty else pd.DataFrame({"sin datos": []})).to_excel(_w, sheet_name="Reposicion", index=False)
        # 3. Transferencias terceras (cruce por sku)
        if not _dft.empty and 'sku' in _dft.columns and 'marca' in _dfc.columns:
            _s2m = dict(zip(_dfc['sku'], _dfc['marca'].str.upper().str.strip()))
            _tr = _dft.copy()
            _tr['marca'] = _tr['sku'].map(_s2m)
            _tr = _tr[_tr['marca'].isin(_M)]
        else:
            _tr = pd.DataFrame()
        (_tr if not _tr.empty else pd.DataFrame({"sin datos": []})).to_excel(_w, sheet_name="Transferencias", index=False)
        # 4. Gestión de precios (pirámide)
        _pr = agente_terceras.sugerencias_precio_terceras(_dfc)
        (_pr if not _pr.empty else pd.DataFrame({"sin datos": []})).to_excel(_w, sheet_name="Precios", index=False)
    _buf.seek(0)
    return _buf.read()


def _build_excel_propias(_dfc, _dfr, _dft, _gaps, _ret):
    """Excel-paquete de Marcas Propias: Reposición · Transferencias · Precios ·
    Predistribución (gaps limpios + retenidos CD). Filtrado a las 7 propias."""
    _P = agente_terceras.MARCAS_PROPIAS_SET
    _buf = io.BytesIO()
    with pd.ExcelWriter(_buf, engine="openpyxl") as _w:
        # 1. Reposición propias (con margen + edad)
        _rep = _dfr[_dfr['marca'].str.upper().str.strip().isin(_P)].copy() if not _dfr.empty and 'marca' in _dfr.columns else pd.DataFrame()
        if not _rep.empty:
            _rep = _rep[_rep['a_reponer'] > 0] if 'a_reponer' in _rep.columns else _rep
            if 'sku' in _dfc.columns and 'margen_efectivo' in _dfc.columns:
                _mg = _dfc.drop_duplicates('sku').set_index('sku')['margen_efectivo']
                _rep['margen_efectivo_pct'] = (_rep['sku'].map(_mg).fillna(0) * 100).round(1)
            if 'sku' in _dfc.columns and 'edad_semanas' in _dfc.columns:
                _rep['edad_sem'] = _rep['sku'].map(_dfc.groupby('sku')['edad_semanas'].max())
        (_rep if not _rep.empty else pd.DataFrame({"sin datos": []})).to_excel(_w, sheet_name="Reposicion", index=False)
        # 2. Transferencias propias (cruce por sku)
        if not _dft.empty and 'sku' in _dft.columns and 'marca' in _dfc.columns:
            _s2m = dict(zip(_dfc['sku'], _dfc['marca'].str.upper().str.strip()))
            _tr = _dft.copy(); _tr['marca'] = _tr['sku'].map(_s2m); _tr = _tr[_tr['marca'].isin(_P)]
        else:
            _tr = pd.DataFrame()
        (_tr if not _tr.empty else pd.DataFrame({"sin datos": []})).to_excel(_w, sheet_name="Transferencias", index=False)
        # 3. Precios propias (pirámide)
        _pr = agente_terceras.sugerencias_precio_terceras(_dfc, marcas=_P)
        (_pr if not _pr.empty else pd.DataFrame({"sin datos": []})).to_excel(_w, sheet_name="Precios", index=False)
        # 4. Predistribución — gaps limpios (stock CD>0, edad<=8) + retenidos CD
        _g = _gaps[_gaps['marca'].str.upper().str.strip().isin(_P)].copy() if not _gaps.empty and 'marca' in _gaps.columns else pd.DataFrame()
        if not _g.empty:
            if 'stock_cd' in _g.columns:
                _g = _g[_g['stock_cd'] > 0]
            if 'edad_semanas' in _g.columns:
                _g = _g[_g['edad_semanas'].fillna(999) <= 8]
        (_g if not _g.empty else pd.DataFrame({"sin datos": []})).to_excel(_w, sheet_name="Predist Gaps", index=False)
        _r = _ret[_ret['marca'].str.upper().str.strip().isin(_P)].copy() if not _ret.empty and 'marca' in _ret.columns else pd.DataFrame()
        (_r if not _r.empty else pd.DataFrame({"sin datos": []})).to_excel(_w, sheet_name="Retenidos CD", index=False)
    _buf.seek(0)
    return _buf.read()


if nav_page == "🏠 Dashboard":
    st.markdown(f'<div class="section-header"><h3>Dashboard</h3><span class="live-badge">LIVE</span></div>', unsafe_allow_html=True)

    # ── Delta KPIs semanales (Snapshots) ──
    if _HAS_SNAPSHOTS:
        _snap_weeks = snapshots_engine.list_available_weeks()
        if len(_snap_weeks) >= 2:
            _snap_sem_b = _snap_weeks[-1]
            _snap_sem_a = _snap_weeks[-2]
            _snap_cmp = snapshots_engine.compare_weeks(_snap_sem_a, _snap_sem_b)
            if _snap_cmp:
                _sd = _snap_cmp['deltas']
                _sa = _snap_cmp['semana_a']
                _sb = _snap_cmp['semana_b']

                def _delta_arrow(val, pct, invert=False):
                    """Genera flecha + color para un delta."""
                    if pct > 0:
                        _c = "#10b981" if not invert else "#ef4444"
                        _arr = "▲"
                    elif pct < 0:
                        _c = "#ef4444" if not invert else "#10b981"
                        _arr = "▼"
                    else:
                        _c = "var(--capi-text2)"
                        _arr = "–"
                    return f'<span style="color:{_c}; font-weight:600;">{_arr} {abs(pct):.1f}%</span>'

                _delta_cards = [
                    ("Venta S/", f"S/ {_sb['venta_soles']:,.0f}", _delta_arrow(_sd['venta_soles_delta'], _sd['venta_soles_pct'])),
                    ("Venta Uds", f"{_sb['venta_unidades']:,}", _delta_arrow(_sd['venta_unidades_delta'], _sd['venta_unidades_pct'])),
                    ("Stock Uds", f"{_sb['stock_total']:,}", _delta_arrow(_sd['stock_total_delta'], _sd['stock_total_pct'], invert=True)),
                    ("Cob Prom", f"{_sb['cob_promedio']:.1f} sem", _delta_arrow(_sd['cob_promedio_delta'], _sd['cob_promedio_pct'])),
                ]

                _delta_html = f"""<div style="background:var(--capi-bg-surface); border:1px solid var(--capi-border); border-radius:12px; padding:14px 18px; margin-bottom:16px;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                        <span style="font-weight:600; color:var(--capi-text); font-size:0.82rem;">📊 Semana {_snap_sem_b} vs {_snap_sem_a}</span>
                        <span style="font-size:0.68rem; color:var(--capi-text2);">{len(_snap_weeks)} semanas históricas</span>
                    </div>
                    <div style="display:grid; grid-template-columns:repeat(4, 1fr); gap:12px;">"""

                for _lbl, _val, _arr in _delta_cards:
                    _delta_html += f"""<div style="text-align:center;">
                        <div style="font-size:0.68rem; color:var(--capi-text2); margin-bottom:2px;">{_lbl}</div>
                        <div style="font-size:1.1rem; font-weight:700; color:var(--capi-text);">{_val}</div>
                        <div style="font-size:0.75rem;">{_arr}</div>
                    </div>"""

                _delta_html += "</div></div>"
                st.markdown(_delta_html, unsafe_allow_html=True)

    # ── Filtros del dashboard ──
    st.markdown(f"""<div style="background:var(--capi-bg-surface); border:1px solid var(--capi-border); border-radius:12px; padding:12px 16px; margin-bottom:16px;">
    <span style="font-weight:600; color:var(--capi-text); font-size:0.9rem;">Filtros del Dashboard</span>
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
            "QUIEBRE", "PRE-QUIEBRE", "ÓPTIMO", "ALTO", "SOBRESTOCK",
            "ESTANCADO", "LIQUIDAR", "NUEVO SIN VENTA", "DORMIDO", "MUERTO",
        ]
        estado_counts["Estado"] = pd.Categorical(estado_counts["Estado"], categories=estado_order, ordered=True)
        estado_counts = estado_counts.sort_values("Estado").dropna(subset=["Estado"])

        # Display labels: hoy todos los estados ya son su propio label (no se renombran).
        estado_counts["Label"] = estado_counts["Estado"].astype(str)

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
            title=dict(text=_donut_title, font=dict(size=14, color=TH_TEXT_PY)),
            showlegend=False,
            height=380,
        )
        total_donut = len(_df_donut)
        fig_donut.add_annotation(
            text=f"<b>{total_donut:,}</b><br><span style='font-size:10px;color:var(--capi-text2)'>SKU×Tienda</span>",
            showarrow=False, font=dict(size=18, color=TH_TEXT_PY),
        )
        st.plotly_chart(fig_donut, use_container_width=True)

    with dash_c2:
        # ── Leyenda: criterio de cada estado (10 estados de la taxonomía v2) ──
        _estado_criterios = {
            "QUIEBRE":     {"color": STATUS_QUIEBRE,     "icon": "🔴", "regla": "Cobertura < 4 semanas"},
            "PRE-QUIEBRE": {"color": STATUS_BAJA,        "icon": "🟠", "regla": "Cobertura 4–8 semanas"},
            "ÓPTIMO":      {"color": STATUS_OPTIMO,     "icon": "🟢", "regla": "Cobertura 8–16 semanas"},
            "ALTO":        {"color": STATUS_ALTO,       "icon": "🟡", "regla": "Cobertura 16–26 semanas"},
            "SOBRESTOCK":  {"color": STATUS_SOBRESTOCK, "icon": "🟤", "regla": "Cobertura 26–52 semanas"},
            "ESTANCADO":   {"color": STATUS_ESTANCADO,  "icon": "⬛", "regla": "Cob > 52 sem · edad ≤ 6 meses"},
            "LIQUIDAR":    {"color": STATUS_LIQUIDAR,   "icon": "💀", "regla": "Cob > 52 sem · edad > 6 meses"},
            "NUEVO SIN VENTA": {"color": STATUS_LANZAMIENTO,"icon": "🆕", "regla": "Sin venta · edad < 2 meses"},
            "DORMIDO":     {"color": STATUS_DORMIDO,    "icon": "😴", "regla": "Sin venta · edad 2–6 meses"},
            "MUERTO":      {"color": STATUS_MUERTO,     "icon": "⚫", "regla": "Sin venta · edad > 6 meses"},
        }

        st.markdown(f"""<div style="font-weight:600; color:var(--capi-text); font-size:0.95rem; margin-bottom:10px;">
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
                    <span style="font-weight:600; color:var(--capi-text); font-size:0.82rem;">{_est_info.get('label', _est_name)}</span>
                    <span style="color:var(--capi-text2); font-size:0.78rem;"> — {_est_info['regla']}</span>
                </div>
                <div style="text-align:right; min-width:80px;">
                    <span style="font-weight:700; color:{_est_info['color']}; font-size:0.85rem;">{_n_est:,}</span>
                    <span style="color:var(--capi-text2); font-size:0.72rem;"> ({_pct_est:.0f}%)</span>
                </div>
            </div>""", unsafe_allow_html=True)

        st.markdown(f"""<div style="margin-top:10px; padding:8px 12px; background:var(--capi-bg-surface); border-radius:8px; font-size:0.78rem; color:var(--capi-text2);">
        Capital total: <strong style="color:var(--capi-text);">S/ {_df_donut['stock_valor_costo'].sum():,.0f}</strong> &nbsp;·&nbsp;
        {total_donut:,} combos SKU×Tienda
        </div>""", unsafe_allow_html=True)

    # ── Desglose por marca del estado seleccionado + descarga ──
    st.markdown("---")

    # Display labels: hoy cada estado se muestra con su propio nombre.
    _est_opciones = [e for e in estado_order if e in _df_donut["estado"].values]
    _est_default = _est_opciones.index("QUIEBRE") if "QUIEBRE" in _est_opciones else 0

    _det_c1, _det_c2 = st.columns([1, 2])
    with _det_c1:
        _est_sel = st.selectbox("Ver detalle por estado", _est_opciones, index=_est_default, key="donut_estado_sel")

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

            st.markdown(f"""<div style="background:{'#FEF2F2' if _est_sel in ('QUIEBRE','PRE-QUIEBRE') else TH_BG_SURFACE_PY};
            border-left:4px solid {_estado_color_map.get(_est_sel, SLATE_500)};
            padding:10px 14px; border-radius:10px; margin-bottom:10px;">
            <strong style="color:{_estado_color_map.get(_est_sel, TH_TEXT_PY)};">{_est_sel}</strong>
            <span style="color:var(--capi-text2); font-size:0.82em;"> &nbsp;·&nbsp; {len(_df_est):,} combos · {_df_est['sku'].nunique()} SKUs · S/ {_df_est['stock_valor_costo'].sum():,.0f}</span>
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
    <div style="font-size:14px; font-weight:600; color:var(--capi-text); margin-bottom:4px;">Inventario a Costo por {group_label} (Top 10)</div>
    <div style="display:flex; gap:16px; font-size:12px; color:var(--capi-text2); margin-bottom:12px;">
        <span style="display:flex; align-items:center; gap:4px;"><span style="width:10px; height:10px; border-radius:2px; background:{TEAL_700}; display:inline-block;"></span>Capital a costo</span>
        <span style="display:flex; align-items:center; gap:4px;"><span style="width:10px; height:10px; border-radius:2px; background:#5DCAA5; display:inline-block;"></span>Venta a costo (4 sem)</span>
        <span style="display:flex; align-items:center; gap:4px;"><span style="background:var(--capi-bg-surface); border:1px solid var(--capi-border); border-radius:4px; padding:0 5px; font-size:10px; color:var(--capi-text);">5.2</span>Cobertura (meses)</span>
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
            _cob_html = f'<span style="background:var(--capi-bg-surface); color:var(--capi-text2); border:1px solid var(--capi-border); border-radius:6px; padding:2px 8px; font-size:11px; min-width:50px; text-align:center;">—</span>'

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

    st.markdown(f'<div class="section-header"><h3>KPIs de Inventario</h3><span class="live-badge">10 ESTADOS</span></div>', unsafe_allow_html=True)

    # Fila 1: estados con stock en movimiento
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.markdown(_kpi_html(s["n_critico"], "🔴 QUIEBRE", "red"), unsafe_allow_html=True)
    with c2:
        st.markdown(_kpi_html(s["n_precritico"], "🟠 PRE-QUIEBRE", "orange"), unsafe_allow_html=True)
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
        st.markdown(_kpi_html(s["n_estancado"], "📦 ESTANCADO", "gray"), unsafe_allow_html=True)

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
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Margen efectivo global</div>
                <div style="font-size:1.8rem; font-weight:700; color:{_mg_color};">{_mg_pct:.1f}%</div>
                <div style="font-size:0.7rem; color:var(--capi-text2);">Contribución / Venta (4 sem)</div>
            </div>""", unsafe_allow_html=True)
        with _mk2:
            st.markdown(f"""<div style="background:#EFF6FF; border-radius:12px; padding:16px 20px; border-left:4px solid #3b82f6;">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Venta total (4 sem)</div>
                <div style="font-size:1.8rem; font-weight:700; color:#3b82f6;">S/ {_vta_soles_total:,.0f}</div>
                <div style="font-size:0.7rem; color:var(--capi-text2);">Sin IGV</div>
            </div>""", unsafe_allow_html=True)
        with _mk3:
            st.markdown(f"""<div style="background:#F0FDF4; border-radius:12px; padding:16px 20px; border-left:4px solid {TEAL_600};">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Contribución total (4 sem)</div>
                <div style="font-size:1.8rem; font-weight:700; color:{TEAL_700};">S/ {_contrib_soles_total:,.0f}</div>
                <div style="font-size:0.7rem; color:var(--capi-text2);">Venta - Costo</div>
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
                    <tr style="background:var(--capi-bg-surface); border-bottom:2px solid var(--capi-border);">
                        <th style="padding:8px 12px; text-align:left;">Marca</th>
                        <th style="padding:8px 12px; text-align:right;">Venta S/</th>
                        <th style="padding:8px 12px; text-align:right;">Contribución S/</th>
                        <th style="padding:8px 12px; text-align:right;">Margen %</th>
                        <th style="padding:8px 12px; text-align:left;">Vol. relativo</th>
                    </tr>
                </thead>
                <tbody>{_rows_html}</tbody>
            </table></div>""", unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════
    #  TICKET PROMEDIO + COMPARATIVO VS AÑO PASADO (LY)
    # ══════════════════════════════════════════════════════════
    # En modo demo se oculta la sección YoY para aligerar el Dashboard (guion 3 min)
    if (not _DEMO_MODE) and ly_comparison and ly_comparison.get('ticket_actual_global', 0) > 0:
        st.markdown("<div style='height:32px'></div>", unsafe_allow_html=True)
        st.markdown(f'<div class="section-header"><h3>🎫 Ticket Promedio & vs Año Pasado</h3><span class="live-badge">YoY</span></div>', unsafe_allow_html=True)

        _ly_g = ly_comparison.get('ly_global')
        _sem_act = ly_comparison.get('semana_actual', '?')
        _ticket_act = ly_comparison.get('ticket_actual_global', 0)

        _tk1, _tk2, _tk3, _tk4 = st.columns(4)
        with _tk1:
            st.markdown(f"""<div style="background:#EFF6FF; border-radius:12px; padding:16px 20px; border-left:4px solid #3b82f6;">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Ticket promedio actual</div>
                <div style="font-size:1.8rem; font-weight:700; color:#3b82f6;">S/ {_ticket_act:,.0f}</div>
                <div style="font-size:0.7rem; color:var(--capi-text2);">Venta S/ / Unidades (4 sem)</div>
            </div>""", unsafe_allow_html=True)

        if _ly_g:
            _ticket_ly = _ly_g.get('ticket_ly', 0)
            _d_ticket = _ly_g.get('delta_ticket_pct', 0)
            _d_vta = _ly_g.get('delta_vta_soles_pct', 0)
            _d_uds = _ly_g.get('delta_vta_uds_pct', 0)
            _year_ly = _ly_g.get('año_ly', '?')

            _clr_ticket = "#10b981" if _d_ticket >= 0 else "#ef4444"
            _clr_vta = "#10b981" if _d_vta >= 0 else "#ef4444"
            _clr_uds = "#10b981" if _d_uds >= 0 else "#ef4444"
            _arrow_ticket = "▲" if _d_ticket >= 0 else "▼"
            _arrow_vta = "▲" if _d_vta >= 0 else "▼"
            _arrow_uds = "▲" if _d_uds >= 0 else "▼"

            with _tk2:
                st.markdown(f"""<div style="background:{'#F0FDF4' if _d_ticket >= 0 else '#FEF2F2'}; border-radius:12px; padding:16px 20px; border-left:4px solid {_clr_ticket};">
                    <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Δ Ticket vs LY (sem {_sem_act})</div>
                    <div style="font-size:1.8rem; font-weight:700; color:{_clr_ticket};">{_arrow_ticket} {abs(_d_ticket):.1f}%</div>
                    <div style="font-size:0.7rem; color:var(--capi-text2);">LY: S/ {_ticket_ly:,.0f} ({_year_ly})</div>
                </div>""", unsafe_allow_html=True)
            with _tk3:
                st.markdown(f"""<div style="background:{'#F0FDF4' if _d_vta >= 0 else '#FEF2F2'}; border-radius:12px; padding:16px 20px; border-left:4px solid {_clr_vta};">
                    <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Δ Venta S/ vs LY</div>
                    <div style="font-size:1.8rem; font-weight:700; color:{_clr_vta};">{_arrow_vta} {abs(_d_vta):.1f}%</div>
                    <div style="font-size:0.7rem; color:var(--capi-text2);">Sem actual vs misma sem {_year_ly}</div>
                </div>""", unsafe_allow_html=True)
            with _tk4:
                st.markdown(f"""<div style="background:{'#F0FDF4' if _d_uds >= 0 else '#FEF2F2'}; border-radius:12px; padding:16px 20px; border-left:4px solid {_clr_uds};">
                    <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Δ Unidades vs LY</div>
                    <div style="font-size:1.8rem; font-weight:700; color:{_clr_uds};">{_arrow_uds} {abs(_d_uds):.1f}%</div>
                    <div style="font-size:0.7rem; color:var(--capi-text2);">Sem actual vs misma sem {_year_ly}</div>
                </div>""", unsafe_allow_html=True)

            # ── Tabla comparativa por marca ──
            _ly_marca_data = ly_comparison.get('ly_marca', [])
            if _ly_marca_data:
                st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
                st.markdown(f"<h4 style='color:{SLATE_800}; margin:0 0 8px 0;'>Comparativo por Marca — Semana {_sem_act} actual vs {_year_ly}</h4>", unsafe_allow_html=True)

                _ly_rows_html = ""
                for _lm in _ly_marca_data:
                    _m_name = _lm.get('marca', '')
                    _m_ticket = _lm.get('ticket', 0)
                    _m_ticket_ly = _lm.get('ticket_ly', 0)
                    _m_dvta = _lm.get('delta_vta_pct', 0)
                    _m_dticket = _lm.get('delta_ticket_pct', 0)
                    _clr_mv = "#10b981" if _m_dvta >= 0 else "#ef4444"
                    _clr_mt = "#10b981" if _m_dticket >= 0 else "#ef4444"
                    _arr_mv = "▲" if _m_dvta >= 0 else "▼"
                    _arr_mt = "▲" if _m_dticket >= 0 else "▼"
                    _ly_rows_html += f"""<tr>
                        <td style="padding:6px 10px; font-weight:500;">{_m_name}</td>
                        <td style="padding:6px 10px; text-align:right;">S/ {_m_ticket:,.0f}</td>
                        <td style="padding:6px 10px; text-align:right; color:var(--capi-text2);">S/ {_m_ticket_ly:,.0f}</td>
                        <td style="padding:6px 10px; text-align:right; font-weight:600; color:{_clr_mt};">{_arr_mt} {abs(_m_dticket):.1f}%</td>
                        <td style="padding:6px 10px; text-align:right; font-weight:600; color:{_clr_mv};">{_arr_mv} {abs(_m_dvta):.1f}%</td>
                    </tr>"""

                st.markdown(f"""<div style="overflow-x:auto; max-height:400px; overflow-y:auto;">
                <table style="width:100%; border-collapse:collapse; font-size:0.82rem;">
                    <thead>
                        <tr style="background:var(--capi-bg-surface); border-bottom:2px solid var(--capi-border); position:sticky; top:0;">
                            <th style="padding:8px 10px; text-align:left;">Marca</th>
                            <th style="padding:8px 10px; text-align:right;">Ticket Actual</th>
                            <th style="padding:8px 10px; text-align:right;">Ticket LY</th>
                            <th style="padding:8px 10px; text-align:right;">Δ Ticket</th>
                            <th style="padding:8px 10px; text-align:right;">Δ Venta S/</th>
                        </tr>
                    </thead>
                    <tbody>{_ly_rows_html}</tbody>
                </table></div>""", unsafe_allow_html=True)
        else:
            with _tk2:
                st.markdown(f"""<div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {SLATE_400};">
                    <div style="font-size:0.75rem; color:var(--capi-text2);">Comparativo LY</div>
                    <div style="font-size:1rem; color:var(--capi-text2);">Sin data para sem {_sem_act}</div>
                </div>""", unsafe_allow_html=True)

    # (Sección "Acciones del Día" eliminada — info disponible en vistas del sidebar)

    # ── Evolución Semanal (Prompt B — Snapshots Engine) ──
    if _HAS_SNAPSHOTS:
        _snap_weeks = snapshots_engine.list_available_weeks()
        if len(_snap_weeks) >= 2:
            st.markdown("---")
            st.markdown(f'<div class="section-header"><h3>📊 Evolución Semanal</h3><span class="live-badge">{len(_snap_weeks)} SEM</span></div>', unsafe_allow_html=True)

            _snap_resumenes = []
            for _sw in _snap_weeks:
                _sr = snapshots_engine.api.get_resumen_semanal(_sw)
                if _sr:
                    _snap_resumenes.append(_sr)

            if _snap_resumenes:
                _snap_df = pd.DataFrame(_snap_resumenes)

                _evol_c1, _evol_c2 = st.columns(2)

                with _evol_c1:
                    _fig_vta = go.Figure()
                    _fig_vta.add_trace(go.Bar(
                        x=_snap_df['semana_iso'], y=_snap_df['venta_total_unidades'],
                        marker_color=TEAL_600, name='Vta Unidades',
                        text=_snap_df['venta_total_unidades'].apply(lambda x: f"{x/1000:.0f}K"),
                        textposition='outside',
                    ))
                    _fig_vta.update_layout(
                        title="Venta Total Unidades por Semana",
                        height=300, margin=dict(t=40, b=30, l=40, r=20),
                        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                        font=dict(size=11),
                        yaxis=dict(showgrid=True, gridcolor='rgba(0,0,0,0.05)'),
                    )
                    st.plotly_chart(_fig_vta, use_container_width=True)

                with _evol_c2:
                    _fig_stk = go.Figure()
                    _fig_stk.add_trace(go.Bar(
                        x=_snap_df['semana_iso'], y=_snap_df['stock_total_unidades'],
                        marker_color=SLATE_500, name='Stock Total',
                        text=_snap_df['stock_total_unidades'].apply(lambda x: f"{x/1000:.0f}K"),
                        textposition='outside',
                    ))
                    _fig_stk.update_layout(
                        title="Stock Total Unidades por Semana",
                        height=300, margin=dict(t=40, b=30, l=40, r=20),
                        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                        font=dict(size=11),
                        yaxis=dict(showgrid=True, gridcolor='rgba(0,0,0,0.05)'),
                    )
                    st.plotly_chart(_fig_stk, use_container_width=True)

                # KPI deltas: última semana vs penúltima
                if len(_snap_resumenes) >= 2:
                    _curr = _snap_resumenes[-1]
                    _prev = _snap_resumenes[-2]
                    _d_vta = _curr['venta_total_unidades'] - _prev['venta_total_unidades']
                    _d_stk = _curr['stock_total_unidades'] - _prev['stock_total_unidades']
                    _d_skus = _curr['n_skus'] - _prev['n_skus']
                    _pct_vta = (_d_vta / _prev['venta_total_unidades'] * 100) if _prev['venta_total_unidades'] else 0

                    _dc1, _dc2, _dc3, _dc4 = st.columns(4)
                    _delta_color = lambda v: "#059669" if v >= 0 else "#DC2626"
                    _dc1.markdown(f'<div style="text-align:center; padding:8px;"><div style="font-size:0.7rem; color:var(--capi-text2);">Δ Venta U</div><div style="font-size:1.2rem; font-weight:700; color:{_delta_color(_d_vta)};">{"+" if _d_vta>=0 else ""}{_d_vta:,.0f}</div></div>', unsafe_allow_html=True)
                    _dc2.markdown(f'<div style="text-align:center; padding:8px;"><div style="font-size:0.7rem; color:var(--capi-text2);">Δ Venta %</div><div style="font-size:1.2rem; font-weight:700; color:{_delta_color(_pct_vta)};">{"+" if _pct_vta>=0 else ""}{_pct_vta:.1f}%</div></div>', unsafe_allow_html=True)
                    _dc3.markdown(f'<div style="text-align:center; padding:8px;"><div style="font-size:0.7rem; color:var(--capi-text2);">Δ Stock U</div><div style="font-size:1.2rem; font-weight:700; color:{_delta_color(-_d_stk)};">{"+" if _d_stk>=0 else ""}{_d_stk:,.0f}</div></div>', unsafe_allow_html=True)
                    _dc4.markdown(f'<div style="text-align:center; padding:8px;"><div style="font-size:0.7rem; color:var(--capi-text2);">Δ SKUs</div><div style="font-size:1.2rem; font-weight:700; color:var(--capi-text);">{"+" if _d_skus>=0 else ""}{_d_skus}</div></div>', unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════
    #  💸 VENTAS PERDIDAS POR QUIEBRE (lost sales)
    #  Banda histórica (snapshots, nivel SKU) + quiebre actual
    #  (df_cob, SKU×tienda). Feature estilo RELEX/Blue Yonder.
    # ══════════════════════════════════════════════════════════
    if _HAS_SNAPSHOTS:
        @st.cache_data(show_spinner=False)
        def _vp_calcular(_clave: str):
            return snapshots_engine.estimate_lost_sales(marcas=_MARCAS_VIGENTES)

        @st.cache_data(show_spinner=False)
        def _vp_procedencia_map(_path: str):
            """Mapa SKU → procedencia (NAC/IMP) leído de la columna 'Proced.' de la Base Profundidad.
            Define si un quiebre sin stock en cadena tiene reorder viable (NAC, semanas)
            o es estructural (IMP, ~2-3 meses — no llega para esta venta)."""
            try:
                _dfp = pd.read_excel(_path, usecols=['Cód. Prod.', 'Proced.'])
                return dict(zip(_dfp['Cód. Prod.'],
                                _dfp['Proced.'].astype(str).str.strip().str.upper()))
            except Exception:
                return {}

        def _vp_base_path():
            _p = st.session_state.get("_base_profundidad_path")
            if _p and os.path.exists(_p):
                return _p
            _dir = os.path.join(os.path.dirname(__file__), "data2", "bases antiguas")
            try:
                _bases = [f for f in os.listdir(_dir) if f.lower().endswith(".xlsx") and not f.startswith("~$")]
                if _bases:
                    return os.path.join(_dir, max(_bases, key=lambda f: os.path.getmtime(os.path.join(_dir, f))))
            except OSError:
                pass
            return None

        @st.cache_data(show_spinner=False)
        def _vp_cd_reliability():
            return snapshots_engine.estimate_cd_reliability()

        try:
            _vp = _vp_calcular("marcas-vigentes")
        except Exception:
            _vp = None

        if _vp and _vp.get('banda_max', 0) > 0:
            # Componente B: quiebre ACTUAL por SKU×tienda (base cargada)
            _vp_q = pd.DataFrame()
            if 'estado' in df_cob.columns and 'prom_vta_uds' in df_cob.columns and 'precio_vigente' in df_cob.columns:
                _vp_q = df_cob[(df_cob['estado'] == 'QUIEBRE') & (df_cob['prom_vta_uds'].fillna(0) > 0)].copy()
                if not _vp_q.empty:
                    _vp_q['perdida_sem_soles'] = (_vp_q['prom_vta_uds'] * _vp_q['precio_vigente'].fillna(0)).round(0)
                    # Contribución en riesgo: lo que de verdad pierde el P&L (feedback Franco:
                    # "si meto más descuento vendo más, pero no necesariamente mejor contribución")
                    _vp_q['contrib_riesgo_sem'] = (
                        (_vp_q['perdida_sem_soles'] * _vp_q['margen_efectivo'].fillna(0)).round(0)
                        if 'margen_efectivo' in _vp_q.columns else _vp_q['perdida_sem_soles']
                    )
                    _vp_q['evitable'] = _vp_q['stock_cd'].fillna(0) > 0 if 'stock_cd' in _vp_q.columns else False

                    # ── Solución por quiebre: cruzar con el plan del motor ──
                    # 1) Plan de reposición (despacho CD u orden a proveedor)
                    if not df_rep.empty and 'a_reponer' in df_rep.columns and 'tienda' in df_rep.columns:
                        _vp_rep = df_rep[df_rep['a_reponer'] > 0][
                            ['sku', 'tienda', 'a_reponer'] +
                            (['requiere_proveedor'] if 'requiere_proveedor' in df_rep.columns else [])
                        ].drop_duplicates(['sku', 'tienda'])
                        _vp_q = _vp_q.merge(_vp_rep, on=['sku', 'tienda'], how='left')
                    # 1b) ATP del CD: el reporte no es tiempo real → solo cd_prometible_pct%
                    #     del CD reportado es prometible; deriva/volatilidad desde snapshots
                    try:
                        _vp_cdrel = _vp_cd_reliability()
                    except Exception:
                        _vp_cdrel = pd.DataFrame()
                    if not _vp_cdrel.empty:
                        _vp_q = _vp_q.merge(_vp_cdrel, on='sku', how='left')
                    if 'cd_deriva_sem' not in _vp_q.columns:
                        _vp_q['cd_deriva_sem'] = 0.0
                        _vp_q['cd_volatil'] = False
                    # clip(0): el reporte trae stock CD negativo en algunos SKUs (ajustes/tránsito)
                    _vp_q['cd_atp'] = (_vp_q['stock_cd'].fillna(0).clip(lower=0) * cd_prometible_pct / 100).astype(int) if 'stock_cd' in _vp_q.columns else 0
                    # Asignación del ATP por SKU: el CD es un pool ÚNICO compartido entre
                    # todas las tiendas quebradas del mismo SKU — se reparte por prioridad
                    # de venta en riesgo (no se promete el mismo stock dos veces)
                    _vp_q['uds_despacho'] = 0
                    if 'a_reponer' in _vp_q.columns:
                        # Prioridad por CONTRIBUCIÓN en riesgo, no por venta (decisión Franco 2026-06)
                        _vp_q = _vp_q.sort_values('contrib_riesgo_sem', ascending=False)
                        for _vp_sku_g, _vp_grp in _vp_q.groupby('sku', sort=False):
                            _vp_rem = int(_vp_grp['cd_atp'].iloc[0] or 0)
                            for _vp_gi, _vp_gr in _vp_grp.iterrows():
                                _vp_rp = _vp_gr.get('a_reponer')
                                if _vp_rp is None or pd.isna(_vp_rp) or _vp_rp <= 0 or _vp_gr.get('requiere_proveedor') is True:
                                    continue
                                _vp_asig = min(int(_vp_rp), max(_vp_rem, 0))
                                _vp_q.at[_vp_gi, 'uds_despacho'] = _vp_asig
                                _vp_rem -= _vp_asig
                    # 2) Transferencias sugeridas (desde tiendas con exceso)
                    if not df_trans.empty and 'tienda_destino' in df_trans.columns:
                        _vp_tr = df_trans.groupby(['sku', 'tienda_destino']).agg(
                            _vp_uds_tr=('uds_transferir', 'sum'),
                            _vp_origen=('tienda_origen', 'first'),
                            _vp_n_orig=('tienda_origen', 'nunique'),
                        ).reset_index().rename(columns={'tienda_destino': 'tienda'})
                        _vp_q = _vp_q.merge(_vp_tr, on=['sku', 'tienda'], how='left')

                    # Regla Franco 2026-06-12: categorías producibles en Perú (reorder
                    # nacional viable) vs solo-importado (costo nacional alto / mala calidad)
                    _VP_NACIONALIZABLES = {'POLOS M/C', 'CAMISAS M/L', 'CAMISAS M/C',
                                           'PANTALONES', 'JEANS', 'POLERONES'}
                    _VP_SOLO_IMPORTADO = {'CASACAS', 'CHOMPAS'}

                    def _vp_accion(r):
                        _vol = " ⚠️CD volátil" if r.get('cd_volatil') is True else ""
                        _rep = r.get('a_reponer')
                        if _rep is not None and not pd.isna(_rep) and _rep > 0:
                            if r.get('requiere_proveedor') is True:
                                return f"📋 Orden a proveedor — {int(_rep)} uds"
                            _asig = int(r.get('uds_despacho', 0) or 0)
                            if _asig <= 0:
                                return f"⏳ ATP del CD agotado por tiendas de mayor prioridad — transferir u ordenar (plan pedía {int(_rep)}){_vol}"
                            if _asig < int(_rep):
                                return f"🚚 Despachar del CD — {_asig} uds (ATP {cd_prometible_pct}% compartido; plan pedía {int(_rep)}){_vol}"
                            return f"🚚 Despachar del CD — {_asig} uds{_vol}"
                        _tr = r.get('_vp_uds_tr')
                        if _tr is not None and not pd.isna(_tr) and _tr > 0:
                            _org = str(r.get('_vp_origen', ''))
                            _extra = f" (+{int(r['_vp_n_orig']) - 1} tiendas)" if r.get('_vp_n_orig', 1) > 1 else ""
                            return f"🔄 Transferir {int(_tr)} uds desde {_org}{_extra}"
                        if r.get('evitable'):
                            return f"🚚 Stock en CD — revisar (excluido del plan por regla de dscto){_vol}"
                        _proc = str(r.get('procedencia', '') or '')
                        if _proc.startswith('IMP'):
                            _cat = str(r.get('categoria', '') or '').strip().upper()
                            if _cat in _VP_NACIONALIZABLES:
                                return "⛔→🇵🇪 Importado agotado (reorder no llega) — VIABLE producir nacional: esta categoría se hace en Perú"
                            if _cat in _VP_SOLO_IMPORTADO:
                                return "⛔ Estructural — solo importado (esta categoría no se hace nacional): sustituto o asumir pérdida"
                            return "⛔ Importado sin stock en cadena — reorder ~2-3 meses, no llega: evaluar sustituto o asumir pérdida"
                        if _proc.startswith('NAC'):
                            return "📋 Reorder nacional — gestionar con proveedor local (semanas)"
                        return "📋 Orden de compra (sin stock en cadena)"

                    _vp_base = _vp_base_path()
                    _vp_proc = _vp_procedencia_map(_vp_base) if _vp_base else {}
                    _vp_q['procedencia'] = _vp_q['sku'].map(_vp_proc).fillna('')

                    _vp_q['accion_sugerida'] = _vp_q.apply(_vp_accion, axis=1)
                    _vp_q = _vp_q.sort_values('contrib_riesgo_sem', ascending=False)

            st.markdown("---")
            st.markdown(f'<div class="section-header"><h3>💸 Ventas Perdidas por Quiebre</h3><span class="live-badge">NUEVO</span></div>', unsafe_allow_html=True)

            _vp_sem = _vp['semanas_analizadas']
            _vp_evit_n = int(_vp_q['evitable'].sum()) if not _vp_q.empty else 0
            _vp_evit_soles = float(_vp_q.loc[_vp_q['evitable'], 'perdida_sem_soles'].sum()) if not _vp_q.empty else 0.0

            _vp_c1, _vp_c2, _vp_c3 = st.columns([2, 1, 1])
            with _vp_c1:
                st.markdown(f"""
                <div style="background:#FEF2F2; border-radius:12px; padding:16px 20px; border-left:4px solid #DC2626;">
                    <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Venta perdida estimada — semanas {_vp_sem[0]} a {_vp_sem[-1]}</div>
                    <div style="font-size:1.6rem; font-weight:700; color:#DC2626;">S/ {_vp['banda_min']:,.0f} – S/ {_vp['banda_max']:,.0f}</div>
                    <div style="font-size:0.7rem; color:var(--capi-text2);">Dinero que se dejó de vender por quiebres de stock (banda conservadora–optimista)</div>
                </div>""", unsafe_allow_html=True)
            with _vp_c2:
                st.markdown(f"""
                <div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_CRITICO};">
                    <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">SKUs con quiebre en el período</div>
                    <div style="font-size:1.6rem; font-weight:700; color:{STATUS_CRITICO};">{_vp['n_skus_afectados']:,}</div>
                    <div style="font-size:0.7rem; color:var(--capi-text2);">con venta comprobada antes del quiebre</div>
                </div>""", unsafe_allow_html=True)
            with _vp_c3:
                st.markdown(f"""
                <div style="background:#FFFBEB; border-radius:12px; padding:16px 20px; border-left:4px solid #D97706;">
                    <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Quiebres evitables HOY</div>
                    <div style="font-size:1.6rem; font-weight:700; color:#D97706;">{_vp_evit_n:,}</div>
                    <div style="font-size:0.7rem; color:var(--capi-text2);">combos con stock en CD · S/ {_vp_evit_soles:,.0f}/sem en juego</div>
                </div>""", unsafe_allow_html=True)

            st.caption(" · ".join(_vp['supuestos']))

            with st.expander(f"Ver detalle y soluciones — top quiebres actuales por tienda ({len(_vp_q):,} combos)", expanded=False):
                if _vp_q.empty:
                    st.info("No hay quiebres activos con venta en la base cargada.")
                else:
                    # Mix de soluciones (resumen accionable)
                    _vp_n_cd = int(_vp_q['accion_sugerida'].str.startswith('🚚').sum())
                    _vp_n_tr = int(_vp_q['accion_sugerida'].str.startswith('🔄').sum())
                    _vp_n_oc = int(_vp_q['accion_sugerida'].str.startswith('📋').sum())
                    _vp_n_ag = int(_vp_q['accion_sugerida'].str.startswith('⏳').sum())
                    _vp_n_est = int(_vp_q['accion_sugerida'].str.startswith('⛔').sum())
                    st.markdown(
                        f"**Cómo se resuelven:** 🚚 **{_vp_n_cd:,}** despachos desde CD · "
                        f"🔄 **{_vp_n_tr:,}** transferencias entre tiendas · "
                        f"📋 **{_vp_n_oc:,}** órdenes de compra/proveedor"
                        + (f" · ⏳ **{_vp_n_ag:,}** en cola (ATP del CD agotado)" if _vp_n_ag else "")
                        + (f" · ⛔ **{_vp_n_est:,}** estructurales (importado agotado{', ' + str(int(_vp_q['accion_sugerida'].str.startswith('⛔→').sum())) + ' con opción de producir nacional' if _vp_q['accion_sugerida'].str.startswith('⛔→').any() else ''})" if _vp_n_est else "")
                    )
                    _vp_cols = [c for c in ['marca', 'sku', 'nombre', 'tienda', 'procedencia', 'prom_vta_uds',
                                             'precio_vigente', 'pct_descuento', 'margen_efectivo',
                                             'perdida_sem_soles', 'contrib_riesgo_sem', 'accion_sugerida'] if c in _vp_q.columns]
                    _vp_show = _vp_q[_vp_cols].head(20).rename(columns={
                        'marca': 'Marca', 'sku': 'SKU', 'nombre': 'Producto', 'tienda': 'Tienda',
                        'procedencia': 'Origen', 'prom_vta_uds': 'Vta/sem (uds)', 'precio_vigente': 'Precio',
                        'pct_descuento': 'Dscto', 'margen_efectivo': 'Margen efect.',
                        'perdida_sem_soles': 'Venta en riesgo S//sem',
                        'contrib_riesgo_sem': 'Contrib. en riesgo S//sem', 'accion_sugerida': 'Acción sugerida',
                    })
                    st.dataframe(_vp_show.style.format({
                        'Vta/sem (uds)': '{:.1f}', 'Precio': 'S/ {:,.2f}',
                        'Dscto': '{:.0%}', 'Margen efect.': '{:.0%}',
                        'Venta en riesgo S//sem': 'S/ {:,.0f}', 'Contrib. en riesgo S//sem': 'S/ {:,.0f}',
                    }, na_rep="—"), use_container_width=True, hide_index=True)
                    st.caption("La tabla y la asignación del ATP se priorizan por CONTRIBUCIÓN en riesgo (venta × margen efectivo), no por venta: un SKU con descuento alto vende más unidades pero puede aportar menos al P&L. Dscto y Margen efect. dan el contexto para decidir si la velocidad es demanda real o evento de precio.")
                    st.caption(f"La acción sale del plan del motor: despacho si hay stock en CD (cantidades del plan de reposición), transferencia si otra tienda tiene exceso, orden de compra si no hay stock en la cadena. "
                               f"ATP: el reporte de CD no es tiempo real, por eso solo el {cd_prometible_pct}% del CD reportado se considera prometible (configurable en ⚙️) y los SKUs con CD volátil entre cortes van flagueados ⚠️.")

                _vp_xl_buf = io.BytesIO()
                with pd.ExcelWriter(_vp_xl_buf, engine='openpyxl') as _vp_w:
                    if not _vp['df_detalle'].empty:
                        _vp['df_detalle'].to_excel(_vp_w, sheet_name='Perdida Historica por SKU', index=False)
                    if not _vp_q.empty:
                        _vp_q[_vp_cols].to_excel(_vp_w, sheet_name='Quiebre Actual SKUxTienda', index=False)
                _vp_xl_buf.seek(0)
                st.download_button(
                    "📥 Descargar análisis de ventas perdidas (.xlsx)",
                    data=_vp_xl_buf.getvalue(),
                    file_name="Capi_Ventas_Perdidas.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_ventas_perdidas",
                )


# ═══════════════════════════════════════════════════════════════
#  SALUD DEL STOCK (Prompt C)
#  Health Score compuesto: cobertura + quiebre + eficiencia + margen
# ═══════════════════════════════════════════════════════════════

elif nav_page == "🩺 Salud del Stock":
    st.markdown(f'<div class="section-header"><h3>Salud del Stock</h3><span class="live-badge">HEALTH SCORE</span></div>', unsafe_allow_html=True)

    if not _HAS_SNAPSHOTS:
        st.warning("El modulo de snapshots no esta disponible. Se necesitan snapshots para calcular Salud del Stock.")
    else:
        _hs_weeks = snapshots_engine.list_available_weeks()
        if not _hs_weeks:
            st.info("No hay snapshots disponibles. Carga datos para generar el Health Score.")
        else:
            _hs_sem = _hs_weeks[-1]
            _hs_df = snapshots_engine.api.get_snapshot(_hs_sem)

            if _hs_df is None or _hs_df.empty:
                st.warning(f"Snapshot de semana {_hs_sem} esta vacio.")
            else:
                st.caption(f"Semana: {_hs_sem} | {len(_hs_df):,} SKUs analizados")

                # Calcular scores
                _hs_global = motor_v2.build_health_score(_hs_df)
                _hs_marca = motor_v2.build_health_score(_hs_df, grupo_cols=['marca'])
                _hs_detail = motor_v2.build_health_detail(_hs_df)

                # ── Delta temporal: comparar con semana anterior ──
                _hs_prev_global = None
                _hs_prev_marca = None
                if len(_hs_weeks) >= 2:
                    _hs_sem_prev = _hs_weeks[-2]
                    _hs_df_prev = snapshots_engine.api.get_snapshot(_hs_sem_prev)
                    if _hs_df_prev is not None and not _hs_df_prev.empty:
                        _hs_prev_global = motor_v2.build_health_score(_hs_df_prev)
                        _hs_prev_marca = motor_v2.build_health_score(_hs_df_prev, grupo_cols=['marca'])

                _hs_tabs = st.tabs([
                    "🎯 Diagnostico Rapido",
                    "🏆 Ranking Marcas",
                    "🔍 Drill-down",
                    "📋 Detalle SKU",
                ])

                # ── Tab 1: Diagnostico Rapido ──
                with _hs_tabs[0]:
                    if _hs_global is None or _hs_global.empty:
                        st.warning("No se pudo calcular el Health Score con los datos actuales.")
                        st.stop()
                    _g = _hs_global.iloc[0]
                    _hs_score = float(_g['health_score'])
                    _hs_semaforo = str(_g['semaforo'])

                    # Colores de semaforo
                    _sem_colors = {
                        'SALUDABLE': '#059669',
                        'ACEPTABLE': '#0ea5e9',
                        'EN RIESGO': '#f59e0b',
                        'CRITICO': '#dc2626',
                    }
                    _sem_color = _sem_colors.get(_hs_semaforo, SLATE_500)

                    # Gauge visual con HTML + delta semanal
                    _gauge_pct = min(_hs_score / 100, 1.0)
                    _delta_html = ""
                    if _hs_prev_global is not None and not _hs_prev_global.empty:
                        _prev_score = float(_hs_prev_global.iloc[0]['health_score'])
                        _delta = _hs_score - _prev_score
                        if abs(_delta) >= 0.1:
                            _d_arrow = "▲" if _delta > 0 else "▼"
                            _d_color = "#059669" if _delta > 0 else "#dc2626"
                            _delta_html = f'<span style="font-size:1rem; color:{_d_color}; margin-left:8px;">{_d_arrow} {abs(_delta):.1f}</span>'
                    st.markdown(f"""
                    <div style="text-align:center; padding:24px 0 16px 0;">
                        <div style="font-size:4rem; font-weight:800; color:{_sem_color}; line-height:1;">{_hs_score:.0f}{_delta_html}</div>
                        <div style="font-size:0.85rem; font-weight:600; color:{_sem_color}; letter-spacing:2px; margin-top:4px;">{_hs_semaforo}</div>
                        <div style="margin:12px auto 0; width:200px; height:8px; background:{SLATE_200}; border-radius:4px; overflow:hidden;">
                            <div style="width:{_gauge_pct*100:.0f}%; height:100%; background:{_sem_color}; border-radius:4px;"></div>
                        </div>
                        <div style="font-size:0.7rem; color:var(--capi-text2); margin-top:6px;">Health Score (0-100){' | vs semana anterior' if _delta_html else ''}</div>
                    </div>
                    """, unsafe_allow_html=True)

                    # 5 componentes en cards
                    _comp_data = [
                        ("Cobertura", f"{float(_g['pct_optimo_alto'])*100:.0f}%", f"{float(_g['score_cobertura']):.0f}/100", "25%",
                         "SKUs en OPTIMO + ALTO", '#059669' if float(_g['score_cobertura']) >= 70 else '#f59e0b' if float(_g['score_cobertura']) >= 40 else '#dc2626'),
                        ("Quiebre", f"{float(_g['pct_quiebre'])*100:.0f}%", f"{float(_g['score_quiebre']):.0f}/100", "20%",
                         "En QUIEBRE + PRE-QUIEBRE", '#059669' if float(_g['score_quiebre']) >= 70 else '#f59e0b' if float(_g['score_quiebre']) >= 40 else '#dc2626'),
                        ("Sobrestock", f"{float(_g['pct_exceso'])*100:.0f}%", f"{float(_g['score_sobrestock']):.0f}/100", "15%",
                         "Capital parado (menos = mejor)", '#059669' if float(_g['score_sobrestock']) >= 70 else '#f59e0b' if float(_g['score_sobrestock']) >= 40 else '#dc2626'),
                        ("Eficiencia", f"{float(_g['rotacion'])*100:.1f}%", f"{float(_g['score_eficiencia']):.0f}/100", "20%",
                         "Rotacion a costo", '#059669' if float(_g['score_eficiencia']) >= 70 else '#f59e0b' if float(_g['score_eficiencia']) >= 40 else '#dc2626'),
                        ("Margen", f"{float(_g['margen_pct'])*100:.1f}%", f"{float(_g['score_margen']):.0f}/100", "20%",
                         "Contribucion / Venta", '#059669' if float(_g['score_margen']) >= 70 else '#f59e0b' if float(_g['score_margen']) >= 40 else '#dc2626'),
                    ]
                    _hc1, _hc2, _hc3, _hc4, _hc5 = st.columns(5)
                    for _col_hs, (_title, _val, _sc, _weight, _desc, _color) in zip([_hc1, _hc2, _hc3, _hc4, _hc5], _comp_data):
                        with _col_hs:
                            st.markdown(f"""
                            <div style="background:var(--capi-bg-surface); border:1px solid {SLATE_200}; border-radius:10px; padding:14px; text-align:center; border-top:3px solid {_color};">
                                <div style="font-size:0.7rem; color:var(--capi-text2); font-weight:600; letter-spacing:1px;">{_title.upper()}</div>
                                <div style="font-size:1.6rem; font-weight:700; color:{_color}; margin:4px 0;">{_val}</div>
                                <div style="font-size:0.75rem; color:var(--capi-text2);">Score: {_sc} | Peso: {_weight}</div>
                                <div style="font-size:0.65rem; color:{SLATE_400}; margin-top:4px;">{_desc}</div>
                            </div>
                            """, unsafe_allow_html=True)

                    # KPIs de contexto
                    st.markdown("---")
                    _kc1, _kc2, _kc3, _kc4 = st.columns(4)
                    _kc1.metric("Total SKUs", f"{int(_g['n_skus']):,}")
                    _kc2.metric("Con Stock", f"{int(_g['n_con_stock']):,}")
                    _kc3.metric("Capital Parado", f"S/{float(_g['capital_parado']):,.0f}")
                    _kc4.metric("Venta en Riesgo", f"S/{float(_g['venta_en_riesgo']):,.0f}")

                # ── Tab 2: Ranking Marcas ──
                with _hs_tabs[1]:
                    st.markdown("##### Ranking de marcas por Health Score")

                    # ── Filtro de materialidad: excluir marcas insignificantes ──
                    _MIN_SKUS_MATERIAL = 5
                    _MIN_VENTA_MATERIAL = 500
                    _hs_marca_material = _hs_marca[
                        (_hs_marca['n_skus'] >= _MIN_SKUS_MATERIAL) |
                        (_hs_marca['venta_total'] >= _MIN_VENTA_MATERIAL)
                    ].copy()
                    _n_excluidas = len(_hs_marca) - len(_hs_marca_material)
                    if _n_excluidas > 0:
                        st.caption(f"{_n_excluidas} marcas excluidas por baja materialidad (<{_MIN_SKUS_MATERIAL} SKUs activos y <S/{_MIN_VENTA_MATERIAL:,} venta)")

                    # Filtro de semaforo
                    _hs_fc1, _hs_fc2 = st.columns([2, 1])
                    with _hs_fc1:
                        _hs_sem_filter = st.multiselect(
                            "Filtrar por semaforo",
                            ['SALUDABLE', 'ACEPTABLE', 'EN RIESGO', 'CRITICO'],
                            default=['SALUDABLE', 'ACEPTABLE', 'EN RIESGO', 'CRITICO'],
                            key="hs_sem_filter"
                        )
                    with _hs_fc2:
                        _hs_sort_by = st.selectbox(
                            "Ordenar por",
                            ['Health Score', 'Capital Parado', 'Venta en Riesgo', 'Impacto Negocio'],
                            key="hs_sort_by"
                        )
                    _hs_marca_f = _hs_marca_material[_hs_marca_material['semaforo'].isin(_hs_sem_filter)].copy()

                    if _hs_marca_f.empty:
                        st.info("No hay marcas con el filtro seleccionado.")
                    else:
                        # ── Delta semanal por marca ──
                        if _hs_prev_marca is not None and not _hs_prev_marca.empty:
                            _prev_map = _hs_prev_marca.set_index('marca')['health_score'].to_dict()
                            _hs_marca_f['_prev_score'] = _hs_marca_f['marca'].map(_prev_map)
                            _hs_marca_f['delta_score'] = (_hs_marca_f['health_score'] - _hs_marca_f['_prev_score'].fillna(_hs_marca_f['health_score'])).round(1)
                            _hs_marca_f = _hs_marca_f.drop(columns=['_prev_score'])
                        else:
                            _hs_marca_f['delta_score'] = 0.0

                        # Ordenar según selección
                        _sort_col_map = {
                            'Health Score': 'health_score',
                            'Capital Parado': 'capital_parado',
                            'Venta en Riesgo': 'venta_en_riesgo',
                            'Impacto Negocio': 'impacto',
                        }
                        _sort_col = _sort_col_map.get(_hs_sort_by, 'health_score')
                        # Impacto ordena desc (mayor impacto primero); el resto asc (peor score primero)
                        _sort_asc = False if _sort_col == 'impacto' else True
                        _hs_marca_sorted = _hs_marca_f.sort_values(_sort_col, ascending=_sort_asc)

                        # Horizontal bar chart con Plotly
                        _bar_colors = []
                        for _s in _hs_marca_sorted['semaforo']:
                            _bar_colors.append(_sem_colors.get(str(_s), SLATE_400))

                        _fig_rank = go.Figure(go.Bar(
                            x=_hs_marca_sorted['health_score'],
                            y=_hs_marca_sorted['marca'],
                            orientation='h',
                            marker_color=_bar_colors,
                            text=[f"{v:.0f}" for v in _hs_marca_sorted['health_score']],
                            textposition='outside',
                            textfont=dict(size=11),
                        ))
                        _fig_rank.update_layout(
                            height=max(300, len(_hs_marca_sorted) * 28),
                            margin=dict(l=0, r=40, t=10, b=10),
                            xaxis=dict(range=[min(_hs_marca_sorted['health_score'].min() - 10, 0), 105], title="Health Score"),
                            yaxis=dict(automargin=True),
                            plot_bgcolor='rgba(0,0,0,0)',
                            paper_bgcolor='rgba(0,0,0,0)',
                        )
                        # Lineas de referencia para semaforo
                        for _thresh, _lbl in [(40, 'CRITICO'), (60, 'EN RIESGO'), (75, 'ACEPTABLE')]:
                            _fig_rank.add_vline(x=_thresh, line_dash="dot", line_color=SLATE_400, line_width=1,
                                                annotation_text=_lbl, annotation_position="top")
                        st.plotly_chart(_fig_rank, use_container_width=True)

                        # ── Scatter Plot: Score vs Capital Parado ──
                        st.markdown("##### Mapa de Impacto: Score vs Capital")
                        _fig_scatter = go.Figure()
                        _scatter_colors = [_sem_colors.get(str(s), SLATE_400) for s in _hs_marca_f['semaforo']]
                        # Bubble size = impacto (si existe) o n_skus
                        if 'impacto' in _hs_marca_f.columns and _hs_marca_f['impacto'].max() > 0:
                            _bubble_raw = _hs_marca_f['impacto'].fillna(0)
                            _bubble_size = (_bubble_raw / _bubble_raw.max() * 35 + 8).clip(upper=50)
                            _hover_extra = "Impacto: %{customdata:.1f}<br>"
                            _customdata = _hs_marca_f['impacto']
                        else:
                            _bubble_size = (_hs_marca_f['n_skus'] / _hs_marca_f['n_skus'].max() * 30 + 8).clip(upper=45)
                            _hover_extra = ""
                            _customdata = None
                        _fig_scatter.add_trace(go.Scatter(
                            x=_hs_marca_f['health_score'],
                            y=_hs_marca_f['capital_parado'],
                            mode='markers+text',
                            marker=dict(
                                size=_bubble_size,
                                color=_scatter_colors,
                                opacity=0.8,
                                line=dict(width=1, color='white'),
                            ),
                            text=_hs_marca_f['marca'],
                            textposition='top center',
                            textfont=dict(size=9),
                            customdata=_customdata,
                            hovertemplate="<b>%{text}</b><br>Score: %{x:.0f}<br>Capital Parado: S/%{y:,.0f}<br>" + _hover_extra + "<extra></extra>",
                        ))
                        # Cuadrante peligroso: bajo score + alto capital
                        _fig_scatter.add_shape(
                            type="rect", x0=-100, x1=60, y0=_hs_marca_f['capital_parado'].median(), y1=_hs_marca_f['capital_parado'].max() * 1.1,
                            fillcolor="rgba(220,38,38,0.05)", line=dict(width=0),
                        )
                        _fig_scatter.add_annotation(
                            x=30, y=_hs_marca_f['capital_parado'].max() * 0.95,
                            text="ZONA CRITICA", showarrow=False,
                            font=dict(size=10, color="#dc2626"), opacity=0.6,
                        )
                        _fig_scatter.update_layout(
                            height=400,
                            margin=dict(l=0, r=0, t=10, b=0),
                            xaxis=dict(title="Health Score", zeroline=True),
                            yaxis=dict(title="Capital Parado (S/)", tickformat=","),
                            plot_bgcolor='rgba(0,0,0,0)',
                            paper_bgcolor='rgba(0,0,0,0)',
                        )
                        st.plotly_chart(_fig_scatter, use_container_width=True)

                        # Tabla resumen
                        st.markdown("##### Detalle por marca")
                        _tbl_cols = ['marca', 'health_score', 'semaforo', 'n_skus', 'n_con_stock',
                                     'pct_optimo_alto', 'pct_quiebre', 'pct_exceso', 'rotacion', 'margen_pct',
                                     'capital_parado', 'venta_en_riesgo']
                        _tbl_headers = ['Marca', 'Score', 'Semaforo', 'SKUs', 'Con Stock',
                                        '% Opt+Alto', '% Quiebre', '% Exceso', 'Rotación %', 'Margen %',
                                        'Capital Parado', 'Vta en Riesgo']
                        # Agregar impacto y prioridad si existen (motor los genera a nivel marca)
                        if 'impacto' in _hs_marca_f.columns:
                            _tbl_cols.extend(['impacto', 'prioridad'])
                            _tbl_headers.extend(['Impacto', 'Prioridad'])
                        # Agregar delta si existe
                        if 'delta_score' in _hs_marca_f.columns and _hs_marca_f['delta_score'].abs().sum() > 0:
                            _tbl_cols.insert(2, 'delta_score')
                            _tbl_headers.insert(2, 'Delta')
                        _tbl_marca = _hs_marca_f[_tbl_cols].copy()
                        _tbl_marca.columns = _tbl_headers
                        _tbl_marca['% Opt+Alto'] = (_tbl_marca['% Opt+Alto'] * 100).round(1)
                        _tbl_marca['% Quiebre'] = (_tbl_marca['% Quiebre'] * 100).round(1)
                        _tbl_marca['% Exceso'] = (_tbl_marca['% Exceso'] * 100).round(1)
                        _tbl_marca['Rotación %'] = (_tbl_marca['Rotación %'] * 100).round(1)
                        _tbl_marca['Margen %'] = (_tbl_marca['Margen %'] * 100).round(1)
                        _tbl_marca['Capital Parado'] = _tbl_marca['Capital Parado'].apply(lambda x: f"S/{x:,.0f}")
                        _tbl_marca['Vta en Riesgo'] = _tbl_marca['Vta en Riesgo'].apply(lambda x: f"S/{x:,.0f}")
                        _tbl_marca = _tbl_marca.sort_values('Score', ascending=False)
                        st.dataframe(_tbl_marca, use_container_width=True, hide_index=True, height=500)

                        # Tags de prioridad con color (debajo de la tabla)
                        if 'impacto' in _hs_marca_f.columns:
                            _foco = _hs_marca_f[_hs_marca_f['prioridad'] == 'FOCO URGENTE']['marca'].tolist()
                            _monit = _hs_marca_f[_hs_marca_f['prioridad'] == 'MONITOREAR']['marca'].tolist()
                            if _foco:
                                _foco_tags = " ".join([f"<span style='background:#dc2626;color:white;padding:2px 8px;border-radius:4px;font-size:0.82em;font-weight:600;'>{m}</span>" for m in _foco])
                                st.markdown(f"🔴 **FOCO URGENTE**: {_foco_tags}", unsafe_allow_html=True)
                            if _monit:
                                _monit_tags = " ".join([f"<span style='background:#f59e0b;color:white;padding:2px 8px;border-radius:4px;font-size:0.82em;font-weight:600;'>{m}</span>" for m in _monit])
                                st.markdown(f"🟡 **MONITOREAR**: {_monit_tags}", unsafe_allow_html=True)

                        # Excel download
                        _buf_marca = io.BytesIO()
                        _tbl_marca.to_excel(_buf_marca, index=False, sheet_name='Ranking Marcas')
                        st.download_button(
                            "Descargar ranking Excel",
                            data=_buf_marca.getvalue(),
                            file_name=f"health_ranking_marcas_{_hs_sem}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="hs_dl_ranking"
                        )

                # ── Tab 3: Drill-down ──
                with _hs_tabs[2]:
                    st.markdown("##### Drill-down: Marca > Linea > Temporada")

                    _dd_marca_sel = st.selectbox(
                        "Seleccionar marca",
                        sorted(_hs_marca['marca'].unique()),
                        key="hs_dd_marca"
                    )

                    if _dd_marca_sel and not _hs_marca[_hs_marca['marca'] == _dd_marca_sel].empty:
                        # Score de la marca
                        _dd_marca_row = _hs_marca[_hs_marca['marca'] == _dd_marca_sel].iloc[0]
                        _dd_color = _sem_colors.get(str(_dd_marca_row['semaforo']), SLATE_500)
                        st.markdown(f"""
                        <div style="background:var(--capi-bg-surface); border:1px solid {SLATE_200}; border-radius:10px; padding:12px 16px; margin-bottom:12px; display:flex; align-items:center; gap:16px;">
                            <div style="font-size:2rem; font-weight:800; color:{_dd_color};">{float(_dd_marca_row['health_score']):.0f}</div>
                            <div>
                                <div style="font-size:1rem; font-weight:600; color:var(--capi-text);">{_dd_marca_sel}</div>
                                <div style="font-size:0.75rem; color:{_dd_color}; font-weight:600;">{_dd_marca_row['semaforo']}</div>
                            </div>
                            <div style="margin-left:auto; display:flex; gap:20px;">
                                <div style="text-align:center;"><div style="font-size:0.65rem; color:var(--capi-text2);">SKUs</div><div style="font-weight:600;">{int(_dd_marca_row['n_skus'])}</div></div>
                                <div style="text-align:center;"><div style="font-size:0.65rem; color:var(--capi-text2);">Rotación %</div><div style="font-weight:600;">{float(_dd_marca_row['rotacion'])*100:.1f}%</div></div>
                                <div style="text-align:center;"><div style="font-size:0.65rem; color:var(--capi-text2);">Margen</div><div style="font-weight:600;">{float(_dd_marca_row['margen_pct'])*100:.1f}%</div></div>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)

                        # Drill-down por linea
                        _dd_df_marca = _hs_df[_hs_df['marca'] == _dd_marca_sel]
                        _dd_linea = motor_v2.build_health_score(_dd_df_marca, grupo_cols=['linea'])

                        if not _dd_linea.empty:
                            st.markdown(f"###### Por Linea ({len(_dd_linea)} lineas)")
                            _dd_linea_sorted = _dd_linea.sort_values('health_score', ascending=False)
                            _dd_bar_colors = [_sem_colors.get(str(s), SLATE_400) for s in _dd_linea_sorted['semaforo']]

                            _fig_dd = go.Figure(go.Bar(
                                x=_dd_linea_sorted['linea'],
                                y=_dd_linea_sorted['health_score'],
                                marker_color=_dd_bar_colors,
                                text=[f"{v:.0f}" for v in _dd_linea_sorted['health_score']],
                                textposition='outside',
                            ))
                            _fig_dd.update_layout(
                                height=350,
                                margin=dict(l=0, r=0, t=10, b=0),
                                yaxis=dict(range=[0, 105], title="Health Score"),
                                xaxis=dict(automargin=True),
                                plot_bgcolor='rgba(0,0,0,0)',
                                paper_bgcolor='rgba(0,0,0,0)',
                            )
                            st.plotly_chart(_fig_dd, use_container_width=True)

                        # Drill-down por temporada dentro de la marca
                        _dd_temp = motor_v2.build_health_score(_dd_df_marca, grupo_cols=['temporada'])
                        if not _dd_temp.empty:
                            st.markdown("###### Por Temporada")
                            _tc1, _tc2, _tc3 = st.columns(3)
                            for _col_t, _temp in zip([_tc1, _tc2, _tc3], ['OI', 'PV', 'TT']):
                                with _col_t:
                                    _tr = _dd_temp[_dd_temp['temporada'] == _temp]
                                    if not _tr.empty:
                                        _tr = _tr.iloc[0]
                                        _tc = _sem_colors.get(str(_tr['semaforo']), SLATE_400)
                                        st.markdown(f"""
                                        <div style="background:var(--capi-bg-surface); border:1px solid {SLATE_200}; border-radius:8px; padding:12px; text-align:center;">
                                            <div style="font-size:0.7rem; color:var(--capi-text2); font-weight:600;">{_temp}</div>
                                            <div style="font-size:1.8rem; font-weight:700; color:{_tc};">{float(_tr['health_score']):.0f}</div>
                                            <div style="font-size:0.65rem; color:{_tc};">{_tr['semaforo']}</div>
                                            <div style="font-size:0.6rem; color:var(--capi-text2); margin-top:4px;">{int(_tr['n_skus'])} SKUs | {float(_tr['pct_quiebre'])*100:.0f}% quiebre</div>
                                        </div>
                                        """, unsafe_allow_html=True)
                                    else:
                                        st.markdown(f"""
                                        <div style="background:var(--capi-bg-surface); border:1px solid {SLATE_200}; border-radius:8px; padding:12px; text-align:center;">
                                            <div style="font-size:0.7rem; color:var(--capi-text2);">{_temp}</div>
                                            <div style="font-size:1rem; color:{SLATE_400};">Sin data</div>
                                        </div>
                                        """, unsafe_allow_html=True)

                        # Heatmap linea × temporada
                        _dd_lt = motor_v2.build_health_score(_dd_df_marca, grupo_cols=['linea', 'temporada'])
                        if not _dd_lt.empty and len(_dd_lt) > 1:
                            st.markdown("###### Heatmap: Linea x Temporada")
                            _pivot_hs = _dd_lt.pivot_table(index='linea', columns='temporada', values='health_score', aggfunc='first')
                            # Reorder temporada columns
                            _temp_order = [t for t in ['OI', 'PV', 'TT'] if t in _pivot_hs.columns]
                            _pivot_hs = _pivot_hs[_temp_order]

                            _fig_hm = go.Figure(go.Heatmap(
                                z=_pivot_hs.values,
                                x=_pivot_hs.columns.tolist(),
                                y=_pivot_hs.index.tolist(),
                                colorscale=[[0, '#dc2626'], [0.4, '#f59e0b'], [0.6, '#0ea5e9'], [0.75, '#059669'], [1, '#059669']],
                                zmin=0, zmax=100,
                                text=[[f"{v:.0f}" if pd.notna(v) else "" for v in row] for row in _pivot_hs.values],
                                texttemplate="%{text}",
                                textfont=dict(size=14, color="white"),
                                hovertemplate="Linea: %{y}<br>Temporada: %{x}<br>Health Score: %{z:.0f}<extra></extra>",
                                colorbar=dict(title="Score", tickvals=[0, 25, 50, 75, 100]),
                            ))
                            _fig_hm.update_layout(
                                height=max(200, len(_pivot_hs) * 35 + 60),
                                margin=dict(l=0, r=0, t=10, b=0),
                                yaxis=dict(automargin=True),
                                plot_bgcolor='rgba(0,0,0,0)',
                                paper_bgcolor='rgba(0,0,0,0)',
                            )
                            st.plotly_chart(_fig_hm, use_container_width=True)

                # ── Tab 4: Detalle SKU ──
                with _hs_tabs[3]:
                    st.markdown("##### Detalle a nivel SKU")

                    # Filtros
                    _dt_c1, _dt_c2, _dt_c3 = st.columns(3)
                    with _dt_c1:
                        _dt_marca = st.selectbox("Marca", ['Todas'] + sorted(_hs_detail['marca'].unique()), key="hs_dt_marca")
                    with _dt_c2:
                        _lineas_avail = sorted(_hs_detail['linea'].dropna().unique()) if _dt_marca == 'Todas' else sorted(_hs_detail[_hs_detail['marca'] == _dt_marca]['linea'].dropna().unique())
                        _dt_linea = st.selectbox("Linea", ['Todas'] + list(_lineas_avail), key="hs_dt_linea")
                    with _dt_c3:
                        _dt_estado = st.multiselect("Estado", sorted(_hs_detail['estado'].unique()), default=[], key="hs_dt_estado")

                    _dt_df = _hs_detail.copy()
                    if _dt_marca != 'Todas':
                        _dt_df = _dt_df[_dt_df['marca'] == _dt_marca]
                    if _dt_linea != 'Todas':
                        _dt_df = _dt_df[_dt_df['linea'] == _dt_linea]
                    if _dt_estado:
                        _dt_df = _dt_df[_dt_df['estado'].isin(_dt_estado)]

                    st.caption(f"{len(_dt_df):,} SKUs filtrados")

                    # Tabla
                    _dt_show = _dt_df[['sku', 'descripcion', 'marca', 'linea', 'temporada', 'estado',
                                       'cobertura_sem', 'stock_total', 'stock_cd', 'venta_soles',
                                       'contribucion_soles', 'stock_valor_costo', 'pct_descuento',
                                       'edad_semanas']].copy()
                    _dt_show.columns = ['SKU', 'Descripcion', 'Marca', 'Linea', 'Temp', 'Estado',
                                        'Cob (sem)', 'Stock Total', 'Stock CD', 'Venta S/',
                                        'Contrib S/', 'Stock Costo', '% Dscto', 'Edad (sem)']
                    _dt_show['Cob (sem)'] = _dt_show['Cob (sem)'].round(1)
                    _dt_show['Venta S/'] = _dt_show['Venta S/'].round(0)
                    _dt_show['Contrib S/'] = _dt_show['Contrib S/'].round(0)
                    _dt_show['Stock Costo'] = _dt_show['Stock Costo'].round(0)
                    _dt_show['% Dscto'] = (_dt_show['% Dscto'] * 100).round(0)

                    st.dataframe(_dt_show, use_container_width=True, hide_index=True, height=500)

                    # Excel download
                    _buf_detail = io.BytesIO()
                    _dt_show.to_excel(_buf_detail, index=False, sheet_name='Detalle SKU')
                    st.download_button(
                        "Descargar detalle Excel",
                        data=_buf_detail.getvalue(),
                        file_name=f"health_detalle_sku_{_hs_sem}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="hs_dl_detail"
                    )


# ═══════════════════════════════════════════════════════════════
#  VISTA CAPI 1: REPOSICIÓN
#  KPIs: % SKUs críticos, # tiendas cob baja, capital atrapado
# ═══════════════════════════════════════════════════════════════

elif nav_page == "📦 Reposición":

    # ── KPIs específicos de la vista ──
    _n_critico = s.get('n_critico', 0)
    _n_total = s.get('total_combos', 1)
    _pct_critico = (_n_critico / _n_total * 100) if _n_total > 0 else 0
    _capital_critico = df_cob[df_cob['estado'] == 'QUIEBRE']['stock_valor_costo'].sum() if 'estado' in df_cob.columns else 0
    _tiendas_cob_baja = df_cob[df_cob['cobertura_sem'] < params['umbral_critico']]['tienda'].nunique() if 'tienda' in df_cob.columns else 0

    st.markdown(f'<div class="section-header"><h3>Vista Reposición</h3><span class="live-badge">GESTIÓN DE STOCK</span></div>', unsafe_allow_html=True)

    _kpi_cols = st.columns(3)
    with _kpi_cols[0]:
        st.markdown(f"""
        <div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_CRITICO};">
            <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">% SKUs en estado crítico</div>
            <div style="font-size:1.6rem; font-weight:700; color:{STATUS_CRITICO};">{_pct_critico:.1f}%</div>
            <div style="font-size:0.7rem; color:var(--capi-text2);">{_n_critico:,} de {_n_total:,} combos</div>
        </div>""", unsafe_allow_html=True)
    with _kpi_cols[1]:
        st.markdown(f"""
        <div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_PRECRITICO};">
            <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Tiendas con cobertura baja</div>
            <div style="font-size:1.6rem; font-weight:700; color:{STATUS_PRECRITICO};">{_tiendas_cob_baja}</div>
            <div style="font-size:0.7rem; color:var(--capi-text2);">< {params['umbral_critico']} semanas</div>
        </div>""", unsafe_allow_html=True)
    with _kpi_cols[2]:
        st.markdown(f"""
        <div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {TEAL_600};">
            <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Capital atrapado en críticos</div>
            <div style="font-size:1.6rem; font-weight:700; color:{TEAL_700};">S/ {_capital_critico:,.0f}</div>
            <div style="font-size:0.7rem; color:var(--capi-text2);">SKUs en estado QUIEBRE</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    st.markdown("---")

    st.markdown(f"""<div style="background:#FEF2F2; border-left:4px solid {STATUS_CRITICO}; padding:10px 14px; border-radius:10px; margin-bottom:10px;">
    <strong style="color:{STATUS_CRITICO};">Reposición Pendiente por Marca</strong>
    <span style="color:var(--capi-text2); font-size:0.82em;"> &nbsp;·&nbsp; Unidades a reponer y costo total estimado por temporada</span>
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

    # ── REPO EN RIESGO — Mini-vista Prompt F ──
    # SKUs con repo calculada pero riesgo de que no se materialice
    if not df_rep.empty and 'riesgo_repo' in df_rep.columns:
        _df_riesgo = df_rep[df_rep['riesgo_repo'] == True].copy()
        if not _df_riesgo.empty:
            st.markdown("---")
            _n_riesgo = len(_df_riesgo)
            _n_inminente = _df_riesgo['quiebre_inminente'].sum() if 'quiebre_inminente' in _df_riesgo.columns else 0
            _n_proveedor = _df_riesgo['requiere_proveedor'].sum() if 'requiere_proveedor' in _df_riesgo.columns else 0
            _n_descont = _df_riesgo['descontinuado_temporal'].sum() if 'descontinuado_temporal' in _df_riesgo.columns else 0

            st.markdown(f"""<div style="background:#FEF2F2; border-left:4px solid #DC2626; padding:12px 16px; border-radius:10px; margin-bottom:12px;">
            <strong style="color:#DC2626;">Repo en Riesgo</strong>
            <span style="color:var(--capi-text2); font-size:0.82em;"> &nbsp;·&nbsp; {_n_riesgo} líneas con repo calculada pero riesgo de no materializarse</span>
            </div>""", unsafe_allow_html=True)

            _rk1, _rk2, _rk3 = st.columns(3)
            with _rk1:
                st.markdown(f"""
                <div style="background:var(--capi-bg-surface); border-radius:10px; padding:12px 16px; border-left:3px solid #DC2626;">
                    <div style="font-size:0.72rem; color:var(--capi-text2);">Quiebre Inminente</div>
                    <div style="font-size:1.4rem; font-weight:700; color:#DC2626;">{int(_n_inminente)}</div>
                    <div style="font-size:0.68rem; color:var(--capi-text2);">Cobertura &lt; lead time proveedor</div>
                </div>""", unsafe_allow_html=True)
            with _rk2:
                st.markdown(f"""
                <div style="background:var(--capi-bg-surface); border-radius:10px; padding:12px 16px; border-left:3px solid #F59E0B;">
                    <div style="font-size:0.72rem; color:var(--capi-text2);">Depende de Proveedor</div>
                    <div style="font-size:1.4rem; font-weight:700; color:#F59E0B;">{int(_n_proveedor)}</div>
                    <div style="font-size:0.68rem; color:var(--capi-text2);">Terceras sin stock CD</div>
                </div>""", unsafe_allow_html=True)
            with _rk3:
                st.markdown(f"""
                <div style="background:var(--capi-bg-surface); border-radius:10px; padding:12px 16px; border-left:3px solid #8B5CF6;">
                    <div style="font-size:0.72rem; color:var(--capi-text2);">Descontinuado Temporal</div>
                    <div style="font-size:1.4rem; font-weight:700; color:#8B5CF6;">{int(_n_descont)}</div>
                    <div style="font-size:0.68rem; color:var(--capi-text2);">Temporada opuesta + edad &gt;16 sem</div>
                </div>""", unsafe_allow_html=True)

            # Tabla detalle de riesgo
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
            _riesgo_cols = ['marca', 'sku', 'nombre', 'tienda', 'cobertura_actual', 'a_reponer',
                            'lead_time_dias', 'stock_cd', 'temporada', 'urgencia']
            _riesgo_show = [c for c in _riesgo_cols if c in _df_riesgo.columns]

            # Agregar columna legible de tipo de riesgo
            def _tipo_riesgo(row):
                tipos = []
                if row.get('quiebre_inminente', False):
                    tipos.append('Quiebre Inminente')
                if row.get('requiere_proveedor', False):
                    tipos.append('Sin Stock CD')
                if row.get('descontinuado_temporal', False):
                    tipos.append('Descontinuado')
                return ' · '.join(tipos) if tipos else ''

            _df_riesgo_show = _df_riesgo[_riesgo_show].copy()
            _df_riesgo_show['tipo_riesgo'] = _df_riesgo.apply(_tipo_riesgo, axis=1)

            _riesgo_rename = {
                'marca': 'Marca', 'sku': 'SKU', 'nombre': 'Nombre',
                'tienda': 'Tienda', 'cobertura_actual': 'Cob Sem',
                'a_reponer': 'A Reponer', 'lead_time_dias': 'Lead Time (d)',
                'stock_cd': 'Stock CD', 'temporada': 'Temp', 'urgencia': 'Urgencia',
                'tipo_riesgo': 'Tipo Riesgo',
            }
            _df_riesgo_show = _df_riesgo_show.rename(columns=_riesgo_rename)

            with st.expander(f"Ver detalle — {_n_riesgo} líneas en riesgo", expanded=False):
                st.dataframe(_df_riesgo_show, use_container_width=True, hide_index=True)


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

    st.markdown(f'<div class="section-header"><h3>Vista Sobrestock</h3><span class="live-badge">GESTIÓN DE STOCK</span></div>', unsafe_allow_html=True)

    _kpi_cols2 = st.columns(3)
    with _kpi_cols2[0]:
        st.markdown(f"""
        <div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_SOBRESTOCK};">
            <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Capital en sobrestock</div>
            <div style="font-size:1.6rem; font-weight:700; color:{STATUS_SOBRESTOCK};">S/ {_capital_sobre:,.0f}</div>
            <div style="font-size:0.7rem; color:var(--capi-text2);">{_n_sobrestock:,} combos SKU×Tienda</div>
        </div>""", unsafe_allow_html=True)
    with _kpi_cols2[1]:
        st.markdown(f"""
        <div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_ALTO};">
            <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">% sobrestock aparente</div>
            <div style="font-size:1.6rem; font-weight:700; color:{STATUS_ALTO};">{_pct_aparente:.0f}%</div>
            <div style="font-size:0.7rem; color:var(--capi-text2);">{_n_aparente} combos con >60% stock en CD</div>
        </div>""", unsafe_allow_html=True)
    with _kpi_cols2[2]:
        st.markdown(f"""
        <div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_MUERTO};">
            <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">SKUs con >40 sem cobertura</div>
            <div style="font-size:1.6rem; font-weight:700; color:{STATUS_MUERTO};">{_skus_40sem:,}</div>
            <div style="font-size:0.7rem; color:var(--capi-text2);">Candidatos a markdown</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)

    # ── Tabs: Real vs Aparente ──
    _sobre_tab1, _sobre_tab2, _sobre_tab3 = st.tabs(["Sobrestock Real", "Sobrestock Aparente (Empuje)", "Obsoletos"])

    with _sobre_tab1:
        st.markdown("##### Sobrestock real — acción: markdown o transferencia")
        st.caption("Estados SOBRESTOCK y LIQUIDAR. Los SKUs en estado ALTO (16-26 sem) se vigilan pero no se intervienen aquí — solo aparecen en Empuje si su stock está retenido en CD.")
        # ALTO se excluye del markdown: pertenece al universo 'aparente' (empuje), no al de intervención de precio
        _df_real = _df_sobre[(~_df_sobre['sobrestock_aparente']) & (_df_sobre['estado'] != 'ALTO')].copy() if 'sobrestock_aparente' in _df_sobre.columns else _df_sobre[_df_sobre['estado'] != 'ALTO'].copy()
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
        # Solo MUERTO (>26 sem sin venta) y LIQUIDAR (>52 sem cob + edad >26)
        # DORMIDO (8-26 sem sin venta) NO es obsoleto — necesita empuje/precio, no liquidación
        _df_obs_tab = df_cob[
            (df_cob['estado'].isin(['MUERTO', 'LIQUIDAR'])) &
            (df_cob['edad_semanas'].fillna(0) >= 26)
        ].copy() if 'estado' in df_cob.columns else pd.DataFrame()
        if _df_obs_tab.empty:
            st.info("No hay mercadería obsoleta detectada (edad > 26 semanas).")
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
            st.caption(f"{len(_df_obs_tab):,} combos en estados MUERTO/LIQUIDAR (edad ≥ 26 sem) · Capital: S/ {_df_obs_tab['stock_valor_costo'].sum():,.0f}")

    # ── Descarga Excel Sobrestock ──
    if not _df_sobre.empty:
        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
        _sobre_xl_cols = [c for c in ['marca', 'sku', 'nombre', 'categoria', 'tienda', 'estado',
                                       'stock_total', 'stock_cd', 'cobertura_sem', 'edad_semanas',
                                       'pct_descuento', 'stock_valor_costo', 'sobrestock_aparente'] if c in _df_sobre.columns]
        _sobre_xl_buf = io.BytesIO()
        with pd.ExcelWriter(_sobre_xl_buf, engine='openpyxl') as _w:
            _df_real_xl = _df_sobre[(~_df_sobre.get('sobrestock_aparente', pd.Series(False, index=_df_sobre.index))) & (_df_sobre['estado'] != 'ALTO')].copy() if 'sobrestock_aparente' in _df_sobre.columns else _df_sobre[_df_sobre['estado'] != 'ALTO'].copy()
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


# ═════════════════════════════════════════════════════════════════════
#  ACCIONES DE STOCK — sección Sprint 1 Capi (Prompt C-light)
#  Unifica venta cero + sobrestock + alto bajo lógica de tiers (A1-A4/B1-B3/C)
# ═════════════════════════════════════════════════════════════════════

elif nav_page == "🎯 Acciones de Stock":

    from acciones_stock import build_acciones_stock, build_resumen_acciones, filtrar_acciones

    # ── Calcular tabla (lazy, on-demand) ──
    # df_maestro=None: la función no requiere maestro hoy (reservado para Sprint 3).
    df_acciones = build_acciones_stock(df_cob, df_maestro=None, params=params)
    _resumen = build_resumen_acciones(df_acciones)

    # ── Header ──
    st.markdown(
        '<div class="section-header"><h3>Acciones de Stock</h3>'
        '<span class="live-badge">GESTIÓN DE STOCK</span></div>',
        unsafe_allow_html=True
    )
    st.caption(
        "SKUs que requieren intervención, agrupados por tier de criticidad. "
        "Markdown NO es palanca primaria — la acción sugerida prioriza exhibición y transferencia primero."
    )

    if df_acciones.empty:
        st.info("No hay SKUs en estados accionables con la data actual.")
    else:
        # ── KPIs ejecutivos (4 cards) ──
        _k1, _k2, _k3, _k4 = st.columns(4)
        with _k1:
            st.markdown(f"""
            <div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_SOBRESTOCK};">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Capital inmovilizado</div>
                <div style="font-size:1.6rem; font-weight:700; color:{STATUS_SOBRESTOCK};">S/ {_resumen['capital_total']:,.0f}</div>
                <div style="font-size:0.7rem; color:var(--capi-text2);">{_resumen['n_combos']:,} combos · {_resumen['n_skus']:,} SKUs</div>
            </div>""", unsafe_allow_html=True)
        with _k2:
            _pct_pareto = (_resumen['capital_pareto'] / _resumen['capital_total'] * 100) if _resumen['capital_total'] > 0 else 0
            st.markdown(f"""
            <div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_ALTO};">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Pareto 80% (concentración)</div>
                <div style="font-size:1.6rem; font-weight:700; color:{STATUS_ALTO};">S/ {_resumen['capital_pareto']:,.0f}</div>
                <div style="font-size:0.7rem; color:var(--capi-text2);">{_pct_pareto:.0f}% del total · foco prioritario</div>
            </div>""", unsafe_allow_html=True)
        with _k3:
            st.markdown(f"""
            <div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_LIQUIDAR};">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Sin markdown intentado</div>
                <div style="font-size:1.6rem; font-weight:700; color:{STATUS_LIQUIDAR};">{_resumen['n_sin_markdown']:,}</div>
                <div style="font-size:0.7rem; color:var(--capi-text2);">SKUs ESTANCADO/LIQUIDAR con dscto &lt;20%</div>
            </div>""", unsafe_allow_html=True)
        with _k4:
            st.markdown(f"""
            <div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_MUERTO};">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Tiendas afectadas</div>
                <div style="font-size:1.6rem; font-weight:700; color:{STATUS_MUERTO};">{_resumen['n_tiendas_afectadas']}</div>
                <div style="font-size:0.7rem; color:var(--capi-text2);">de las 32 tiendas + ecommerce</div>
            </div>""", unsafe_allow_html=True)

        st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)

        # ── Top marcas + top tiendas (lado a lado) ──
        _top1, _top2 = st.columns(2)
        with _top1:
            st.markdown("##### Top 5 marcas con capital parado")
            if _resumen['top_marcas']:
                _df_top_m = pd.DataFrame(
                    [(m, c) for m, c in _resumen['top_marcas'].items()],
                    columns=['Marca', 'Capital S/']
                )
                st.dataframe(
                    _df_top_m.style.format({'Capital S/': 'S/ {:,.0f}'}),
                    use_container_width=True, hide_index=True
                )
        with _top2:
            st.markdown("##### Top 5 tiendas con capital parado")
            if _resumen['top_tiendas']:
                _df_top_t = pd.DataFrame(
                    [(t, c) for t, c in _resumen['top_tiendas'].items()],
                    columns=['Tienda', 'Capital S/']
                )
                st.dataframe(
                    _df_top_t.style.format({'Capital S/': 'S/ {:,.0f}'}),
                    use_container_width=True, hide_index=True
                )

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

        # ── Filtros ──
        with st.expander("🔍 Filtros", expanded=False):
            _f1, _f2, _f3, _f4 = st.columns(4)
            _marcas_disp  = sorted(df_acciones['marca'].dropna().unique()) if 'marca' in df_acciones.columns else []
            _tiendas_disp = sorted(df_acciones['tienda'].dropna().unique()) if 'tienda' in df_acciones.columns else []
            _cats_disp    = sorted(df_acciones['categoria'].dropna().unique()) if 'categoria' in df_acciones.columns else []
            _tiers_disp   = sorted(df_acciones['tier'].dropna().unique())

            with _f1:
                _marcas_sel = st.multiselect("Marca", _marcas_disp, default=[])
            with _f2:
                _tiendas_sel = st.multiselect("Tienda", _tiendas_disp, default=[])
            with _f3:
                _cats_sel = st.multiselect("Categoría", _cats_disp, default=[])
            with _f4:
                _tiers_sel = st.multiselect("Tier", _tiers_disp, default=[])

            _t1, _t2 = st.columns(2)
            with _t1:
                _solo_pareto = st.toggle("Solo Pareto 80% por tienda", value=False, key="ac_pareto")
            with _t2:
                _solo_sin_mkdn = st.toggle("Solo sin markdown intentado", value=False, key="ac_sin_mkdn")

        # ── Aplicar filtros ──
        df_filt = filtrar_acciones(
            df_acciones,
            marcas=_marcas_sel or None,
            tiendas=_tiendas_sel or None,
            categorias=_cats_sel or None,
            tiers=_tiers_sel or None,
            solo_pareto=_solo_pareto,
            solo_sin_markdown=_solo_sin_mkdn,
        )

        st.caption(
            f"{len(df_filt):,} combos · S/ {df_filt['stock_valor_costo'].sum():,.0f} capital filtrado"
            + (f" (de {len(df_acciones):,} totales)" if len(df_filt) < len(df_acciones) else "")
        )

        # ── Tabla principal ──
        _cols_show = ['tier', 'criticidad', 'marca', 'sku', 'nombre', 'categoria', 'tienda',
                      'stock_total', 'stock_valor_costo', 'cobertura_sem', 'edad_semanas',
                      'estado', 'accion_sugerida', 'pct_descuento']
        _cols_disp = [c for c in _cols_show if c in df_filt.columns]
        _df_show = df_filt[_cols_disp].rename(columns={
            'tier': 'Tier', 'criticidad': 'Criticidad', 'marca': 'Marca', 'sku': 'SKU',
            'nombre': 'Producto', 'categoria': 'Categoría', 'tienda': 'Tienda',
            'stock_total': 'Stock', 'stock_valor_costo': 'Capital S/',
            'cobertura_sem': 'Cob (sem)', 'edad_semanas': 'Edad (sem)',
            'estado': 'Estado', 'accion_sugerida': 'Acción sugerida',
            'pct_descuento': 'Dscto',
        })

        _format_dict = {}
        if 'Capital S/' in _df_show.columns:  _format_dict['Capital S/']  = 'S/ {:,.0f}'
        if 'Cob (sem)' in _df_show.columns:   _format_dict['Cob (sem)']   = '{:.1f}'
        if 'Edad (sem)' in _df_show.columns:  _format_dict['Edad (sem)']  = '{:.0f}'
        if 'Dscto' in _df_show.columns:       _format_dict['Dscto']       = '{:.0%}'

        st.dataframe(
            _df_show.head(500).style.format(_format_dict, na_rep="—"),
            use_container_width=True, hide_index=True, height=500
        )
        if len(df_filt) > 500:
            st.caption(f"Mostrando primeros 500 de {len(df_filt):,}. Refina con filtros o exporta Excel.")

        # ── Export Excel ──
        _buf = io.BytesIO()
        with pd.ExcelWriter(_buf, engine='openpyxl') as _writer:
            df_filt.to_excel(_writer, index=False, sheet_name='Acciones de Stock')
        st.download_button(
            "📥 Exportar a Excel",
            data=_buf.getvalue(),
            file_name=f"acciones_stock_{pd.Timestamp.now().strftime('%Y%m%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=False,
        )


# ═══════════════════════════════════════════════════════════════
#  VISTA CAPI 3: MARCAS TERCERAS
#  KPIs: margen efectivo, cobertura por marca, capital invertido
# ═══════════════════════════════════════════════════════════════

elif nav_page == "🏷️ Marcas Terceras":

    st.markdown(f'<div class="section-header"><h3>Vista Marcas Terceras</h3><span class="live-badge">GESTIÓN COMERCIAL</span></div>', unsafe_allow_html=True)

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
            <div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {TEAL_600};">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Margen efectivo promedio</div>
                <div style="font-size:1.6rem; font-weight:700; color:{TEAL_700};">{_margen_prom:.1f}%</div>
                <div style="font-size:0.7rem; color:var(--capi-text2);">Contribución / Vta Soles 4sem</div>
            </div>""", unsafe_allow_html=True)
        with _kpi_cols3[1]:
            st.markdown(f"""
            <div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {SLATE_700};">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Cobertura promedio</div>
                <div style="font-size:1.6rem; font-weight:700; color:var(--capi-text);">{_cob_prom_terc:.1f} sem</div>
                <div style="font-size:0.7rem; color:var(--capi-text2);">{len(_t_marca)} marcas terceras</div>
            </div>""", unsafe_allow_html=True)
        with _kpi_cols3[2]:
            st.markdown(f"""
            <div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {SLATE_900};">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Capital invertido</div>
                <div style="font-size:1.6rem; font-weight:700; color:var(--capi-text);">S/ {_capital_total_terc:,.0f}</div>
                <div style="font-size:0.7rem; color:var(--capi-text2);">Top 10 concentran {(_t_marca.head(10)['capital'].sum() / _capital_total_terc * 100):.0f}%</div>
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
        # Gradient manual para Margen % (compatible con st.dataframe)
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
            # NO usar 'color: inherit' — Streamlit lo interpreta mal y oculta el texto
            return f'background-color: rgba({r},{g},50,0.25)'

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

    # ══════════════════════════════════════════════════════════
    #  PROYECCIÓN DE VENTAS + OTB (Forecast por marca tercera)
    # ══════════════════════════════════════════════════════════
    _forecast_data = res.get('forecast', {})
    if _forecast_data and _forecast_data.get('por_marca'):
        st.markdown("<div style='height:32px'></div>", unsafe_allow_html=True)
        st.markdown(f'<div class="section-header"><h3>📈 Proyección de Ventas & OTB</h3><span class="live-badge">FORECAST</span></div>', unsafe_allow_html=True)
        st.caption(f"Proyección basada en tendencia actual + patrón estacional LY. Stock = Tienda + CD + Tránsito.")

        # Selector de horizonte (dinámico — recalcula al cambiar)
        _horizonte_opts = [4, 8, 12, 16]
        _h_sel = st.selectbox("Horizonte de proyección (semanas)", _horizonte_opts, index=1, key="forecast_horizonte")

        # Recalcular forecast con horizonte seleccionado si difiere del default
        if _h_sel != _forecast_data.get('horizonte', 8):
            _forecast_data = motor_v2.build_forecast_marca(df_cob, horizonte_semanas=_h_sel)

        # Filtrar solo marcas terceras
        _fc_terceras = [m for m in _forecast_data['por_marca']
                        if m['marca'].upper() not in _MARCAS_PROPIAS and m['vta_uds_sem_actual'] > 0]

        if _fc_terceras:

            # KPIs resumen
            _fc_con_stockout = [m for m in _fc_terceras if m.get('semana_stockout')]
            _fc_otb_total = sum(m['otb_total_soles'] for m in _fc_terceras)

            _fk1, _fk2, _fk3 = st.columns(3)
            with _fk1:
                _so_color = "#ef4444" if len(_fc_con_stockout) > 0 else "#10b981"
                st.markdown(f"""<div style="background:{'#FEF2F2' if _fc_con_stockout else '#F0FDF4'}; border-radius:12px; padding:16px 20px; border-left:4px solid {_so_color};">
                    <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Marcas con stockout proyectado</div>
                    <div style="font-size:1.8rem; font-weight:700; color:{_so_color};">{len(_fc_con_stockout)} / {len(_fc_terceras)}</div>
                    <div style="font-size:0.7rem; color:var(--capi-text2);">En las próximas {_forecast_data['horizonte']} semanas</div>
                </div>""", unsafe_allow_html=True)
            with _fk2:
                st.markdown(f"""<div style="background:#FEF2F2; border-radius:12px; padding:16px 20px; border-left:4px solid #ef4444;">
                    <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">OTB requerido total</div>
                    <div style="font-size:1.8rem; font-weight:700; color:#ef4444;">S/ {_fc_otb_total:,.0f}</div>
                    <div style="font-size:0.7rem; color:var(--capi-text2);">Para sostener tendencia actual</div>
                </div>""", unsafe_allow_html=True)
            with _fk3:
                _sem_actual_fc = _forecast_data.get('semana_actual', 0)
                st.markdown(f"""<div style="background:#EFF6FF; border-radius:12px; padding:16px 20px; border-left:4px solid #3b82f6;">
                    <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Semana actual</div>
                    <div style="font-size:1.8rem; font-weight:700; color:#3b82f6;">{_sem_actual_fc}</div>
                    <div style="font-size:0.7rem; color:var(--capi-text2);">Proyectando sem {_sem_actual_fc+1} → {_sem_actual_fc + _forecast_data['horizonte']}</div>
                </div>""", unsafe_allow_html=True)

            # Tabla de forecast por marca
            st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
            st.markdown(f"<h4 style='color:{SLATE_800}; margin:0 0 8px 0;'>Detalle por Marca Tercera</h4>", unsafe_allow_html=True)

            _fc_rows_html = ""
            for _fm in _fc_terceras:
                _m_name = _fm['marca']
                _m_vta = _fm['vta_soles_sem_actual']
                _m_stock = _fm['stock_disponible_uds']
                _m_cob = _fm['cobertura_actual_sem']
                _m_perf = _fm.get('perf_ratio_vs_ly', 1.0)
                _m_so = _fm.get('semana_stockout')
                _m_otb = _fm['otb_total_soles']

                _so_txt = f"<span style='color:#ef4444; font-weight:700;'>Sem {_m_so}</span>" if _m_so else "<span style='color:#10b981;'>OK</span>"
                _otb_txt = f"<span style='color:#ef4444; font-weight:600;'>S/ {_m_otb:,.0f}</span>" if _m_otb > 0 else "—"
                _perf_clr = "#10b981" if _m_perf >= 1 else "#ef4444"
                _perf_arrow = "▲" if _m_perf >= 1 else "▼"
                _perf_pct = ((_m_perf - 1) * 100)

                _fc_rows_html += f"""<tr>
                    <td style="padding:7px 10px; font-weight:500;">{_m_name}</td>
                    <td style="padding:7px 10px; text-align:right;">S/ {_m_vta:,.0f}</td>
                    <td style="padding:7px 10px; text-align:right;">{_m_stock:,}</td>
                    <td style="padding:7px 10px; text-align:right;">{_m_cob:.0f}</td>
                    <td style="padding:7px 10px; text-align:right; color:{_perf_clr};">{_perf_arrow} {abs(_perf_pct):.0f}%</td>
                    <td style="padding:7px 10px; text-align:center;">{_so_txt}</td>
                    <td style="padding:7px 10px; text-align:right;">{_otb_txt}</td>
                </tr>"""

            st.markdown(f"""<div style="overflow-x:auto; max-height:500px; overflow-y:auto;">
            <table style="width:100%; border-collapse:collapse; font-size:0.82rem;">
                <thead>
                    <tr style="background:var(--capi-bg-surface); border-bottom:2px solid var(--capi-border); position:sticky; top:0;">
                        <th style="padding:8px 10px; text-align:left;">Marca</th>
                        <th style="padding:8px 10px; text-align:right;">Vta S/ /sem</th>
                        <th style="padding:8px 10px; text-align:right;">Stock Disp.</th>
                        <th style="padding:8px 10px; text-align:right;">Cob (sem)</th>
                        <th style="padding:8px 10px; text-align:right;">vs LY</th>
                        <th style="padding:8px 10px; text-align:center;">Stockout</th>
                        <th style="padding:8px 10px; text-align:right;">OTB Req S/</th>
                    </tr>
                </thead>
                <tbody>{_fc_rows_html}</tbody>
            </table></div>""", unsafe_allow_html=True)

            # Detalle expandible por marca con stockout
            if _fc_con_stockout:
                st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
                st.markdown(f"<h4 style='color:#ef4444; margin:0 0 8px 0;'>⚠️ Marcas que requieren compra</h4>", unsafe_allow_html=True)
                for _fm in _fc_con_stockout[:10]:
                    with st.expander(f"🔴 {_fm['marca']} — Stockout sem {_fm['semana_stockout']} | OTB S/ {_fm['otb_total_soles']:,.0f}", expanded=False):
                        _proy = _fm.get('proyeccion_semanal', [])
                        if _proy:
                            _proy_df = pd.DataFrame(_proy)
                            _proy_df.columns = ['Semana', 'Vta Uds Proy', 'Vta S/ Proy', 'Stock Remanente', 'OTB S/']
                            st.dataframe(_proy_df, use_container_width=True, hide_index=True)
                            st.caption(f"Performance vs LY: {_fm.get('perf_ratio_vs_ly', 1.0):.2f}x | Costo prom/ud: S/ {_fm.get('costo_prom_uds', 0):.2f}")

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
        <span style="color:var(--capi-text2); font-size:0.82em;"> &nbsp;·&nbsp; Oportunidad de reposición</span>
        </div>""", unsafe_allow_html=True)

        for _, row_t in top5_menor.iterrows():
            tienda_name = row_t["tienda"]
            cob_val = row_t["cobertura_tienda"]
            capital_val = row_t["stock_valor_costo"]

            st.markdown(f"""<div style="background:var(--capi-bg-card); border:1px solid var(--capi-border); border-radius:10px; padding:10px 14px; margin-bottom:4px;">
            <span style="font-weight:600; color:var(--capi-text);">{tienda_name}</span>
            &nbsp;·&nbsp; <span style="color:{STATUS_CRITICO}; font-weight:700;">{cob_val:.1f} sem</span>
            &nbsp;·&nbsp; <span style="color:var(--capi-text2); font-size:0.85em;">Capital: S/ {capital_val:,.0f}</span>
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
        <span style="color:var(--capi-text2); font-size:0.82em;"> &nbsp;·&nbsp; Requieren acción</span>
        </div>""", unsafe_allow_html=True)

        for _, row_t in top5_mayor.iterrows():
            tienda_name = row_t["tienda"]
            cob_val = row_t["cobertura_tienda"]
            capital_val = row_t["stock_valor_costo"]

            st.markdown(f"""<div style="background:var(--capi-bg-card); border:1px solid var(--capi-border); border-radius:10px; padding:10px 14px; margin-bottom:4px;">
            <span style="font-weight:600; color:var(--capi-text);">{tienda_name}</span>
            &nbsp;·&nbsp; <span style="color:{STATUS_SOBRESTOCK}; font-weight:700;">{cob_val:.1f} sem</span>
            &nbsp;·&nbsp; <span style="color:var(--capi-text2); font-size:0.85em;">Capital: S/ {capital_val:,.0f}</span>
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




# ═══════════════════════════════════════════════════════════════
#  GESTIÓN DE MARCAS PROPIAS — reposición / transferencias / precios /
#  predistribución filtrados a las 7 marcas propias.
# ═══════════════════════════════════════════════════════════════

elif nav_page == "📦 Reposición Propias":
    st.markdown(f'<div class="section-header"><h3>📦 Reposición Propias</h3><span class="live-badge">MARCAS PROPIAS</span></div>', unsafe_allow_html=True)

    # Paquete de trabajo: todo el análisis de propias en un Excel
    st.download_button(
        "📦 Descargar TODO el análisis de Marcas Propias (.xlsx)",
        data=_build_excel_propias(df_cob, df_rep, df_trans, df_gaps_dist, df_retenidos_cd),
        file_name="Capi_Analisis_Marcas_Propias.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True, key="dl_pack_propias",
        help="5 pestañas: Reposición · Transferencias · Precios · Predist Gaps · Retenidos CD",
    )
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    _MARCAS_P = agente_terceras.MARCAS_PROPIAS_SET
    _rp = df_rep[df_rep['marca'].str.upper().str.strip().isin(_MARCAS_P)].copy() if not df_rep.empty and 'marca' in df_rep.columns else pd.DataFrame()
    if _rp.empty:
        st.info("No hay reposiciones sugeridas para las marcas propias con la base actual.")
    else:
        _rp = _rp[_rp['a_reponer'] > 0] if 'a_reponer' in _rp.columns else _rp
        if 'sku' in df_cob.columns:
            if 'margen_efectivo' in df_cob.columns:
                _mgp = df_cob.drop_duplicates('sku').set_index('sku')['margen_efectivo']
                _rp['margen_efectivo'] = (_rp['sku'].map(_mgp).fillna(0) * 100).round(1)
            if 'edad_semanas' in df_cob.columns:
                _rp['edad'] = _rp['sku'].map(df_cob.groupby('sku')['edad_semanas'].max())
        st.caption(f"{len(_rp):,} líneas en {_rp['marca'].nunique()} marcas propias · {int(_rp['a_reponer'].sum()):,} uds a reponer")
        _rp_sel = st.selectbox("Marca", ["Todas"] + sorted(_rp['marca'].unique().tolist()), key="rp_marca")
        _rp_v = _rp if _rp_sel == "Todas" else _rp[_rp['marca'] == _rp_sel]
        _rp_cols = [c for c in ['marca', 'sku', 'nombre', 'categoria', 'tienda', 'edad', 'stock_actual',
                                'prom_vta_sem', 'cobertura_actual', 'a_reponer', 'cob_post_rep', 'stock_cd',
                                'pct_descuento', 'margen_efectivo', 'urgencia'] if c in _rp_v.columns]
        _rp_disp = _rp_v[_rp_cols].rename(columns={
            'marca': 'Marca', 'sku': 'SKU', 'nombre': 'Producto', 'categoria': 'Línea', 'edad': 'Edad (sem)',
            'tienda': 'Tienda', 'stock_actual': 'Stock', 'prom_vta_sem': 'Vta/sem', 'cobertura_actual': 'Cob (sem)',
            'a_reponer': 'A reponer (uds)', 'cob_post_rep': 'Cob post', 'stock_cd': 'Stock CD',
            'pct_descuento': 'Dscto', 'margen_efectivo': 'Margen efect. %', 'urgencia': 'Urgencia',
        })
        st.dataframe(_rp_disp.style.format({'Vta/sem': '{:.1f}', 'Cob (sem)': '{:.1f}', 'Cob post': '{:.1f}', 'Dscto': '{:.0%}'}, na_rep="—"),
                     use_container_width=True, hide_index=True, height=440)
        _rp_buf = io.BytesIO()
        with pd.ExcelWriter(_rp_buf, engine='openpyxl') as _w:
            _rp_v[_rp_cols].to_excel(_w, sheet_name='Reposicion Propias', index=False)
        _rp_buf.seek(0)
        st.download_button("📥 Descargar reposición propias (.xlsx)", data=_rp_buf.getvalue(),
                           file_name="Capi_Reposicion_Propias.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_repo_propias")


elif nav_page == "🔄 Transferencias Propias":
    st.markdown(f'<div class="section-header"><h3>🔄 Transferencias Propias</h3><span class="live-badge">MARCAS PROPIAS</span></div>', unsafe_allow_html=True)
    _MARCAS_P = agente_terceras.MARCAS_PROPIAS_SET
    if df_trans.empty or 'sku' not in df_trans.columns:
        st.info("No hay transferencias sugeridas con la base actual.")
    else:
        _s2m_p = dict(zip(df_cob['sku'], df_cob['marca'].str.upper().str.strip())) if 'sku' in df_cob.columns and 'marca' in df_cob.columns else {}
        _tp = df_trans.copy()
        _tp['_marca'] = _tp['sku'].map(_s2m_p)
        _tp = _tp[_tp['_marca'].isin(_MARCAS_P)]
        if _tp.empty:
            st.info("No hay transferencias sugeridas para las marcas propias con la base actual.")
        else:
            st.caption(f"{len(_tp):,} movimientos en marcas propias · {int(_tp['uds_transferir'].sum()):,} unidades")
            _tp_cols = [c for c in ['_marca', 'sku', 'nombre', 'tienda_origen', 'tienda_destino', 'uds_transferir',
                                    'cob_origen_pre', 'cob_destino_pre', 'cob_origen_post', 'cob_destino_post', 'motivo'] if c in _tp.columns]
            _tp_disp = _tp[_tp_cols].rename(columns={
                '_marca': 'Marca', 'sku': 'SKU', 'nombre': 'Producto', 'tienda_origen': 'Tienda Origen',
                'tienda_destino': 'Tienda Destino', 'uds_transferir': 'Uds a Transferir',
                'cob_origen_pre': 'Cob Origen (pre)', 'cob_destino_pre': 'Cob Destino (pre)',
                'cob_origen_post': 'Cob Origen (post)', 'cob_destino_post': 'Cob Destino (post)', 'motivo': 'Motivo',
            })
            st.dataframe(_tp_disp.style.format({'Cob Origen (pre)': '{:.1f}', 'Cob Destino (pre)': '{:.1f}', 'Cob Origen (post)': '{:.1f}', 'Cob Destino (post)': '{:.1f}'}, na_rep="—"),
                         use_container_width=True, hide_index=True, height=440)
            _tp_buf = io.BytesIO()
            with pd.ExcelWriter(_tp_buf, engine='openpyxl') as _w:
                _tp[_tp_cols].to_excel(_w, sheet_name='Transferencias Propias', index=False)
            _tp_buf.seek(0)
            st.download_button("📥 Descargar transferencias propias (.xlsx)", data=_tp_buf.getvalue(),
                               file_name="Capi_Transferencias_Propias.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_trans_propias")


elif nav_page == "💰 Gestión de Precios Propias":
    st.markdown(f'<div class="section-header"><h3>💰 Gestión de Precios Propias</h3><span class="live-badge">MARCAS PROPIAS</span></div>', unsafe_allow_html=True)
    st.caption("Descuento sugerido por antigüedad (misma pirámide), aplicado a las 7 marcas propias.")
    _gpp = agente_terceras.sugerencias_precio_terceras(df_cob, marcas=agente_terceras.MARCAS_PROPIAS_SET)
    if _gpp.empty:
        st.info("No hay SKUs de marcas propias para analizar con la base actual.")
    else:
        _gpp_subir = int((_gpp['gap'] >= 0.05).sum())
        _gpp_sobre = int((_gpp['gap'] <= -0.05).sum())
        _gpp_ok = int(len(_gpp) - _gpp_subir - _gpp_sobre)
        _gppc1, _gppc2, _gppc3 = st.columns(3)
        _gppc1.markdown(f"""<div style="background:#FEF2F2; border-radius:12px; padding:14px 18px; border-left:4px solid #DC2626;">
            <div style="font-size:0.75rem; color:var(--capi-text2);">SKUs a subir descuento</div>
            <div style="font-size:1.5rem; font-weight:700; color:#DC2626;">{_gpp_subir:,}</div></div>""", unsafe_allow_html=True)
        _gppc2.markdown(f"""<div style="background:#FFFBEB; border-radius:12px; padding:14px 18px; border-left:4px solid #D97706;">
            <div style="font-size:0.75rem; color:var(--capi-text2);">Sobre-descontados</div>
            <div style="font-size:1.5rem; font-weight:700; color:#D97706;">{_gpp_sobre:,}</div></div>""", unsafe_allow_html=True)
        _gppc3.markdown(f"""<div style="background:var(--capi-bg-surface); border-radius:12px; padding:14px 18px; border-left:4px solid #059669;">
            <div style="font-size:0.75rem; color:var(--capi-text2);">Alineados</div>
            <div style="font-size:1.5rem; font-weight:700; color:#059669;">{_gpp_ok:,}</div></div>""", unsafe_allow_html=True)
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        _gpp_filtro = st.radio("Ver", ["Solo a subir descuento", "Todos"], horizontal=True, key="gpp_filtro")
        _gpp_v = _gpp[_gpp['gap'] >= 0.05] if _gpp_filtro.startswith("Solo") else _gpp
        _gpp_sel = st.selectbox("Marca", ["Todas"] + sorted(_gpp_v['marca'].unique().tolist()), key="gpp_marca")
        if _gpp_sel != "Todas":
            _gpp_v = _gpp_v[_gpp_v['marca'] == _gpp_sel]
        _gpp_cols = [c for c in ['marca', 'sku', 'nombre', 'categoria', 'edad', 'dscto_actual', 'dscto_sugerido', 'tipo', 'accion', 'capital'] if c in _gpp_v.columns]
        _gpp_disp = _gpp_v[_gpp_cols].rename(columns={
            'marca': 'Marca', 'sku': 'SKU', 'nombre': 'Producto', 'categoria': 'Línea', 'edad': 'Edad (sem)',
            'dscto_actual': 'Dscto actual', 'dscto_sugerido': 'Dscto sugerido', 'tipo': 'Tipo', 'accion': 'Acción', 'capital': 'Capital S/',
        })
        st.dataframe(_gpp_disp.head(300).style.format({'Dscto actual': '{:.0%}', 'Dscto sugerido': '{:.0%}', 'Capital S/': 'S/ {:,.0f}'}, na_rep="—"),
                     use_container_width=True, hide_index=True, height=440)
        _gpp_buf = io.BytesIO()
        with pd.ExcelWriter(_gpp_buf, engine='openpyxl') as _w:
            _gpp_v[_gpp_cols].to_excel(_w, sheet_name='Precios Propias', index=False)
        _gpp_buf.seek(0)
        st.download_button("📥 Descargar sugerencias de precio propias (.xlsx)", data=_gpp_buf.getvalue(),
                           file_name="Capi_Precios_Propias.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_precios_propias")


elif nav_page == "🚚 Predistribución Propias":
    st.markdown(f'<div class="section-header"><h3>🚚 Predistribución Propias</h3><span class="live-badge">MARCAS PROPIAS</span></div>', unsafe_allow_html=True)
    st.caption("Gaps de distribución (tiendas faltantes) y stock retenido en CD, filtrado a marcas propias.")
    _MARCAS_P = agente_terceras.MARCAS_PROPIAS_SET
    _pp_gaps = df_gaps_dist[df_gaps_dist['marca'].str.upper().str.strip().isin(_MARCAS_P)].copy() if not df_gaps_dist.empty and 'marca' in df_gaps_dist.columns else pd.DataFrame()
    # Limpieza (Franco): un gap solo es accionable si hay stock en CD para enviar
    # y el producto es reciente (≤8 sem); lo demás es ruido / ya pasó su momento.
    if not _pp_gaps.empty:
        if 'stock_cd' in _pp_gaps.columns:
            _pp_gaps = _pp_gaps[_pp_gaps['stock_cd'] > 0]
        if 'edad_semanas' in _pp_gaps.columns:
            _pp_gaps = _pp_gaps[_pp_gaps['edad_semanas'].fillna(999) <= 8]
    _pp_ret = df_retenidos_cd[df_retenidos_cd['marca'].str.upper().str.strip().isin(_MARCAS_P)].copy() if not df_retenidos_cd.empty and 'marca' in df_retenidos_cd.columns else pd.DataFrame()
    if _pp_gaps.empty and _pp_ret.empty:
        st.info("No hay datos de predistribución para marcas propias con la base actual.")
    else:
        if not _pp_gaps.empty:
            st.markdown("##### Gaps de distribución — SKUs que faltan en tiendas")
            _ppg_cols = [c for c in ['marca', 'sku', 'nombre', 'categoria', 'edad_semanas', 'stock_cd',
                                     'n_tiendas_esperadas', 'n_tiendas_presentes', 'n_tiendas_faltantes', 'pct_cobertura_dist'] if c in _pp_gaps.columns]
            st.dataframe(_pp_gaps[_ppg_cols].rename(columns={
                'marca': 'Marca', 'sku': 'SKU', 'nombre': 'Producto', 'categoria': 'Línea', 'edad_semanas': 'Edad (sem)',
                'stock_cd': 'Stock CD', 'n_tiendas_esperadas': 'Tiendas esperadas', 'n_tiendas_presentes': 'Tiendas presentes',
                'n_tiendas_faltantes': 'Tiendas faltantes', 'pct_cobertura_dist': '% cobertura dist',
            }).style.format({'% cobertura dist': '{:.0%}'}, na_rep="—"), use_container_width=True, hide_index=True, height=400)
        if not _pp_ret.empty:
            st.markdown("##### Stock retenido en CD")
            st.dataframe(_pp_ret.rename(columns={'marca': 'Marca', 'sku': 'SKU', 'nombre': 'Producto'}),
                         use_container_width=True, hide_index=True, height=300)
        _pp_buf = io.BytesIO()
        with pd.ExcelWriter(_pp_buf, engine='openpyxl') as _w:
            if not _pp_gaps.empty: _pp_gaps.to_excel(_w, sheet_name='Gaps Distribucion', index=False)
            if not _pp_ret.empty: _pp_ret.to_excel(_w, sheet_name='Retenidos CD', index=False)
        _pp_buf.seek(0)
        st.download_button("📥 Descargar predistribución propias (.xlsx)", data=_pp_buf.getvalue(),
                           file_name="Capi_Predistribucion_Propias.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_predist_propias")


# ═══════════════════════════════════════════════════════════════
#  📦 REPOSICIÓN TERCERAS — plan de reposición filtrado a las 10 marcas
# ═══════════════════════════════════════════════════════════════

elif nav_page == "📦 Reposición Terceras":
    st.markdown(f'<div class="section-header"><h3>📦 Reposición Terceras</h3><span class="live-badge">MARCAS TERCERAS</span></div>', unsafe_allow_html=True)
    _MARCAS_T = agente_terceras.MARCAS_AGENTE
    _rt = df_rep[df_rep['marca'].str.upper().str.strip().isin(_MARCAS_T)].copy() if not df_rep.empty and 'marca' in df_rep.columns else pd.DataFrame()
    if _rt.empty:
        st.info("No hay reposiciones sugeridas para las marcas terceras con la base actual.")
    else:
        _rt = _rt[_rt['a_reponer'] > 0] if 'a_reponer' in _rt.columns else _rt
        # Cruzar margen efectivo y antigüedad por SKU (desde df_cob) — para que el
        # proveedor pueda filtrar qué reponer según rentabilidad y edad del producto.
        if 'sku' in df_cob.columns:
            if 'margen_efectivo' in df_cob.columns:
                _mg = df_cob.drop_duplicates('sku').set_index('sku')['margen_efectivo']
                _rt['margen_efectivo'] = (_rt['sku'].map(_mg).fillna(0) * 100).round(1)
            if 'edad_semanas' in df_cob.columns:
                _ed = df_cob.groupby('sku')['edad_semanas'].max()
                _rt['edad'] = _rt['sku'].map(_ed)
        st.caption(f"{len(_rt):,} líneas de reposición en {_rt['marca'].nunique()} marcas terceras · "
                   f"{int(_rt['a_reponer'].sum()):,} unidades a reponer")
        _rt_marca = ["Todas"] + sorted(_rt['marca'].unique().tolist())
        _rt_sel = st.selectbox("Marca", _rt_marca, key="rt_marca")
        _rt_v = _rt if _rt_sel == "Todas" else _rt[_rt['marca'] == _rt_sel]
        _rt_cols = [c for c in ['marca', 'sku', 'nombre', 'categoria', 'tienda', 'edad',
                                'stock_actual', 'prom_vta_sem', 'cobertura_actual', 'a_reponer',
                                'cob_post_rep', 'stock_cd', 'pct_descuento', 'margen_efectivo',
                                'urgencia', 'requiere_proveedor'] if c in _rt_v.columns]
        _rt_disp = _rt_v[_rt_cols].rename(columns={
            'marca': 'Marca', 'sku': 'SKU', 'nombre': 'Producto', 'categoria': 'Línea',
            'edad': 'Edad (sem)', 'tienda': 'Tienda', 'stock_actual': 'Stock', 'prom_vta_sem': 'Vta/sem',
            'cobertura_actual': 'Cob (sem)', 'a_reponer': 'A reponer (uds)',
            'cob_post_rep': 'Cob post', 'stock_cd': 'Stock CD', 'pct_descuento': 'Dscto',
            'margen_efectivo': 'Margen efect. %', 'urgencia': 'Urgencia',
            'requiere_proveedor': 'Req. proveedor',
        })
        st.dataframe(_rt_disp.style.format({
            'Vta/sem': '{:.1f}', 'Cob (sem)': '{:.1f}', 'Cob post': '{:.1f}', 'Dscto': '{:.0%}',
        }, na_rep="—"), use_container_width=True, hide_index=True, height=440)
        _rt_buf = io.BytesIO()
        with pd.ExcelWriter(_rt_buf, engine='openpyxl') as _w:
            _rt_v[_rt_cols].to_excel(_w, sheet_name='Reposicion Terceras', index=False)
        _rt_buf.seek(0)
        st.download_button("📥 Descargar reposición terceras (.xlsx)", data=_rt_buf.getvalue(),
                           file_name="Capi_Reposicion_Terceras.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           key="dl_repo_terceras")


# ═══════════════════════════════════════════════════════════════
#  🔄 TRANSFERENCIAS TERCERAS — transferencias filtradas a las 10 marcas
# ═══════════════════════════════════════════════════════════════

elif nav_page == "🔄 Transferencias Terceras":
    st.markdown(f'<div class="section-header"><h3>🔄 Transferencias Terceras</h3><span class="live-badge">MARCAS TERCERAS</span></div>', unsafe_allow_html=True)
    _MARCAS_T = agente_terceras.MARCAS_AGENTE
    # df_trans no trae 'marca' → cruzar por sku con df_cob
    if df_trans.empty or 'sku' not in df_trans.columns:
        st.info("No hay transferencias sugeridas con la base actual.")
    else:
        _sku2marca = {}
        if 'sku' in df_cob.columns and 'marca' in df_cob.columns:
            _sku2marca = dict(zip(df_cob['sku'], df_cob['marca'].str.upper().str.strip()))
        _tt = df_trans.copy()
        _tt['_marca'] = _tt['sku'].map(_sku2marca)
        _tt = _tt[_tt['_marca'].isin(_MARCAS_T)]
        if _tt.empty:
            st.info("No hay transferencias sugeridas para las marcas terceras con la base actual.")
        else:
            st.caption(f"{len(_tt):,} movimientos en marcas terceras · {int(_tt['uds_transferir'].sum()):,} unidades")
            _tt_cols = [c for c in ['_marca', 'sku', 'nombre', 'tienda_origen', 'tienda_destino',
                                    'uds_transferir', 'cob_origen_pre', 'cob_destino_pre',
                                    'cob_origen_post', 'cob_destino_post', 'motivo'] if c in _tt.columns]
            _tt_disp = _tt[_tt_cols].rename(columns={
                '_marca': 'Marca', 'sku': 'SKU', 'nombre': 'Producto',
                'tienda_origen': 'Tienda Origen', 'tienda_destino': 'Tienda Destino',
                'uds_transferir': 'Uds a Transferir', 'cob_origen_pre': 'Cob Origen (pre)',
                'cob_destino_pre': 'Cob Destino (pre)', 'cob_origen_post': 'Cob Origen (post)',
                'cob_destino_post': 'Cob Destino (post)', 'motivo': 'Motivo',
            })
            st.dataframe(_tt_disp.style.format({
                'Cob Origen (pre)': '{:.1f}', 'Cob Destino (pre)': '{:.1f}',
                'Cob Origen (post)': '{:.1f}', 'Cob Destino (post)': '{:.1f}',
            }, na_rep="—"), use_container_width=True, hide_index=True, height=440)
            _tt_buf = io.BytesIO()
            with pd.ExcelWriter(_tt_buf, engine='openpyxl') as _w:
                _tt[_tt_cols].to_excel(_w, sheet_name='Transferencias Terceras', index=False)
            _tt_buf.seek(0)
            st.download_button("📥 Descargar transferencias terceras (.xlsx)", data=_tt_buf.getvalue(),
                               file_name="Capi_Transferencias_Terceras.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                               key="dl_trans_terceras")


# ═══════════════════════════════════════════════════════════════
#  💰 GESTIÓN DE PRECIOS TERCERAS — pirámide de descuentos por antigüedad
# ═══════════════════════════════════════════════════════════════

elif nav_page == "💰 Gestión de Precios Terceras":
    st.markdown(f'<div class="section-header"><h3>💰 Gestión de Precios Terceras</h3><span class="live-badge">MARCAS TERCERAS</span></div>', unsafe_allow_html=True)
    st.caption("Descuento sugerido por antigüedad (pirámide), igual para las 10 marcas terceras. "
               "Compara el descuento actual de cada SKU con el que le tocaría por su edad.")
    _gp = agente_terceras.sugerencias_precio_terceras(df_cob)
    if _gp.empty:
        st.info("No hay SKUs de marcas terceras para analizar con la base actual.")
    else:
        _gp_subir = int((_gp['gap'] >= 0.05).sum())
        _gp_sobre = int((_gp['gap'] <= -0.05).sum())
        _gp_ok = int(len(_gp) - _gp_subir - _gp_sobre)
        _gpc1, _gpc2, _gpc3 = st.columns(3)
        _gpc1.markdown(f"""<div style="background:#FEF2F2; border-radius:12px; padding:14px 18px; border-left:4px solid #DC2626;">
            <div style="font-size:0.75rem; color:var(--capi-text2);">SKUs a subir descuento</div>
            <div style="font-size:1.5rem; font-weight:700; color:#DC2626;">{_gp_subir:,}</div>
            <div style="font-size:0.7rem; color:var(--capi-text2);">por debajo de la pirámide</div></div>""", unsafe_allow_html=True)
        _gpc2.markdown(f"""<div style="background:#FFFBEB; border-radius:12px; padding:14px 18px; border-left:4px solid #D97706;">
            <div style="font-size:0.75rem; color:var(--capi-text2);">Sobre-descontados</div>
            <div style="font-size:1.5rem; font-weight:700; color:#D97706;">{_gp_sobre:,}</div>
            <div style="font-size:0.7rem; color:var(--capi-text2);">más descuento del sugerido</div></div>""", unsafe_allow_html=True)
        _gpc3.markdown(f"""<div style="background:var(--capi-bg-surface); border-radius:12px; padding:14px 18px; border-left:4px solid #059669;">
            <div style="font-size:0.75rem; color:var(--capi-text2);">Alineados</div>
            <div style="font-size:1.5rem; font-weight:700; color:#059669;">{_gp_ok:,}</div>
            <div style="font-size:0.7rem; color:var(--capi-text2);">descuento correcto</div></div>""", unsafe_allow_html=True)

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        _gp_filtro = st.radio("Ver", ["Solo a subir descuento", "Todos"], horizontal=True, key="gp_filtro")
        _gp_v = _gp[_gp['gap'] >= 0.05] if _gp_filtro.startswith("Solo") else _gp
        _gp_marca = ["Todas"] + sorted(_gp_v['marca'].unique().tolist())
        _gp_sel = st.selectbox("Marca", _gp_marca, key="gp_marca")
        if _gp_sel != "Todas":
            _gp_v = _gp_v[_gp_v['marca'] == _gp_sel]

        _gp_cols = [c for c in ['marca', 'sku', 'nombre', 'categoria', 'edad', 'dscto_actual',
                                'dscto_sugerido', 'tipo', 'accion', 'capital'] if c in _gp_v.columns]
        _gp_disp = _gp_v[_gp_cols].rename(columns={
            'marca': 'Marca', 'sku': 'SKU', 'nombre': 'Producto', 'categoria': 'Línea',
            'edad': 'Edad (sem)', 'dscto_actual': 'Dscto actual', 'dscto_sugerido': 'Dscto sugerido',
            'tipo': 'Tipo', 'accion': 'Acción', 'capital': 'Capital S/',
        })
        st.dataframe(_gp_disp.head(300).style.format({
            'Dscto actual': '{:.0%}', 'Dscto sugerido': '{:.0%}', 'Capital S/': 'S/ {:,.0f}',
        }, na_rep="—"), use_container_width=True, hide_index=True, height=440)
        st.caption("Tipo: 'Eventual' = descuento de evento temporal (sem 8-18) · 'Fijo' = markdown permanente (sem 19+).")

        _gp_buf = io.BytesIO()
        with pd.ExcelWriter(_gp_buf, engine='openpyxl') as _w:
            _gp_v[_gp_cols].to_excel(_w, sheet_name='Precios Terceras', index=False)
        _gp_buf.seek(0)
        st.download_button("📥 Descargar sugerencias de precio (.xlsx)", data=_gp_buf.getvalue(),
                           file_name="Capi_Precios_Terceras.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           key="dl_precios_terceras")


# ═══════════════════════════════════════════════════════════════
#  🤝 AGENTE TERCERAS — primer agente de Capi
#  Detecta oportunidades con marcas terceras y genera BORRADORES de
#  correo a proveedores. Nunca envía solo: Franco aprueba y envía.
# ═══════════════════════════════════════════════════════════════

elif nav_page == "🤝 Agente Terceras":
    st.markdown(f'<div class="section-header"><h3>🤝 Agente Terceras</h3><span class="live-badge">AGENTE</span></div>', unsafe_allow_html=True)
    st.caption("Detecta oportunidades con marcas terceras y redacta el correo al proveedor. "
               "El agente genera un BORRADOR — tú lo revisas y lo envías. Nunca manda nada solo.")

    # Paquete de trabajo: todo el análisis de terceras en un Excel
    st.download_button(
        "📦 Descargar TODO el análisis de Marcas Terceras (.xlsx)",
        data=_build_excel_terceras(df_cob, df_rep, df_trans),
        file_name="Capi_Analisis_Marcas_Terceras.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True, key="dl_pack_terceras",
        help="4 pestañas: SKUs Críticos · Reposición · Transferencias · Precios",
    )
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    _at_prov = agente_terceras.cargar_proveedores()

    _at_tipo = st.radio(
        "Tipo de oportunidad",
        ["💰 Capital parado (rebate / markdown support)", "📦 Quiebre (reorder urgente)"],
        horizontal=True, key="at_tipo",
    )

    if _at_tipo.startswith("💰"):
        _at_op = agente_terceras.detectar_capital_parado(df_cob)
        if _at_op.empty:
            st.info("No hay marcas terceras con capital parado sobre el umbral.")
        else:
            st.markdown(f"**{len(_at_op)} marcas terceras con capital inmovilizado y baja rotación:**")
            _at_show = _at_op[['marca', 'capital', 'n_skus', 'cob_prom', 'sell_through', 'margen_efectivo']].rename(columns={
                'marca': 'Marca', 'capital': 'Capital S/', 'n_skus': 'SKUs',
                'cob_prom': 'Cob (sem)', 'sell_through': 'Sell-through %', 'margen_efectivo': 'Margen %',
            })
            st.dataframe(_at_show.style.format({
                'Capital S/': 'S/ {:,.0f}', 'Cob (sem)': '{:.0f}', 'Sell-through %': '{:.0f}%', 'Margen %': '{:.0f}%',
            }, na_rep="—"), use_container_width=True, hide_index=True)

            _at_marcas_op = _at_op['marca'].tolist()
            _at_sel = st.selectbox("Marca para generar el correo", _at_marcas_op, key="at_sel_cap")
            _at_row = _at_op[_at_op['marca'] == _at_sel].iloc[0]
            _at_prov_marca = _at_prov.get(_at_sel.upper())
            if _at_prov_marca and _at_prov_marca.get('contacto'):
                st.caption(f"Destinatario sugerido: {_at_prov_marca.get('contacto','')} ({_at_prov_marca.get('empresa','')})")
            if st.button("✍️ Generar texto del correo", key="at_gen_cap", type="primary"):
                with st.spinner("Redactando con IA..."):
                    try:
                        _at_skus = agente_terceras.top_skus_marca(df_cob, _at_sel)
                        _at_det = agente_terceras.top5_por_marca_linea(df_cob)
                        if not _at_det.empty:
                            _at_det = _at_det[_at_det['marca'].str.upper() == _at_sel.upper()]
                        _at_correo = agente_terceras.generar_correo_capital_parado(
                            _at_row, _at_prov_marca, _at_skus, detalle_lineas=_at_det)
                        st.session_state["at_borrador"] = _at_correo
                    except Exception as _at_e:
                        st.error(f"No se pudo generar el texto: {_at_e}")

    else:
        _at_op = agente_terceras.detectar_quiebre_tercera(df_cob)
        if _at_op.empty:
            st.info("No hay marcas terceras en quiebre con venta relevante.")
        else:
            st.markdown(f"**{len(_at_op)} marcas terceras con quiebres que vendían bien:**")
            _at_show = _at_op.rename(columns={
                'marca': 'Marca', 'n_skus_quiebre': 'SKUs en quiebre',
                'venta_riesgo_sem': 'Venta en riesgo S//sem', 'vta_sem_uds': 'Vta/sem (uds)',
            })
            st.dataframe(_at_show.style.format({
                'Venta en riesgo S//sem': 'S/ {:,.0f}', 'Vta/sem (uds)': '{:.0f}',
            }), use_container_width=True, hide_index=True)

            _at_sel = st.selectbox("Marca para generar el correo", _at_op['marca'].tolist(), key="at_sel_q")
            _at_row = _at_op[_at_op['marca'] == _at_sel].iloc[0]
            _at_prov_marca = _at_prov.get(_at_sel.upper())
            if _at_prov_marca and _at_prov_marca.get('contacto'):
                st.caption(f"Destinatario sugerido: {_at_prov_marca.get('contacto','')} ({_at_prov_marca.get('empresa','')})")
            if st.button("✍️ Generar texto del correo", key="at_gen_q", type="primary"):
                with st.spinner("Redactando con IA..."):
                    try:
                        _at_correo = agente_terceras.generar_correo_reorder(_at_row, _at_prov_marca)
                        st.session_state["at_borrador"] = _at_correo
                    except Exception as _at_e:
                        st.error(f"No se pudo generar el texto: {_at_e}")

    # ── Borrador generado: revisar / editar / copiar ──
    _at_bor = st.session_state.get("at_borrador")
    if _at_bor:
        st.markdown("---")
        st.markdown(f"##### ✉️ Texto para {_at_bor.get('marca','')} — revisar y copiar")
        st.text_input("Asunto", value=_at_bor.get("asunto", ""), key="at_asunto")
        st.text_area("Cuerpo", value=_at_bor.get("cuerpo", ""), height=320, key="at_cuerpo")
        st.caption("Revisa y ajusta el texto, luego cópialo a tu correo de Ripley para enviarlo al proveedor. "
                   "El agente solo redacta — el envío lo haces tú.")
        if st.button("🗑️ Descartar", key="at_descartar"):
            del st.session_state["at_borrador"]
            st.rerun()


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
            import math as _math_mod  # necesario: cada elif es scope independiente

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
                    <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Capital en mercadería vieja (>16 sem)</div>
                    <div style="font-size:1.8rem; font-weight:700; color:#ef4444;">S/ {_cap_viejo:,.0f}</div>
                    <div style="font-size:0.7rem; color:var(--capi-text2);">{_pct_viejo:.0f}% del inventario total</div>
                </div>""", unsafe_allow_html=True)
            with _kc2:
                st.markdown(f"""<div style="background:#FFFBEB; border-radius:12px; padding:16px 20px; border-left:4px solid #f59e0b;">
                    <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Edad promedio ponderada</div>
                    <div style="font-size:1.8rem; font-weight:700; color:#f59e0b;">{_edad_prom:.1f} sem</div>
                    <div style="font-size:0.7rem; color:var(--capi-text2);">Ponderada por capital invertido</div>
                </div>""", unsafe_allow_html=True)
            with _kc3:
                st.markdown(f"""<div style="background:#F0FDF4; border-radius:12px; padding:16px 20px; border-left:4px solid {TEAL_600};">
                    <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">SKUs en zona de riesgo (8-16 sem)</div>
                    <div style="font-size:1.8rem; font-weight:700; color:{TEAL_700};">{_n_riesgo:,}</div>
                    <div style="font-size:0.7rem; color:var(--capi-text2);">Aún rescatables con empuje a piso</div>
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
                    _legend_parts += f'<span style="display:inline-flex; align-items:center; gap:4px; margin-right:12px;"><span style="width:10px; height:10px; border-radius:2px; background:{_clr}; display:inline-block;"></span><span style="font-size:0.7rem; color:var(--capi-text2);">{_lbl}</span></span>'
                st.markdown(f"""<div style="margin-top:16px; background:var(--capi-bg-card); border-radius:8px; padding:12px 16px; border:1px solid var(--capi-border);">
                    <div style="font-size:0.8rem; font-weight:600; color:var(--capi-text); margin-bottom:8px;">Distribución de capital por edad</div>
                    <div style="width:100%; height:20px; border-radius:6px; overflow:hidden; background:var(--capi-border); display:flex;">{_bar_parts}</div>
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

                    st.markdown(f"<div style='background:var(--capi-bg-card); border:1px solid var(--capi-border); border-radius:8px; padding:16px; margin-top:8px;'>", unsafe_allow_html=True)
                    st.markdown(f"<div style='font-weight:600; color:{SLATE_800}; margin-bottom:8px;'>Drill-down: {_sel_cat}</div>", unsafe_allow_html=True)
                    st.caption("Top marcas por capital viejo (>16 sem)")

                    _max_cap_drill = _drill_marca['capital'].max() if not _drill_marca.empty else 1
                    for _, _dr in _drill_marca.iterrows():
                        _bar_w_d = max(5, int(_dr['capital'] / _max_cap_drill * 100))
                        _dr_color = '#ef4444' if _dr['edad_prom'] > 26 else ('#f97316' if _dr['edad_prom'] > 16 else ('#f59e0b' if _dr['edad_prom'] > 8 else '#10b981'))
                        st.markdown(f"""<div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
                            <span style="width:120px; font-size:0.8rem; font-weight:500; color:var(--capi-text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{_dr['marca']}</span>
                            <div style="flex:1; background:var(--capi-border); border-radius:4px; height:16px;">
                                <div style="background:{_dr_color}; border-radius:4px; height:16px; width:{_bar_w_d}%;"></div>
                            </div>
                            <span style="font-size:0.75rem; color:var(--capi-text2); white-space:nowrap;">S/{_dr['capital']:,.0f}</span>
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
                            <thead><tr style="background:var(--capi-bg-surface); border-bottom:1px solid var(--capi-border);">
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
                        _ej_html += f"""<div style="margin-bottom:8px; padding:6px 8px; background:var(--capi-bg-card); border-radius:6px; border:1px solid var(--capi-border);">
                            <div style="font-size:0.72rem; font-weight:600; color:{SLATE_800};">• {_ej['nombre'][:45]}</div>
                            <div style="font-size:0.68rem; color:var(--capi-text2); margin-top:2px;">{_ej['detalle']}</div>
                            <div style="font-size:0.68rem; color:{_cfg['color']}; margin-top:2px;">→ {_ej['sugerencia'][:60]}</div>
                        </div>"""
                    if not _ej_html:
                        _ej_html = f'<div style="font-size:0.72rem; color:var(--capi-text3); padding:8px;">Sin casos detectados</div>'

                    st.markdown(f"""<div style="background:{_cfg['bg']}; border:2px solid {_cfg['border']}; border-radius:12px; padding:14px; height:100%;">
                        <div style="font-weight:700; font-size:0.85rem; color:{_cfg['color']}; margin-bottom:2px;">{_cfg['icon']} {_cfg['title']}</div>
                        <div style="font-size:0.68rem; color:var(--capi-text2); margin-bottom:10px;">{_cfg['criteria']}</div>
                        {_ej_html}
                        <div style="background:{_cfg['color']}; color:white; padding:6px 10px; border-radius:6px; font-size:0.72rem; font-weight:600; text-align:center; margin-top:8px;">{_cfg['n_label']}</div>
                        <div style="font-size:0.68rem; color:var(--capi-text2); text-align:center; margin-top:4px;">{_cfg['accion']}</div>
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
                    <tr style="background:var(--capi-bg-surface); border-bottom:2px solid var(--capi-border);">
                        <th style="padding:8px 12px; text-align:left; color:var(--capi-text);">Criterio</th>
                        <th style="padding:8px 12px; text-align:center; color:#84cc16;">🟢 Empuje</th>
                        <th style="padding:8px 12px; text-align:center; color:#f59e0b;">🟡 Markdown</th>
                        <th style="padding:8px 12px; text-align:center; color:#f97316;">🟠 Negociar</th>
                        <th style="padding:8px 12px; text-align:center; color:#ef4444;">🔴 Liquidar</th>
                    </tr>
                </thead>
                <tbody>
                    <tr style="border-bottom:1px solid var(--capi-border);">
                        <td style="padding:8px 12px; font-weight:500;">Edad (sem)</td>
                        <td style="padding:8px 12px; text-align:center;">8 – 16</td>
                        <td style="padding:8px 12px; text-align:center;">16 – 26</td>
                        <td style="padding:8px 12px; text-align:center;">16+ (terceras)</td>
                        <td style="padding:8px 12px; text-align:center;">>26</td>
                    </tr>
                    <tr style="border-bottom:1px solid var(--capi-border);">
                        <td style="padding:8px 12px; font-weight:500;">Sell-through</td>
                        <td style="padding:8px 12px; text-align:center;">>5%</td>
                        <td style="padding:8px 12px; text-align:center;">2% – 5%</td>
                        <td style="padding:8px 12px; text-align:center;">cualquiera</td>
                        <td style="padding:8px 12px; text-align:center;"><2%</td>
                    </tr>
                    <tr style="border-bottom:1px solid var(--capi-border);">
                        <td style="padding:8px 12px; font-weight:500;">Descuento actual</td>
                        <td style="padding:8px 12px; text-align:center;"><10% o ninguno</td>
                        <td style="padding:8px 12px; text-align:center;"><40%</td>
                        <td style="padding:8px 12px; text-align:center;">cualquiera</td>
                        <td style="padding:8px 12px; text-align:center;">ya con 30%+</td>
                    </tr>
                    <tr style="border-bottom:1px solid var(--capi-border);">
                        <td style="padding:8px 12px; font-weight:500;">Tipo marca</td>
                        <td style="padding:8px 12px; text-align:center;">cualquiera</td>
                        <td style="padding:8px 12px; text-align:center;">cualquiera</td>
                        <td style="padding:8px 12px; text-align:center;">solo terceras</td>
                        <td style="padding:8px 12px; text-align:center;">cualquiera</td>
                    </tr>
                    <tr style="border-bottom:1px solid var(--capi-border);">
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
                <span style="color:var(--capi-text2); font-size:0.82em;"> &nbsp;·&nbsp; {len(df_obs):,} combos · S/ {df_obs['stock_valor_costo'].sum():,.0f} en capital</span>
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
                        title=dict(text="Capital Obsoleto por Antigüedad", font=dict(size=14, color=TH_TEXT_PY)),
                        showlegend=False,
                        height=340,
                    )
                    _obs_total = obs_rango["capital"].sum()
                    _obs_pct_total = (_obs_total / df_cob["stock_valor_costo"].sum() * 100) if df_cob["stock_valor_costo"].sum() > 0 else 0
                    fig_obs.add_annotation(
                        text=f"<b>{_obs_pct_total:.1f}%</b><br><span style='font-size:10px;color:var(--capi-text2)'>del inventario</span>",
                        showarrow=False, font=dict(size=18, color=TH_TEXT_PY),
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
        st.info("ℹ️ No hay transferencias sugeridas. Esto ocurre cuando no hay simultáneamente tiendas en SOBRESTOCK y QUIEBRE del mismo SKU.")
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
                    "QUIEBRE": STATUS_QUIEBRE, "PRE-QUIEBRE": STATUS_BAJA,
                    "ÓPTIMO": STATUS_OPTIMO, "ALTO": STATUS_ALTO,
                    "SOBRESTOCK": STATUS_SOBRESTOCK, "LIQUIDAR": STATUS_LIQUIDAR,
                    "NUEVO SIN VENTA": STATUS_NUEVO_SV, "DORMIDO": STATUS_DORMIDO,
                    "MUERTO": STATUS_MUERTO, "ESTANCADO": STATUS_ESTANCADO,
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


# ─── FENÓMENO DEL NIÑO ───────────────────────────────────────

elif nav_page == "🌡️ Fenómeno del Niño":
    st.markdown("#### 🌡️ Fenómeno del Niño — Análisis Climático de Inventario")
    st.caption("Análisis del impacto del Fenómeno del Niño en la demanda por categoría calórica.")

    # ── Cargar snapshots y temperatura ──
    import clima_engine as _ce
    from snapshots_engine import load_snapshot as _nino_load_snap, list_available_weeks as _nino_list_weeks

    _nino_weeks = _nino_list_weeks()
    if not _nino_weeks:
        st.warning("No hay snapshots disponibles para el análisis del Fenómeno del Niño.")
    else:
        _nino_snaps = {}
        for _w in _nino_weeks:
            try:
                _nino_snaps[_w] = _nino_load_snap(_w)
            except Exception:
                pass

        # Temperatura semanal
        _nino_start, _nino_end = _ce.get_snapshot_date_range()
        _nino_temp = []
        if _nino_start and _nino_end:
            try:
                _nino_temp = _ce.get_weekly_temperature(_nino_start, _nino_end)
            except Exception:
                _nino_temp = []

        # Ejecutar motor
        from motor_v2 import build_fenomeno_nino as _build_nino
        _nino_result = _build_nino(_nino_snaps, _nino_temp)

        if 'error' in _nino_result:
            st.error(_nino_result['error'])
        else:
            _nino_resumen = _nino_result['resumen_ejecutivo']

            # ── KPIs resumen ejecutivo (Output 6) ──
            _k1, _k2, _k3, _k4 = st.columns(4)
            with _k1:
                _temp_act = _nino_resumen.get('temp_promedio_actual')
                _delta_t = _nino_resumen.get('delta_temp_vs_normal')
                _delta_str = f"+{_delta_t}°C" if _delta_t and _delta_t > 0 else f"{_delta_t}°C" if _delta_t else ""
                st.markdown(f"""<div style="background:{SLATE_50};border:1px solid {SLATE_200};border-radius:8px;padding:12px;text-align:center;">
                    <div style="font-size:0.75rem;color:{SLATE_500};text-transform:uppercase;letter-spacing:0.5px;">Temp. Promedio</div>
                    <div style="font-size:1.8rem;font-weight:700;color:#dc2626;">{_temp_act or '—'}°C</div>
                    <div style="font-size:0.75rem;color:#dc2626;">{_delta_str} vs normal</div>
                </div>""", unsafe_allow_html=True)
            with _k2:
                _ratio = _nino_resumen.get('ratio_venta_ligero_grueso', 0)
                st.markdown(f"""<div style="background:{SLATE_50};border:1px solid {SLATE_200};border-radius:8px;padding:12px;text-align:center;">
                    <div style="font-size:0.75rem;color:{SLATE_500};text-transform:uppercase;letter-spacing:0.5px;">Ratio Ligero/Grueso</div>
                    <div style="font-size:1.8rem;font-weight:700;color:{TEAL_600};">{_ratio}x</div>
                    <div style="font-size:0.75rem;color:{SLATE_500};">venta LIGERO vs GRUESO</div>
                </div>""", unsafe_allow_html=True)
            with _k3:
                _cap_riesgo = _nino_resumen.get('capital_grueso_en_riesgo', 0)
                st.markdown(f"""<div style="background:{SLATE_50};border:1px solid {SLATE_200};border-radius:8px;padding:12px;text-align:center;">
                    <div style="font-size:0.75rem;color:{SLATE_500};text-transform:uppercase;letter-spacing:0.5px;">Capital GRUESO</div>
                    <div style="font-size:1.8rem;font-weight:700;color:#dc2626;">S/{_cap_riesgo:,.0f}</div>
                    <div style="font-size:0.75rem;color:{SLATE_500};">{_nino_resumen.get('pct_capital_en_grueso', 0)}% del total</div>
                </div>""", unsafe_allow_html=True)
            with _k4:
                _n_riesgo = _nino_resumen.get('n_skus_ligero_riesgo_quiebre', 0)
                st.markdown(f"""<div style="background:{SLATE_50};border:1px solid {SLATE_200};border-radius:8px;padding:12px;text-align:center;">
                    <div style="font-size:0.75rem;color:{SLATE_500};text-transform:uppercase;letter-spacing:0.5px;">SKUs Ligero en Riesgo</div>
                    <div style="font-size:1.8rem;font-weight:700;color:#f59e0b;">{_n_riesgo}</div>
                    <div style="font-size:0.75rem;color:{SLATE_500};">cobertura &lt; 4 sem</div>
                </div>""", unsafe_allow_html=True)

            st.markdown(f"<div style='border-bottom:1px solid {SLATE_200};margin:16px 0;'></div>", unsafe_allow_html=True)

            # ── Tabs para los 5 outputs restantes ──
            _nino_tab1, _nino_tab2, _nino_tab3, _nino_tab4, _nino_tab5 = st.tabs([
                "📊 Riesgo Quiebre por Línea",
                "🚨 SKUs en Riesgo",
                "💰 Capital por Calórica",
                "🏷️ Marcas Expuestas",
                "📈 Tendencia Temp×Venta",
            ])

            # ── Tab 1: Output 1 — Riesgo quiebre por línea ──
            with _nino_tab1:
                st.markdown("##### Cobertura restante por línea — ¿Cuántas semanas de stock quedan?")
                st.caption("Decisión: Si cobertura < 4 semanas → hablar con comprador para adelantar reposición")
                _df_riesgo = _nino_result['riesgo_quiebre_linea'].copy()
                _df_riesgo_display = _df_riesgo[_df_riesgo['cat_calorica'] != 'NEUTRO'].copy()

                # Formatear tabla
                _rows_riesgo = ""
                for _, _r in _df_riesgo_display.iterrows():
                    _cat = _r['cat_calorica']
                    _cat_color = '#dc2626' if _cat == 'GRUESO' else '#f59e0b' if _cat == 'MEDIO' else '#10b981'
                    _estado = str(_r['estado_riesgo'])
                    _cob = _r['cobertura_semanas']
                    _cob_color = '#dc2626' if _cob < 4 else '#f59e0b' if _cob < 8 else SLATE_700
                    _rows_riesgo += f"""<tr>
                        <td style="padding:8px 10px;font-weight:600;">{_r['linea']}</td>
                        <td style="padding:8px 10px;"><span style="background:{_cat_color}15;color:{_cat_color};padding:2px 8px;border-radius:4px;font-size:0.75rem;font-weight:600;">{_cat}</span></td>
                        <td style="padding:8px 10px;text-align:right;">{_r['n_skus']:,}</td>
                        <td style="padding:8px 10px;text-align:right;">{_r['stock_total']:,.0f}</td>
                        <td style="padding:8px 10px;text-align:right;">{_r['vta_semanal_uds']:,.0f}</td>
                        <td style="padding:8px 10px;text-align:right;font-weight:700;color:{_cob_color};">{_cob:.1f}</td>
                        <td style="padding:8px 10px;">{_estado}</td>
                    </tr>"""

                st.markdown(f"""<div style="overflow-x:auto;">
                <table style="width:100%;border-collapse:collapse;font-size:0.82rem;">
                <thead><tr style="background:{SLATE_100};border-bottom:2px solid {SLATE_200};">
                    <th style="padding:8px 10px;text-align:left;">Línea</th>
                    <th style="padding:8px 10px;text-align:left;">Calórica</th>
                    <th style="padding:8px 10px;text-align:right;">SKUs</th>
                    <th style="padding:8px 10px;text-align:right;">Stock Uds</th>
                    <th style="padding:8px 10px;text-align:right;">Venta/Sem</th>
                    <th style="padding:8px 10px;text-align:right;">Cob. Semanas</th>
                    <th style="padding:8px 10px;text-align:left;">Estado</th>
                </tr></thead>
                <tbody>{_rows_riesgo}</tbody>
                </table></div>""", unsafe_allow_html=True)

            # ── Tab 2: Output 2 — SKUs en riesgo de quiebre ──
            with _nino_tab2:
                st.markdown("##### SKUs LIGERO con riesgo de quiebre — Priorizar reposición")
                st.caption("Decisión: Enviar lista al comprador con urgencia de reposición")
                _df_skus_r = _nino_result['skus_riesgo_quiebre']
                if _df_skus_r.empty:
                    st.success("No hay SKUs LIGERO en riesgo de quiebre en este momento.")
                else:
                    st.warning(f"**{len(_df_skus_r)} SKUs** de productos LIGERO con cobertura < 4 semanas y venta superior a mediana")

                    _rows_sku = ""
                    for _, _s in _df_skus_r.head(30).iterrows():
                        _sem_rest = _s.get('semanas_restantes', 0)
                        _sem_color = '#dc2626' if _sem_rest < 2 else '#f59e0b' if _sem_rest < 3 else SLATE_700
                        _rows_sku += f"""<tr>
                            <td style="padding:6px 8px;font-size:0.78rem;">{_s['sku']}</td>
                            <td style="padding:6px 8px;font-size:0.78rem;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{_s.get('descripcion','')}</td>
                            <td style="padding:6px 8px;font-size:0.78rem;">{_s.get('marca','')}</td>
                            <td style="padding:6px 8px;font-size:0.78rem;">{_s.get('linea','')}</td>
                            <td style="padding:6px 8px;text-align:right;font-size:0.78rem;">{_s.get('stock_total',0):,.0f}</td>
                            <td style="padding:6px 8px;text-align:right;font-size:0.78rem;font-weight:700;color:{_sem_color};">{_sem_rest:.1f}</td>
                            <td style="padding:6px 8px;text-align:right;font-size:0.78rem;">S/{_s.get('venta_soles',0):,.0f}</td>
                        </tr>"""

                    st.markdown(f"""<div style="overflow-x:auto;max-height:500px;overflow-y:auto;">
                    <table style="width:100%;border-collapse:collapse;font-size:0.82rem;">
                    <thead><tr style="background:{SLATE_100};border-bottom:2px solid {SLATE_200};position:sticky;top:0;">
                        <th style="padding:6px 8px;text-align:left;">SKU</th>
                        <th style="padding:6px 8px;text-align:left;">Descripción</th>
                        <th style="padding:6px 8px;text-align:left;">Marca</th>
                        <th style="padding:6px 8px;text-align:left;">Línea</th>
                        <th style="padding:6px 8px;text-align:right;">Stock</th>
                        <th style="padding:6px 8px;text-align:right;">Sem. Rest.</th>
                        <th style="padding:6px 8px;text-align:right;">Venta S/</th>
                    </tr></thead>
                    <tbody>{_rows_sku}</tbody>
                    </table></div>""", unsafe_allow_html=True)

            # ── Tab 3: Output 3 — Capital por categoría calórica ──
            with _nino_tab3:
                st.markdown("##### Capital invertido por categoría calórica")
                st.caption("Decisión: Si GRUESO crece >5% semana a semana → activar liquidación")
                _df_cap = _nino_result['capital_por_calorica']

                _rows_cap = ""
                for _, _c in _df_cap.iterrows():
                    _cat = _c['cat_calorica']
                    _cat_color = '#dc2626' if _cat == 'GRUESO' else '#f59e0b' if _cat == 'MEDIO' else '#10b981' if _cat == 'LIGERO' else SLATE_500
                    _rot = _c['rotacion']
                    _rot_color = '#10b981' if _rot > 1.5 else '#f59e0b' if _rot > 0.5 else '#dc2626'
                    _rows_cap += f"""<tr>
                        <td style="padding:10px 12px;"><span style="background:{_cat_color}15;color:{_cat_color};padding:3px 10px;border-radius:4px;font-weight:700;font-size:0.82rem;">{_cat}</span></td>
                        <td style="padding:10px 12px;text-align:right;">{_c['n_skus']:,}</td>
                        <td style="padding:10px 12px;text-align:right;font-weight:700;">S/{_c['capital_invertido']:,.0f}</td>
                        <td style="padding:10px 12px;text-align:right;">{_c['pct_capital']:.1f}%</td>
                        <td style="padding:10px 12px;text-align:right;">S/{_c['venta_soles']:,.0f}</td>
                        <td style="padding:10px 12px;text-align:right;font-weight:700;color:{_rot_color};">{_rot*100:.1f}%</td>
                    </tr>"""

                st.markdown(f"""<div style="overflow-x:auto;">
                <table style="width:100%;border-collapse:collapse;font-size:0.85rem;">
                <thead><tr style="background:{SLATE_100};border-bottom:2px solid {SLATE_200};">
                    <th style="padding:10px 12px;text-align:left;">Categoría</th>
                    <th style="padding:10px 12px;text-align:right;">SKUs</th>
                    <th style="padding:10px 12px;text-align:right;">Capital S/</th>
                    <th style="padding:10px 12px;text-align:right;">% Capital</th>
                    <th style="padding:10px 12px;text-align:right;">Venta S/</th>
                    <th style="padding:10px 12px;text-align:right;">Rotación</th>
                </tr></thead>
                <tbody>{_rows_cap}</tbody>
                </table></div>""", unsafe_allow_html=True)

                # Insight box
                _cap_grueso_val = _df_cap[_df_cap['cat_calorica']=='GRUESO']['capital_invertido'].sum()
                _rot_grueso = _df_cap[_df_cap['cat_calorica']=='GRUESO']['rotacion'].values
                _rot_grueso_val = _rot_grueso[0] if len(_rot_grueso) > 0 else 0
                _rot_ligero = _df_cap[_df_cap['cat_calorica']=='LIGERO']['rotacion'].values
                _rot_ligero_val = _rot_ligero[0] if len(_rot_ligero) > 0 else 0
                st.markdown(f"""<div style="background:#fef2f2;border-left:4px solid #dc2626;padding:12px 16px;border-radius:4px;margin-top:12px;font-size:0.85rem;">
                    <strong>GRUESO tiene S/{_cap_grueso_val:,.0f} de capital invertido con rotación de solo {_rot_grueso_val*100:.1f}%</strong>,
                    mientras que LIGERO rota a {_rot_ligero_val*100:.1f}% — {_rot_ligero_val/_rot_grueso_val:.0f}x más rápido.
                    El Fenómeno del Niño está generando que el capital en prendas abrigadoras se quede parado.
                </div>""", unsafe_allow_html=True)

            # ── Tab 4: Output 4 — Marcas expuestas ──
            with _nino_tab4:
                st.markdown("##### Marcas más expuestas al Fenómeno del Niño")
                st.caption("Decisión: Priorizar negociación con proveedores de marcas más vulnerables")
                _df_exp = _nino_result['marcas_expuestas']

                _rows_exp = ""
                for _, _m in _df_exp.head(15).iterrows():
                    _vuln = _m['idx_vulnerabilidad']
                    _vuln_color = '#dc2626' if _vuln > 0.3 else '#f59e0b' if _vuln > 0.15 else '#10b981'
                    _vuln_label = 'ALTA' if _vuln > 0.3 else 'MEDIA' if _vuln > 0.15 else 'BAJA'
                    _rows_exp += f"""<tr>
                        <td style="padding:8px 10px;font-weight:600;">{_m['marca']}</td>
                        <td style="padding:8px 10px;text-align:right;">S/{_m['capital_total']:,.0f}</td>
                        <td style="padding:8px 10px;text-align:right;">S/{_m['capital_grueso']:,.0f}</td>
                        <td style="padding:8px 10px;text-align:right;">{_m['pct_capital_grueso']:.1f}%</td>
                        <td style="padding:8px 10px;text-align:right;">{_m['rotacion_grueso']*100:.1f}%</td>
                        <td style="padding:8px 10px;text-align:center;"><span style="background:{_vuln_color}15;color:{_vuln_color};padding:2px 8px;border-radius:4px;font-size:0.75rem;font-weight:600;">{_vuln_label} ({_vuln:.3f})</span></td>
                    </tr>"""

                st.markdown(f"""<div style="overflow-x:auto;">
                <table style="width:100%;border-collapse:collapse;font-size:0.82rem;">
                <thead><tr style="background:{SLATE_100};border-bottom:2px solid {SLATE_200};">
                    <th style="padding:8px 10px;text-align:left;">Marca</th>
                    <th style="padding:8px 10px;text-align:right;">Capital Total</th>
                    <th style="padding:8px 10px;text-align:right;">Capital GRUESO</th>
                    <th style="padding:8px 10px;text-align:right;">% GRUESO</th>
                    <th style="padding:8px 10px;text-align:right;">Rotación GRUESO</th>
                    <th style="padding:8px 10px;text-align:center;">Vulnerabilidad</th>
                </tr></thead>
                <tbody>{_rows_exp}</tbody>
                </table></div>""", unsafe_allow_html=True)

            # ── Tab 5: Output 5 — Tendencia Temp × Venta ──
            with _nino_tab5:
                st.markdown("##### Tendencia: Temperatura vs Venta semanal por categoría calórica")
                st.caption("Insight: Cómo responde la venta de cada categoría a los cambios de temperatura")
                _df_tend = _nino_result['tendencia_temp_venta']
                if _df_tend.empty:
                    st.info("Se necesitan al menos 2 snapshots para calcular tendencia.")
                else:
                    # Chart con Plotly
                    import plotly.graph_objects as go
                    from plotly.subplots import make_subplots

                    _fig_nino = make_subplots(specs=[[{"secondary_y": True}]])

                    for _cat, _color in [('GRUESO', '#dc2626'), ('LIGERO', '#10b981'), ('MEDIO', '#f59e0b')]:
                        _sub = _df_tend[_df_tend['cat_calorica'] == _cat].sort_values('semana_iso')
                        if not _sub.empty:
                            _fig_nino.add_trace(
                                go.Bar(x=_sub['semana_iso'], y=_sub['delta_venta'],
                                       name=f'Venta {_cat}', marker_color=_color, opacity=0.7),
                                secondary_y=False,
                            )

                    # Temperatura como línea
                    _temp_df = _df_tend[['semana_iso', 'temp_avg']].drop_duplicates().dropna().sort_values('semana_iso')
                    if not _temp_df.empty:
                        _fig_nino.add_trace(
                            go.Scatter(x=_temp_df['semana_iso'], y=_temp_df['temp_avg'],
                                       name='Temp. Promedio °C', line=dict(color='#6366f1', width=3),
                                       mode='lines+markers'),
                            secondary_y=True,
                        )

                    _fig_nino.update_layout(
                        barmode='group',
                        height=400,
                        margin=dict(l=40, r=40, t=30, b=40),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                        plot_bgcolor='rgba(0,0,0,0)',
                    )
                    _fig_nino.update_yaxes(title_text="Venta Semanal S/", secondary_y=False)
                    _fig_nino.update_yaxes(title_text="Temperatura °C", secondary_y=True)

                    st.plotly_chart(_fig_nino, use_container_width=True)

                    st.markdown(f"""<div style="background:{TEAL_50};border-left:4px solid {TEAL_600};padding:12px 16px;border-radius:4px;font-size:0.85rem;">
                        <strong>Nota:</strong> Con solo {len(_temp_df)} semanas de datos, la tendencia es visual — no estadística.
                        Cuando se integre la data 2023, se podrá calcular la correlación formal temperatura × venta.
                    </div>""", unsafe_allow_html=True)

            # ── Comparación histórica ──
            st.markdown(f"<div style='border-bottom:1px solid {SLATE_200};margin:16px 0;'></div>", unsafe_allow_html=True)
            st.markdown("##### 🌍 Comparación Histórica — Temperatura Mar-May por año")
            try:
                _hist = _ce.get_historical_comparison()
                _rows_hist = ""
                for _h in _hist:
                    _label_color = '#dc2626' if _h['label'] == 'Niño' else '#10b981'
                    _rows_hist += f"""<tr>
                        <td style="padding:8px 12px;font-weight:700;font-size:1rem;">{_h['year']}</td>
                        <td style="padding:8px 12px;text-align:center;"><span style="background:{_label_color}15;color:{_label_color};padding:2px 10px;border-radius:4px;font-weight:600;">{_h['label']}</span></td>
                        <td style="padding:8px 12px;text-align:right;font-weight:700;font-size:1rem;">{_h['temp_avg']}°C</td>
                        <td style="padding:8px 12px;text-align:right;">{_h['temp_max_periodo']}°C</td>
                        <td style="padding:8px 12px;text-align:right;">{_h['temp_min_periodo']}°C</td>
                        <td style="padding:8px 12px;text-align:right;">{_h['n_dias']} días</td>
                    </tr>"""
                st.markdown(f"""<div style="overflow-x:auto;">
                <table style="width:100%;border-collapse:collapse;font-size:0.85rem;">
                <thead><tr style="background:{SLATE_100};border-bottom:2px solid {SLATE_200};">
                    <th style="padding:8px 12px;text-align:left;">Año</th>
                    <th style="padding:8px 12px;text-align:center;">Clima</th>
                    <th style="padding:8px 12px;text-align:right;">Temp Prom</th>
                    <th style="padding:8px 12px;text-align:right;">Máx Período</th>
                    <th style="padding:8px 12px;text-align:right;">Mín Período</th>
                    <th style="padding:8px 12px;text-align:right;">Datos</th>
                </tr></thead>
                <tbody>{_rows_hist}</tbody>
                </table></div>""", unsafe_allow_html=True)

                st.markdown(f"""<div style="background:#fef3c7;border-left:4px solid #f59e0b;padding:12px 16px;border-radius:4px;margin-top:12px;font-size:0.85rem;">
                    <strong>2023 vs 2026:</strong> Temperatura similar (Niño), pero contexto económico opuesto.
                    2023 = economía débil → venta desastre. 2026 = economía fuerte → oportunidad real.
                    La data 2023 servirá como control para aislar el efecto temperatura del efecto económico.
                </div>""", unsafe_allow_html=True)
            except Exception as _e_hist:
                st.info(f"No se pudo cargar la comparación histórica: {_e_hist}")


# ─── Afinidad Producto × Plaza ────────────────────────────────

elif nav_page == "🏪 Afinidad Producto×Plaza":
    import glob as _glob_mod
    import io as _io_af
    from afinidad_engine import build_afinidad
    from transformar_profundidad import STORE_NAMES as _STORE_NAMES_AF

    st.markdown(f'<h2 style="color:{SLATE_900};margin-bottom:4px;">🏪 Afinidad Producto × Plaza</h2>', unsafe_allow_html=True)
    st.caption("Qué productos funcionan en qué tiendas — empujes inteligentes, redistribución y señales de producción")

    # Detectar base más reciente — priorizar la última subida por el usuario
    _base_path_af = st.session_state.get("_base_profundidad_path")
    if _base_path_af and os.path.exists(_base_path_af):
        _base_name_af = os.path.basename(_base_path_af)
    else:
        _bases_af = _glob_mod.glob("data2/bases antiguas/Base*.xlsx")
        _bases_af = sorted(_bases_af, key=os.path.getmtime)  # por fecha real, no alfabético
        if _bases_af:
            _base_path_af = _bases_af[-1]
            _base_name_af = os.path.basename(_base_path_af)
        else:
            _base_path_af = None
    if _base_path_af is None:
        st.warning("No se encontraron Bases de Profundidad. Sube tu archivo y ejecuta el análisis primero.")
    else:
        st.info(f"📂 Base: **{_base_name_af}**")

        with st.spinner("Analizando afinidad producto × plaza..."):
            try:
                _af_result = build_afinidad(_base_path_af)
            except Exception as _e_af:
                st.error(f"Error en análisis de afinidad: {_e_af}")
                _af_result = None

        if _af_result is not None:
            _rm = _af_result['rotation_matrix']
            _cl = _af_result['clusters_df']
            _an = _af_result['anomalias_df']
            _emp = _af_result['empujes_df']
            _red = _af_result['redistribucion_df']
            _prod = _af_result['produccion_df']
            _tiendas_af = _af_result['tiendas_activas']

            # KPI cards
            _n_empujes = len(_emp)
            _u_empujes = int(_emp['unidades_sugeridas'].sum()) if _n_empujes > 0 else 0
            _n_redist = len(_red)
            _n_anomalias = len(_an)
            _n_prod = len(_prod)

            _kpi_html_af = f"""<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px;">
              <div style="background:{SLATE_100};border-radius:8px;padding:14px 16px;">
                <div style="font-size:12px;color:{SLATE_500};">Empujes CD→Tiendas</div>
                <div style="font-size:22px;font-weight:600;color:{TEAL_600};">{_n_empujes:,}</div>
                <div style="font-size:11px;color:{SLATE_400};">{_u_empujes:,} unidades</div>
              </div>
              <div style="background:{SLATE_100};border-radius:8px;padding:14px 16px;">
                <div style="font-size:12px;color:{SLATE_500};">Redistribuciones</div>
                <div style="font-size:22px;font-weight:600;color:{TEAL_600};">{_n_redist:,}</div>
                <div style="font-size:11px;color:{SLATE_400};">entre tiendas</div>
              </div>
              <div style="background:{SLATE_100};border-radius:8px;padding:14px 16px;">
                <div style="font-size:12px;color:{SLATE_500};">Anomalías</div>
                <div style="font-size:22px;font-weight:600;color:#B45309;">{_n_anomalias:,}</div>
                <div style="font-size:11px;color:{SLATE_400};">producto malo + mal match</div>
              </div>
              <div style="background:{SLATE_100};border-radius:8px;padding:14px 16px;">
                <div style="font-size:12px;color:{SLATE_500};">Señales Producción</div>
                <div style="font-size:22px;font-weight:600;color:#059669;">{_n_prod}</div>
                <div style="font-size:11px;color:{SLATE_400};">líneas con demanda insatisfecha</div>
              </div>
            </div>"""
            st.markdown(_kpi_html_af, unsafe_allow_html=True)

            # Tabs
            _tab_hm, _tab_cl, _tab_emp, _tab_red, _tab_an, _tab_prod = st.tabs([
                "🗺️ Heatmap Rotación", "🔗 Clusters", "📦 Empujes CD→Tiendas",
                "🔄 Redistribución", "⚠️ Anomalías", "🏭 Producción"
            ])

            # ── TAB 1: Heatmap ──
            with _tab_hm:
                import plotly.graph_objects as _go_af
                _rm_display = _rm.copy()
                _rm_display.index = [l.title() for l in _rm_display.index]
                _rm_display.columns = [_STORE_NAMES_AF.get(c, c) for c in _rm_display.columns]

                _fig_hm = _go_af.Figure(data=_go_af.Heatmap(
                    z=_rm_display.values,
                    x=_rm_display.columns.tolist(),
                    y=_rm_display.index.tolist(),
                    colorscale=[[0, '#FEE2E2'], [0.25, '#FECACA'], [0.5, '#FDE68A'], [0.75, '#A7F3D0'], [1, '#059669']],
                    text=[[f"{v*100:.1f}%" for v in row] for row in _rm_display.values],
                    texttemplate="%{text}",
                    textfont={"size": 10},
                    hovertemplate="Línea: %{y}<br>Tienda: %{x}<br>Rotación: %{z:.2%}<extra></extra>",
                    colorbar=dict(title=dict(text="Rotación %", side="right"), tickformat=".0%"),
                ))
                _fig_hm.update_layout(
                    height=500, margin=dict(l=120, r=20, t=30, b=80),
                    xaxis=dict(tickangle=-45, tickfont=dict(size=10)),
                    yaxis=dict(tickfont=dict(size=11)),
                    plot_bgcolor='white',
                )
                st.plotly_chart(_fig_hm, use_container_width=True)
                st.caption("Rotación = Vta / Stk. Verde = alta rotación (oportunidad). Rojo = baja rotación (capital parado).")

            # ── TAB 2: Clusters ──
            with _tab_cl:
                for _cid in sorted(_cl['cluster_id'].unique()):
                    _sub_cl = _cl[_cl['cluster_id'] == _cid]
                    _nombre_cl = _sub_cl['cluster_nombre'].iloc[0]
                    _n_tiendas_cl = len(_sub_cl)
                    _vta_cl = _sub_cl['vta_total'].sum()

                    _tiendas_list = ", ".join([
                        f"{_STORE_NAMES_AF.get(t, t)}" for t in _sub_cl['tienda'].tolist()
                    ])

                    _perfiles_af = _af_result['perfiles']
                    _perfil_cl = _perfiles_af.get(_cid, {})
                    _top3_lineas = sorted(_perfil_cl.items(), key=lambda x: -x[1])[:3]
                    _top3_str = ", ".join([f"{l.title()} ({r:.3f})" for l, r in _top3_lineas])

                    st.markdown(f"""<div style="background:{SLATE_100};border-radius:8px;padding:14px 18px;margin-bottom:10px;">
                      <div style="font-weight:600;color:{SLATE_900};font-size:14px;">{_nombre_cl}</div>
                      <div style="color:{SLATE_500};font-size:12px;margin:4px 0;">{_n_tiendas_cl} tiendas — Vta total: {_vta_cl:,}u</div>
                      <div style="color:{SLATE_700};font-size:13px;">{_tiendas_list}</div>
                      <div style="color:{TEAL_600};font-size:12px;margin-top:4px;">Top líneas: {_top3_str}</div>
                    </div>""", unsafe_allow_html=True)

            # ── TAB 3: Empujes CD→Tiendas ──
            with _tab_emp:
                if len(_emp) == 0:
                    st.info("No se encontraron oportunidades de empuje con los umbrales actuales.")
                else:
                    _col_filt1, _col_filt2 = st.columns(2)
                    with _col_filt1:
                        _marca_filt_emp = st.selectbox("Marca", ["Todas"] + sorted(_emp['marca'].unique().tolist()), key="af_emp_marca")
                    with _col_filt2:
                        _tienda_filt_emp = st.selectbox("Tienda destino", ["Todas"] + sorted(_emp['tienda'].unique().tolist()), key="af_emp_tienda")

                    _emp_show = _emp.copy()
                    if _marca_filt_emp != "Todas":
                        _emp_show = _emp_show[_emp_show['marca'] == _marca_filt_emp]
                    if _tienda_filt_emp != "Todas":
                        _emp_show = _emp_show[_emp_show['tienda'] == _tienda_filt_emp]

                    _emp_show['tienda_nombre'] = _emp_show['tienda'].map(lambda t: _STORE_NAMES_AF.get(t, t))
                    _emp_cols = ['marca', 'descripcion', 'tienda_nombre', 'stk_actual_tienda',
                                 'stock_cd', 'rotacion_linea_tienda']
                    # Nuevas columnas de cobertura (si existen en el output del motor)
                    _has_cob = 'vta_semanal_est' in _emp_show.columns and 'target_stock' in _emp_show.columns
                    if _has_cob:
                        _emp_cols += ['vta_semanal_est', 'target_stock']
                    _emp_cols += ['unidades_sugeridas', 'es_marca_propia']
                    _emp_display = _emp_show[_emp_cols].head(100)
                    _emp_headers = ['Marca', 'Descripción', 'Tienda', 'Stk Tienda', 'Stk CD', 'Rot. %']
                    if _has_cob:
                        _emp_headers += ['Vta/Sem Est', 'Target 12s']
                    _emp_headers += ['Empujar', 'Propia']
                    _emp_display.columns = _emp_headers
                    _emp_display['Rot. %'] = (_emp_display['Rot. %'] * 100).round(1)

                    _cob_sem = 12  # default
                    try:
                        import json as _json_emp
                        _cfg_path_emp = os.path.join(os.path.dirname(__file__), 'config_afinidad.json')
                        with open(_cfg_path_emp) as _f_emp:
                            _cob_sem = _json_emp.load(_f_emp).get('empujes', {}).get('semanas_cobertura_target', 12)
                    except Exception:
                        pass
                    st.markdown(f"**{len(_emp_show):,}** empujes — **{_emp_show['unidades_sugeridas'].sum():,}** unidades"
                                f" — Cobertura target: **{_cob_sem} semanas**")
                    st.dataframe(_emp_display, use_container_width=True, hide_index=True)

                    # Descarga Excel
                    _buf_emp = _io_af.BytesIO()
                    _emp_show.to_excel(_buf_emp, index=False, sheet_name="Empujes CD")
                    st.download_button("📥 Descargar empujes", _buf_emp.getvalue(),
                                       file_name="empujes_cd_tiendas.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                       key="dl_empujes_af")

            # ── TAB 4: Redistribución ──
            with _tab_red:
                if len(_red) == 0:
                    st.info("No se encontraron oportunidades de redistribución con los umbrales actuales.")
                else:
                    _red_show = _red.copy()
                    _red_show['origen_nombre'] = _red_show['tienda_origen'].map(lambda t: _STORE_NAMES_AF.get(t, t))
                    _red_show['destino_nombre'] = _red_show['tienda_destino'].map(lambda t: _STORE_NAMES_AF.get(t, t))

                    _red_display = _red_show[['marca', 'descripcion', 'linea', 'origen_nombre', 'stk_origen',
                                               'destino_nombre', 'stk_destino', 'rotacion_destino',
                                               'unidades_sugeridas', 'mismo_cluster']].head(100)
                    _red_display.columns = ['Marca', 'Descripción', 'Línea', 'Origen', 'Stk Origen',
                                            'Destino', 'Stk Destino', 'Rot. Destino %', 'Mover', 'Mismo Cluster']
                    _red_display['Rot. Destino %'] = (_red_display['Rot. Destino %'] * 100).round(1)

                    st.markdown(f"**{len(_red_show):,}** oportunidades — **{_red_show['unidades_sugeridas'].sum():,}** unidades a redistribuir")
                    st.dataframe(_red_display, use_container_width=True, hide_index=True)

                    _buf_red = _io_af.BytesIO()
                    _red_show.to_excel(_buf_red, index=False, sheet_name="Redistribución")
                    st.download_button("📥 Descargar redistribución", _buf_red.getvalue(),
                                       file_name="redistribucion_tiendas.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                       key="dl_redist_af")

            # ── TAB 5: Anomalías ──
            with _tab_an:
                if len(_an) == 0:
                    st.info("No se detectaron anomalías cruzadas.")
                else:
                    _pm_af = _an[_an['tipo_anomalia'] == 'producto_malo']
                    _mm_af = _an[_an['tipo_anomalia'] == 'mal_match_plaza']

                    st.markdown(f"""<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px;">
                      <div style="background:#FEF2F2;border-radius:8px;padding:12px 16px;">
                        <div style="font-size:12px;color:#991B1B;">Producto malo (no vende en ninguna tienda)</div>
                        <div style="font-size:20px;font-weight:600;color:#B91C1C;">{len(_pm_af)}</div>
                        <div style="font-size:11px;color:#DC2626;">Stk parado: {_pm_af['stk_parado'].sum():,}u → Liquidar</div>
                      </div>
                      <div style="background:#FFF7ED;border-radius:8px;padding:12px 16px;">
                        <div style="font-size:12px;color:#92400E;">Mal match plaza (vende en unas, no en otras)</div>
                        <div style="font-size:20px;font-weight:600;color:#B45309;">{len(_mm_af)}</div>
                        <div style="font-size:11px;color:#D97706;">Stk parado: {_mm_af['stk_parado'].sum():,}u → Redistribuir</div>
                      </div>
                    </div>""", unsafe_allow_html=True)

                    _tipo_filt_an = st.radio("Tipo", ["Todos", "Producto malo", "Mal match plaza"], horizontal=True, key="af_an_tipo")
                    _an_show = _an.copy()
                    if _tipo_filt_an == "Producto malo":
                        _an_show = _pm_af
                    elif _tipo_filt_an == "Mal match plaza":
                        _an_show = _mm_af

                    _an_display = _an_show[['marca', 'descripcion', 'linea', 'tipo_anomalia',
                                            'n_tiendas_sin_venta', 'n_tiendas_con_venta', 'stk_parado', 'accion']].head(100)
                    _an_display.columns = ['Marca', 'Descripción', 'Línea', 'Tipo', 'Sin Venta', 'Con Venta', 'Stk Parado', 'Acción']

                    st.dataframe(_an_display, use_container_width=True, hide_index=True)

            # ── TAB 6: Producción ──
            with _tab_prod:
                if len(_prod) == 0:
                    st.info("No se detectaron señales de producción con los umbrales actuales.")
                else:
                    st.markdown("**Líneas con alta rotación + baja cobertura en múltiples tiendas del mismo cluster**")

                    _prod_display = _prod[['marca', 'linea', 'n_tiendas', 'tiendas', 'vta_total',
                                            'stk_total', 'rotacion_prom', 'es_marca_propia', 'accion']].copy()
                    _prod_display.columns = ['Marca', 'Línea', 'N Tiendas', 'Tiendas', 'Vta Total',
                                             'Stk Total', 'Rotación %', 'Propia', 'Acción']
                    _prod_display['Rotación %'] = (_prod_display['Rotación %'] * 100).round(1)

                    st.dataframe(_prod_display, use_container_width=True, hide_index=True)

                    _propias_prod = _prod[_prod['es_marca_propia']]
                    if len(_propias_prod) > 0:
                        st.success(f"🏭 **{len(_propias_prod)} señales de marcas propias** — tienes control de producción para responder.")


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
                ("🔮 Riesgo Quiebre", "RIESGO QUIEBRE", "#FDF4FF", "#9333EA"),
            ]
            for col, (label, keyword, bg, fg) in zip(_kpi_cols, _tipos_kpi):
                _count = int(df_alertas["tipo_alerta"].str.contains(keyword, na=False).sum())
                _cap_sum = df_alertas.loc[df_alertas["tipo_alerta"].str.contains(keyword, na=False), "capital_stock"].sum()
                col.markdown(
                    f'<div style="background:{bg}; border-radius:12px; padding:12px 14px; text-align:center;">'
                    f'<div style="font-size:1.6em; font-weight:700; color:{fg};">{_count}</div>'
                    f'<div style="font-size:0.75em; color:var(--capi-text2);">{label}</div>'
                    f'<div style="font-size:0.7em; color:var(--capi-text2);">S/ {_cap_sum:,.0f}</div>'
                    f'</div>',
                    unsafe_allow_html=True
                )

            # Capital total en riesgo
            _capital_total_riesgo = df_alertas[
                df_alertas["tipo_alerta"].str.contains("SE DETUVO|FRENANDO|RIESGO QUIEBRE", na=False)
            ]["capital_stock"].sum()
            if _capital_total_riesgo > 0:
                st.markdown(
                    f'<div style="background:linear-gradient(135deg,#FEF2F2,#FFF7ED); border-radius:12px; padding:10px 16px; margin-top:8px; text-align:center;">'
                    f'<span style="font-size:0.85em; color:var(--capi-text);">Capital en riesgo (detuvo + frenando + riesgo quiebre):</span>'
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
                "RIESGO QUIEBRE": ("#FDF4FF", "#9333EA"),
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
                    _t_bg, _t_fg = TH_BG_SURFACE_PY, TH_TEXT2_PY
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
                        _bg, _border = TH_BG_SURFACE_PY, TH_TEXT2_PY
                        for kw, (bg_c, bd_c) in _COLOR_MAP.items():
                            if kw in tipo:
                                _bg, _border = bg_c, bd_c
                                break

                        _temp_tag = f' · <span style="background:rgba(13,148,136,0.1); color:{TEAL_600}; padding:1px 6px; border-radius:4px; font-size:0.78em;">{r["temporada"]}</span>' if r.get("temporada") else ""
                        _cob_txt = f'{r["cobertura_sem"]:.1f} sem' if r.get("cobertura_sem") is not None else "—"

                        st.markdown(
                            f'<div style="background-color:{_bg}; padding:14px 18px; border-radius:12px; margin-bottom:8px; border-left:4px solid {_border};">'
                            f'<div style="display:flex; justify-content:space-between; align-items:center;">'
                            f'  <div><strong style="color:var(--capi-text);">{tipo}</strong>{_temp_tag}</div>'
                            f'  <div style="text-align:right; font-size:0.82em; color:var(--capi-text2);">S/ {r["capital_stock"]:,.0f} en stock</div>'
                            f'</div>'
                            f'<div style="margin-top:4px;">'
                            f'  <code style="background:rgba(0,0,0,0.06); padding:2px 6px; border-radius:4px; font-size:0.82em;">{r["sku"]}</code>'
                            f'  &nbsp;·&nbsp; {str(r["nombre"])[:35]}'
                            f'</div>'
                            f'<div style="margin-top:4px; font-size:0.82em; color:var(--capi-text2);">'
                            f'  Estado: {r["estado_actual"]} &nbsp;·&nbsp; Stock: {int(r["stock_total"])} uds ({int(r["n_tiendas"])} tiendas)'
                            f'  &nbsp;·&nbsp; Cob: {_cob_txt} &nbsp;·&nbsp; Edad: {int(r["edad_semanas"])} sem'
                            f'</div>'
                            f'<div style="margin-top:6px; font-size:0.88em; color:var(--capi-text);">{r["detalle"]}</div>'
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
                    f'<strong style="color:var(--capi-text);">{r["tipo"]}</strong> &nbsp;·&nbsp; '
                    f'<code style="background:rgba(0,0,0,0.06); padding:2px 6px; border-radius:4px; font-size:0.82em;">{r["sku"]}</code> '
                    f'&nbsp;·&nbsp; {str(r["nombre"])[:30]} &nbsp;·&nbsp; Tienda: {r["tienda_anomala"]}<br>'
                    f'<span style="font-size:0.88em; color:var(--capi-text);">{r["detalle"]}</span>'
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
                    f'<div style="font-size:0.82em;color:var(--capi-text2);">Contribución extra mensual</div>'
                    f'<div style="font-size:1.3em;font-weight:700;color:{TEAL_600};">S/{abs(_sim_extra_contrib):,.0f}</div>'
                    f'<div style="font-size:0.78em;color:var(--capi-text2);">'
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
                f'<div style="font-size:0.82em;color:var(--capi-text2);margin-top:6px;">'
                f'Markdown acelera la recuperación de capital pero destruye margen. '
                f'La pregunta no es "cuánto margen pierdo" sino "cuánto vale tener ese capital libre 8 semanas antes".'
                f'</div></div>', unsafe_allow_html=True
            )
        with _tf_col2:
            st.markdown(
                f'<div style="background:#fff;padding:14px 18px;border-radius:10px;border:1px solid #e9ecef;">'
                f'<strong>Diversidad vs rentabilidad</strong>'
                f'<div style="font-size:0.82em;color:var(--capi-text2);margin-top:6px;">'
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

def _build_excel(cob_json, rep_pivot_json, rep_json, trans_json, prec_json, alertas_json, anomalias_json):
    """Genera el Excel de resultados en memoria con columnas de precio y nuevo margen.

    SIN @st.cache_data a propósito: la función llama a helpers externos
    (agente_terceras.*) cuyo código puede cambiar entre deploys. El cache de
    Streamlit solo hashea el código directo + argumentos, NO las funciones
    llamadas, así que cacheaba Excels viejos tras arreglar un helper. Generarlo
    fresco toma ~1-2 seg y elimina esa clase de bug."""
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
        # Revisar Terceras: top 5 SKUs por marca×línea con sobrestock real
        # (cobertura >16 sem, edad ≥3 sem) + criticidad 🔴🟠🟡. Hoja única
        # accionable para revisar/negociar — reemplaza las dos hojas previas.
        _df_rev = agente_terceras.top5_por_marca_linea(_df_cob_ref, top_n=5)
        if not _df_rev.empty:
            _cols_r = [c for c in ['marca', 'categoria', 'criticidad', 'sku', 'nombre', 'edad',
                                   'cobertura', 'stock', 'vta_sem', 'dscto', 'capital',
                                   'sell_through', 'margen_efectivo'] if c in _df_rev.columns]
            _rev_out = _df_rev[_cols_r].rename(columns={
                'marca': 'Marca', 'categoria': 'Línea', 'criticidad': 'Criticidad',
                'sku': 'SKU', 'nombre': 'Producto', 'edad': 'Edad (sem)',
                'cobertura': 'Cobertura (sem)', 'stock': 'Stock (uds)',
                'vta_sem': 'Vta/sem (uds)', 'dscto': 'Dscto', 'capital': 'Capital S/ (costo)',
                'sell_through': 'Sell-through %', 'margen_efectivo': 'Margen efect. %',
            })
            _rev_out.to_excel(writer, sheet_name="Revisar Terceras", index=False)
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
st.markdown(f'<div class="section-header"><h3>📸 Medición de resultados</h3><span class="live-badge">TRACKING</span></div>', unsafe_allow_html=True)

import json as _json_snap

# ── Guardar snapshot ──
_snap_tab1, _snap_tab2, _snap_tab3 = st.tabs(["📸 Guardar snapshot", "📊 Comparativo", "📂 Cargar snapshots"])

with _snap_tab1:
    st.caption("Guarda un snapshot de los KPIs actuales para medir el impacto semana a semana. El primer snapshot es tu baseline (semana 0).")
    _snap_col1, _snap_col2 = st.columns(2)
    with _snap_col1:
        _snap_label = st.text_input("Etiqueta del snapshot", value="semana_0", key="snap_label",
                                     help="Ej: semana_0 (baseline), semana_1, semana_2...")
    with _snap_col2:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if st.button("📸 Guardar snapshot", key="btn_snapshot", use_container_width=True):
            _snap = motor_v2.snapshot_kpis(st.session_state["results"], semana_label=_snap_label)
            st.session_state["_last_snapshot"] = _snap
            st.success(f"Snapshot '{_snap_label}' guardado — {_snap['total_combos']:,} combos, ST {_snap['sell_through_pct']:.1f}%, Margen {_snap.get('margen_efectivo_pct', 0):.1f}%")

    # Botón de descarga del último snapshot
    if st.session_state.get("_last_snapshot"):
        _snap_json = _json_snap.dumps(st.session_state["_last_snapshot"], indent=2, ensure_ascii=False)
        st.download_button(
            "📥 Descargar snapshot (.json)",
            data=_snap_json,
            file_name=f"snapshot_{st.session_state['_last_snapshot'].get('semana', 'actual')}.json",
            mime="application/json",
            key="dl_snapshot",
        )
        st.caption("Descarga y guarda este archivo. Lo necesitarás para comparar en futuras sesiones.")

    # Resumen de snapshots guardados en servidor
    _all_snaps_server = motor_v2.load_snapshots()
    if _all_snaps_server:
        st.markdown(f"**{len(_all_snaps_server)} snapshots en servidor:**")
        for _s_item in _all_snaps_server:
            st.caption(f"• {_s_item.get('semana', '?')} — {_s_item.get('timestamp', '?')[:10]} — ST {_s_item.get('sell_through_pct', 0):.1f}%")
    else:
        st.info("No hay snapshots guardados aún. Guarda tu primer snapshot como baseline (semana_0).")

with _snap_tab2:
    # Cargar snapshots del servidor + session_state (uploads)
    _all_snaps = motor_v2.load_snapshots()
    _uploaded_snaps = st.session_state.get("_uploaded_snapshots", [])
    _all_snaps = _all_snaps + _uploaded_snaps

    if len(_all_snaps) >= 2:
        st.caption(f"{len(_all_snaps)} snapshots disponibles para comparar.")
        _snap_options = [f"{s_item.get('semana', '?')} ({s_item.get('timestamp', '?')[:10]})" for s_item in _all_snaps]
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

                # Descargar comparativo como Excel
                _comp_xl_buf = io.BytesIO()
                with pd.ExcelWriter(_comp_xl_buf, engine='openpyxl') as _cw:
                    _df_comp.to_excel(_cw, sheet_name='Comparativo', index=False)
                st.download_button(
                    "📥 Descargar comparativo (.xlsx)",
                    data=_comp_xl_buf.getvalue(),
                    file_name=f"comparativo_{_comp['baseline_semana']}_vs_{_comp['actual_semana']}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="dl_comparativo",
                )
    elif len(_all_snaps) == 1:
        st.info(f"Baseline guardado: {_all_snaps[0].get('semana', '?')} ({_all_snaps[0].get('timestamp', '?')[:10]}). "
                   "Guarda o carga un segundo snapshot para ver el comparativo.")
    else:
        st.info("Necesitas al menos 2 snapshots para comparar. Guarda tu baseline primero o carga snapshots previos.")

with _snap_tab3:
    st.caption("Carga snapshots JSON que descargaste anteriormente. Útil cuando usas Capi en la nube y los snapshots del servidor se pierden.")
    _snap_uploads = st.file_uploader(
        "Cargar snapshots (.json)", type=["json"],
        accept_multiple_files=True, key="snap_uploader",
    )
    if _snap_uploads:
        _loaded = []
        for _sf in _snap_uploads:
            try:
                _snap_data = _json_snap.load(_sf)
                _loaded.append(_snap_data)
            except Exception as _e:
                st.error(f"Error leyendo {_sf.name}: {_e}")
        if _loaded:
            st.session_state["_uploaded_snapshots"] = _loaded
            st.success(f"{len(_loaded)} snapshots cargados. Ve a la pestaña Comparativo para analizar.")
            for _sl in _loaded:
                st.caption(f"• {_sl.get('semana', '?')} — {_sl.get('timestamp', '?')[:10]}")




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
            f'border-bottom:1px solid {SLATE_200}; margin-bottom:16px;">'
            f'<div style="width:28px; height:28px; background:{TEAL_600}; border-radius:50%; '
            f'display:flex; align-items:center; justify-content:center; color:white; font-weight:700; '
            f'font-size:0.75rem; flex-shrink:0;">C</div>'
            f'<span style="font-weight:600; color:{SLATE_900}; font-size:0.88rem;">Capi</span>'
            f'<span style="background:{TEAL_50}; color:{TEAL_600}; font-size:0.58rem; font-weight:700; '
            f'padding:2px 7px; border-radius:3px; letter-spacing:0.06em; text-transform:uppercase;">AI</span>'
            f'<span style="color:{SLATE_400}; font-size:0.78rem; margin-left:auto; '
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
                    f'<div style="background:{TEAL_50}; color:{SLATE_900}; '
                    f'padding:10px 16px; border-radius:16px 16px 4px 16px; max-width:88%; '
                    f'font-size:0.88rem; line-height:1.5;">{_msg["question"]}</div></div>',
                    unsafe_allow_html=True
                )
            elif _msg["role"] == "ai":
                # Respuesta AI — texto directo estilo Nansen
                _conv = _msg["conversacion"].replace("\n\n", "<br><br>").replace("\n", "<br>")
                _conv = _re_chat.sub(r'\*\*(.+?)\*\*', r'<strong style="color:{SLATE_900};">\1</strong>', _conv)

                # Step verde con check (estilo Nansen "Checking Smart Money positions >")
                _step_html = (
                    f'<div style="display:inline-flex; align-items:center; gap:6px; margin:8px 0 14px 0;">'
                    f'<span style="color:{TEAL_600}; font-size:0.9rem;">●</span>'
                    f'<span style="color:{SLATE_500}; font-size:0.82rem;">'
                    f'Analizando inventario — {_msg.get("n_combos", 0):,} combos</span>'
                    f'<span style="color:{SLATE_400}; font-size:0.82rem;">›</span>'
                    f'</div>'
                )

                st.markdown(
                    f'<div style="margin:4px 0 20px 0;">'
                    f'{_step_html}'
                    f'<div style="color:{SLATE_700}; font-size:0.88rem; line-height:1.7;">{_conv}</div>'
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
                    f'<div style="border-bottom:1px solid {SLATE_200}; margin:4px 0 8px 0;"></div>',
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
                f'<span class="nansen-chip" style="background:{SLATE_100}; '
                f'color:{SLATE_700}; font-size:0.72rem; padding:4px 10px; '
                f'border-radius:6px; border:1px solid {SLATE_200}; '
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
                f'<span style="font-size:0.68rem; color:{SLATE_400};">AI-generated. Verify independently.</span>',
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

    st.markdown(f"""<div style="background:var(--capi-bg-surface); border:1px solid var(--capi-border); border-radius:14px; padding:18px 22px; margin-bottom:16px;">
    <span style="font-weight:700; color:var(--capi-text); font-size:1rem;">Resumen Ejecutivo — Criterios de alertas a tiendas</span>
    <p style="color:var(--capi-text); font-size:0.88rem; margin:10px 0 6px 0;">
    Se generan <strong>dos tipos de alerta</strong> semanales para el personal de piso:
    </p>
    <div style="display:flex; gap:16px; flex-wrap:wrap;">
    <div style="flex:1; min-width:280px; background:var(--capi-bg-card); border-radius:10px; padding:14px; border-left:4px solid {STATUS_MUERTO};">
    <strong style="color:var(--capi-text);">1. Productos con venta cero</strong><br>
    <span style="font-size:0.84rem; color:var(--capi-text);">
    SKUs con stock a costo &ge; S/ 1,000 que no vendieron la semana pasada.<br>
    <strong>Acción:</strong> Revisar exhibición del producto y comunicación de precio (si tiene descuento).<br>
    <strong>Alcance:</strong> {_vc_n_tiendas} tiendas · {_vc_total_skus:,} alertas · S/ {_vc_total_capital:,.0f} capital parado.<br>
    Top 15 SKUs por marca, ordenados por capital parado.
    </span>
    </div>
    <div style="flex:1; min-width:280px; background:var(--capi-bg-card); border-radius:10px; padding:14px; border-left:4px solid {STATUS_SOBRESTOCK};">
    <strong style="color:var(--capi-text);">2. Productos con sobrestock</strong><br>
    <span style="font-size:0.84rem; color:var(--capi-text);">
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
        <span style="color:var(--capi-text2); font-size:0.82em;"> &nbsp;·&nbsp; Capital parado total: S/ {_vc_total_capital:,.0f}</span>
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
                    _it_copy['marca'] = _marca
                    _vc_all_rows.append(_it_copy)
        if _vc_all_rows:
            _vc_all_df = pd.DataFrame(_vc_all_rows)
            _vc_export_cols = ["tienda", "sku", "nombre", "marca", "categoria", "stock_total",
                               "stock_valor_costo", "edad_semanas", "estado", "pct_descuento", "mensaje"]
            _vc_export_cols = [c for c in _vc_export_cols if c in _vc_all_df.columns]
            _vc_xl_buf = io.BytesIO()
            with pd.ExcelWriter(_vc_xl_buf, engine='openpyxl') as _vc_w:
                _vc_all_df[_vc_export_cols].to_excel(_vc_w, sheet_name='Venta Cero', index=False)
            st.download_button(
                "📥 Descargar detalle venta cero (.xlsx)",
                data=_vc_xl_buf.getvalue(),
                file_name="detalle_venta_cero.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
        <span style="color:var(--capi-text2); font-size:0.82em;"> &nbsp;·&nbsp; Capital inmovilizado total: S/ {_at_total_capital:,.0f}</span>
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
            _at_xl_buf = io.BytesIO()
            with pd.ExcelWriter(_at_xl_buf, engine='openpyxl') as _at_w:
                _at_all_df[_at_export_cols].to_excel(_at_w, sheet_name='Sobrestock', index=False)
            st.download_button(
                "📥 Descargar detalle sobrestock (.xlsx)",
                data=_at_xl_buf.getvalue(),
                file_name="detalle_sobrestock.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
            if prioridad <= 5: return TH_BG_SURFACE_PY, TH_TEXT2_PY
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
            st.markdown(f"""<div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {TEAL_600};">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Ventanas activas</div>
                <div style="font-size:1.6rem; font-weight:700; color:{TEAL_700};">{_ek.get('n_ventanas', 0)}</div>
            </div>""", unsafe_allow_html=True)
        with _ek_c2:
            st.markdown(f"""<div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_OPTIMO};">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Mejor ventana</div>
                <div style="font-size:1.6rem; font-weight:700; color:{STATUS_OPTIMO};">{_ek.get('mejor_ventana', '—')}</div>
                <div style="font-size:0.7rem; color:var(--capi-text2);">Retorno/sem: {_ek.get('mejor_retorno', 0):.2%}</div>
            </div>""", unsafe_allow_html=True)
        with _ek_c3:
            st.markdown(f"""<div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_CRITICO};">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Peor ventana</div>
                <div style="font-size:1.6rem; font-weight:700; color:{STATUS_CRITICO};">{_ek.get('peor_ventana', '—')}</div>
                <div style="font-size:0.7rem; color:var(--capi-text2);">Retorno/sem: {_ek.get('peor_retorno', 0):.2%}</div>
            </div>""", unsafe_allow_html=True)
        with _ek_c4:
            st.markdown(f"""<div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_PRECRITICO};">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Ventanas en rojo</div>
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

            st.markdown(f"""<div style="background:var(--capi-bg-card); border:1px solid var(--capi-border); border-left:4px solid {_border_color}; border-radius:10px; padding:12px 16px; margin-bottom:6px;">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap;">
                <div>
                    <strong style="color:var(--capi-text); font-size:1rem;">{_icon} Ventana {_v}</strong>
                    <span style="color:var(--capi-text2); font-size:0.82em;"> &nbsp;·&nbsp; {vrow['label']}</span>
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
            st.markdown(f"""<div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {_ret_color};">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">SKUs retenidos en CD</div>
                <div style="font-size:1.6rem; font-weight:700; color:{_ret_color};">{_n_ret:,}</div>
            </div>""", unsafe_allow_html=True)
        with _r_c2:
            st.markdown(f"""<div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {TEAL_600};">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Unidades retenidas</div>
                <div style="font-size:1.6rem; font-weight:700; color:{TEAL_700};">{_uds_ret:,}</div>
            </div>""", unsafe_allow_html=True)
        with _r_c3:
            st.markdown(f"""<div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {STATUS_ALTO};">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Capital retenido</div>
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
            st.markdown(f"""<div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {_gap_color};">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">SKUs con gaps</div>
                <div style="font-size:1.6rem; font-weight:700; color:{_gap_color};">{_n_gaps:,}</div>
            </div>""", unsafe_allow_html=True)
        with _g_c2:
            st.markdown(f"""<div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {TEAL_600};">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Gaps con stock CD</div>
                <div style="font-size:1.6rem; font-weight:700; color:{TEAL_700};">{_n_gaps_cd:,}</div>
                <div style="font-size:0.7rem; color:var(--capi-text2);">Accionables (hay stock para enviar)</div>
            </div>""", unsafe_allow_html=True)
        with _g_c3:
            _cob_color = STATUS_OPTIMO if _prom_cob >= 0.8 else (STATUS_ALTO if _prom_cob >= 0.5 else STATUS_CRITICO)
            st.markdown(f"""<div style="background:var(--capi-bg-surface); border-radius:12px; padding:16px 20px; border-left:4px solid {_cob_color};">
                <div style="font-size:0.75rem; color:var(--capi-text2); font-weight:500;">Cobertura distribución prom.</div>
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


# ══════════════════════════════════════════════════════════════
#  EVOLUCIÓN SEMANAL — Análisis histórico de snapshots
# ══════════════════════════════════════════════════════════════

elif nav_page == "📈 Evolución Semanal":
    st.markdown(f'<div class="section-header"><h3>📈 Evolución Semanal</h3><span class="live-badge">SNAPSHOTS</span></div>', unsafe_allow_html=True)

    if not _HAS_SNAPSHOTS:
        st.warning("El módulo de snapshots no está disponible.")
    else:
        _evol_weeks = snapshots_engine.list_available_weeks()
        if len(_evol_weeks) < 2:
            st.info(f"Se necesitan al menos 2 snapshots para análisis comparativo. Disponibles: {len(_evol_weeks)}")
        else:
            st.caption(f"Analizando {len(_evol_weeks)} semanas: {_evol_weeks[0]} → {_evol_weeks[-1]}")

            _evol_tabs = st.tabs([
                "📊 Resumen Semanal",
                "🔄 Cambios de Estado",
                "🏷️ Tendencia Marcas",
                "📦 Cumplimiento Repo",
                "🚀 SKUs Acelerando",
                "⚠️ Predicción Quiebre",
            ])

            # ── Tab 1: Resumen Semanal (compare_weeks) ──
            with _evol_tabs[0]:
                st.markdown("##### Comparativo semana a semana")
                _evol_sem_opts = _evol_weeks[::-1]
                _ec1, _ec2 = st.columns(2)
                with _ec1:
                    _evol_sem_b = st.selectbox("Semana actual", _evol_sem_opts, index=0, key="evol_sem_b")
                with _ec2:
                    _evol_sem_a_opts = [w for w in _evol_sem_opts if w < _evol_sem_b]
                    _evol_sem_a = st.selectbox("Comparar con", _evol_sem_a_opts, index=0, key="evol_sem_a") if _evol_sem_a_opts else None

                if _evol_sem_a:
                    _cmp = snapshots_engine.compare_weeks(_evol_sem_a, _evol_sem_b)
                    if _cmp:
                        _cd = _cmp['deltas']
                        _ca = _cmp['semana_a']
                        _cb = _cmp['semana_b']

                        _metrics = [
                            ("Venta S/", f"S/ {_ca['venta_soles']:,.0f}", f"S/ {_cb['venta_soles']:,.0f}", _cd['venta_soles_pct']),
                            ("Venta Uds", f"{_ca['venta_unidades']:,}", f"{_cb['venta_unidades']:,}", _cd['venta_unidades_pct']),
                            ("Stock Total", f"{_ca['stock_total']:,}", f"{_cb['stock_total']:,}", _cd['stock_total_pct']),
                            ("Stock Valorizado", f"S/ {_ca['stock_valorizado']:,.0f}", f"S/ {_cb['stock_valorizado']:,.0f}", _cd['stock_valorizado_pct']),
                            ("Contribución", f"S/ {_ca['contribucion']:,.0f}", f"S/ {_cb['contribucion']:,.0f}", _cd['contribucion_pct']),
                            ("Cob Promedio", f"{_ca['cob_promedio']:.1f} sem", f"{_cb['cob_promedio']:.1f} sem", _cd['cob_promedio_pct']),
                        ]

                        _tbl_html = f"""<table style="width:100%; border-collapse:collapse; font-size:0.85rem;">
                        <thead><tr style="border-bottom:2px solid var(--capi-border);">
                            <th style="text-align:left; padding:8px; color:var(--capi-text2);">KPI</th>
                            <th style="text-align:right; padding:8px; color:var(--capi-text2);">Sem {_evol_sem_a}</th>
                            <th style="text-align:right; padding:8px; color:var(--capi-text2);">Sem {_evol_sem_b}</th>
                            <th style="text-align:right; padding:8px; color:var(--capi-text2);">Δ %</th>
                        </tr></thead><tbody>"""

                        for _m_lbl, _m_va, _m_vb, _m_pct in _metrics:
                            _m_color = "#10b981" if _m_pct > 0 else "#ef4444" if _m_pct < 0 else "var(--capi-text2)"
                            _m_arr = "▲" if _m_pct > 0 else "▼" if _m_pct < 0 else "–"
                            _tbl_html += f"""<tr style="border-bottom:1px solid var(--capi-border);">
                                <td style="padding:8px; font-weight:500; color:var(--capi-text);">{_m_lbl}</td>
                                <td style="padding:8px; text-align:right; color:var(--capi-text2);">{_m_va}</td>
                                <td style="padding:8px; text-align:right; font-weight:600; color:var(--capi-text);">{_m_vb}</td>
                                <td style="padding:8px; text-align:right; font-weight:600; color:{_m_color};">{_m_arr} {abs(_m_pct):.1f}%</td>
                            </tr>"""

                        _tbl_html += "</tbody></table>"
                        st.markdown(_tbl_html, unsafe_allow_html=True)

                        # ── Sparklines: tendencia de cada KPI a lo largo de todas las semanas ──
                        if len(_evol_weeks) >= 3:
                            st.markdown("")
                            st.markdown("##### Tendencia histórica")
                            _spark_data = {}
                            for _sw in _evol_weeks:
                                _sr = snapshots_engine.get_resumen_semanal(_sw)
                                if _sr:
                                    _spark_data[_sw] = _sr

                            if len(_spark_data) >= 3:
                                _spark_kpis = [
                                    ("Venta S/", 'venta_total_soles', "S/"),
                                    ("Venta Uds", 'venta_total_unidades', ""),
                                    ("Stock Uds", 'stock_total_unidades', ""),
                                ]
                                _spark_cols = st.columns(len(_spark_kpis))
                                for _si, (_slbl, _skey, _sprefix) in enumerate(_spark_kpis):
                                    with _spark_cols[_si]:
                                        _svals = [_spark_data[w].get(_skey, 0) for w in _evol_weeks if w in _spark_data]
                                        _sweeks = [w for w in _evol_weeks if w in _spark_data]
                                        if _svals:
                                            _sfig = go.Figure()
                                            _sfig.add_trace(go.Scatter(
                                                x=_sweeks, y=_svals,
                                                mode='lines+markers',
                                                line=dict(color=TEAL_600, width=2),
                                                marker=dict(size=6),
                                                hovertemplate="%{x}: %{y:,.0f}<extra></extra>",
                                            ))
                                            _sfig.update_layout(
                                                height=120, margin=dict(l=0, r=0, t=24, b=0),
                                                title=dict(text=_slbl, font=dict(size=11)),
                                                xaxis=dict(showticklabels=False, showgrid=False),
                                                yaxis=dict(showticklabels=False, showgrid=False),
                                                plot_bgcolor='rgba(0,0,0,0)',
                                                paper_bgcolor='rgba(0,0,0,0)',
                                                showlegend=False,
                                            )
                                            st.plotly_chart(_sfig, use_container_width=True, key=f"spark_{_skey}")

                    else:
                        st.warning("No se pudo comparar las semanas seleccionadas.")

            # ── Tab 2: Cambios de Estado ──
            with _evol_tabs[1]:
                st.markdown("##### SKUs que cambiaron de estado")
                _sc_c1, _sc_c2 = st.columns(2)
                with _sc_c1:
                    _sc_sem_b = st.selectbox("Semana actual", _evol_sem_opts, index=0, key="sc_sem_b")
                with _sc_c2:
                    _sc_a_opts = [w for w in _evol_sem_opts if w < _sc_sem_b]
                    _sc_sem_a = st.selectbox("Comparar con", _sc_a_opts, index=0, key="sc_sem_a") if _sc_a_opts else None

                if _sc_sem_a:
                    with st.spinner("Clasificando estados..."):
                        _sc_df = snapshots_engine.detect_state_changes(_sc_sem_a, _sc_sem_b)

                    if _sc_df.empty:
                        st.success("No hubo cambios de estado entre las semanas seleccionadas.")
                    else:
                        _sc_k1, _sc_k2, _sc_k3 = st.columns(3)
                        _n_mejora = int((_sc_df['cambio'] == 'mejora').sum())
                        _n_empeora = int((_sc_df['cambio'] == 'empeora').sum())
                        with _sc_k1:
                            st.metric("Total cambios", len(_sc_df))
                        with _sc_k2:
                            st.metric("Mejoran", _n_mejora, delta=f"{_n_mejora}", delta_color="normal")
                        with _sc_k3:
                            st.metric("Empeoran", _n_empeora, delta=f"-{_n_empeora}", delta_color="inverse")

                        _sc_filter = st.radio("Filtrar", ["Todos", "Empeoran", "Mejoran"], horizontal=True, key="sc_filter")
                        _sc_show = _sc_df.copy()
                        if _sc_filter == "Empeoran":
                            _sc_show = _sc_show[_sc_show['cambio'] == 'empeora']
                        elif _sc_filter == "Mejoran":
                            _sc_show = _sc_show[_sc_show['cambio'] == 'mejora']

                        st.dataframe(
                            _sc_show[['sku', 'marca', 'estado_a', 'estado_b', 'cambio']].rename(columns={
                                'sku': 'SKU', 'marca': 'Marca', 'estado_a': f'Estado {_sc_sem_a}',
                                'estado_b': f'Estado {_sc_sem_b}', 'cambio': 'Dirección'
                            }),
                            use_container_width=True, hide_index=True,
                        )

            # ── Tab 3: Tendencia Marcas ──
            with _evol_tabs[2]:
                st.markdown("##### Evolución por marca a lo largo del tiempo")
                with st.spinner("Calculando tendencias por marca..."):
                    _em_df = snapshots_engine.evolucion_marca()

                if _em_df.empty:
                    st.info("Sin datos de evolución.")
                else:
                    _em_marcas = sorted(_em_df['marca'].unique())
                    _em_marca_sel = st.selectbox("Marca", ["Todas"] + _em_marcas, key="em_marca")

                    _em_show = _em_df if _em_marca_sel == "Todas" else _em_df[_em_df['marca'] == _em_marca_sel]

                    if _em_marca_sel == "Todas":
                        # Agregar por semana
                        _em_agg = _em_show.groupby('semana_iso', as_index=False).agg(
                            cob_promedio=('cob_promedio', 'mean'),
                            pct_quiebre=('pct_quiebre', 'mean'),
                            venta_unidades=('venta_unidades', 'sum'),
                            venta_soles=('venta_soles', 'sum'),
                            stock_total=('stock_total', 'sum'),
                        )
                    else:
                        _em_agg = _em_show.copy()

                    _em_fig = go.Figure()
                    _em_fig.add_trace(go.Bar(
                        x=_em_agg['semana_iso'], y=_em_agg['venta_unidades'],
                        name='Venta Uds', marker_color=TEAL_600, opacity=0.7,
                    ))
                    _em_fig.add_trace(go.Scatter(
                        x=_em_agg['semana_iso'], y=_em_agg['cob_promedio'],
                        name='Cob Prom (sem)', yaxis='y2',
                        line=dict(color=STATUS_QUIEBRE, width=2), mode='lines+markers',
                    ))
                    _em_fig.update_layout(
                        **_plotly_layout,
                        title=f"Venta y Cobertura — {_em_marca_sel}",
                        yaxis=dict(title="Venta Uds"),
                        yaxis2=dict(title="Cob Prom (sem)", overlaying='y', side='right'),
                        legend=dict(orientation='h', y=-0.15),
                        barmode='group',
                    )
                    st.plotly_chart(_em_fig, use_container_width=True)

                    # Tabla resumen
                    st.dataframe(
                        _em_agg.rename(columns={
                            'semana_iso': 'Semana', 'cob_promedio': 'Cob Prom',
                            'pct_quiebre': '% Quiebre', 'venta_unidades': 'Venta Uds',
                            'venta_soles': 'Venta S/', 'stock_total': 'Stock Total',
                        }),
                        use_container_width=True, hide_index=True,
                    )

            # ── Tab 4: Cumplimiento Repo ──
            with _evol_tabs[3]:
                st.markdown("##### ¿Se repuso lo que se pidió?")
                _rc_c1, _rc_c2 = st.columns(2)
                with _rc_c1:
                    _rc_sem_a = st.selectbox("Semana pedido", _evol_sem_opts[1:] if len(_evol_sem_opts) > 1 else _evol_sem_opts, index=0, key="rc_sem_a")
                with _rc_c2:
                    _rc_b_opts = [w for w in _evol_sem_opts if w > _rc_sem_a]
                    _rc_sem_b = st.selectbox("Semana verificación", _rc_b_opts, index=0, key="rc_sem_b") if _rc_b_opts else None

                if _rc_sem_b:
                    with st.spinner("Analizando movimientos de stock..."):
                        _rc_df = snapshots_engine.detect_repo_cumplimiento(_rc_sem_a, _rc_sem_b)

                    if _rc_df.empty:
                        st.info("No se detectaron movimientos de reposición entre las semanas seleccionadas.")
                    else:
                        _rc_k1, _rc_k2, _rc_k3 = st.columns(3)
                        with _rc_k1:
                            st.metric("SKUs con repo al CD", int(_rc_df['repo_cd'].sum()))
                        with _rc_k2:
                            st.metric("SKUs con despacho a tiendas", int(_rc_df['despacho_tiendas'].sum()))
                        with _rc_k3:
                            _uds_repo = int(_rc_df['unidades_repo_cd'].sum()) + int(_rc_df['unidades_despacho'].sum())
                            st.metric("Unidades movidas", f"{_uds_repo:,}")

                        _rc_marca_filter = st.selectbox("Filtrar marca", ["Todas"] + sorted(_rc_df['marca'].unique().tolist()), key="rc_marca")
                        _rc_show = _rc_df if _rc_marca_filter == "Todas" else _rc_df[_rc_df['marca'] == _rc_marca_filter]

                        st.dataframe(
                            _rc_show[['sku', 'marca', 'stock_cd_a', 'stock_cd_b', 'stock_tiendas_a',
                                      'stock_tiendas_b', 'venta_b', 'repo_cd', 'despacho_tiendas',
                                      'unidades_repo_cd', 'unidades_despacho']].rename(columns={
                                'sku': 'SKU', 'marca': 'Marca',
                                'stock_cd_a': f'Stock CD {_rc_sem_a}', 'stock_cd_b': f'Stock CD {_rc_sem_b}',
                                'stock_tiendas_a': f'Stock Tdas {_rc_sem_a}', 'stock_tiendas_b': f'Stock Tdas {_rc_sem_b}',
                                'venta_b': f'Venta {_rc_sem_b}',
                                'repo_cd': 'Repo CD', 'despacho_tiendas': 'Despacho Tdas',
                                'unidades_repo_cd': 'Uds Repo CD', 'unidades_despacho': 'Uds Despacho',
                            }),
                            use_container_width=True, hide_index=True,
                        )

            # ── Tab 5: SKUs Acelerando ──
            with _evol_tabs[4]:
                st.markdown("##### SKUs con velocidad de venta en aumento")
                _ac_umbral = st.slider("Umbral de aceleración (ratio)", 1.1, 3.0, 1.3, 0.1, key="ac_umbral",
                                        help="Un ratio de 1.3 significa +30% de venta reciente vs antigua")

                with st.spinner("Detectando aceleración..."):
                    _ac_df = snapshots_engine.detect_aceleracion(umbral_ratio=_ac_umbral)

                if _ac_df.empty:
                    st.info("No se pudo calcular aceleración (datos insuficientes).")
                else:
                    _ac_k1, _ac_k2, _ac_k3, _ac_k4 = st.columns(4)
                    _n_acel = int((_ac_df['tendencia'] == 'ACELERANDO').sum())
                    _n_desacel = int((_ac_df['tendencia'] == 'DESACELERANDO').sum())
                    _n_estable = int((_ac_df['tendencia'] == 'ESTABLE').sum())
                    _n_react = int((_ac_df['tendencia'] == 'REACTIVADO').sum())
                    with _ac_k1:
                        st.metric("🚀 Acelerando", _n_acel)
                    with _ac_k2:
                        st.metric("📉 Desacelerando", _n_desacel)
                    with _ac_k3:
                        st.metric("➡️ Estable", _n_estable)
                    with _ac_k4:
                        st.metric("🔄 Reactivado", _n_react)

                    _ac_tend_filter = st.radio("Filtrar", ["ACELERANDO", "DESACELERANDO", "ESTABLE", "REACTIVADO", "Todos"],
                                                horizontal=True, key="ac_tend_filter")
                    _ac_show = _ac_df if _ac_tend_filter == "Todos" else _ac_df[_ac_df['tendencia'] == _ac_tend_filter]

                    # Filtro por marca
                    _ac_marcas = ["Todas"] + sorted(_ac_show['marca'].unique().tolist())
                    _ac_marca_sel = st.selectbox("Marca", _ac_marcas, key="ac_marca")
                    if _ac_marca_sel != "Todas":
                        _ac_show = _ac_show[_ac_show['marca'] == _ac_marca_sel]

                    st.dataframe(
                        _ac_show[['sku', 'marca', 'vta_reciente', 'vta_antigua', 'ratio', 'tendencia']].rename(columns={
                            'sku': 'SKU', 'marca': 'Marca', 'vta_reciente': 'Vta Reciente (2 sem)',
                            'vta_antigua': 'Vta Antigua (2 sem)', 'ratio': 'Ratio',
                            'tendencia': 'Tendencia',
                        }).head(200),
                        use_container_width=True, hide_index=True,
                    )
                    st.caption(f"Mostrando top 200 de {len(_ac_show)} SKUs")

            # ── Tab 6: Predicción Quiebre ──
            with _evol_tabs[5]:
                st.markdown("##### ¿En cuántas semanas quebramos stock?")
                st.caption("Proyección basada en velocidad de venta ponderada (semanas recientes pesan más)")

                with st.spinner("Proyectando quiebres..."):
                    _pq_df = snapshots_engine.predict_stockout()

                if _pq_df.empty:
                    st.info("No se pudo calcular predicción de quiebre.")
                else:
                    _pq_k1, _pq_k2, _pq_k3, _pq_k4 = st.columns(4)
                    with _pq_k1:
                        _n_crit = int((_pq_df['riesgo'] == 'CRÍTICO').sum())
                        st.markdown(f"""<div style="background:#FEF2F2; border-radius:10px; padding:12px; text-align:center; border-left:4px solid #DC2626;">
                            <div style="font-size:0.7rem; color:#991B1B;">CRÍTICO (&lt;2 sem)</div>
                            <div style="font-size:1.4rem; font-weight:700; color:#DC2626;">{_n_crit}</div>
                        </div>""", unsafe_allow_html=True)
                    with _pq_k2:
                        _n_alto = int((_pq_df['riesgo'] == 'ALTO').sum())
                        st.markdown(f"""<div style="background:#FFF7ED; border-radius:10px; padding:12px; text-align:center; border-left:4px solid #F59E0B;">
                            <div style="font-size:0.7rem; color:#92400E;">ALTO (2-4 sem)</div>
                            <div style="font-size:1.4rem; font-weight:700; color:#F59E0B;">{_n_alto}</div>
                        </div>""", unsafe_allow_html=True)
                    with _pq_k3:
                        _n_medio = int((_pq_df['riesgo'] == 'MEDIO').sum())
                        st.markdown(f"""<div style="background:#FFFBEB; border-radius:10px; padding:12px; text-align:center; border-left:4px solid #FBBF24;">
                            <div style="font-size:0.7rem; color:#78350F;">MEDIO (4-8 sem)</div>
                            <div style="font-size:1.4rem; font-weight:700; color:#FBBF24;">{_n_medio}</div>
                        </div>""", unsafe_allow_html=True)
                    with _pq_k4:
                        _n_bajo = int((_pq_df['riesgo'] == 'BAJO').sum())
                        st.markdown(f"""<div style="background:#F0FDF4; border-radius:10px; padding:12px; text-align:center; border-left:4px solid #10B981;">
                            <div style="font-size:0.7rem; color:#065F46;">BAJO (&gt;8 sem)</div>
                            <div style="font-size:1.4rem; font-weight:700; color:#10B981;">{_n_bajo}</div>
                        </div>""", unsafe_allow_html=True)

                    _pq_riesgo_filter = st.radio("Filtrar riesgo", ["CRÍTICO", "ALTO", "MEDIO", "BAJO", "Todos"],
                                                  horizontal=True, key="pq_riesgo", index=0)
                    _pq_show = _pq_df if _pq_riesgo_filter == "Todos" else _pq_df[_pq_df['riesgo'] == _pq_riesgo_filter]

                    _pq_marcas = ["Todas"] + sorted(_pq_show['marca'].unique().tolist())
                    _pq_marca_sel = st.selectbox("Marca", _pq_marcas, key="pq_marca")
                    if _pq_marca_sel != "Todas":
                        _pq_show = _pq_show[_pq_show['marca'] == _pq_marca_sel]

                    # Columnas disponibles dependen de la versión del motor
                    _pq_cols = ['sku', 'marca', 'stock_total', 'velocidad_ajustada',
                                'semanas_hasta_quiebre']
                    _pq_rename = {
                        'sku': 'SKU', 'marca': 'Marca', 'stock_total': 'Stock',
                        'velocidad_ajustada': 'Vel. Ajustada (u/sem)',
                        'semanas_hasta_quiebre': 'Sem. Quiebre',
                    }
                    # Nuevas columnas P1-1: lead_time + margen_real
                    if 'lead_time_sem' in _pq_show.columns:
                        _pq_cols += ['lead_time_sem', 'margen_real_sem']
                        _pq_rename['lead_time_sem'] = 'Lead Time (sem)'
                        _pq_rename['margen_real_sem'] = 'Margen Real (sem)'
                    _pq_cols += ['riesgo', 'tendencia_vta']
                    _pq_rename['riesgo'] = 'Riesgo'
                    _pq_rename['tendencia_vta'] = 'Tend. Venta'
                    # P2-6: venta en riesgo
                    if 'venta_riesgo_soles' in _pq_show.columns:
                        _pq_cols.append('venta_riesgo_soles')
                        _pq_rename['venta_riesgo_soles'] = 'Venta Riesgo S/'

                    _pq_display = _pq_show[[c for c in _pq_cols if c in _pq_show.columns]].rename(
                        columns=_pq_rename
                    ).head(200)

                    st.dataframe(_pq_display, use_container_width=True, hide_index=True)

                    _pq_caption = f"Mostrando top 200 de {len(_pq_show)} SKUs."
                    if 'margen_real_sem' in _pq_show.columns:
                        _pq_caption += " Margen Real = Sem. Quiebre − Lead Time proveedor."
                    st.caption(_pq_caption)


# ══════════════════════════════════════════════════════════════
#  DIARIO DE GESTIÓN — Sección propia en sidebar
# ══════════════════════════════════════════════════════════════

elif nav_page == "📝 Diario de Gestión":
    import json as _json_diario
    from datetime import datetime as _dt_diario

    st.markdown(f'<div class="section-header"><h3>📝 Diario de Gestión</h3><span class="live-badge">LOG</span></div>', unsafe_allow_html=True)
    st.caption("Registra tus acciones semanales y haz seguimiento del impacto de tus decisiones. Las entradas se organizan por fecha.")

    # Inicializar session state para el diario
    if "_diario_entries" not in st.session_state:
        st.session_state["_diario_entries"] = []

    # ── Layout: 2 tabs — Nueva entrada | Gestión de datos ──
    _diario_tab1, _diario_tab2 = st.tabs(["✏️ Nueva entrada", "📂 Importar / Exportar"])

    # ════════════════ TAB 1: Nueva entrada ════════════════
    with _diario_tab1:
        st.markdown(f"""<div style="background:var(--capi-bg-surface); border:1px solid var(--capi-border);
            border-radius:12px; padding:16px; margin-bottom:16px;">
            <span style="font-weight:600; color:var(--capi-text); font-size:0.92rem;">Nueva entrada</span>
        </div>""", unsafe_allow_html=True)

        _d_col1, _d_col2 = st.columns(2)
        with _d_col1:
            _d_semana = st.text_input("Semana", value=f"semana_{_dt_diario.now().strftime('%Y%m%d')}",
                                       key="diario_semana", help="Ej: semana_0, semana_1, 2026-05-05")
        with _d_col2:
            _d_categoria = st.selectbox("Categoría", [
                "Reposición", "Markdown / Precio", "Negociación Terceras",
                "Transferencias", "Predistribución", "Liquidación", "Otro"
            ], key="diario_cat")

        _d_accion = st.text_area(
            "¿Qué acción tomaste?",
            placeholder="Ej: Reposé 15 SKUs de MARQUIS a tiendas con quiebre. Pedí markdown de 30% en 8 SKUs SILBON con >20 sem antigüedad.",
            key="diario_accion", height=100,
        )

        _d_skus = st.text_input(
            "SKUs o marcas afectadas (opcional)",
            placeholder="Ej: MARQUIS, NAVIGATA, SKU-12345",
            key="diario_skus",
        )

        _d_resultado = st.text_input(
            "Resultado esperado (opcional)",
            placeholder="Ej: Reducir capital parado en S/ 15,000. Mejorar ST en 2pp.",
            key="diario_resultado",
        )

        if st.button("💾 Guardar entrada", key="btn_diario_save", use_container_width=True):
            if _d_accion.strip():
                _entry = {
                    "timestamp": _dt_diario.now().isoformat(),
                    "semana": _d_semana,
                    "categoria": _d_categoria,
                    "accion": _d_accion.strip(),
                    "skus_marcas": _d_skus.strip() if _d_skus else "",
                    "resultado_esperado": _d_resultado.strip() if _d_resultado else "",
                }
                st.session_state["_diario_entries"].append(_entry)
                st.success(f"Entrada guardada — {_d_categoria}: {_d_accion[:60]}...")
            else:
                st.warning("Escribe la acción que tomaste antes de guardar.")

    # ════════════════ TAB 2: Importar / Exportar ════════════════
    with _diario_tab2:
        st.caption("Exporta tu diario como respaldo o importa entradas de sesiones anteriores.")

        _ie_col1, _ie_col2 = st.columns(2)
        with _ie_col1:
            st.markdown("**Exportar**")
            if st.session_state["_diario_entries"]:
                _diario_json = _json_diario.dumps(st.session_state["_diario_entries"], indent=2, ensure_ascii=False)
                st.download_button(
                    "📥 Descargar diario (.json)",
                    data=_diario_json,
                    file_name=f"diario_gestion_{_dt_diario.now().strftime('%Y%m%d')}.json",
                    mime="application/json",
                    key="dl_diario",
                    use_container_width=True,
                )
            else:
                st.info("No hay entradas para exportar.")

        with _ie_col2:
            st.markdown("**Importar**")
            _diario_upload = st.file_uploader(
                "Cargar diario (.json)", type=["json"],
                accept_multiple_files=False, key="diario_uploader",
            )
            if _diario_upload:
                try:
                    _loaded_entries = _json_diario.load(_diario_upload)
                    if isinstance(_loaded_entries, list):
                        _existing_ts = {e.get("timestamp") for e in st.session_state["_diario_entries"]}
                        _new_count = 0
                        for _le in _loaded_entries:
                            if _le.get("timestamp") not in _existing_ts:
                                st.session_state["_diario_entries"].append(_le)
                                _new_count += 1
                        st.success(f"{_new_count} entradas nuevas importadas.")
                    else:
                        st.error("Formato inválido (debe ser una lista).")
                except Exception as _e:
                    st.error(f"Error: {_e}")

    # ══════════════════════════════════════════════════════════════
    #  HISTORIAL — Vista principal por fecha (siempre visible)
    # ══════════════════════════════════════════════════════════════

    st.markdown("---")
    st.markdown(f"""<div style="background:var(--capi-bg-surface); border:1px solid var(--capi-border);
        border-radius:12px; padding:12px 16px; margin-bottom:12px;">
        <span style="font-weight:600; color:var(--capi-text); font-size:0.92rem;">Historial de acciones</span>
    </div>""", unsafe_allow_html=True)

    _all_entries = st.session_state.get("_diario_entries", [])

    if _all_entries:
        # Extraer fechas únicas para filtro
        _fechas_unicas = sorted(set(e.get("timestamp", "")[:10] for e in _all_entries if e.get("timestamp")), reverse=True)

        # Filtros
        _filt_col1, _filt_col2 = st.columns([2, 1])
        with _filt_col1:
            _filtro_fechas = st.multiselect(
                "Filtrar por fecha",
                options=_fechas_unicas,
                default=[],
                key="diario_filtro_fecha",
                help="Deja vacío para ver todas las entradas",
            )
        with _filt_col2:
            _filtro_cat = st.multiselect(
                "Filtrar por categoría",
                options=["Reposición", "Markdown / Precio", "Negociación Terceras",
                         "Transferencias", "Predistribución", "Liquidación", "Otro"],
                default=[],
                key="diario_filtro_cat",
            )

        # Aplicar filtros
        _filtered = _all_entries
        if _filtro_fechas:
            _filtered = [e for e in _filtered if e.get("timestamp", "")[:10] in _filtro_fechas]
        if _filtro_cat:
            _filtered = [e for e in _filtered if e.get("categoria", "") in _filtro_cat]

        st.caption(f"{len(_filtered)} de {len(_all_entries)} entradas")

        # Agrupar por fecha
        _fechas_dict = {}
        for _e in _filtered:
            _fecha = _e.get("timestamp", "")[:10]
            if _fecha not in _fechas_dict:
                _fechas_dict[_fecha] = []
            _fechas_dict[_fecha].append(_e)

        _cat_colors = {
            "Reposición": TEAL_600, "Markdown / Precio": STATUS_ALTO,
            "Negociación Terceras": STATUS_SOBRESTOCK, "Transferencias": "#6366F1",
            "Predistribución": "#8B5CF6", "Liquidación": STATUS_LIQUIDAR, "Otro": TH_TEXT2_PY,
        }

        for _fecha_key in sorted(_fechas_dict.keys(), reverse=True):
            _fecha_entries = _fechas_dict[_fecha_key]
            # Nombre del día
            try:
                _dt_obj = _dt_diario.strptime(_fecha_key, "%Y-%m-%d")
                _dias = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
                _dia_nombre = _dias[_dt_obj.weekday()]
                _fecha_display = f"{_dia_nombre} {_fecha_key}"
            except Exception:
                _fecha_display = _fecha_key

            st.markdown(f"""<div style="background:var(--capi-bg-card); border:1px solid var(--capi-border);
                border-radius:10px; padding:10px 14px; margin:8px 0 4px 0;">
                <span style="font-weight:700; color:var(--capi-text); font-size:0.95rem;">{_fecha_display}</span>
                <span style="color:var(--capi-text3); font-size:0.8rem; float:right;">{len(_fecha_entries)} acción{"es" if len(_fecha_entries) > 1 else ""}</span>
            </div>""", unsafe_allow_html=True)

            for _e in _fecha_entries:
                _cat_color = _cat_colors.get(_e.get("categoria", ""), TH_TEXT2_PY)
                _hora = _e.get("timestamp", "")[11:16] if len(_e.get("timestamp", "")) > 16 else ""
                _semana_tag = f" · {_e.get('semana', '')}" if _e.get('semana') else ""
                st.markdown(f"""<div style="background:var(--capi-bg-surface); border-left:3px solid {_cat_color};
                    border-radius:8px; padding:10px 14px; margin:4px 0 6px 16px; font-size:0.88rem;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                        <span style="color:{_cat_color}; font-weight:600; font-size:0.8rem;">{_e.get('categoria', '')}</span>
                        <span style="color:var(--capi-text3); font-size:0.72rem;">{_hora}{_semana_tag}</span>
                    </div>
                    <div style="color:var(--capi-text); line-height:1.4;">{_e.get('accion', '')}</div>
                    {"<div style='color:var(--capi-text2); font-size:0.82rem; margin-top:4px;'>SKUs: " + _e.get('skus_marcas', '') + "</div>" if _e.get('skus_marcas') else ""}
                    {"<div style='color:var(--capi-text2); font-size:0.82rem; margin-top:2px;'>Resultado esperado: " + _e.get('resultado_esperado', '') + "</div>" if _e.get('resultado_esperado') else ""}
                </div>""", unsafe_allow_html=True)

    else:
        st.info("No hay entradas aún. Usa la pestaña 'Nueva entrada' para registrar tu primera acción.")


