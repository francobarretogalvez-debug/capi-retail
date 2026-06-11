# Primera Sesión en Claude Code — Guía para Franco

## Cómo arrancar

1. Abre Claude Code en tu terminal
2. Navega al repo: `cd capi-retail`
3. Pega este prompt de arranque:

---

**PROMPT DE ARRANQUE (copiar y pegar):**

```
Lee CLAUDE.md en la raíz del repo. Luego lee docs/context/INDEX.md y docs/context/ALL_MEMORIES.md para tener el contexto completo del proyecto. Cuando termines, dame un resumen de lo que entendiste y pregúntame en qué quiero trabajar hoy.
```

---

## Verificación

Claude Code debería responder con:
- ✅ Confirmación de que leyó CLAUDE.md
- ✅ Resumen de la arquitectura (17 vistas, 10 estados, etc.)
- ✅ Mención del roadmap pendiente (Prompts D, E, G)
- ✅ Pregunta sobre qué quieres trabajar

Si no menciona algo de esto, pídele que relea los archivos.

## Tareas sugeridas para primera sesión

1. **Prompt D — Vista Cobertura mejorada** (~3h)
   - Mejora visual y funcional de la vista de cobertura por marca/tienda
   
2. **Prompt E — Acciones de Precio mejoradas** (~2h)
   - Lógica de markdown más inteligente basada en margen y antigüedad

3. **Prompt G — Chat IA mejorado** (~2h)
   - Agregar herramientas al chat para que pueda ejecutar análisis

4. **Task #173 — Auto-captura de KPIs** 
   - Que el motor guarde KPIs automáticamente al correr análisis

## Tips para trabajar con Claude Code

- **Sé específico**: "Agrega filtro de temporada a la tabla de empujes en Afinidad" > "mejora Afinidad"
- **Pide auditoría**: Después de cambios grandes, pide "corre auditoría de código con el skill code-audit"
- **Verifica compilación**: Pide "corre py_compile en app_streamlit.py y motor_v2.py"
- **Revisa antes de push**: Pide "muéstrame el diff de lo que cambiaste" antes de commitear
- **Un cambio a la vez**: Es mejor hacer cambios incrementales que una reestructuración masiva

## Estructura del repo

```
CLAUDE.md                    ← Claude lee esto automáticamente
docs/
  context/
    INDEX.md                 ← Índice de memorias
    ALL_MEMORIES.md          ← Todas las memorias consolidadas
  PRIMER_SESION_CLAUDE_CODE.md  ← Este archivo (guía de arranque)
app_streamlit.py             ← UI principal
motor_v2.py                  ← Motor de cálculo
config.py                    ← Colores, constantes
...demás archivos Python...
```

## Nota importante

Claude Code lee CLAUDE.md automáticamente al inicio de cada sesión. Los archivos en docs/context/ necesitan ser leídos explícitamente (por eso el prompt de arranque los pide).
