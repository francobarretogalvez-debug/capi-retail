# PROMPT A — Taxonomía Maestra: Matriz Cobertura × Edad

> **Pre-requisito**: haber leído y confirmado el PROMPT 0 (MASTER).
> **Frente**: A
> **Dependencias**: ninguna. Es el cimiento conceptual del resto de prompts.

---

## 1. Contexto del problema

Actualmente la herramienta tiene **deuda conceptual grave** en el sistema de clasificación de estados de SKU.

### 1.1. Sistema actual (problemático)

La sección de Estados clasifica SKU × tienda en 9 estados lineales:

| Estado | Criterio actual | SKU×Tienda |
|---|---|---|
| CRÍTICO | Cobertura <4 sem | 1,470 (8%) |
| PRE-CRÍTICO | Cobertura 4-8 sem | 1,622 (8%) |
| ÓPTIMO | Cobertura 8-12 sem | 1,172 (6%) |
| ALTO | Cobertura 12-16 sem | 822 (4%) |
| SOBRESTOCK | Cobertura >16 sem | 2,000 (10%) |
| LIQUIDAR | Edad >26 sem | 102 (1%) |
| SIN VENTA | Sin venta, edad 0-3 meses | 5,728 (29%) |
| DORMIDO | Sin venta, edad 3-6 meses | 3,683 (19%) |
| MUERTO | Sin venta, edad >6 meses | 2,893 (15%) |

**Capital total: S/ 20.8M en 19,492 combos SKU×Tienda.**

### 1.2. Problemas estructurales identificados

**Problema 1 — Dos universos mezclados que se solapan:**
- Universo cobertura: CRÍTICO / PRE-CRÍTICO / ÓPTIMO / ALTO / SOBRESTOCK
- Universo edad-sin-venta: SIN VENTA / DORMIDO / MUERTO / LIQUIDAR
- Un SKU puede pertenecer a ambos universos simultáneamente, pero el sistema fuerza una clasificación única → **se pierde información**.

**Problema 2 — ÓPTIMO mal definido:**
- Banda 8-12 sem termina justo en el target (12 sem).
- Un SKU con 13 sem queda como "ALTO" cuando está sano.
- Banda óptima debe ser **alrededor del target**, no terminando en él.

**Problema 3 — SOBRESTOCK arranca muy bajo y no se subdivide:**
- ">16 sem" agrupa indistintamente cobertura 17 sem y 80 sem.
- Inconsistente con la sección detalle_sobrestock que sí los diferencia (vigilar / claro / zombie).

**Problema 4 — LIQUIDAR mal definido:**
- Solo edad >26 sem ignora velocidad de venta.
- Marca para liquidar SKUs antiguos que están girando bien.
- Deja fuera SKUs jóvenes con sobrestock zombie (cobertura 100 sem, edad 4 meses).

**Problema 5 — SIN VENTA edad 0-3 meses (29% del universo) infla el problema:**
- Probablemente incluye lanzamientos en rampa que no son problema real.
- 29% del total es sospechoso → revisar si estamos contando producto recién llegado como "sin venta".

**Problema 6 — Inconsistencia entre secciones:**
- La sección Sobrestock usa unos tiers.
- La sección Estados usa otros.
- La nueva Salud del Stock (Prompt C) tendrá los suyos si no unificamos.
- **Resultado actual: 4 sistemas de clasificación paralelos para describir lo mismo.**

---

## 2. Objetivo del prompt

Definir e implementar **una taxonomía maestra única** que clasifique cada combo SKU × Tienda en un único estado, basada en la combinación de **cobertura × edad**, y propagarla retroactivamente a **toda la herramienta** para eliminar inconsistencias.

---

## 3. Solución propuesta: Matriz 2 ejes (cobertura × edad)

### 3.1. Eje Cobertura (alineado con target 12 semanas)

| Banda | Lectura | Color |
|---|---|---|
| <4 sem | Quiebre | Rojo oscuro |
| 4-8 sem | Alerta | Naranja |
| **8-16 sem** | **Óptimo (banda alrededor del target)** | Verde |
| 16-26 sem | Vigilar | Amarillo |
| 26-52 sem | Sobrestock | Marrón |
| >52 sem | Zombie | Negro |

### 3.2. Eje Edad

| Banda | Lectura |
|---|---|
| <8 sem | Nuevo (en rampa) |
| 8-26 sem | Mid-life |
| >26 sem | Maduro |

### 3.3. Caso especial — Sin venta

Cuando velocidad de venta = 0, la cobertura matemática es infinita. Tratar como dimensión separada:

| Edad sin venta | Estado |
|---|---|
| <8 sem (nuevo) | RAMPA — esperar |
| 8-26 sem | DORMIDO |
| >26 sem | MUERTO |

### 3.4. Matriz combinada (estados resultantes)

Cada combo SKU × Tienda cae en una sola celda:

```
                          EJE COBERTURA →
                          
            <4 sem      4-8 sem     8-16 sem    16-26 sem   26-52 sem   >52 sem    Sin venta
         ┌──────────┬───────────┬──────────┬───────────┬───────────┬──────────┬────────────┐
NUEVO    │ Quiebre  │ Alerta-N  │ Óptimo-N │ Vigilar-N │ Mal lanz. │ Mal lanz │ RAMPA      │
(<8 sem) │ nuevo    │           │          │           │           │ grave    │            │
         ├──────────┼───────────┼──────────┼───────────┼───────────┼──────────┼────────────┤
MID      │ QUIEBRE  │ ALERTA    │ ÓPTIMO   │ VIGILAR   │SOBRESTOCK │ ZOMBIE   │ DORMIDO    │
(8-26 s) │          │           │          │           │           │          │            │
         ├──────────┼───────────┼──────────┼───────────┼───────────┼──────────┼────────────┤
MADURO   │ QUIEBRE-M│ ALERTA-M  │ MADURO OK│ VIGILAR-M │SOBRESTOCK │ LIQUIDAR │ MUERTO     │
(>26 sem)│          │           │          │           │           │          │            │
         └──────────┴───────────┴──────────┴───────────┴───────────┴──────────┴────────────┘
                                                          EJE EDAD ↓
```

### 3.5. Acciones derivadas (orientativas, no parte del cálculo)

| Estado | Acción primaria sugerida |
|---|---|
| ÓPTIMO / ÓPTIMO-N / MADURO OK | No tocar — sano |
| QUIEBRE / QUIEBRE-N | Validar reposición urgente |
| ALERTA / ALERTA-N | Validar reposición |
| VIGILAR / VIGILAR-N | Monitorear, posible empuje |
| RAMPA | Esperar — producto nuevo en lanzamiento |
| MAL LANZAMIENTO | Revisar exhibición / VM antes de markdown |
| SOBRESTOCK | Revisar exhibición → empuje → evaluar markdown |
| ZOMBIE | Markdown + transferencia evaluadas |
| LIQUIDAR | Markdown agresivo + liquidación |
| DORMIDO | Revisar exhibición → markdown si no responde |
| MUERTO | Markdown agresivo + liquidación |

---

## 4. Notas a validar antes de ejecutar

> Estas son preguntas que necesitas resolver conmigo **antes** de tocar código.

1. **Campo de edad del SKU**: el reporte micro/profundidad debe entregar fecha de ingreso del SKU (o equivalente que permita calcular edad). Confirmar campo exacto a usar y su nombre técnico.
2. **Definición operativa de "sin venta"**: ¿venta = 0 en últimas 4 semanas? ¿última semana? Definir ventana de medición. **Recomendación**: usar 4 semanas (alineado con snapshot semanal del Prompt B).
3. **Granularidad**: ¿la taxonomía aplica a SKU × tienda únicamente, o también a vista agregada SKU (todas las tiendas)?
   - **Recomendación**: implementar a nivel SKU × tienda como base, y derivar agregaciones para vista por SKU usando regla de mayoría (>70% en mismo estado → SKU clasificado así; si no, MIXTO).
4. **Subdivisión de SOBRESTOCK**: ¿mantener un único estado SOBRESTOCK que cubre 26-52 sem, o subdividir en SOBRESTOCK CLARO (26-52) y ZOMBIE (>52)? **Recomendación**: subdividir, alineado con detalle_sobrestock.
5. **Validar nombres finales de los estados**: la propuesta usa nombres técnicos ("ÓPTIMO-N", "QUIEBRE-M"). ¿Mantener así o simplificar la nomenclatura?
6. **Migración**: la sección de Estados actual tiene 9 estados ya en uso. ¿Mantener compatibilidad con clasificación anterior durante un periodo de transición o migración directa?

---

## 5. Arquitectura técnica esperada

### 5.1. Módulo central

Crear un módulo Python único (sugerencia: `taxonomia.py` o `classification.py`) que:

1. Reciba un DataFrame con columnas mínimas: `sku`, `tienda`, `stock_total`, `venta_4sem` (o equivalente), `fecha_ingreso` (para calcular edad).
2. Calcule:
   - `velocidad_semanal` = venta_4sem / 4
   - `cobertura_sem` = stock_total / velocidad_semanal (manejar división por cero como "Sin venta")
   - `edad_sem` = (fecha_actual - fecha_ingreso) en semanas
3. Aplique la matriz y devuelva una columna nueva `estado` con el estado resultante.
4. Devuelva también columnas auxiliares: `eje_cobertura`, `eje_edad`, `accion_sugerida`.

### 5.2. Configuración centralizada

Los **umbrales de las bandas** deben vivir en un solo archivo de configuración (sugerencia: `config.py` o YAML), no hardcodeados en el módulo. Ejemplo:

```python
UMBRALES_COBERTURA = {
    'quiebre': 4,
    'alerta': 8,
    'optimo_max': 16,
    'vigilar_max': 26,
    'sobrestock_max': 52,
    # >52 = zombie
}

UMBRALES_EDAD = {
    'nuevo': 8,
    'midlife': 26,
}

VENTANA_SIN_VENTA_SEMANAS = 4
```

Esto permite ajustar la taxonomía sin tocar lógica.

### 5.3. Propagación a otras secciones

Una vez creado el módulo, **todas las secciones de la herramienta deben llamar a este módulo** para clasificar SKUs. No debe haber clasificaciones paralelas en otras secciones.

Identificar todas las secciones que hoy hacen clasificación propia y migrarlas a usar `taxonomia.py`. Mínimo conocido:
- Sección Estados
- Sección detalle_sobrestock
- Sección detalle_venta_cero
- Sección Cobertura (rediseño en Prompt D)
- Sección Salud del Stock (rediseño en Prompt C)

---

## 6. Criterios de aceptación

### 6.1. Funcionales

- [ ] Existe un módulo único de clasificación que recibe DataFrame y devuelve estado por SKU × tienda.
- [ ] Los umbrales viven en archivo de configuración separado, no hardcodeados.
- [ ] Cada combo SKU × tienda cae en exactamente un estado (no solapamiento).
- [ ] El estado "Sin venta" se trata correctamente (no genera división por cero, no se confunde con cobertura alta).
- [ ] Existe una función de agregación que clasifica SKU completo (todas las tiendas) usando regla >70% del mismo estado → estado dominante; si no, MIXTO.
- [ ] Todas las secciones de la herramienta que hacían clasificación propia ahora llaman al módulo central.

### 6.2. Métricos

- [ ] El cálculo de cobertura coincide con el reporte fuente en una muestra aleatoria de 10 SKU × tienda (tolerancia: redondeo a 1 decimal).
- [ ] La distribución total de SKU × tienda por estado se reporta en una tabla resumen, comparable con la distribución actual de los 9 estados.
- [ ] El número total de SKU × tienda clasificados coincide con el total del reporte fuente (ningún registro perdido).

### 6.3. Auditoría

- [ ] Ejecutar skill `code-audit` y entregar resultados.
- [ ] Validación manual de 10 SKU × tienda elegidos al azar: estado calculado vs estado esperado por la matriz.

---

## 7. Entregables

1. Módulo `taxonomia.py` (o nombre equivalente) con la lógica de clasificación.
2. Archivo de configuración con umbrales.
3. Tabla resumen comparativa: distribución actual (9 estados) vs distribución nueva (matriz).
4. Lista de secciones migradas al módulo central.
5. Resultados de auditoría (skill `code-audit`).
6. Validación de muestra de 10 SKU × tienda.
7. Backlog de hallazgos detectados durante la implementación.

---

## 8. Lo que NO entra en este prompt

- Rediseño visual de la sección Estados (eso entra cuando se ejecute Prompt G).
- Cambio de UI de otras secciones (entra en sus respectivos prompts).
- Sugerencia automática de acciones (las acciones son orientativas en esta tabla, no se calculan).
- Cualquier feature del backlog global del MASTER.

---

**Fin del Prompt A.**
