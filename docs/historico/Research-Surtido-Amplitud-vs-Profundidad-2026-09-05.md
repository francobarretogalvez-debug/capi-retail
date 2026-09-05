---
tipo: research
fecha: "2026-09-05"
proyecto: Capi
frente: S13 Surtido amplitud vs profundidad
para: "pregunta de Lázaro Calderón — camisas Día del Padre"
estado: base para diseñar el módulo (sesión 3-4 del sprint Chile)
relacionado: ["[[2026-09-06-Sprint-Chile-2-Semanas]]", "[[Brainstorm-Nuevas-Funcionalidades-Capi]]", "[[Aprendizaje-Motor-Transicion-Marquis]]"]
---

# Surtido de camisas: amplitud vs profundidad — research para el módulo de Capi

> Investigación web (agente, 2026-09-05). Frameworks, práctica de retailers, literatura, demanda censurada, campañas pico. Todo output cuantitativo del módulo debe salir en bandas y declarar qué inputs son estimados (regla Loop-Auditor).

## 1. Resumen ejecutivo

1. La literatura formal (Kök, Fisher & Vaidyanathan) define el problema como: elegir qué opciones y cuántas unidades maximizan margen sujeto a presupuesto y **espacio de exhibición**; la sustitución entre opciones es el núcleo del problema ([survey](https://link.springer.com/chapter/10.1007/978-1-4899-7562-1_8)).
2. El trade-off es asimétrico: cada opción adicional canibaliza a las similares (rendimientos decrecientes); cada unidad de profundidad que falta en una talla mayor apaga la opción completa (Zara la retira del piso cuando falta S/M/L) ([Caro & Gallien, MIT/Zara](https://web.mit.edu/jgallien/www/ZaraInterfacesPaperAugust3.pdf)).
3. Regla empírica de industria: 20% de las opciones hace 80% de la venta y se sabe a la semana 6 ([Style Arcade](https://www.stylearcade.com/blog/the-top-5-rules-of-successful-fashion-buying-to-apply-now)); recortar cola larga no baja ventas si se reinvierte en profundidad de ganadoras (BCG: colección −20% con ahorro €36M; Bain: +17–19% venta tras cortar hasta 42% de SKUs, citado por [Tellius](https://www.tellius.com/resources/blog/sku-rationalization-in-cpg-why-the-spreadsheet-isnt-enough-anymore)).
4. Los dos modelos extremos: Zara = muchas opciones, poca profundidad (~11k ítems/año, lotes <1,000 uds/estilo); Uniqlo = <4,000 ítems, 2× más colores por categoría que Zara, tallas XS–XXL y profundidad total ([Lectra](https://www.lectra.com/en/library/back-to-basics-uniqlos-brand-strategy)). Ripley, como department store multimarca, se parece más a Macy's: localización de 10–15% del surtido por distrito ([SAS/Macy's](https://blogs.sas.com/content/sascom/2010/10/28/my-macys-the-science-of-localization/)).
5. La profundidad mínima por opción-tienda no la fija la demanda sino la **curva de talla completa**: el MDQ (Minimum Display Quantity), ej. camisa S1/M2/L2/XL1 = 6 uds ([Toolio](https://www.toolio.com/post/pre-packs-case-packs-and-minimum-display-quantities)).
6. Las herramientas comerciales (Oracle, Blue Yonder, o9, Nextail) usan los mismos inputs que Franco ya tiene: venta y stock por SKU-tienda, capacidad de espacio, clusters, curva de talla, y devuelven # opciones por cluster + compra por opción.
7. La venta observada en un quiebre está **censurada**: hay que descensurar (tasa de venta × tiempo real en stock, o EM/primary demand) antes de dimensionar; ignorarlo subestimó una talla en 54% en un caso documentado ([Madden](https://www.maddenanalytics.com/news/what-neglecting-size-curve-forecasting-is-costing-your-brand)).
8. Demand transference (Oracle) da la fórmula simple para "¿cuánto de lo que no compré se perdió de verdad?": venta incremental = demanda × (1 − % sustituible) ([Oracle AIF](https://docs.oracle.com/en/industries/retail/ai-foundation-cloud-service/26.1.201.0/aifim/using-demand-transference.htm)).
9. Para un pico de campaña no hay regla académica; la práctica es índice de campaña sobre baseline, profundidad = ROS descensurada × semanas de campaña ÷ ST objetivo, y amplitud acotada por capacidad de m².
10. Capi puede responder a Lázaro con tres números por marca: demanda real de camisas (venta + perdida por quiebre + perdida por sub-amplitud), # opciones que cabían con productividad marginal positiva, y profundidad por opción con curva de talla.

## 2. Métricas estándar

| Métrica | Fórmula | Para qué sirve | Fuente |
|---|---|---|---|
| **Sell-through (ST)** | uds vendidas ÷ uds recibidas | Lee demanda vs profundidad comprada. Moda: 65–85% sano; <60% dispara markdown; >90% = probable quiebre | [Toolio](https://www.toolio.com/post/sell-through-rate-how-to-calculate-and-5-strategies-to-optimize) |
| **Rate of sale (ROS)** | uds vendidas ÷ semanas en venta (por opción-tienda) | Base de la profundidad; varía 10× entre tiendas | [Style Arcade](https://www.stylearcade.com/blog/how-to-achieve-profit-growth-with-a-killer-assortment-plan) |
| **Weeks of cover** | stock ÷ ROS | Cuántas semanas dura cada opción; señal de sobre/sub-compra | [StyleMatrix](https://stylematrix.io/weeks-of-cover-retail-how-much-stock-should-you-hold/) |
| **Profundidad por opción** | ROS × semanas de vida ÷ ST objetivo | Compra por opción | derivada |
| **Option count / productividad por opción** | venta (o margen) ÷ # opciones ÷ semana | Decide # opciones: se agregan mientras la productividad marginal ≥ umbral | [Oracle APCS](https://docs.oracle.com/en/industries/retail/retail-assortment-planning-cloud-service/25.2.401.0/apcsu/to_2523Assortment_Planning.htm) |
| **Pareto de opciones** | % opciones acumuladas que hacen 80% venta | Identifica cola larga; ~20/80 a la semana 6 | [Style Arcade](https://www.stylearcade.com/blog/the-top-5-rules-of-successful-fashion-buying-to-apply-now) |
| **Curva de talla** | share de cada talla en semanas "limpias" (todas las tallas en stock, sin markdown) | Distribución de la profundidad; error típico hasta 40% si se calcula mal | [Madden](https://www.maddenanalytics.com/news/what-neglecting-size-curve-forecasting-is-costing-your-brand), [Blue Yonder](https://info.blueyonder.com/retail-planning-category-management/what-is-size-scaling) |
| **MDQ / presentación mínima** | uds por talla que una tienda debe tener siempre (ej. 1-2-2-1) | Profundidad mínima por opción-tienda para exhibir curva completa | [Toolio](https://www.toolio.com/post/pre-packs-case-packs-and-minimum-display-quantities) |
| **Curva rota** | opción sin alguna talla mayor (S/M/L) | Zara la saca del piso; mide días-opción "muertos" | [Caro & Gallien](https://web.mit.edu/jgallien/www/ZaraInterfacesPaperAugust3.pdf), [RetailDogma](https://www.retaildogma.com/broken-sizes/) |
| **% sustituible (demand transfer)** | fracción de la demanda de una opción que se queda en el surtido si se retira | Distingue opción incremental de opción redundante | [Oracle AIF](https://docs.oracle.com/en/industries/retail/ai-foundation-cloud-service/26.1.201.0/aifim/using-demand-transference.htm) |
| **Capacidad de exhibición** | m² × densidad (opciones/m² o uds/m²); ~2" de barra por prenda | Techo físico de la amplitud por marca-tienda | [Blue Yonder](https://info.blueyonder.com/retail-planning-category-management/what-is-blue-yonder-assortment-optimization), [Klassic Rack](https://klassicrack.com/articles/garment-spacing-on-retail-racks/) |

## 3. Cómo lo hace cada retailer

| Retailer | Regla concreta | Fuente |
|---|---|---|
| **Zara/Inditex** | Muchas opciones, lotes <1,000 uds/estilo, ~11k ítems/año. Regla de piso: si falta una **talla mayor** (S/M/L) la opción va al backroom; faltar XS/XXL no la retira. El modelo de distribución que respeta esta regla subió ventas 3–4% | [SCM Globe](https://www.scmglobe.com/zara-clothing-company-supply-chain/), [Caro & Gallien](https://web.mit.edu/jgallien/www/ZaraInterfacesPaperAugust3.pdf) |
| **Uniqlo** | <4,000 ítems; ~2× más colores por categoría que Zara; tallas XS–XXL; prioriza no perder venta por talla sobre variedad de estilos | [Lectra](https://www.lectra.com/en/library/back-to-basics-uniqlos-brand-strategy) |
| **Macy's** | "My Macy's": 69 distritos, planners locales; 10–15% del surtido localizado | [Macy's Inc.](https://www.macysinc.com/newsroom/news/news-details/2009/Macys-Inc.-to-Expand-My-Macys-Localization-Initiative-Adopt-New-Operating-Structure-Reduce-Expenses-02-02-2009/default.aspx), [SAS](https://blogs.sas.com/content/sascom/2010/10/28/my-macys-the-science-of-localization/) |
| **Clientes Nextail (River Island, Pepe Jeans)** | Asignación inicial reducida + holdback; consolidación de curvas rotas entre tiendas; claims: +5–10% venta, −30% stock en tienda, −60% quiebres | [FashionNetwork](https://uk.fashionnetwork.com/news/River-island-adds-ai-tech-from-nextail-co-claims-it-can-boost-sales-by-10-,1089428.html) |
| **Cliente BCG (apparel europeo)** | Recortó colección 20% → €36M/año; márgenes +400–700 pb | [BCG](https://www.bcg.com/publications/2013/retail-supply-chain-management-fast-flexible-lean-rethinking-fashion-supply-chain) |
| **Tendencia 2026 (McKinsey/BoF)** | "Menos SKUs, más fuertes" | [McKinsey SoF 2026](https://www.mckinsey.com/industries/retail/our-insights/state-of-fashion) |
| **Fisher–Vaidyanathan (tire retailer, snacks)** | Surtido por atributos: +20% y +32% venta simulada; +6% real. Sustitución asimétrica entre marcas: 89% vs 22% | [Fisher & Vaidyanathan](https://pubsonline.informs.org/doi/10.1287/mnsc.2014.1904) |
| **Albert Heijn (Kök & Fisher)** | Sustitución estimada con tiendas de surtidos distintos; +50% utilidad de categoría proyectada | [Kök & Fisher 2007](https://ideas.repec.org/a/inm/oropre/v55y2007i6p1001-1021.html) |
| **Falabella / Ripley Chile** | Sin reglas públicas de surtido | [Peru-Retail](https://www.peru-retail.com/falabella-cencosud-ripley-negocio-departamentales-chile/) |

**Amplitud por m² / mueble:** nadie publica un número; los software lo tratan como restricción calibrada con el planograma. La regla operativa pública es la de Zara: **una opción existe en el piso solo si tiene sus tallas mayores completas** → el MDQ es la unidad de profundidad mínima.

## 4. Estimar demanda real cuando hubo quiebre

**Método 1 — Extrapolación por tiempo en stock (Anupindi, Dada & Gupta 1998).** `λ_talla = ventas_talla ÷ días_en_stock_talla` → `Demanda_real = λ × días_campaña`. Para una opción: sumar tallas, o contar solo días con tallas mayores completas (regla Zara).

**Método 2 — Curva de talla "limpia" como proxy.** Curva con semanas donde todas las tallas estaban en stock y sin markdown; `Demanda_talla_quebrada = ventas_tallas_sanas × (share_talla_quebrada ÷ share_tallas_sanas)`. Es lo que usan los planners ([Madden](https://www.maddenanalytics.com/news/what-neglecting-size-curve-forecasting-is-costing-your-brand), [o9](https://o9solutions.com/articles/advanced-size-curve-analysis)).

**Método 3 — Primary demand con sustitución (Vulcano, van Ryzin & Ratliff 2012).** Simplificación operativa (Oracle): `Venta_perdida_neta = Demanda_no_atendida × (1 − %sustituible)`, con %sustituible = uplift de las opciones similares (misma marca, fit, banda de precio) en la misma tienda-semana del quiebre ([Vulcano et al.](https://pubsonline.informs.org/doi/10.1287/opre.1110.1012)).

**Sub-compra (nunca hubo opciones):** benchmark cross-sectional: share de camisas en tops (o venta camisas/m²) en tiendas-marca con surtido completo, aplicado a las que no lo tuvieron: `Demanda_camisas_tienda = venta_tops_tienda × share_camisas_benchmark`.

## 5. Propuesta: módulo Capi "Surtido: amplitud vs profundidad"

**Inputs (ya existen):** transaccional SKU (estilo×color×talla)×tienda×día; stock por variación×tienda (`.xlsb`); m² por marca×tienda; cobertura y ST; venta perdida por quiebre. Parámetros: ventana de campaña, ST objetivo (70–80%), tallas mayores por marca (ej. M/L/XL), curva MDQ base (1-2-2-1), umbral de productividad marginal.

**Cálculos (4 capas):**
1. **Perfil por opción-tienda.** ROS limpia (solo días con tallas mayores en stock), ST a fecha, weeks of cover, días con curva rota, venta perdida descensurada (M1) + ajuste por sustitución (M3). Clase: ganadora / media / cola (Pareto 80%).
2. **Curva de talla por marca × cluster** (semanas limpias; fallback a marca-total). MDQ_opción_tienda = curva redondeada hacia arriba, ≥1 ud por talla mayor.
3. **Amplitud por marca-tienda.** Capacidad = m² × densidad autocalibrada (opciones simultáneas históricas ÷ m² en tiendas-marca con ST sano). N opciones = mín(capacidad, punto donde la productividad marginal × (1 − %sustituible) cae bajo umbral). En banda.
4. **Profundidad por opción-tienda.** `Uds = max(MDQ, ROS_descensurada × semanas_campaña × índice_campaña ÷ ST_objetivo)` repartido por curva; índice_campaña = ROS en ventana Día del Padre ÷ baseline. Chequeos: Σ uds ≤ OTB, Σ exhibidas ≤ capacidad.

**Outputs:** (a) marca × cluster: # opciones actual vs recomendado, profundidad media, ST y cobertura esperados, m² usados; (b) opciones cola a recortar / ganadoras a profundizar; (c) "demanda real de la campaña" = venta + perdida por quiebre + perdida por sub-amplitud, en banda; (d) plan de compra por opción con curva por tienda.

**Respuesta a Lázaro (camisas, Día del Padre, por marca):**
- *¿Cuántas debimos tener?* → venta real + perdida descensurada por quiebre (regla tallas mayores) + brecha vs benchmark share camisas/tops. Rango, ej. "Lacoste: vendimos X; demanda estimada X+35% a X+60%".
- *¿Cuántas opciones?* → N por marca-cluster acotado por m² y productividad marginal; cuántas de las que hubo fueron cola (ST bajo con stock): evidencia de si el problema fue profundidad de ganadoras o falta de estilos.
- *¿Qué profundidad?* → por opción: MDQ por tienda × tiendas + demanda de campaña ÷ ST objetivo, con curva por cluster; flag de tiendas donde el MDQ excede la demanda (no exhibir ahí).

## 6. Riesgos y límites
- **M1 (Poisson por tiempo en stock):** asume tasa constante; en campaña no lo es (40–60% del pico en 48 h). Mitigar con curva intra-campaña del año previo.
- **M2 (curva limpia):** sin semanas limpias (quiebre crónico) no hay base; agregar a marca-cluster. Error de curva hasta 40%.
- **M3 (sustitución):** exige variación de surtido entre tiendas; falla con <20 ítems; la MNL asume proporcionalidad que Fisher–Vaidyanathan muestran falsa. Usar proxy de uplift y declararlo.
- **Benchmark cross-sectional:** confunde surtido con tráfico/mix; controlar por m² y cluster.
- **Capacidad por m²:** autocalibración hereda sesgos históricos; validar con planograma de 2–3 tiendas.
- **Pareto 20/80 y ST 65–85%:** heurísticas de consultoras, no evidencia académica; umbrales configurables.
- **Cifras de Zara/Uniqlo/Nextail:** secundarias o de vendor; dirección, no número a copiar.

## Hallazgo clave
La regla más accionable y con evidencia dura es la de **tallas mayores de Zara**: una opción sin S/M/L no existe en el piso. Convierte la profundidad mínima por opción-tienda en un número calculable con los datos de Franco, y es probablemente la explicación de "pocas camisas" si las que hubo tenían curva rota.

## Fuentes principales
Kök, Fisher & Vaidyanathan (survey) · Fisher & Vaidyanathan 2014 · Kök & Fisher 2007 · Caro & Gallien (Zara) · Vulcano, van Ryzin & Ratliff 2012 · Conlon & Mortimer (NBER) · Oracle APCS / AIF · Blue Yonder · o9 · Madden · Toolio · Lectra · SAS/Macy's · BCG · McKinsey SoF 2026 · Nextail/FashionNetwork · Style Arcade · RetailDogma. URLs inline arriba.
