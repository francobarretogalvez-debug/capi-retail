# Aprendizaje — Motor de cálculo de compra para programas en transición

> Documento de aprendizaje del caso **Marquis / Showroom Mayo 2026** (reemplazo del bestseller "El Diegol" por Dario + Bremen + Bronco). Su propósito es **sembrar un motor reutilizable** dentro de Capi (módulo de predistribución, brief §9). Captura la metodología que emergió, los errores que cazamos en el camino, y la spec de lo que hay que generalizar.

Archivos del caso:
- `transicion_engine.py` — motor del caso (funciones puras + runner).
- `audit_transicion.py` — auditoría numérica independiente (44/44 PASS).
- `Bases/Transicion_Marquis_Mayo2026.xlsx` — salida Excel (8 hojas).
- `Bases/Pitch_Transicion_Marquis.pptx` — deck didáctico (13 slides).

---

## 1. El problema (caso genérico)

Un **bestseller sale de una marca/categoría** (decisión tomada). No hay reemplazo 1:1 de un bestseller → la venta cae sí o sí. El objetivo NO es igualar, es **minimizar la caída y cuantificarla** para sustentar la compra de los reemplazos ante Planificación.

Patrón recurrente → candidato a feature de Capi: *"dado un programa que sale y N programas que entran, dimensionar la compra y la cobertura del hueco."*

---

## 2. Arquitectura: dos rutas de cálculo

| Ruta | Cuándo | Cómo se proyecta la venta |
|---|---|---|
| **A — vía DATA** | El programa entrante YA se vende (tiene historia por tienda) | `venta = captura × hueco`, donde captura = venta_entrante/venta_saliente, corregida por factores |
| **B — vía CARGA** | El programa NO tiene historia (o es de ventana) | `venta = carga × agotamiento`, carga = uds/tienda × n_tiendas (input manual) |

**Modelo unificado:** todo sale de `venta = tasa × base`. La **compra (presupuesto)** se despeja: `compra = venta / agotamiento`.

Dos fuentes de datos que se complementan (clave del caso):
- **Profundidad abierta a tienda** (formato wide "Base al DD.MM"): única con apertura por tienda → Ruta A (captura, exhibición, stock).
- **BD agregada por modelo-color** (nivel cadena, semanal): magnitud, márgenes, captura temporada completa, precios. NO abre por tienda.

---

## 3. El framework de corrección de captura (la innovación central)

La captura cruda (venta_entrante / venta_saliente) **nunca es la captura real** — está distorsionada por factores que hay que corregir. El caso desarrolló **3 correcciones encadenadas**, cada una multiplicativa:

```
captura_corregida = captura_cruda × f_exhibición × f_precio   (y un check de stock)
```

| Corrección | Dirección | Qué corrige | Cómo se calcula |
|---|---|---|---|
| **Exhibición** | ↑ sube | El saliente vende más por tener más espacio (ej. doble exhibición en pasarela), no por más demanda | Normalizar venta por punto de exhibición: dividir la venta del saliente entre sus puntos en las tiendas afectadas, recalcular captura. Factor = captura_norm / captura_cruda |
| **Stock** | contexto | El entrante puede estar sub-comprado (vende poco por poco stock, no por poca demanda) | Productividad = venta/stock; cobertura = stock/(venta/sem). Si el entrante rota sano, no hay sub-stock; si está hambriento, su captura cruda es piso |
| **Precio** | ↓ baja | El entrante vende más por estar más barato que el saliente; a paridad vendería menos | Factor = 1 + elasticidad × (Δprecio%). Ej. elasticidad −1.0, subir +25% → factor 0.75 |

**Rango de escenarios (anclado en datos reales, NO en punto medio):**
- **Piso** = captura conservadora (temporada completa) × correcciones.
- **Base** = captura reciente real normalizada × precio (el dato típico).
- **Techo** = captura de las mejores tiendas (capada al 100% por premisa) × precio.

Ejemplo Marquis: cruda 22% → piso **23%**, base **35%**, techo **75%** (los tres × 0.75 precio).

---

## 4. Aprendizajes y pivotes clave (lo más valioso)

> Cada uno de estos fue un error, supuesto falso o refinamiento que cazamos en el camino. Son las trampas que el motor generalizado debe evitar por diseño.

### 4.1 Matchear por CÓDIGO de modelo, no por nombre
El match por substring de nombre ("DIEGOL") barría 5 variantes distintas (incl. "MARQUIS TWILL DIEGO" que es otro producto, y un OI19 viejo), inflando el baseline de 9,546 a 12,372. **Siempre matchear por código de modelo exacto** (`CodModelo` en BD / `Cód. Prod.` en wide), con lista explícita por programa.

### 4.2 La venta por tienda del loader está PRORRATEADA
El loader del motor reparte las semanas 2-4 desde el total del SKU por proporción de la semana 1 → genera valores sintéticos repetidos. **No usar el prorrateo para captura.** Solución: **sumar la columna `<tienda> Vta` (venta real de 1 semana) a lo largo de varios snapshots semanales.** El caso sumó 7 snapshots = 7 semanas reales.

### 4.3 No asumir "liquidación" sin evidencia
Asumí que el Diegol estaba en liquidación (por stock alto/agotamiento bajo). Era falso. **No inferir estados comerciales de proxies;** validar con el dueño del negocio.

### 4.4 Los valores negativos de venta son DEVOLUCIONES
Netean la venta, son legítimos. No filtrarlos como errores.

### 4.5 BUG de stock: promediar multi-SKU mal (el más grave)
Un programa con 3 SKUs: el cálculo promediaba el stock sobre (3 SKUs × 7 snapshots) en vez de **sumar los SKUs por snapshot y luego promediar entre snapshots**. Subvaluaba el stock ÷3 → productividad falsa de 4.6× (real 1.5×), cobertura 6 sem (real 18.6). **Patrón a codificar:** stock = `mean_over_snapshots( sum_over_skus( stock ) )`; venta = `sum( todo )`. Lo destapó la intuición del usuario ("no me cuadra 20 uds/tienda") → **siempre dar números sanity-checkeables al usuario.**

### 4.6 Normalización por exhibición (pasarela)
Si el saliente tiene doble exhibición en ciertas tiendas, su venta ahí está inflada por espacio. Dividir su venta entre n° de puntos de exhibición en esas tiendas antes de comparar. El factor se mide donde hay apertura por tienda (semanas recientes) y se transfiere al número de temporada completa (supuesto: distorsión estable entre periodos).

### 4.7 El "uplift" es un factor RELATIVO, no un número de captura
Ej. ×1.37 = "cuánto sube la captura al normalizar". Se aplica multiplicando, no reemplaza la captura.

### 4.8 Precio: el saliente y el entrante a distinto precio
El entrante (Dario) estaba a S/79.99 por error (debió ser 99.99, paridad). Más barato vende más → su captura está inflada por precio. **Cuidado:** la data no siempre muestra el efecto limpio (correlación confundida por liquidaciones de saldos). El factor de elasticidad es un **supuesto del negocio**, no un dato medido — hay que pedirlo explícito.

### 4.9 Dos periodos = dos crudas (reconciliar explícito)
Captura temporada completa (22%, conservadora) vs reciente (35%, el programa sube). Mostrar AMBAS y dejar claro qué rol cumple cada una (piso usa la conservadora; base/techo la reciente). No esconder la diferencia — confunde y se cuestiona.

### 4.10 Escenarios: anclar en datos, NO en punto medio
Probamos "base = punto medio entre piso y techo". Era frágil: el punto medio lo jala un techo aspiracional, y producía la paradoja de que "anclar conservador" daba un base MÁS ALTO. **Cada escenario debe ser un dato real distinto** (piso = conservador full-season; base = reciente real; techo = mejores tiendas reales capadas por premisa).

### 4.11 Valorización: VtaSMF (venta neta) consistente, descuento embebido
3 niveles de precio: lista > vigente (con descuento) > **VtaSMF** (neto de descuento e IGV/financiero). Usar **VtaSMF consistente** en todos los programas. El descuento de categoría (~41% vs lista) YA está dentro del VtaSMF → no aplicarlo aparte (doble conteo) ni usar lista (sobre-estima).

### 4.12 Consistencia precio–unidad al valorizar
Si castigas las UNIDADES del entrante por subirlo a paridad, debes valorizar esas unidades **al precio de paridad** (no al precio viejo barato). Lo contrario es doble castigo (menos unidades Y precio bajo). Consecuencia interesante: la **cobertura en soles puede superar a la de unidades** si los reemplazos son productos de mayor precio (caso: 85% uds vs 101% S/).

### 4.13 El presupuesto se dimensiona por UNIDADES/cubicaje, no por soles
La compra se ancla en cubicaje (uds/tienda escalonado por volumen de tienda) y en cobertura de unidades. Los soles son el argumento de "venta defendida", no el dimensionador.

---

## 5. Principios metodológicos (guardrails)

1. **Captura cruda = piso, no verdad.** Siempre corregir por exhibición, stock y precio antes de confiar.
2. **Premisa del bestseller:** ningún reemplazo lo supera 1:1 → capar capturas a 100%; capturas >100% por tienda = ruido o distorsión (stock/exhibición/precio), investigar.
3. **Mismo periodo / normalizar por espacio** al comparar entrante vs saliente.
4. **Supuestos del negocio explícitos** (elasticidad, agotamiento objetivo, qué precio): pedirlos, no asumir.
5. **Dar números sanity-checkeables al usuario** (uds/tienda, cobertura en semanas): así se cazan los bugs.
6. **Auditoría numérica independiente** recomputando desde data cruda (no confiar solo en consistencia interna del motor).
7. **Programas sin historia (ej. Bronco)** = menor certeza; marcarlos y heredar economics de un análogo, pero flaggear.

---

## 6. Spec del motor reutilizable (para Capi)

Funciones a generalizar (hoy en `transicion_engine.py`):

- `dimensionar_programas(bd, codigos)` → uds/S/margen por programa, por ventana, captura cadena, descuento categoría, economics (precio_u, costo_u, margen_u). **Match por código.**
- `cargar_profundidad_tienda(rutas[], codigos)` → venta real multi-snapshot (suma) + stock promedio (mean over snapshots de sum over SKUs) por (programa, tienda). **Sin prorrateo.**
- `productividad_stock(por_tienda)` → productividad y cobertura por programa (check de sub-compra).
- `ranking_captura(por_tienda, pasarela)` → captura cruda y normalizada por exhibición por tienda.
- `uplift_exhibicion(por_tienda, pasarela)` → factor relativo de exhibición.
- `proyectar_programa_data(...)` → rango piso/base/techo con correcciones encadenadas (exhibición, precio) y techo = mejores tiendas capado.
- `proyeccion_manual(carga, agotamiento)` → Ruta B, con distribución escalonada por volumen de tienda (tiers).
- `cuadro_mitigacion(...)` → saliente vs reemplazo en uds, S/ (paridad), margen, + % cobertura.
- `construir_pitch(...)` → bloques de slides; `exportar_excel(...)` → hojas.

**Inputs externos (los pone el usuario):**
- Códigos de modelo por programa (saliente + entrantes).
- Tiendas con exhibición especial (pasarela) y n° de puntos.
- Elasticidad de precio y precios objetivo.
- Para Ruta B: carga/tienda, n° tiendas, agotamiento objetivo por ventana.
- Cubicaje/tiers de distribución.

**Generalización en Capi:** parametrizar marca/categoría/programas; el framework de corrección de captura es agnóstico del caso. Encaja en el módulo de predistribución (extrapolar captura de tiendas gemelas es el mismo patrón).

---

## 7. Supuestos y limitaciones a arrastrar

- **Elasticidad −1.0** del castigo de precio: supuesto, no medido (no hubo experimento limpio de precio).
- **Transferencia del uplift de exhibición** entre periodos (medido reciente, aplicado a full-season): supone distorsión estable.
- **Piso (full-season) y base/techo (reciente)** mezclan periodos a propósito; documentar siempre.
- **Bronco sin historia:** hereda precio/margen/agotamiento de un análogo (Bremen) — el reemplazo de menor confianza.
- **Baseline saliente = ventana de la BD** (no año calendario), etiquetar "temporada completa".
- **Cubicaje físico/planograma** es input externo, no lo calcula el motor.

---

## 8. Resultado del caso (referencia)

- Hueco Diegol (cód 27390791): **9,546 uds / S/689,645 / margen S/381,060** (temporada completa).
- Rango Dario: piso 23% / **base 35%** / techo 75% (cruda 22% × exhib 1.37 × precio 0.75; base = reciente real; techo = mejores tiendas capadas).
- Bremen (ventana D, agot 40%) → 1,880 uds; Bronco (ventana B, agot 60%) → 2,820 uds. Carga escalonada 200/150/100 por tier, 4,700 c/u.
- **Recomendado (base):** compra 15,465 uds · inversión **S/604,991** · cubre **85% en unidades / 101% en soles** del hueco.
- Auditoría: **44/44 PASS**.
