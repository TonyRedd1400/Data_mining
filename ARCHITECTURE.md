# ARCHITECTURE.md — Sistema local de investigación de demanda

Versión: 1.1 (revisión C-01…C-08) · Fecha: 2026-10-31 · Companion de SPEC.md

---

## 1. Vista general

Aplicación **CLI monolítica en Python** que se ejecuta en Windows/PowerShell, con un núcleo de
orquestación y cuatro capas de adaptadores intercambiables. Persistencia única en **SQLite**
(archivo local). Sin servidor web, sin colas externas, sin contenedores.

```
                    ┌──────────────────────────────────────────┐
                    │              CLI (dlm run ...)           │
                    └───────┬──────────┬──────────┬────────────┘
                            │          │          │
                 ┌──────────▼──┐ ┌─────▼─────┐ ┌──▼───────────┐
                 │ SearchStage │ │ClassifyStg│ │  ExportStage │   Orquestación
                 └──────┬──────┘ └─────┬─────┘ └──▲───────────┘   (etapas idempotentes)
                        │              │          │
        ┌───────────────▼──┐   ┌───────▼───────┐  │
        │ SearchProvider   │   │  Classifier   │  │  Contratos (interfaces)
        │ (interfaz única) │   │ (interfazúnica)│ │
        ├──────────────────┤   ├───────────────┤  │
        │ BraveAdapter     │   │ GeminiAdapter │  │
        │ MockAdapter      │   │ MockAdapter   │  │
        │ (futuro: Serper) │   │ (futuro: otro)│  │
        └──────────────────┘   └───────────────┘  │
                        │              │          │
                 ┌──────▼──────────────▼──────────┴──┐
                 │        Repository (SQLite)        │  Persistencia append-first
                 │  + Deduplicator + ReviewQueue     │  Servicios de dominio
                 └───────────────────────────────────┘
```

Principios rectorales:

1. **Etapas separadas e idempotentes**: `search`, `classify`, `review`, `export` son comandos independientes; cada uno puede fallar/repetirse sin corromper el estado global.
2. **Original vs derivado**: los datos del proveedor viven en tablas/columnas *originales* inmutables; toda clasificación o marca vive en columnas/tablas *derivadas*. Nunca se escribe sobre un original.
3. **Interfaz antes que implementación**: el orquestador solo conoce `SearchProvider` y `Classifier`; el proveedor/modelo activo se resuelve por configuración.
4. **Detener y reanudar**: el progreso se persiste por unidad de trabajo (por BQ y por resultado), no al final del lote.

## 2. Componentes y responsabilidades

| # | Componente | Responsabilidad | No hace |
|---|---|---|---|
| C1 | **CLI / Entry point** (`dlm`) | Parsear comandos y flags (`--mock`, `--bq BQ-007`, `--dry-run`), cargar config, invocar etapas, imprimir resumen, manejar Ctrl+C limpiamente. Incluye `dlm menu`: vista de menú numerado que despacha a estos mismos subcomandos para el operador no técnico (C-05/RFC RF-19). | Lógica de negocio. |
| C2 | **Config Loader** | Fusionar `config/settings.yaml` + variables de entorno (`.env` vía carga simple); validar esquema; exponer objeto de configuración inmutable. | Guardar secretos fuera de env. |
| C3 | **Query Catalog** | Leer `config/queries.yaml`; validar ids BQ-001…BQ-024 únicos; expeditar lista de BQ pendientes según estado en DB. | Llamar APIs. |
| C4 | **Orchestrator (SearchStage)** | Por cada BQ pendiente: paginar al proveedor, aplicar elegibilidad, persistir resultados crudos+elegibles, actualizar estado BQ y `eligible_count`. Respetar rate limiter. | Clasificar; exportar. |
| C5 | **SearchProvider (interfaz)** + **BraveAdapter**, **MockAdapter** | Contrato único `search(query, page, country, lang) -> RawSearchResponse`. Mapear respuesta nativa → modelo canónico. Reportar errores tipificados (`QuotaExceeded`, `TransientError`, `AuthError`, `SchemaError`). | Decidir elegibilidad; persistir. |
| C6 | **Eligibility Rules** | Función pura: `evaluate(raw_result, bq_rules, seen_urls) -> eligible | rejected(reason) | duplicate(key)`. | Llamar red; usar LLM. |
| C7 | **Deduplicator** | Normalizar URL (esquema www/tracking params/case/slash final); clave `sha256(url_norm)`; detectar duplicados intra-BQ e inter-BQ; registrar `duplicate_of`. | Eliminar filas. |
| C8 | **Orchestrator (ClassifyStage)** | Para cada resultado `captured` no duplicado: construir prompt canónico desde campos originales, llamar `Classifier`, validar salida, escribir fila en `classifications` y transición de estado. Batch pequeño, retries con backoff. | Modificar originales. |
| C9 | **Classifier (interfaz)** + **GeminiAdapter**, **MockAdapter** | Contrato `classify(payload) -> ClassificationResult` con salida estructurada (JSON validado contra enum). | Acceder a la DB; decidir revisión. |
| C10 | **Confidence Router** | Aplicar umbrales: ≥ accept-threshold → `accepted`; ≤ reject-threshold y relevancia baja → `rejected`; en medio o error de esquema tras reintentos → `needs_review` + crea `ReviewItem`. | Revisar manualmente. |
| C11 | **Evidence Builder** | Derivar evidencia citable: snippet/texto relevante + offsets + vínculo al resultado y su clasificación. Append-only. | Inventar texto (solo recorta originales). |
| C12 | **Findings Synthesizer** | Por BQ con clasificación completa: pedir al `Classifier` una síntesis 1..n hallazgos referenciando evidencias; guardar como borrador pendiente de validación humana. | Auto-publicarse a Notion. |
| C13 | **Review Queue** | `list/export/import/resolve` de ítems dudosos; cada resolución es una fila append en `review_events` y actualiza estado derivado. | Bloquear exportación de ítems ya resueltos. |
| C14 | **Export Stage** | Proyectar vistas planas a CSV (UTF-8-BOM) y XLSX; encabezados contractuales; columna de versionado de esquema; filtrar según estados finales. | Transformar texto original. |
| C15 | **Repository (SQLite)** | DDL/migraciones simples; transacciones por unidad de trabajo; claves únicas de idempotencia; WAL mode. | Reglas de negocio. |
| C16 | **Rate Limiter / Quota Guard** | Intervalo mínimo entre llamadas, tope diario por proveedor, contador persistente; pausa cooperativa cuando queda poca cuota. | Adivinar cuotas remotas. |
| C17 | **Logger/Audit** | NDJSON por run + tabla `runs`/`run_stats`; correlación `run_id ↔ bq_id ↔ result_id`. | Loggear secretos. |

## 3. Flujo de datos

```
queries.yaml ──► QueryCatalog ──► [BQ pending]
                                     │
        SearchStage (C4) ◄── RateLimiter (C16)
             │  provider.search(...)            (Brave / Mock)
             ▼
        RawSearchResponse (canónico)
             │  EligibilityRules (C6) + Deduplicator (C7)
             ▼
        SQLite: raw_results (inmutable) + results (elegibles, posición, fecha)
             │
        ClassifyStage (C8) ── Classifier (C9, Gemini/Mock)
             ▼
        SQLite: classifications (derivado) ── ConfidenceRouter (C10)
             │                         │
             ▼                         ▼
        accepted/rejected        needs_review → ReviewItem (C13)
             │                         │  humano: resolve
             ▼                         ▼
        EvidenceBuilder (C11) ──► evidence ◄── review_events
             │
        FindingsSynthesizer (C12) ──► findings (borrador → validado)
             │
        ExportStage (C14) ──► exports/resultados_BQ.csv|xlsx, hallazgos.csv|xlsx, revision_pendiente.csv
```

Regla de oro del flujo: **cada flecha persiste antes de la siguiente**; ninguna etapa depende de
mantener estado en memoria entre ejecuciones.

## 4. Trazabilidad

Identificadores y enlaces obligatorios en toda fila derivada:

- `run_id` (UUID por ejecución) en cada escritura.
- `bq_id` en todo lo generado por una búsqueda.
- `result_id` en clasificaciones, evidencias, revisiones y hallazgos (los hallazgos guardan `evidence_ids[]` serializado JSON).
- Columnas exportadas que permiten reconstruir la cadena: `bq_id, query_text, url, position, captured_at_utc, provider, classification_id, llm_model, review_status`.
- Hashes: `content_hash = sha256(title_raw ‖ \x1f ‖ url_raw ‖ \x1f ‖ snippet_raw)` sobre los campos **originales completos y verbatim** (sin lower(), sin normalizar, sin truncar — C-01; el separador `\x1f` evita colisiones de concatenación) para auditar que el original no cambió; verificación opcional `dlm verify`.

## 5. Separación original / derivado

| Datos originales (append-only) | Datos derivados (editables por estado, nunca destructivos) |
|---|---|
| title, url, display_url, snippet, rank_position, site, language, country, provider, query_text, captured_at | llm_category, llm_relevance, llm_confidence, llm_rationale, status, is_duplicate, duplicate_of, review_* , finding refs |

Mecánica: los originales viven en `raw_results`/`results` con UPDATE prohibido por convención y
reforzado por triggers SQLite (`BEFORE UPDATE OF title, url, snippet … RAISE ABORT`) — ver
DATA_MODEL.md §triggers. Las clasificaciones viven en tablas separadas con FK al original.

## 6. Idempotencia y reanudación

1. **Claves naturales únicas** (todas sobre `url_norm`, corregido C-02 para que sea consistente con DATA_MODEL §2.2/§5):
   - `raw_results`: UNIQUE(`bq_id`, `provider`, `page`, `rank_position`, `url_norm`) — la URL normalizada evita crudos duplicados de la misma URL lógica cuando cambian los parámetros de tracking entre páginas o corridas; `url_raw` verbatim sigue almacenándose como columna inmutable aparte.
   - `results`: UNIQUE(`bq_id`, `url_norm`) — un resultado cuenta una vez por BQ.
   - `classifications`: UNIQUE(`result_id`, `prompt_version`, `model_id`) — re-clasificar crea versión nueva, no borra.
   - Inserciones con `INSERT ... ON CONFLICT DO NOTHING` ⇒ reejecución no duplica.
2. **Checkpoint por BQ**: `queries.status` + `queries.last_page_done`. Al reanudar, `search` continúa desde `last_page_done + 1` y salta BQ cuyo estado ya es ≥ `searching_done`.
3. **Unidad de transacción**: 1 página de proveedor = 1 transacción (crudos + elegibles + checkpoint). Ctrl+C entre páginas pierde máximo "una llamada" y esa llamada se re-intenta (su inserción es idempotente).
4. **Cuota diaria persistente**: tabla `quota_usage(day, provider, calls)`; el QuotaGuard consulta la DB, no memoria.
5. **Anti-repetición de llamadas LLM**: antes de clasificar, se comprueba existencia de `classification` vigente para `(result_id, prompt_version, model_id)`.
6. **Comandos con `--dry-run`**: muestran planes ("quedarían 7 BQ pendientes, 43 resultados por clasificar") sin escribir.

## 7. Estrategia de deduplicación

Capas sucesivas (baratas primero):

1. **Normalización de URL** → `url_norm`: lowercase host, quitar `www.`, eliminar parámetros de tracking conocidos (`utm_*`, `gclid`, `fbclid`, `ref`), quitar fragmento `#`, slash final consistente. Clave = `sha256(url_norm)`. Exact-dup por esta clave.
2. **Colisión near-dup intra-BQ**: mismo `host` + similitud de título (Jaccard sobre tokens normalizados ≥ 0.9) ⇒ marcado `is_duplicate=1, dup_method='near_title'`, **va a revisión si consume cupo** (no se descarta silenciosamente).
3. **Inter-BQ**: si `url_norm` ya existe en otra BQ, se conserva en ambas pero registra `seen_in_bq[]`; el export marca `cross_duplicate=1` para que Notion pueda filtrar.
4. Los duplicados **nunca se borran**: se marcan y no consumen el cupo de 20.

## 8. Manejo de errores, límites y reintentos

Errores tipificados por capa:

| Error | Detección | Acción |
|---|---|---|
| `AuthError` (401/403 proveedor o LLM) | HTTP status | Abortar etapa inmediatamente (no reintentar); mensaje claro: revisar `.env`. |
| `QuotaExceeded` (429 o cuota local agotada) | Status / contador | Backoff exponencial con techo (3 intentos: 5s→15s→60s); si persiste, pausar run cooperativamente y dejar BQ en `partial` reanudable. |
| `TransientError` (timeout, 5xx, red) | Excepción | Hasta 3 reintentos con jitter; luego `partial`. |
| `SchemaError` (respuesta mal formada) | Validación pydantic/dict-check | Registrar en `errors` con payload truncado; marcar BQ `failed_search`; continuar con la siguiente BQ (fallo aislado). |
| `LLMInvalidOutput` | JSON inválido / enum desconocido | Reintentar 1 vez con temperature 0 y recordatorio de esquema; si falla, `needs_review` (el humano decide). |
| Corrupción/constraint violation | sqlite3.IntegrityError | Se considera éxito idempotente si viola UNIQUE natural; abortar transacción contraria y loggear. |

Límites globales (defaults en `settings.yaml`):

- `max_eligible_per_bq: 20`, `max_raw_pages_per_bq: 3` (≈60 crudos), `page_size: 20`
- `min_call_interval_sec: 1.2`, `max_daily_provider_calls: 100`, `max_daily_llm_calls: 500`
- `http_timeout_sec: 10`, `llm_max_output_tokens` fijo por modelo
- `confidence_accept: 0.75`, `confidence_reject: 0.35` (entre ambos → revisión)
- **Sin tope de almacenamiento de snippets (C-01):** `raw_results.snippet_raw` guarda el texto completo verbatim; no se trunca nada en la capa de persistencia. El único truncamiento con flag (`export_truncated`) ocurre, si acaso, en la capa de exportación para legibilidad, y nunca afecta a la base ni al hash. Los payloads de auditoría LLM (`llm_payloads`) sí tienen tope de 4 KB porque son copia de conveniencia, no original.

Política general: **un fallo nunca detiene las 24 BQ completas**; el pipeline degrada a `partial`
y reporta al final cuántas BQ quedaron pendientes de reintentar.

## 9. Contratos entre componentes (señales, no código)

### 9.1 `SearchProvider`

```
name: str
capabilities: {supports_pagination: bool, returns_snippet: bool}

search(req: SearchRequest) -> SearchResponse
  SearchRequest: { query: str, page: int(1-based), per_page: int≤20,
                   country: 'MX', language: 'es', safe_search: 'moderate' }
  SearchResponse: { items: [RawItem], total_estimated: int|null,
                    provider_meta: {request_id, credits_used} }
  RawItem: { title, url, display_url, snippet, rank_position, site_name }
Raises: AuthError | QuotaExceeded | TransientError | SchemaError
```

### 9.2 `Classifier`

```
name: str ; model_id: str ; PROMPT_VERSION: str
classify(req: ClassifyRequest) -> ClassificationResult
  ClassifyRequest: { bq_id, query_text, item: {title,url,snippet,position},
                    taxonomy: [Category...], prompt_version }
  ClassificationResult: { category: enum, relevance: float[0..1],
                         confidence: float[0..1], rationale: str≤500,
                         demand_signal: enum[strong|moderate|weak|none|unclear],
                         raw_response_ref }
synthesize(req: SynthesizeRequest) -> list[FindingDraft]
  FindingDraft: { statement_es, evidence_refs: [result_id], theme_tag, strength }
Raises: AuthError | QuotaExceeded | TransientError | LLMInvalidOutput
```

Invariantes del contrato: el adaptador **recibe copias** de los campos originales y sus salidas
solo pueden escribirse en tablas derivadas; ningún método devuelve datos "listos para sobrescribir"
originales; toda salida incluye `model_id` y `prompt_version` para auditoría.

### 9.3 Etapas ↔ Repository

Cada etapa expone `run(ctx) -> StageReport {processed, skipped_existing, failed, quota_used}`.
El repository ofrece únicamente: `insert_ignore`, `get_state`, `set_derived_field`, `append_event`.
No hay API genérica de UPDATE sobre originales.

### 9.4 Cola de revisión (formato CSV contractual)

`review_id, bq_id, result_id, url, title, reason, llm_category, llm_confidence,
 human_decision(aceptar|rechazar|reclasificar), human_category, notes`
La importación aplica decisiones por `review_id` y genera `review_events` (append-only).

## 10. Estructura de repositorio propuesta (raíz del proyecto)

```
/ (root del repo Data_mining)
├── SPEC.md  ARCHITECTURE.md  DATA_MODEL.md  DECISIONS.md
├── ACCEPTANCE_TESTS.md  IMPLEMENTATION_PLAN.md  README.md
├── .gitignore  .env.example  requirements.txt  pyproject.toml
├── config/
│   ├── queries.yaml          # 24 BQ (versionado, sin datos sensibles)
│   └── settings.yaml         # límites, umbrales, proveedor activo
├── src/dlm/                  # paquete principal (nombre interno: dlm)
│   ├── cli.py  config.py  catalog.py
│   ├── stages/ (search.py classify.py review.py export.py)
│   ├── providers/ (base.py brave.py mock.py)
│   ├── llm/ (base.py gemini.py mock.py prompts/)
│   ├── domain/ (eligibility.py dedup.py routing.py evidence.py findings.py)
│   ├── db/ (schema.sql repo.py migrations.py)
│   └── observability/ (logging.py limits.py)
├── data/                     # pipeline.db  (gitignored)
├── exports/                  # salidas (gitignored)
├── logs/                     # ndjson (gitignored)
├── scripts/                  # PowerShell de un paso para operador no técnico (C-05): setup.ps1, run_mock.ps1, run_completo.ps1, revision_export.ps1, revision_import.ps1, exportar.ps1
├── tests/
│   ├── fixtures/             # respuestas mock anonimizadas (sí versionadas)
│   ├── unit/  integration/  acceptance/
└── docs/adr/                 # ADR espejo resumido de DECISIONS.md
    └── GUIA_OPERACION.md     # guía paso a paso con capturas (Hito 9, C-05)
```

Nota sobre `dlm menu` (C-05): el menú interactivo de consola vive en `cli.py` como una vista delgada
que despacha a los mismos subcomandos; no introduce lógica nueva ni dependencias.

## 11. Dependencias (pocas, deliberadamente)

| Paquete | Uso | Justificación |
|---|---|---|
| `requests` (o stdlib `urllib` wrapper propio) | HTTP a APIs | Simple, ubicuo. |
| `pydantic` v2 | Validación de esquemas de entrada/salida | RF-14 sin costo de diseño. |
| `PyYAML` | configs | Legibilidad del catálogo de BQ. |
| `openpyxl` | XLSX | Único requisito de Excel; CSV con stdlib. |
| `python-dotenv` | Carga de `.env` | Segregación de secretos. |
| `pytest` (dev) | Pruebas | Estándar. |

SQLite, argparse, hashlib, sqlite3 triggers: stdlib. El cliente de Gemini se implementa contra su
REST API con HTTP directo para evitar SDK pesado (decisión D-008, reversible).

## 12. Seguridad y cumplimiento operativo

- Secretos exclusivamente en `.env` local; `.env.example` con valores ficticios (`BRAVE_API_KEY=<PEGA_TU_CLAVE_AQUI>`).
- Ningún log imprime headers de autenticación; sanitizer en logger.
- Solo se almacena contenido público devuelto por APIs oficiales; sin evasión de robots/captchas; respetar ToS de Brave y Google Gemini.
- Repo público: CI/pre-commit debe incluir escaneo simple de patrones de clave (regex de `sk-`,Bearer tokens) antes de commit.

## 13. Windows / PowerShell

Todos los comandos documentados usan PowerShell 5.1+:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env    # luego editar con claves reales (nunca commitear)
python -m dlm run search --mock
python -m dlm run classify --mock
python -m dlm review list
python -m dlm export all --format csv
```

Manejo de Ctrl+C: el CLI captura `KeyboardInterrupt`, cierra transacción pendiente (rollback),
marca `runs.status='aborted'` y sale con resumen reanudable.
