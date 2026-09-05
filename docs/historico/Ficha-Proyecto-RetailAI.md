---
fecha-creacion: 2026-04-08
proyecto: Herramienta Retail Generada con AI
estado-general: MVP listo
tipo: ficha-proyecto
---

# Ficha de Proyecto: Herramienta Retail Generada con AI

## 1. Qué es este proyecto

SaaS de gestión de inventario para marcas de ropa en Perú. Automatiza el análisis que un buyer hace manualmente en Excel (3-4h/semana): calcula cobertura por SKU×tienda, genera reposiciones, sugiere transferencias entre tiendas y recomienda acciones de precio para sobrestock y productos dormidos. El usuario sube un Excel con 4 pestañas y recibe un dashboard interactivo con decisiones priorizadas.

## 2. Modelo de monetización

SaaS mensual. Rango definido por Franco: S/400-800/mes por cliente. Precio final pendiente de validación con primeros usuarios. Sin inversión de infraestructura aún — corre local en la máquina del cliente vía Streamlit.

## 3. Estado actual — sin adornos

**Funciona hoy:**
- Motor de cálculo completo (motor_v2.py, ~1000 líneas): cobertura, reposiciones, transferencias, acciones de precio, alertas IA (5 tipos), anomalías por tienda, briefing ejecutivo auto-generado.
- App Streamlit con 6 tabs, UX revisada, exportación Excel de 7 hojas.
- Plantilla de input v2 con data simulada para demos (13 SKUs, 2 tiendas, historial 52 semanas).
- Lanzador macOS (INICIAR_APP.command) para que usuarios no técnicos ejecuten.
- Roadmap documentado V1→V3 con auditoría de 31 funcionalidades.

**Solo existe como idea/diseño:**
- SKU hijo (talla×color) — diseño definido, código pendiente.
- Índice estacional (motor necesita data LY real de clientes).
- Clustering de tiendas, predicción de demanda, elasticidad de precio.
- Despliegue cloud (hoy es 100% local).

**No existe aún:**
- Validación con un cliente real pagando.
- Landing page, material comercial, proceso de onboarding.
- Infraestructura cloud o multi-tenant.

## 4. Qué falta para generar el primer ingreso

1. Demo funcional con data real de Ayrton D'Ambrossio (Moments) — validar que el output es accionable.
2. Ajustar motor según feedback de Moments (probablemente: multi-tienda, formatos específicos).
3. Definir pricing final basado en valor percibido por el cliente.
4. Crear proceso mínimo de onboarding (cómo llenar la plantilla, cómo ejecutar).
5. Cerrar acuerdo con Moments como primer cliente pagado.
6. Definir canal de soporte (WhatsApp, email, reuniones periódicas).

## 5. Complejidad y recursos

- **Complejidad técnica:** Media — el motor funciona y la lógica de negocio es sólida, pero escalar a cloud y multi-tenant requiere migración arquitectónica que Franco no puede hacer solo.
- **Requiere inversión monetaria:** No para validación inicial (corre local). Sí para escalar: hosting cloud estimado S/100-300/mes cuando haya 3+ clientes.
- **Dependencias externas:** Data real de clientes para validar (Moments es la dependencia #1). Sin datos reales, el producto no se puede validar comercialmente.
- **Habilidades que Franco necesita y aún no tiene:** Despliegue cloud (Streamlit Cloud / Railway / similar). Ventas B2B estructuradas — hoy depende de su red personal.

## 6. Potencial de ingreso

- **Tipo de ingreso:** Recurrente (SaaS mensual)
- **Estimación mensual realista a 6 meses:** S/800-2,400/mes (2-3 clientes a S/400-800). Conservador porque el ciclo de venta B2B en retail peruano es lento y Franco vende en paralelo a su trabajo en Ripley.
- **Escalabilidad:** Media — el producto es replicable entre clientes similares (marcas de ropa con 5-50 tiendas), pero cada onboarding requiere configuración manual y soporte personalizado. Alta escalabilidad solo llega con cloud + self-service, que es V3.

## 7. Riesgos principales

1. **Dependencia de Moments como primer cliente.** Si Ayrton no avanza, no hay validación. Mitiga: tener Nuqa y contacto Ripley como alternativas en paralelo.
2. **Producto local sin cloud = fricción de adopción.** Un buyer no técnico puede no lograr instalar Python/Streamlit. Mitiga: INICIAR_APP.command simplifica, pero no elimina el problema.
3. **Franco opera solo y en paralelo a su trabajo full-time.** Capacidad de ejecución limitada a ~10h/semana. Mitiga: Claude como co-builder técnico, pero el bottleneck son las ventas y relaciones comerciales que solo Franco puede hacer.

## 8. Conexión con otros proyectos

SIN DATOS — preguntar a Franco. No tengo visibilidad sobre los otros 6 proyectos que gestiona. Posibles sinergias: si algún otro proyecto involucra el ecosistema retail peruano o relaciones B2B con marcas de moda, este proyecto genera credibilidad y red de contactos que podrían alimentarlos.

---

## Preguntas pendientes para Franco

1. ¿Cuál es el estado real de la relación con Ayrton/Moments? ¿Ya vio una demo? ¿Hay fecha tentativa?
2. ¿Cuántas horas semanales reales le estás dedicando a este proyecto?
3. ¿Los otros 6 proyectos comparten mercado, habilidades o red de contactos con este?
4. ¿Tienes deadline externo (ej: contrato, compromiso verbal) o el timeline es autoimpuesto?
5. ¿Consideraste un modelo freemium o trial gratuito para reducir fricción del primer cliente?
