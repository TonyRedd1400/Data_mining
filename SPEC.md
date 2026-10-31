# SPEC.md — Sistema local de investigación de demanda (Directorio Laboral México)

Versión: 1.0 (diseño MVP) · Fecha: 2026-10-31 · Rama: `feature/bq-research-pipeline`

---

## 1. Propósito

Automatizar, en una máquina local con Windows, la ejecución de las **24 búsquedas de demanda
identificadas como BQ-001 a BQ-024**, capturando hasta **20 resultados elegibles por búsqueda**,
conservando el texto original sin modificación, clasificando con un LLM de forma auxiliar,
detectando duplicados, encolando casos dudosos para revisión humana, y exportando resultados
compatibles con las bases actuales de Notion (CSV/Excel).

Pipeline conceptual: **búsqueda → resultado → evidencia → hallazgo**.

## 2. Alcance (MVP)

Dentro del alcance:

1. Catálogo de 24 BQ definido en un archivo de configuración versionado (`config/queries.yaml`).
2. Ejecución de cada BQ contra un **proveedor de búsqueda intercambiable** (adaptador inicial: Brave Search API), país México, idioma español.
3. Captura de hasta 20 resultados **elegibles** por BQ; si hay menos, se registra el total real.
4. Almacenamiento inmutable del texto original: título, URL, descripción/snippet, posición, fecha de captura, proveedor y consulta exacta usada.
5. Clasificación LLM **auxiliar e intercambiable** (adaptador inicial: Gemini API Free): categoría de demanda, intención, puntaje de relevancia, confianza, justificación corta. El LLM nunca modifica ni reemplaza datos originales.
6. Detección de duplicados (normalización de URL + coincidencia de título+dominio) a nivel de BQ y global.
7. Cola de revisión humana para casos dudosos (baja confianza, conflicto de reglas, sospecha de duplicado no resuelto), gestionable desde un CSV de revisión o CLI simple.
8. Ejecución interrumpible y reanudable sin duplicar información (idempotencia por clave natural).
9. Exportación final a CSV y XLSX con estructura plana compatible con importación en Notion.
10. Registro de ejecución (log estructurado + tabla `runs`) para trazabilidad operativa.
11. Datos de ejemplo pequeños y anonimizados en el repositorio para pruebas offline (modo `--mock`).

Fuera del alcance (explícito):

- Scraping masivo de Google o de cualquier motor; evasión de captchas, autenticación, robots.txt o restricciones técnicas.
- Verificación/crawl profundo del contenido de cada página (leer la página completa, extraer precios, etc.). Solo se usa lo que devuelve la API de búsqueda.
- Multiusuario, servidor web, interfaz gráfica, base de datos cliente-servidor, orquestación en la nube.
- Escritura automática en Notion (la exportación es manual/semi-manual vía CSV/XLSX en el MVP).
- Programación recurrente (cron/agendador). La ejecución es manual por comando.
- Análisis estadístico, dashboards, visualizaciones.
- Soporte para países/idiomas distintos a México/español (el diseño los parametriza, pero el MVP solo opera en MX/es).
- Entrenamiento o fine-tuning de modelos.
- Más allá de los 24 BQ existentes: alta de nuevas BQ es editar el YAML, no una UI.

## 3. Usuarios y roles

| Rol | Descripción |
|---|---|
| Operador único | Persona que ejecuta el pipeline en su máquina Windows, configura claves, revisa la cola y exporta. |
| Revisor humano | Puede ser el mismo operador; decide sobre los ítems en `needs_review`. |

## 4. Entradas

1. **Catálogo de BQ** (`config/queries.yaml`): id BQ-001…BQ-024, consulta textual, metadatos (sector, tipo de ocupación, notas). *Las 24 consultas literales deben confirmarse/proveerse por el usuario; ver §10.*
2. **Configuración operativa** (`config/settings.yaml` + `.env`): proveedor activo, límites, umbrales de confianza, rutas.
3. **Claves de API**: solo vía variables de entorno (`.env` local, nunca en Git).

## 5. Salidas

1. Base local SQLite (`data/pipeline.db`) con todo el historial inmutable.
2. Exportaciones en `exports/`:
   - `resultados_BQ.csv/xlsx` — una fila por resultado elegible/no descartado, con columnas originales + clasificación.
   - `hallazgos.csv/xlsx` — una fila por hallazgo agregado por BQ.
   - `revision_pendiente.csv` — cola de revisión humana.
3. Bitácora: `logs/run_YYYYMMDD_HHMMSS.ndjson` + tabla `runs`.

## 6. Requisitos funcionales

| ID | Requisito | Prioridad |
|---|---|---|
| RF-01 | Ejecutar las 24 BQ contra el proveedor configurado con country=MX, lang=es. | Must |
| RF-02 | Capturar hasta 20 resultados elegibles por BQ; registrar `eligible_count` real aunque sea <20. | Must |
| RF-03 | Conservar intactos: título, URL, snippet/texto original, posición, fecha/hora UTC de captura, consulta exacta, proveedor. Campos originales nunca se sobrescriben tras su inserción. | Must |
| RF-04 | Clasificar cada resultado con LLM en campos separados (`llm_*`), sin tocar campos originales. | Must |
| RF-05 | Detectar duplicados: intra-BQ y entre BQ; marcar `duplicate_of` sin eliminar el registro. | Must |
| RF-06 | Encolar para revisión humana: confianza < umbral, clasificación inválida, duplicado dudoso, error LLM persistente. | Must |
| RF-07 | Permitir resolución de revisión (aprobar/rechazar/clasificar manualmente) y reflejarla en la exportación. | Must |
| RF-08 | Interrumpir (Ctrl+C) y reanudar: al reejecutar, no repetir llamadas ya exitosas ni insertar duplicados. | Must |
| RF-09 | Exportar CSV (UTF-8 con BOM) y XLSX compatibles con Notion (encabezados estables, sin fórmulas). | Must |
| RF-10 | Proveedor de búsqueda intercambiable mediante interfaz común (contract `SearchProvider`). | Must |
| RF-11 | LLM intercambiable mediante interfaz común (contract `Classifier`). | Must |
| RF-12 | Respetar cuota/rate limit del proveedor: intervalo mínimo entre llamadas, límite diario configurable, backoff ante 429/5xx. | Must |
| RF-13 | Modo `--mock` con datos fixture anonimizados para desarrollo y pruebas sin red ni claves. | Must |
| RF-14 | Validar la respuesta del proveedor/LLM antes de persistir (esquema, longitudes, enumeraciones). | Must |
| RF-15 | Generar evidencia (fragmento citado + offset) y hallazgo (síntesis por BQ) derivados, nunca sustitutos, del resultado. | Should |
| RF-16 | Registrar por qué un resultado devuelto por el proveedor **no** fue contado como elegible (motivo de descarte). | Should |
| RF-17 | Comando único por etapa: `run search`, `run classify`, `run review list/resolve`, `run export`. | Must |
| RF-18 | Toda escritura idempotente por clave natural `(provider, query_id, url_norm, position_bucket)` / hash de contenido. | Must |

## 7. Requisitos no funcionales

| ID | Requisito |
|---|---|
| RNF-01 | Ejecutable en Windows 10/11 con PowerShell 5.1+; Python ≥ 3.11. Sin dependencias de sistema binarias complejas (SQLite incluido en stdlib). |
| RNF-02 | ≤ 6 dependencias de terceros directas (ver ARCHITECTURE.md §dependencias). |
| RNF-03 | Costo: Brave Search API tier gratuito + Gemini API free tier; el MVP debe completarse dentro de cuotas gratuitas mensuales con 24 BQ × ≤20 resultados. |
| RNF-04 | Trazabilidad completa: cualquier fila exportada debe poder retrocederse a su BQ, resultado original y run. |
| RNF-05 | Inmutabilidad: los registros originales son append-only; las correcciones se expresan como estado/campos derivados, no como edición del original. |
| RNF-06 | Seguridad: sin secretos en código ni Git; `.env` excluido; repo público. |
| RNF-07 | Duración total de un ciclo completo (24 BQ + clasificación) < 30 min en hardware doméstico, dominado por rate limiting. |
| RNF-08 | Privacidad: no almacenar datos personales innecesarios; los snippets son texto público de motores de búsqueda. |

## 8. Elegibilidad de resultados (definición operativa)

Un resultado devuelto por el proveedor es **elegible** si cumple todas:

1. Tiene URL válida http(s) y título no vacío.
2. No es duplicado exacto ya contado dentro de la misma BQ (se marca como duplicado, no consume cupo).
3. No está en la lista de dominios bloqueados de `settings.yaml` (agregadores genéricos, redes sociales irrelevantes — lista mínima inicial, editable).
4. Pertenece al dominio temático según la regla de la BQ (campo `include_pattern`/`exclude_pattern` opcionales en el YAML de la BQ).

La elegibilidad determinista (reglas 1–4) se aplica **antes** de contar los 20. La clasificación LLM
(es decir, "este resultado realmente expresa demanda del tema") es posterior y puede degradar un
resultado elegible a `rejected` por irrelevancia, pero la decisión borderline va a revisión humana.
Los 20 se cuentan sobre elegibles deterministas; el análisis de demanda usa los aceptados.

## 9. Estados (resumen; detalle en DATA_MODEL.md)

- **BQ**: `pending → running → searching_done → classifying_done → review_open → ready_for_export → exported`; estados de error `failed_search`, `partial`.
- **Result**: `captured → classified → accepted | rejected | needs_review → reviewed_accepted | reviewed_rejected`; además flags `is_duplicate`, `duplicate_of`.
- **ReviewItem**: `open → resolved_accept | resolved_reject | resolved_reclassify | skipped`.
- **Run**: `started → completed | aborted | failed`.

## 10. Supuestos que requieren confirmación del usuario

| # | Supuesto | Impacto si es falso |
|---|---|---|
| A-1 | Las 24 consultas literales de BQ-001…BQ-024 serán proporcionadas (archivo o tabla); este diseño asume que existen con id, consulta y sector. | Bloquea RF-01; `queries.yaml` quedaría con placeholders. |
| A-2 | "20 resultados elegibles" significa paginar hasta reunir 20 elegibles deterministas (con tope duro de paginación de 60 resultados crudos por BQ por cuota). | Cambia lógica de conteo y consumo de cuota. |
| A-3 | Brave Search API tier gratuito vigente es suficiente (~2,000 búsquedas/mes en el plan histórico). Alternativa documentada: SearxNG autohospedado o Serper.dev. | Si la cuota real difiere, ajustar `max_daily_calls`. |
| A-4 | Gemini API Free permite uso de clasificación automatizada bajo sus términos para este fin (uso personal/interno, no redistribución de salidas como dato primario). | Podría exigir otro adaptador LLM. |
| A-5 | Las bases de Notion actuales aceptan importación CSV con estas columnas planas (sin relaciones automáticas); los nombres exactos de propiedades de Notion deben confirmarse para mapear encabezados. | Requiere ajustar mapa de exportación. |
| A-6 | Un "hallazgo" = síntesis por BQ (1..n) generada por LLM a partir de resultados aceptados, validable por humano. No existe otra definición previa del proyecto. | Cambiar granularidad de Findings. |
| A-7 | No se requiere conservar HTML crudo de páginas; solo los campos que devuelve la API. | Si se requiere, añadir fetch de página respetando robots.txt (fuera del MVP). |
| A-8 | El operador acepta revisar la cola manualmente en CSV/CLI (sin UI web). | Requeriría UI (fuera de alcance). |

## 11. Criterios de aceptación (nivel producto)

Ver ACCEPTANCE_TESTS.md para la lista ejecutable. Resumen:

1. Ejecución completa mock: 24 BQ → resultados → clasificación → exportación, sin red.
2. Idempotencia: reejecución tras interrupción no duplica filas ni repite llamadas ya hechas.
3. Inmutabilidad: ningún flujo actualiza columnas originales de `results`.
4. Duplicados: URLs repetidas intra e inter-BQ quedan marcadas con `duplicate_of`.
5. Revisión: items con confianza < umbral aparecen en la cola y su resolución cambia el estado final.
6. Exportación: CSV abre en Excel/Notion con codificación correcta y columnas contractuales.
7. Límites: BQ con <20 elegibles exporta `eligible_count` real; nunca se inventan resultados.
