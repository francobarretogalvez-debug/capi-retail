# Guion demo Capi — Ripley Chile · v0 (esqueleto, 2026-09-05)

> 15 minutos, 5 momentos. Regla del sprint: **se construye para el guion, no al revés.** Lo que no aparece acá no entra al sprint.
> Audiencia: Rodrigo Guajardo, Alfonso Lobato (GG), Lázaro Calderón. Tesis: *"esto no es un mockup, está operando en Perú"*.
> Norte del producto (Franco): avisar rápido los problemas del inventario y dar soluciones prácticas. Cada pantalla se juzga con 3 preguntas: ¿avisa de un porrazo? ¿la acción está al lado del dato? ¿se entiende en 30 segundos?

| # | Momento | Min | Qué se muestra | Frentes del sprint que lo alimentan | Número que se dice |
|---|---|---|---|---|---|
| 1 | **El problema** | 2 | Un buyer, 32 tiendas, 21 mil SKUs, un Excel de 387 columnas cada lunes. Quiebres y capital dormido conviven en la misma tienda y nadie lo ve a tiempo. | (contexto) | Capital inmovilizado S/ __ (snapshot 30-ago) · venta perdida neta S/266–469K (banda) |
| 2 | **Capi lo ve antes** | 4 | Dashboard con KPIs semana vs las 4 anteriores (flechas) → Salud del stock por marca → Obsoletos por tienda + alerta "S/ __ entran a obsoleto en 2 semanas". | S4 comparativo + KPIs · S5 obsoletos · S2 venta perdida · S3 dashboard | Δ capital dormido semana a semana · marca con más plata por entrar a obsoleto |
| 3 | **Capi dice qué hacer** | 4 | Match Producto-Plaza (empujes CD→tienda con filtros de buyer: clima, margen, descuento) → Transferencias con ganancia por tienda receptora → Excel de acciones de la semana para jefes de tienda → Excel por marca tercera para negociar. | S8 transferencias · S7 Excel terceras · Match (existente) · S9 talla/color (si llega) | Ganancia esperada de transferencias S/ __ · uds bloqueadas por clima |
| 4 | **Se ejecuta y se mide** | 3 | Ritual de lunes: acciones enviadas → marcadas → cumplimiento en 4 cuadrantes (pedido y recibido / pedido y no recibido / recibido sin pedir / no hecho). | S6 cumplimiento · Fase E rituales 08 y 15 | % de empujes ejecutados semana 1 y 2 |
| 5 | **Resultado y lo que viene** | 2 | Caso de Éxito: capital en exceso antes/después. Cierre con el módulo de surtido: cuántas opciones y qué profundidad por marca (el caso camisas Día del Padre). | Caso de Éxito (existente) · S13 surtido | Capital liberado S/ __ · "en Día del Padre faltaron __ camisas en __ opciones" |
| + | **"¿Cómo te aseguras que no falle?"** (Alfonso) | 1 slide | Versión visible, validación de la base al subir, modo seguro, tests + CI, firma de tiendas resistente a cambios de formato (ya pasó en agosto y no rompió). | S1 robustez | n° de tests · semanas corriendo en producción |

## Lo que NO entra al guion (y por tanto sale del sprint)
Planificación comercial (exploratorio) · boletín Rodrigo · rediseño visual · roadmap junio · caso Diegol.

## Pendientes del guion
- [ ] Números en blanco (`__`) se llenan con el snapshot 30-ago y el primer ritual (08-sep).
- [ ] v1 en sesión 6 con resultados reales de los rituales.
- [ ] Ensayo cronometrado.
