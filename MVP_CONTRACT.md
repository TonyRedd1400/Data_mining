# Contrato mínimo del MVP de Data Mining

## Propósito

El MVP convierte las 24 búsquedas reales (BQ) en evidencia revisable sobre experiencias laborales en México relacionadas con empresa, liderazgo, equipo, ambiente, contratación y la decisión de aceptar o rechazar una oferta.

## Reglas para los resultados y la evidencia

- Cada BQ puede registrar como máximo sus primeros 20 resultados elegibles. Si encuentra menos, registra solo esos. Nunca añade resultados irrelevantes para completar el cupo.
- Se usa un solo proveedor de búsqueda: Brave Search API, con `country=mx`, `search_lang=es` y un límite de hasta 20 resultados por consulta. No se hace scraping de Google. Cada registro conserva proveedor y fuente; los resultados de Brave no se mezclan con BQ-001 obtenida manualmente en Google, para no combinar fuentes en las conclusiones.
- Un título, enlace o snippet mostrado por el buscador cuenta como resultado, no como evidencia.
- Para registrar evidencia, se recupera contenido público de la URL y se conserva un fragmento copiado exactamente de ese contenido. Si no se puede acceder al contenido, se guarda la limitación y no se presenta el snippet como evidencia.
- Cada exclusión conserva una razón legible.
- El recorrido del MVP es: `BQ → resultado → contenido → evidencia → revisión → CSV`.

## Tecnología y datos mínimos

La implementación usa Python, SQLite y CSV. No incluye framework de agentes, dashboard, base vectorial ni escritura directa a Notion. La clave de Brave se proporciona mediante la variable de entorno `BRAVE_SEARCH_API_KEY`, nunca en el archivo de configuración ni en Git. El modo mock/fixture permite ejecutar pruebas sin credencial ni llamadas al proveedor.

La configuración contiene las 24 BQ y sus consultas. SQLite usa solo estas tres tablas:

- `runs`: identificador, inicio, fin, estado, proveedor, país, idioma y versión de configuración de cada ejecución.
- `search_results`: BQ, ejecución, empresa, consulta literal, posición, proveedor/fuente, fecha de búsqueda, país, correspondencia con México, URL original y normalizada, título, snippet del buscador, contenido público recuperado, estado de elegibilidad, estado de recuperación, limitación y razón de exclusión. La URL normalizada permite evitar duplicados dentro de la misma BQ.
- `evidence`: resultado de origen, URL, fragmento exacto y campos de análisis enumerados abajo.

Cada evidencia conserva: naturaleza; relación con la hipótesis; momento laboral; objeto; tema; tipo de relación; valoración; confianza; impacto; consecuencia; información aportada; información faltante; pregunta de investigación; fragmento exacto e hipótesis. La traza debe permitir volver desde la evidencia al contenido recuperado, su URL, resultado, BQ y ejecución.

El CSV de revisión usa una fila por evidencia y encabezados estables: `bq_id`, `run_id`, `result_id`, `url`, `titulo`, `estado_revision`, `limitacion_acceso`, `naturaleza`, `relacion_hipotesis`, `momento_laboral`, `objeto`, `tema`, `tipo_relacion`, `valoracion`, `confianza`, `impacto`, `consecuencia`, `informacion_aportada`, `informacion_faltante`, `pregunta_investigacion`, `fragmento_exacto`, `hipotesis`. Los encabezados se acuerdan antes de la primera exportación y no cambian entre ejecuciones.

## Entregas

1. Recolector de las 24 BQ y sus resultados elegibles.
2. Recuperación de contenido público y registro de evidencia trazable o de la limitación de acceso.
3. Clasificación de la evidencia con los campos mínimos definidos en este contrato.
4. Exportación CSV y revisión humana de resultados y evidencias.

## Aceptación del MVP

El MVP se acepta cuando se cumplen todos estos puntos:

1. Las 24 BQ se cargan desde configuración.
2. Cada BQ consulta Brave Search API en México y español y guarda hasta sus primeros 20 resultados elegibles, sin relleno artificial. La prueba mock/fixture corre sin credencial; la prueba real usa la variable de entorno.
3. Las URL se normalizan y se deduplican dentro de cada BQ.
4. Cada resultado excluido conserva su razón.
5. Cada evidencia contiene un fragmento exacto trazable a la URL y al contenido público recuperado; si este no estuvo disponible, consta la limitación.
6. Repetir una ejecución no duplica registros.
7. Se genera el CSV con el esquema de este contrato.
8. El flujo completo se prueba con el piloto real `BQ-002` usando Brave Search API; fixtures permiten probar el flujo sin credencial.

Este contrato define el límite del MVP. Cualquier función adicional requiere una decisión y un contrato posteriores.
