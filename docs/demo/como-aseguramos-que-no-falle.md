# ¿Cómo nos aseguramos de que Capi no falle?
*Respuesta para Alfonso Lobato (GG Ripley Chile) · estado al 05-sep-2026 · Capi v2.1.0*

Capi lleva desde abril corriendo cada semana sobre la base real de Ripley Perú (32 tiendas, ~21 mil SKUs). "No fallar" no significa que nunca pase nada: significa que **ningún número mal calculado llegue a una decisión sin que alguien lo vea**. Así está construido hoy:

## 1. La base se valida antes de analizarse
- Cada archivo que se sube pasa un **contrato de entrada**: 14 columnas obligatorias, al menos 10 tiendas con la firma completa (stock + UME + on-order), stock total mayor que cero.
- Si no pasa, **Capi no analiza**. Muestra qué falta y ofrece **modo seguro**: seguir trabajando con el último corte válido, con un aviso visible.
- Las tiendas se detectan por firma, no por posición. En agosto Ripley cambió el formato del reporte micro (renombró columnas) y Capi siguió funcionando sin tocar código.

## 2. El motor tiene una red de regresión
- **Test de oro**: el motor corre sobre una base fija (302 SKUs × 6 tiendas) y se compara contra totales guardados: capital por estado, reposiciones, transferencias, ganancia esperada, margen. Si un cambio mueve un número en silencio, el test se pone rojo.
- **62 tests automáticos** cubren el motor de planificación, la identidad de inventario (stock inicial + compra − venta = stock final), el criterio económico de transferencias, los filtros de empuje (clima, margen, descuento), los exportes Excel, el comparativo semanal, los obsoletos y el cumplimiento de empujes.
- Un **meta-test** revisa que todo "cuadre" contra Excel invoque realmente al motor (una vez encontramos un cuadre que validaba la referencia contra sí misma; ahora eso no puede pasar).

## 3. Cada cambio se verifica antes de salir
- Los tests corren automáticamente en cada push (GitHub Actions). Nada llega a producción con la suite en rojo.
- Las 12 vistas de la app se recorren con la base real de la semana en un test de humo antes de publicar.
- Las versiones de todas las librerías están **fijadas**. Una actualización de Streamlit no puede romper la app un lunes por la mañana.

## 4. Si algo falla, se ve
- La versión y el corte de la base cargada están **siempre visibles** en el panel lateral: cualquier número citado es trazable a una versión y a un archivo.
- Un error de carga muestra un mensaje claro y guarda el detalle técnico; no deja la pantalla en blanco ni muestra un número parcial.
- Si el snapshot semanal no se pudo guardar, la app lo dice; antes fallaba en silencio.
- Cuando el comparativo vs año pasado no puede derivar la semana de la base, lo avisa en rojo en lugar de comparar contra la semana equivocada.

## 5. Los números tienen definición escrita
- Venta perdida: fórmula visible en pantalla (velocidad × semanas en quiebre × precio realizado sin IGV, descontada sustitución, en banda).
- Obsoleto: la app muestra las dos definiciones que conviven en Ripley (más de 6 meses en tienda vs sin venta más de 26 semanas) y la cifra de cada una, para que la elección sea explícita.
- Costo de marcas terceras: se muestra también el capital a costo implícito, porque el campo costo de Ripley subestima su margen ~11.7 pp.

## Lo que todavía no está (y cuándo)
| Hueco | Riesgo | Plan |
|---|---|---|
| Streamlit Cloud tiene disco efímero: los snapshots semanales y el registro de acciones se pierden en un reinicio si no se commitean | Se pierde historia, no números | Persistencia externa, semana del 14-sep |
| El snapshot semanal es por SKU a nivel cadena; el detalle por tienda recién se guarda desde el 05-sep | Series por tienda cortas | Se acumulan semana a semana |
| No hay registro de auditoría de quién subió qué base | Trazabilidad de operación | Log estructurado, semana del 14-sep |

*Todo lo anterior es verificable en el repositorio: `tests/`, `.github/workflows/tests.yml`, `requirements.txt`, `transformar_profundidad.validar_base`.*
