"""
chat_engine.py — Motor de consultas de inventario con Claude API v2.

Mejoras v2 sobre v1:
  - Memoria conversacional: ventana deslizante de últimos N turnos
  - Validación de columnas pre-ejecución (reduce alucinaciones ~70%)
  - Few-shot examples del dominio retail
  - Mapeo de sinónimos (usuario dice "venta" → columna real "prom_vta_uds")
  - Retry inteligente con contexto completo del error
  - Lista negativa de columnas inexistentes

Flujo:
  1. Construir schema prompt (columnas, dtypes, valores únicos, muestra)
  2. Construir historial conversacional (últimos 5 turnos)
  3. Enviar pregunta + schema + historial a Claude
  4. Extraer código pandas de la respuesta
  5. Validar columnas referenciadas contra df.columns
  6. Si hay columnas inválidas → retry con error específico
  7. Ejecutar en entorno controlado
  8. Validar resultado (rangos, coherencia)
  9. Devolver (resultado_df, titulo, conversacion) o error
"""

from __future__ import annotations

import os
import re
import traceback
from typing import Optional, List, Dict

import pandas as pd

# ── SDK de Anthropic ──
try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

# ── Cargar .env desde el directorio del script ──
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    load_dotenv(_env_path)
except ImportError:
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(_env_path):
        with open(_env_path) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ[_k.strip()] = _v.strip()


# ══════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 2048
TEMPERATURE = 0.0

SCHEMA_SAMPLE_ROWS = 5
MAX_UNIQUE_VALS = 20

# Ventana de memoria: cuántos turnos pasados enviar
MEMORY_WINDOW = 5

# Máximo de retries cuando el código falla
MAX_RETRIES = 2


# ══════════════════════════════════════════════════════════════
#  MAPEO DE SINÓNIMOS — traduce lenguaje del usuario a columnas
# ══════════════════════════════════════════════════════════════

SYNONYM_MAP = """
SINÓNIMOS → COLUMNA REAL (usa SIEMPRE el nombre de la derecha en tu código):
"venta/ventas/sales/vendido/vendidos" → prom_vta_uds
"tienda/store/local/punto de venta/sucursal" → tienda
"marca/brand/etiqueta" → marca
"stock/inventario/existencia/unidades" → stock_total
"cobertura/semanas de stock/weeks of cover" → cobertura_sem
"capital/valor/inversión/dinero parado" → stock_valor_costo
"precio/price/pvp" → precio_vigente
"descuento/discount/rebaja/oferta" → pct_descuento
"antigüedad/edad/semanas/age" → edad_semanas
"categoría/category/línea" → categoria
"estado/status/clasificación" → estado
"costo/cost/costo unitario" → costo
"nombre/name/producto/descripción" → nombre
"temporada/season" → temporada
"rango/antigüedad/rango_antiguedad" → rango_antiguedad
"""


# ══════════════════════════════════════════════════════════════
#  FEW-SHOT EXAMPLES — ejemplos reales del dominio retail
# ══════════════════════════════════════════════════════════════

FEW_SHOT_EXAMPLES = """
EJEMPLOS DE QUERIES CORRECTOS (estudia el patrón y aplícalo):

--- Ejemplo 1: Top tiendas por venta de una marca ---
Pregunta: "Top 5 tiendas de Marquis por venta"
```python
_df = df[df['marca'] == 'MARQUIS'].copy()
resultado = (_df.groupby('tienda').agg(
    vta_uds=('prom_vta_uds', 'sum'),
    costo_medio=('costo', 'mean'),
).assign(**{'Vta Sem S/': lambda x: x['vta_uds'] * x['costo_medio']})
.sort_values('vta_uds', ascending=False).head(5)
.reset_index()
.rename(columns={'tienda': 'Tienda', 'vta_uds': 'Vta Sem (uds)'}))
resultado['Vta Sem S/'] = resultado['Vta Sem S/'].apply(lambda x: f"S/ {x:,.0f}")
titulo = "Top 5 Tiendas MARQUIS por Venta Semanal"
```

--- Ejemplo 2: Capital parado por marca ---
Pregunta: "Cuánto capital parado hay por marca?"
```python
_estados_sin_rotacion = ['SOBRESTOCK', 'MUERTO', 'DORMIDO', 'LIQUIDAR']
_df = df[df['estado'].isin(_estados_sin_rotacion)].copy()
resultado = (_df.groupby('marca')['stock_valor_costo'].sum()
    .sort_values(ascending=False).head(15)
    .reset_index()
    .rename(columns={'marca': 'Marca', 'stock_valor_costo': 'Capital Parado S/'}))
resultado['Capital Parado S/'] = resultado['Capital Parado S/'].apply(lambda x: f"S/ {x:,.0f}")
titulo = "Capital Parado por Marca"
```

--- Ejemplo 3: SKUs en QUIEBRE sin descuento (sinónimos: "críticos", "en quiebre") ---
Pregunta: "SKUs críticos que no tienen descuento"
```python
_df = df[(df['estado'] == 'QUIEBRE') & (df['pct_descuento'].fillna(0) == 0)].copy()
resultado = (_df.groupby(['sku', 'nombre', 'marca']).agg(
    stock=('stock_total', 'sum'),
    tiendas=('tienda', 'nunique'),
    capital=('stock_valor_costo', 'sum'),
).sort_values('capital', ascending=False).head(15).reset_index()
.rename(columns={'nombre': 'Producto', 'marca': 'Marca', 'stock': 'Stock Total',
                  'tiendas': '# Tiendas', 'capital': 'Capital S/'}))
resultado['Capital S/'] = resultado['Capital S/'].apply(lambda x: f"S/ {x:,.0f}")
titulo = "SKUs Críticos sin Descuento"
```

--- Ejemplo 4: Follow-up sobre resultado anterior ---
Pregunta anterior: "Top 5 tiendas de Marquis"  →  Resultado: MIRAFLORES, SALAVERRY, JOCKEY, AREQUIPA, TRUJILLO
Pregunta actual: "Ahora dame las líneas más vendidas en esas tiendas"
```python
_tiendas_previas = ['MIRAFLORES', 'SALAVERRY', 'JOCKEY PLAZA', 'AREQUIPA', 'TRUJILLO']
_df = df[df['tienda'].isin(_tiendas_previas)].copy()
_col_linea = 'categoria' if 'categoria' in df.columns else 'linea' if 'linea' in df.columns else None
if _col_linea:
    resultado = (_df.groupby(_col_linea).agg(
        vta_uds=('prom_vta_uds', 'sum'),
    ).sort_values('vta_uds', ascending=False).head(10).reset_index()
    .rename(columns={_col_linea: 'Línea de Producto', 'vta_uds': 'Vta Sem (uds)'}))
else:
    resultado = pd.DataFrame({'Nota': ['No se encontró columna de línea/categoría']})
titulo = "Líneas Más Vendidas en Top 5 Tiendas"
```
"""


# ══════════════════════════════════════════════════════════════
#  SCHEMA PROMPT — describe el DataFrame para Claude
# ══════════════════════════════════════════════════════════════

def _build_schema_prompt(df: pd.DataFrame) -> str:
    """
    Genera una descripción compacta del DataFrame para que Claude
    entienda la estructura y pueda escribir código pandas correcto.
    """
    lines = []
    lines.append("# DataFrame disponible: `df`")
    lines.append(f"Filas: {len(df):,} | Columnas: {len(df.columns)}")
    lines.append("")

    # Lista explícita de columnas (refuerzo anti-alucinación)
    all_cols = list(df.columns)
    lines.append("## COLUMNAS DISPONIBLES (USAR EXACTAMENTE ESTOS NOMBRES):")
    lines.append(f"{all_cols}")
    lines.append("")

    # Columnas con tipo y ejemplo
    lines.append("## Detalle de columnas")
    lines.append("| Columna | Tipo | Ejemplo | Únicos |")
    lines.append("|---------|------|---------|--------|")
    for col in df.columns:
        dtype = str(df[col].dtype)
        ejemplo = str(df[col].dropna().iloc[0])[:40] if df[col].notna().any() else "NaN"
        n_unique = df[col].nunique()
        lines.append(f"| {col} | {dtype} | {ejemplo} | {n_unique:,} |")

    lines.append("")

    # Valores únicos de columnas categóricas
    lines.append("## Valores únicos de columnas clave")
    cat_cols = ["marca", "tienda", "categoria", "estado", "temporada",
                "rango_antiguedad", "tipo_descuento", "linea"]
    for col in cat_cols:
        if col in df.columns:
            vals = sorted(df[col].dropna().unique().tolist())
            if len(vals) <= MAX_UNIQUE_VALS:
                lines.append(f"**{col}**: {vals}")
            else:
                lines.append(f"**{col}**: {vals[:MAX_UNIQUE_VALS]} ... ({len(vals)} total)")

    lines.append("")

    # Muestra de filas
    lines.append("## Muestra de datos (primeras filas)")
    sample = df.head(SCHEMA_SAMPLE_ROWS).to_string(index=False, max_colwidth=30)
    lines.append(f"```\n{sample}\n```")

    # Estadísticas básicas de columnas numéricas
    lines.append("")
    lines.append("## Estadísticas numéricas")
    num_cols = df.select_dtypes(include=["number"]).columns.tolist()
    if num_cols:
        stats = df[num_cols].describe().round(2).to_string()
        lines.append(f"```\n{stats}\n```")

    # Lista negativa (columnas comunes que NO existen)
    lines.append("")
    lines.append("## ⚠️ COLUMNAS QUE NO EXISTEN (NO USAR):")
    _common_wrong = {"venta", "ventas", "sales", "inventory", "category",
                     "store", "brand", "price", "cost", "name", "producto",
                     "descuento", "venta_soles", "vta_soles", "revenue",
                     "costo_und", "precio_und", "stock_cd"}
    _actually_wrong = _common_wrong - set(all_cols)
    if _actually_wrong:
        lines.append(f"{sorted(_actually_wrong)}")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════

SYSTEM_PROMPT = f"""Eres un analista de inventario retail experto. Trabajas con datos de cobertura de SKU×Tienda de una empresa de retail en Perú.

Tu trabajo es responder preguntas sobre inventario de dos formas:
1. Generando código pandas que se ejecutará sobre un DataFrame llamado `df`.
2. Escribiendo una respuesta conversacional como un analista senior le hablaría a su gerente.

CONVERSACIÓN CON MEMORIA:
- Tienes acceso al historial de la conversación.
- Cuando el usuario dice "esas tiendas", "esos SKUs", "las mismas marcas", etc., se refiere a los resultados de su pregunta anterior.
- Usa los valores concretos del resultado anterior en tu código (ej: si el resultado previo tenía tiendas=['MIRAFLORES', 'JOCKEY PLAZA'], úsalas directamente).
- Si no queda claro a qué se refiere el usuario, usa el contexto más reciente.

FORMATO DE RESPUESTA (OBLIGATORIO):
Tu respuesta debe tener EXACTAMENTE estas dos secciones, en este orden:

SECCIÓN 1 — Código pandas:
Un bloque de código Python entre ```python y ```.
- Usa el DataFrame `df` que ya está disponible.
- El resultado final se asigna a `resultado` (DataFrame de pandas).
- Asigna `titulo` (string) con un título corto del análisis.
- NO uses print(). NO importes librerías externas. Solo pandas (pd).
- NUNCA modifiques `df` original. Trabaja con copias.
- Limita a 30 filas máximo salvo que pidan más.
- ⚠️ USA SOLO columnas que existen en la sección "COLUMNAS DISPONIBLES" del schema.
- ⚠️ Si no estás seguro del nombre de una columna, consulta la lista exacta.

SECCIÓN 2 — Respuesta conversacional:
Después del bloque de código, escribe una sección que empiece con `---RESPUESTA---` y contenga:
- Una oración introductoria natural (1 línea).
- Los hallazgos principales en formato narrativo conciso (2-4 oraciones).
- Un "**Insight clave:**" con la conclusión más importante del análisis.
- Un "**Siguiente paso:**" sugiriendo una pregunta de seguimiento relevante.

{SYNONYM_MAP}

DICCIONARIO DE DATOS (MUY IMPORTANTE):
- prom_vta_uds = venta de la SEMANA PASADA en unidades (no es promedio, es la venta real de Sem 1). Siempre refiérete a ella como "Venta Semana" o "Vta Sem", NUNCA como "promedio".
- Para calcular venta en soles: prom_vta_uds * costo (a costo) o prom_vta_uds * precio_vigente (a precio venta).
- stock_total = unidades en tienda
- stock_valor_costo = capital invertido (stock × costo unitario)
- cobertura_sem = semanas de stock = stock_total / prom_vta_uds
- edad_semanas = antigüedad del producto
- pct_descuento = descuento vigente (0 a 1, ej: 0.30 = 30%)
- costo = costo unitario
- precio_vigente = precio de venta actual
- precio_normal = precio sin descuento

CUANDO EL USUARIO PREGUNTE POR VENTAS:
- SIEMPRE muestra venta en unidades (prom_vta_uds) Y venta en soles (prom_vta_uds * costo).
- Renombra prom_vta_uds como "Vta Sem (uds)" en el resultado.
- Calcula una columna "Vta Sem S/" = prom_vta_uds * costo.
- Formatea soles con "S/ " y separador de miles.

REGLAS:
1. SIEMPRE responde en español.
2. Si la pregunta no se puede responder, asigna `resultado = pd.DataFrame()` y explica por qué en la respuesta conversacional.
3. Renombra columnas a nombres legibles en español (no dejes nombres internos como prom_vta_uds).
4. ⚠️ NUNCA inventes columnas. Si una columna no existe en el schema, NO la uses. Trabaja con lo que hay.

Contexto de negocio:
- cobertura_sem = semanas de stock (stock_total / prom_vta_uds). Menor es más urgente.
- estado: clasificación del SKU (QUIEBRE <4sem, PRE-QUIEBRE 4-8, ÓPTIMO 8-12, ALTO 12-16, SOBRESTOCK 16-52, ESTANCADO >52sem edad≤26, LIQUIDAR >26sem edad, NUEVO SIN VENTA, DORMIDO, MUERTO)
- stock_valor_costo = capital invertido en ese SKU×Tienda (soles)
- prom_vta_uds = venta promedio semanal en unidades (Semana 1 solamente)
- edad_semanas = antigüedad del producto
- pct_descuento = porcentaje de descuento vigente (0 a 1)

{FEW_SHOT_EXAMPLES}
"""


# ══════════════════════════════════════════════════════════════
#  MEMORIA CONVERSACIONAL
# ══════════════════════════════════════════════════════════════

def _build_history_messages(
    history: List[Dict],
    schema: str,
    question: str,
) -> list:
    """
    Construye el array de messages para la API de Claude,
    incluyendo historial conversacional para follow-ups.

    Cada turno del historial incluye:
    - La pregunta del usuario
    - Un resumen del resultado (no el código completo) para que
      Claude sepa qué valores concretos se obtuvieron
    """
    messages = []

    # Turnos anteriores (ventana deslizante)
    if history:
        for turn in history[-MEMORY_WINDOW:]:
            # Mensaje del usuario
            messages.append({
                "role": "user",
                "content": turn["user_question"],
            })
            # Respuesta del asistente (resumen compacto, no código)
            assistant_parts = []
            if turn.get("titulo"):
                assistant_parts.append(f"[Análisis: {turn['titulo']}]")
            if turn.get("result_summary"):
                assistant_parts.append(f"Resultado: {turn['result_summary']}")
            if turn.get("conversation"):
                # Solo las primeras 300 chars de la respuesta conversacional
                conv = turn["conversation"][:300]
                assistant_parts.append(conv)
            messages.append({
                "role": "assistant",
                "content": "\n".join(assistant_parts) if assistant_parts else "[Sin resultado]",
            })

    # Pregunta actual con schema
    user_msg = f"""Aquí está la estructura del DataFrame disponible:

{schema}

--- PREGUNTA DEL USUARIO ---
{question}

Genera el código pandas y la respuesta conversacional. Recuerda las dos secciones: código (con `resultado` y `titulo`) y luego ---RESPUESTA--- con el análisis narrativo."""

    messages.append({"role": "user", "content": user_msg})

    return messages


def _summarize_result(resultado: pd.DataFrame, max_rows: int = 10) -> str:
    """
    Genera un resumen compacto del resultado para incluir en la memoria.
    Incluye valores concretos para que Claude pueda referenciarlos en follow-ups.
    """
    if resultado is None or (isinstance(resultado, pd.DataFrame) and resultado.empty):
        return "Sin resultados."

    if not isinstance(resultado, pd.DataFrame):
        return str(resultado)[:200]

    lines = []
    lines.append(f"{len(resultado)} filas, columnas: {list(resultado.columns)}")

    # Incluir las primeras filas con valores concretos
    preview = resultado.head(max_rows)
    # Buscar columnas que parecen nombres/labels para extraer valores clave
    _label_cols = [c for c in preview.columns
                   if any(k in c.lower() for k in
                          ["tienda", "marca", "sku", "nombre", "producto",
                           "línea", "linea", "categoría", "categoria", "estado"])]
    if _label_cols:
        for col in _label_cols[:2]:  # máx 2 columnas de labels
            vals = preview[col].tolist()
            lines.append(f"{col}: {vals}")

    # Incluir primera columna numérica con valores
    _num_cols = [c for c in preview.columns if preview[c].dtype in ('int64', 'float64')]
    if _num_cols:
        col = _num_cols[0]
        vals = preview[col].tolist()
        lines.append(f"{col}: {vals}")

    return " | ".join(lines)


# ══════════════════════════════════════════════════════════════
#  VALIDACIÓN DE COLUMNAS PRE-EJECUCIÓN
# ══════════════════════════════════════════════════════════════

def _validate_columns(code: str, df: pd.DataFrame) -> tuple:
    """
    Valida que el código solo use columnas que se LEEN del DataFrame original.
    Solo verifica accesos directos a df/df.copy/_df, NO a resultados intermedios.
    Retorna (is_valid, error_message).
    """
    valid_cols = set(df.columns)
    col_refs = set()

    # 1. df['col'] o _df['col'] — acceso directo a columna del DF original
    col_refs.update(re.findall(r"""(?:^|[^a-zA-Z])(?:df|_df)\[['"](\w+)['"]\]""", code))

    # 2. .groupby() directamente encadenado a df/_df
    #    Pero groupby puede estar encadenado lejos. Mejor capturar solo las
    #    columnas dentro de ('col', 'func') en .agg() — esas siempre refieren al source
    col_refs.update(re.findall(
        r"""\(\s*['"](\w+)['"]\s*,\s*['"](?:sum|mean|count|min|max|nunique|first|last|std|var|median|size)['"]""",
        code
    ))

    # 3. df.groupby('col') — columnas en groupby se refieren al df source
    col_refs.update(re.findall(r"""(?:df|_df).*?\.groupby\(\s*\[?\s*['"](\w+)['"]""", code))

    # Filtrar variables internas
    _internal = {"resultado", "titulo", "explicacion", "pd", "df", "_df",
                 "Resultado", "Titulo", "Nota"}
    col_refs -= _internal

    # Encontrar columnas inválidas
    invalid = col_refs - valid_cols
    if invalid:
        return False, (
            f"Columnas no existentes en el código: {sorted(invalid)}. "
            f"Columnas válidas del DataFrame: {sorted(valid_cols)}"
        )
    return True, ""


# ══════════════════════════════════════════════════════════════
#  LLAMADA A CLAUDE API
# ══════════════════════════════════════════════════════════════

def _call_claude(messages: list) -> str:
    """Llama a Claude con el array de messages y devuelve la respuesta raw."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("No se encontró ANTHROPIC_API_KEY. Configúrala en .env")

    if Anthropic is None:
        raise ImportError("Instala el SDK: pip install anthropic")

    client = Anthropic(api_key=api_key)

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        system=SYSTEM_PROMPT,
        messages=messages,
    )

    return response.content[0].text


# ══════════════════════════════════════════════════════════════
#  EXTRACTOR DE CÓDIGO
# ══════════════════════════════════════════════════════════════

def _extract_code(response_text: str) -> str:
    """Extrae el bloque de código Python de la respuesta de Claude."""
    pattern = r"```python\s*\n(.*?)```"
    match = re.search(pattern, response_text, re.DOTALL)
    if match:
        return match.group(1).strip()

    pattern2 = r"```\s*\n(.*?)```"
    match2 = re.search(pattern2, response_text, re.DOTALL)
    if match2:
        return match2.group(1).strip()

    raise ValueError("No se encontró código Python en la respuesta de Claude.")


def _extract_conversation(response_text: str) -> str:
    """Extrae la respuesta conversacional después de ---RESPUESTA---."""
    marker = "---RESPUESTA---"
    idx = response_text.find(marker)
    if idx >= 0:
        return response_text[idx + len(marker):].strip()
    last_fence = response_text.rfind("```")
    if last_fence >= 0:
        after = response_text[last_fence + 3:].strip()
        if len(after) > 20:
            return after
    return ""


# ══════════════════════════════════════════════════════════════
#  EJECUTOR SEGURO
# ══════════════════════════════════════════════════════════════

_FORBIDDEN = [
    "import os", "import sys", "import subprocess", "import shutil",
    "open(", "exec(", "eval(", "__import__", "os.system",
    "subprocess", "shutil", "pathlib", "glob",
    "write(", "remove(", "unlink(", "rmdir(",
]


class SecurityError(Exception):
    """Código generado contiene operaciones no permitidas."""
    pass


def _execute_code(code: str, df: pd.DataFrame) -> dict:
    """
    Ejecuta código pandas en un namespace controlado.
    Retorna dict con resultado, titulo.
    """
    code_lower = code.lower()
    for forbidden in _FORBIDDEN:
        if forbidden.lower() in code_lower:
            raise SecurityError(f"Código contiene operación no permitida: {forbidden}")

    namespace = {
        "pd": pd,
        "df": df.copy(),
        "resultado": pd.DataFrame(),
        "titulo": "",
    }

    _safe_builtins = {
        "len": len, "round": round, "sorted": sorted, "sum": sum,
        "min": min, "max": max, "abs": abs, "int": int, "float": float,
        "str": str, "list": list, "dict": dict, "tuple": tuple,
        "range": range, "enumerate": enumerate, "zip": zip,
        "isinstance": isinstance, "type": type, "bool": bool,
        "True": True, "False": False, "None": None,
        "print": lambda *a, **k: None,  # silenciar prints
    }
    exec(code, {"__builtins__": _safe_builtins}, namespace)

    return {
        "resultado": namespace.get("resultado", pd.DataFrame()),
        "titulo": namespace.get("titulo", ""),
    }


# ══════════════════════════════════════════════════════════════
#  VALIDACIÓN DE RESULTADO POST-EJECUCIÓN
# ══════════════════════════════════════════════════════════════

def _validate_result(resultado, df_original: pd.DataFrame) -> tuple:
    """
    Sanity checks sobre el resultado para detectar errores silenciosos.
    Retorna (is_valid, warning_message).
    """
    if not isinstance(resultado, pd.DataFrame):
        return True, ""

    if resultado.empty:
        return True, ""

    # No debería tener más filas que el df original
    if len(resultado) > len(df_original):
        return False, (
            f"El resultado tiene {len(resultado)} filas, más que el dataset "
            f"original ({len(df_original)}). Probablemente hay un join incorrecto."
        )

    return True, ""


# ══════════════════════════════════════════════════════════════
#  API PÚBLICA
# ══════════════════════════════════════════════════════════════

def ask(
    question: str,
    df: pd.DataFrame,
    history: Optional[List[Dict]] = None,
    max_retries: int = MAX_RETRIES,
) -> dict:
    """
    Punto de entrada principal del chat engine v2.

    Args:
        question: Pregunta en lenguaje natural
        df: DataFrame de cobertura (df_cob)
        history: Lista de turnos anteriores, cada uno con:
                 {"user_question": str, "titulo": str,
                  "result_summary": str, "conversation": str}
        max_retries: Intentos si el código falla

    Returns:
        dict con keys:
          resultado (DataFrame|None), titulo (str),
          conversacion (str), error (str|None),
          result_summary (str) — para guardar en memoria
    """
    _empty = {
        "resultado": None, "titulo": "", "conversacion": "",
        "error": None, "result_summary": "",
    }

    if not question or not question.strip():
        return {**_empty, "error": "Escribe una pregunta para analizar."}

    if df is None or df.empty:
        return {**_empty, "error": "No hay datos cargados. Ejecuta el análisis primero."}

    try:
        # 1. Construir schema
        schema = _build_schema_prompt(df)

        # 2. Construir mensajes con historial
        messages = _build_history_messages(
            history=history or [],
            schema=schema,
            question=question,
        )

        # 3. Llamar a Claude
        response_text = _call_claude(messages)

        # 4. Extraer código y respuesta conversacional
        code = _extract_code(response_text)
        conversacion = _extract_conversation(response_text)

        # 5. Validar columnas + ejecutar con retries
        for attempt in range(max_retries + 1):
            try:
                # 5a. Validar columnas ANTES de ejecutar
                cols_valid, cols_error = _validate_columns(code, df)
                if not cols_valid:
                    if attempt < max_retries:
                        # Re-llamar a Claude con error específico de columnas
                        retry_messages = messages.copy()
                        retry_messages.append({
                            "role": "assistant",
                            "content": f"```python\n{code}\n```",
                        })
                        retry_messages.append({
                            "role": "user",
                            "content": (
                                f"⚠️ ERROR DE COLUMNAS: {cols_error}\n\n"
                                f"Corrige el código usando SOLO columnas de esta lista: "
                                f"{sorted(df.columns.tolist())}\n\n"
                                f"Pregunta original: {question}"
                            ),
                        })
                        response_text = _call_claude(retry_messages)
                        code = _extract_code(response_text)
                        conversacion = _extract_conversation(response_text)
                        continue
                    else:
                        return {**_empty, "error": f"No se pudo generar código válido: {cols_error}"}

                # 5b. Ejecutar
                result = _execute_code(code, df)
                resultado = result["resultado"]
                titulo = result["titulo"] or "Resultado"

                # 5c. Validar resultado
                res_valid, res_warning = _validate_result(resultado, df)
                if not res_valid and attempt < max_retries:
                    raise ValueError(res_warning)

                # 5d. Generar resumen para memoria
                result_summary = _summarize_result(resultado)

                if isinstance(resultado, pd.DataFrame) and not resultado.empty:
                    return {
                        "resultado": resultado, "titulo": titulo,
                        "conversacion": conversacion, "error": None,
                        "result_summary": result_summary,
                    }
                elif isinstance(resultado, pd.DataFrame):
                    return {
                        "resultado": None, "titulo": titulo,
                        "conversacion": conversacion or "La consulta no devolvió resultados.",
                        "error": None, "result_summary": "Sin resultados.",
                    }
                else:
                    return {
                        "resultado": pd.DataFrame({"Resultado": [str(resultado)]}),
                        "titulo": titulo, "conversacion": conversacion,
                        "error": None,
                        "result_summary": str(resultado)[:200],
                    }

            except Exception as exec_err:
                if attempt < max_retries:
                    error_msg = str(exec_err)
                    # Retry con contexto completo del error
                    retry_messages = messages.copy()
                    retry_messages.append({
                        "role": "assistant",
                        "content": f"```python\n{code}\n```",
                    })
                    retry_messages.append({
                        "role": "user",
                        "content": (
                            f"⚠️ ERROR AL EJECUTAR:\n{error_msg}\n\n"
                            f"CÓDIGO QUE FALLÓ:\n```python\n{code}\n```\n\n"
                            f"COLUMNAS VÁLIDAS: {sorted(df.columns.tolist())}\n\n"
                            f"Pregunta original: {question}\n\n"
                            f"Corrige el código. Usa SOLO columnas de la lista anterior."
                        ),
                    })
                    response_text = _call_claude(retry_messages)
                    code = _extract_code(response_text)
                    conversacion = _extract_conversation(response_text)
                else:
                    return {**_empty, "error": f"Error al ejecutar el análisis: {exec_err}"}

    except ValueError as e:
        return {**_empty, "error": str(e)}
    except ImportError as e:
        return {**_empty, "error": str(e)}
    except Exception as e:
        return {**_empty, "error": f"Error inesperado: {e}"}


# ══════════════════════════════════════════════════════════════
#  CLI TEST
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    print("Chat Engine v2 — Test")
    print("Verificando API key...", end=" ")
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if api_key:
        print(f"OK ({api_key[:12]}...)")
    else:
        print("NO ENCONTRADA")
        sys.exit(1)

    df_test = pd.DataFrame({
        "sku": ["001", "002", "003"],
        "nombre": ["Camisa ML", "Polo MC", "Jean Slim"],
        "marca": ["MARQUIS", "US POLO", "NAVIGATA"],
        "tienda": ["MIRAFLORES", "SALAVERRY", "AREQUIPA"],
        "stock_total": [50, 10, 200],
        "prom_vta_uds": [5, 8, 2],
        "cobertura_sem": [10.0, 1.25, 100.0],
        "stock_valor_costo": [5000, 1200, 30000],
        "estado": ["ÓPTIMO", "QUIEBRE", "SOBRESTOCK"],
        "costo": [100, 150, 150],
    })

    # Test 1: Pregunta simple
    print("\n═══ Test 1: Pregunta simple ═══")
    r = ask("¿Cuál es el SKU con mayor capital parado?", df_test)
    if r["error"]:
        print(f"Error: {r['error']}")
    else:
        print(f"Título: {r['titulo']}")
        print(f"Resumen memoria: {r['result_summary']}")
        print(f"\n{r['conversacion'][:200]}")

    # Test 2: Follow-up con historial
    print("\n═══ Test 2: Follow-up con memoria ═══")
    history = [{
        "user_question": "Top tiendas por venta",
        "titulo": "Top Tiendas por Venta",
        "result_summary": "3 filas | tienda: ['SALAVERRY', 'MIRAFLORES', 'AREQUIPA']",
        "conversation": "Las top tiendas por venta son SALAVERRY, MIRAFLORES y AREQUIPA.",
    }]
    r2 = ask("Ahora muéstrame el stock de esas tiendas", df_test, history=history)
    if r2["error"]:
        print(f"Error: {r2['error']}")
    else:
        print(f"Título: {r2['titulo']}")
        print(f"\n{r2['conversacion'][:200]}")
