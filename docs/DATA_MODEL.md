# DATA_MODEL.md — Modelo de datos, estados y contratos de persistencia

Versión: 1.1 (revisión C-01…C-08) · Fecha: 2026-10-31 · Motor: SQLite (archivo `data/pipeline.db`, WAL)

Convenciones: ids numéricos autoincrementales internos; claves naturales para idempotencia;
UTC siempre (`TEXT` ISO-8601 `YYYY-MM-DDTHH:MM:SSZ`); JSON en `TEXT`; snake_case; prefijo
`llm_`/`human_` marca el origen del valor. **Nada del modelo borra filas originales** (append-only).

---

## 1. Entidades y relaciones

```
queries (BQ) 1───N raw_results 1───┐
     │                             ├── N results 1───N classifications
     │            results N───1 url_index          │
     │                             ├── N evidence ──┤
     └── N findings N───M evidence (via JSON refs)  │
                    review_items 1───N review_events│
     runs 1───N (query_state_hist, quota_usage, error_log)
```

| Relación | Cardinalidad | Clave foránea |
|---|---|---|
| BQ → resultados crudos | 1:N | `raw_results.bq_id` |
| BQ → resultados elegibles | 1:N (≤20) | `results.bq_id` |
| Resultado → clasificaciones | 1:N (versiones) | `classifications.result_id` |
| Resultado → evidencias | 1:N | `evidence.result_id` |
| BQ → hallazgos | 1:N | `findings.bq_id` |
| Resultado → revisión | 1:N | `review_items.result_id` |
| Revisión → eventos humanos | 1:N | `review_events.review_id` |

---

## 2. Tablas

### 2.1 `queries` — catálogo de BQ (estado derivado convive con definición)

| Columna | Tipo | Regla |
|---|---|---|
| bq_id | TEXT PK | `BQ-001`…`BQ-024`, validado por CHECK regex |
| query_text | TEXT NOT NULL | Consulta literal original (fuente: queries.yaml) |
| sector | TEXT | Etiqueta de sector/ocupación |
| notes | TEXT | Notas del investigador |
| include_pattern / exclude_pattern | TEXT NULL | Regex de elegibilidad determinista |
| status | TEXT NOT NULL | Ver máquina de estados §3.1 |
| last_page_done | INTEGER DEFAULT 0 | Checkpoint de paginación |
| eligible_count | INTEGER DEFAULT 0 | Total real elegible capturado (≤20) |
| total_returned | INTEGER DEFAULT 0 | Crudos devueltos por proveedor |
| schema_version | TEXT | Versión del registro |
| created_at / updated_at | TEXT | UTC |

Idempotencia: fila creada por `INSERT OR IGNORE` desde YAML; reimportar YAML no pisa `status`.

### 2.2 `raw_results` — texto original del proveedor (INMUTABLE)

| Columna | Tipo | Regla |
|---|---|---|
| raw_id | INTEGER PK | |
| bq_id | TEXT FK→queries | |
| run_id | TEXT FK→runs | Quién/qué ejecución lo insertó |
| provider | TEXT | `brave` \| `mock` \| … |
| page | INTEGER | 1-based |
| rank_position | INTEGER | Posición global dentro de la BQ (1..N) |
| title_raw | TEXT NOT NULL | **Verbatim**, sin trim interno ni normalización |
| url_raw | TEXT NOT NULL | **Verbatim** como lo devolvió el proveedor |
| display_url | TEXT | Verbatim si el proveedor la da |
| snippet_raw | TEXT NOT NULL | **Verbatim, completo, sin truncar** — columna de texto íntegro; el sistema NO recorta, edita ni limita el original en ningún caso (corrección C-01) |
| snippet_len | INTEGER | Longitud en caracteres de `snippet_raw` (derivada, para observación; nunca criterio de filtrado de contenido) |
| site_name | TEXT NULL | |
| language / country_requested | TEXT | `es` / `mx` al momento de la llamada |
| captured_at_utc | TEXT NOT NULL | Momento de recepción |
| content_hash | TEXT NOT NULL | sha256(title_raw‖`\x1f`‖url_raw‖`\x1f`‖snippet_raw) sobre los campos **completos** |
| response_ref | TEXT | request_id del proveedor (auditoría) |

**Política de integridad del original (C-01):** no existe tope de almacenamiento de snippets. SQLite
soporta textos muy superiores a cualquier snippet real devuelto por un motor de búsqueda; el campo
se guarda íntegro y `content_hash` se calcula sobre el texto completo. Cualquier exportación que
necesite abreviar visualmente lo hace **solo en la capa de export**, marcando la celda con
`export_truncated=1`, jamás en la base.

**Clave natural de idempotencia (C-02):** UNIQUE(`bq_id`,`provider`,`page`,`rank_position`,`url_norm`)
⇒ reejecución = `INSERT ... ON CONFLICT DO NOTHING`. `url_norm` es derivada (`url_raw` normalizado
según ARCHITECTURE §7) y se calcula antes del insert. Usar `url_raw` en la clave permitiría filas
crudas duplicadas de la misma URL lógica cuando el proveedor varía parámetros de tracking entre
páginas/corridas; `url_norm` cierra ese hueco. El verbatim sigue garantizado porque `url_raw`
permanece como columna separada inmutable.
Trigger de inmutabilidad: ver §5.

### 2.3 `url_index` — normalización para deduplicación (derivada)

| Columna | Tipo | Regla |
|---|---|---|
| url_norm | TEXT PK | URL normalizada (reglas ARCHITECTURE §7) |
| url_sha | TEXT NOT NULL UNIQUE | sha256(url_norm) |
| host | TEXT | Para near-dup |
| first_seen_bq_id | TEXT | BQ dueña del "primer" uso |
| seen_in_bq_json | TEXT | JSON array de bq_ids |

### 2.4 `results` — resultado elegible contado (originales copiados + estado derivado)

| Columna | Tipo | Origen |
|---|---|---|
| result_id | INTEGER PK | interno |
| bq_id | TEXT FK | original-contexto |
| raw_id | INTEGER FK→raw_results | **apuntador al original; nunca copia editable** |
| url_norm | TEXT FK→url_index | derivado |
| position_eligible | INTEGER | 1..20, orden de elegibilidad dentro de BQ |
| is_eligible | INTEGER | siempre 1 aquí (los no elegibles quedan en raw + descarte) |
| discard_reason | TEXT NULL | solo en tabla `discards` (§2.5), no aquí |
| status | TEXT | máquina de estados §3.2 |
| is_duplicate | INTEGER 0/1 | derivado |
| duplicate_of_result_id | INTEGER NULL FK→results | derivado |
| dup_method | TEXT NULL | `exact_url` \| `near_title` |
| cross_duplicate | INTEGER 0/1 | aparece en otra BQ |
| first_run_id / last_run_id | TEXT | trazabilidad |
| created_at / state_changed_at | TEXT | UTC |

UNIQUE(`bq_id`,`url_norm`) WHERE `is_duplicate=0` ⇒ una URL cuenta una sola vez por BQ.

### 2.5 `discards` — por qué un crudo no consumió cupo (RF-16)

`discard_id PK, raw_id FK, bq_id, reason enum[invalid_fields, blocked_domain, pattern_excluded, duplicate_exact, beyond_cap], rule_ref, decided_at, method enum[rule, human]`

### 2.6 `classifications` — salida LLM (DERIVADA, versionada)

| Columna | Tipo | Regla |
|---|---|---|
| classification_id | INTEGER PK | |
| result_id | INTEGER FK | |
| prompt_version | TEXT | p. ej. `cls-v1` |
| model_id | TEXT | p. ej. `gemini-2.5-flash` (free) |
| llm_category | TEXT enum | taxonomía §4 |
| llm_demand_signal | TEXT enum | strong/moderate/weak/none/unclear |
| llm_relevance | REAL 0..1 | |
| llm_confidence | REAL 0..1 | base del routing |
| llm_rationale_es | TEXT ≤500 | justificación corta citando fragmentos existentes (límite aplica **solo a la salida del LLM**, campo derivado — nunca a datos originales) |
| llm_raw_output_ref | TEXT | payload bruto guardado en `llm_payloads` |
| classified_at | TEXT | |
| is_current | INTEGER | última versión vigente (1 por result+prompt_version) |

UNIQUE(`result_id`,`prompt_version`,`model_id`) ⇒ reclasificación crea nueva fila con otro `model_id`/versión o flip de `is_current`; jamás UPDATE destructivo.

**Restricción de vigencia reforzada en BD (C-04):** además de la UNIQUE anterior y de la lógica de
aplicación, existe un índice único parcial que garantiza a nivel de base de datos que no puede haber
dos clasificaciones vigentes para el mismo resultado y versión de prompt:

```sql
CREATE UNIQUE INDEX ux_classifications_current
  ON classifications(result_id, prompt_version) WHERE is_current = 1;
```

SQLite soporta índices parciales `WHERE`; si una segunda clasificación intenta quedar vigente, la
BD rechaza la escritura aunque falle la lógica de aplicación.

### 2.7 `llm_payloads` — auditoría de ida/vuelta (truncate 4 KB, sin secretos)

`payload_id PK, classification_id FK NULL, direction enum[req,resp], model_id, prompt_version, payload_json, created_at`

### 2.8 `evidence` — evidencia citable (derivada, append-only)

| Columna | Regla |
|---|---|
| evidence_id PK | |
| result_id FK | obligatorio |
| bq_id | redundante para consulta rápida |
| quote_text | **substring exacto** de `snippet_raw` o `title_raw` (validado con `quote IN original`) |
| quote_start / quote_end | offsets sobre el campo original referenciado |
| source_field | enum[title_raw, snippet_raw] |
| origin | enum[llm, human, rule] |
| note | comentario humano opcional |
| created_at, run_id | |

### 2.9 `findings` — hallazgo por BQ (derivada)

| Columna | Regla |
|---|---|
| finding_id PK | |
| bq_id FK | |
| statement_es | afirmación sintetizada (LLM o humano) |
| theme_tag | agrupador libre controlado |
| strength | enum[high, medium, low] |
| evidence_ids_json | array JSON de `evidence_id` (traza hacia results → raw) |
| origin | enum[llm, human] |
| status | enum[draft, validated, rejected] |
| model_id, prompt_version | si origin=llm |
| validated_by, validated_at | decisión humana |

UNIQUE parcial: no aplica; los hallazgos admiten varias versiones hasta validación.

### 2.10 `review_items` — cola de revisión humana

| Columna | Regla |
|---|---|
| review_id PK | estable (exporta/importa igual id) |
| result_id FK NULL | NULL si es revisión de nivel BQ/hallazgo |
| finding_id FK NULL | alternativamente |
| reason enum | low_confidence, invalid_llm_output, near_duplicate, cross_query_overlap, finding_validation, other |
| context_json | snapshot de campos relevantes (solo lectura) |
| status | enum[open, resolved_accept, resolved_reject, resolved_reclassify, skipped] |
| created_at, closed_at | |

### 2.11 `review_events` — bitácora append-only de decisiones humanas

`event_id PK, review_id FK, actor TEXT('human'), action enum, decision_category NULL, notes, decided_at`

### 2.12 `runs`, `query_state_hist`, `quota_usage`, `error_log` — operación

- `runs(run_id PK, started_at, finished_at, command, mode enum[live,mock], provider, model, status enum[started,completed,aborted,failed], stats_json)`
- `query_state_hist(id PK, bq_id, from_status, to_status, reason, run_id, at)` — auditoría de estados.
- `quota_usage(day TEXT, provider TEXT, calls INTEGER, llm_calls INTEGER, PRIMARY KEY(day,provider))` — cuota persistente.
- `error_log(error_id PK, run_id, bq_id NULL, result_id NULL, stage, error_type, message, payload_ref, occurred_at)`

### 2.13 `exports_manifest` — qué se exportó

`export_id PK, run_id, kind enum[resultados,hallazgos,revision], format enum[csv,xlsx], path, rows INTEGER, schema_hash, exported_at`

---

## 3. Máquinas de estado

### 3.1 `queries.status`

```
pending ──► searching ──► searching_done ──► classifying_done ──► review_open
   ▲            │                ▲                  │                 │
   │            ▼                │                  ▼                 ▼
   └───── failed_search      partial           (sin pendientes) ready_for_export ──► exported
                              (cuota/transitorio; reanudable)
```

Transiciones permitidas (CHECK + validadas en repo):

| Desde | Hacia | Disparador |
|---|---|---|
| pending | searching | inicio de etapa search |
| searching | searching_done | cupo 20 alcanzado o paginación agotada |
| searching | partial / failed_search | cuota/transitorio / error de esquema irrecuperable |
| partial | searching | reanudación |
| failed_search | searching | reintento explícito `--retry-failed` |
| searching_done | classifying_done | todos los results no-dup tienen clasificación vigente o descarte |
| classifying_done | review_open | existen needs_review abiertos |
| classifying_done / review_open | ready_for_export | cola vacía o marcada skip |
| ready_for_export | exported | export exitoso |
| cualquier estado ≥ searching_done | (prohibido volver a pending) | salvo reset manual documentado `dlm admin reset-bq` |

### 3.2 `results.status`

```
captured ──► classified ──► accepted ──────────────► (exportable)
                │  │              ▲
                │  ├─► rejected ──┘ (vía resolución humana que acepta)
                │  └─► needs_review ─► reviewed_accepted | reviewed_rejected | skipped_review
                └────► (duplicado: queda is_duplicate=1, status=rejected_dup, no compite)
```

Reglas: sólo `accepted`, `reviewed_accepted` y `rejected*` son terminales de exportación;
`needs_review` bloquea `ready_for_export` de su BQ hasta resolución o skip.

### 3.3 `review_items.status`: `open → resolved_accept | resolved_reject | resolved_reclassify | skipped` (terminal; reapertura = nuevo item referenciando el anterior).

### 3.4 `findings.status`: `draft → validated | rejected`; `rejected → draft` permitido con nota.

### 3.5 `runs.status`: `started → completed | aborted | failed` (terminal).

---

## 4. Taxonomía de clasificación LLM (`llm_category`)

Enum cerrado, validado en inserción (una reclasificación fuera de enum = `SchemaError`):

1. `demanda_laboral_directa` — oferta/demanda explícita de trabajo u ocupación.
2. `demanda_de_formacion` — cursos/capacitación orientada a empleo.
3. `demanda_emprendedora` — servicios/freelance/negocios locales.
4. `contexto_mercado` — noticias/estadísticas del mercado laboral.
5. `irrelevante` — sin relación.
6. `incierto` — el modelo declara ambigüedad (fuerza revisión).

`demand_signal` mide intensidad de demanda observada independientemente de la categoría.

---

## 5. Inmutabilidad reforzada (triggers e índices SQLite)

```sql
CREATE TRIGGER trg_raw_results_no_update BEFORE UPDATE OF
  title_raw, url_raw, display_url, snippet_raw, snippet_len,
  rank_position, captured_at_utc, content_hash
ON raw_results BEGIN SELECT RAISE(ABORT,'raw_results es inmutable'); END;

CREATE TRIGGER trg_raw_results_no_delete BEFORE DELETE ON raw_results
  BEGIN SELECT RAISE(ABORT,'raw_results es append-only'); END;

-- C-02: clave natural de idempotencia sobre URL normalizada (ver §2.2)
CREATE UNIQUE INDEX ux_raw_results_natural
  ON raw_results(bq_id, provider, page, rank_position, url_norm);

-- C-04: como máximo una clasificación vigente por (result_id, prompt_version)
CREATE UNIQUE INDEX ux_classifications_current
  ON classifications(result_id, prompt_version) WHERE is_current = 1;
```

`raw_results` incluye además la columna derivada `url_norm TEXT NOT NULL` (calculada en la inserción;
protegida contra UPDATE junto con las demás en una segunda variante del trigger si se editara).

(Análogo para `evidence.quote_text` y `review_events`.) Las columnas de estado/derivadas sí se
actualizan, siempre acompañadas de historia (`query_state_hist` / `review_events`).

---

## 6. Esquemas contractuales de exportación (Notion-friendly)

Encabezados estables; orden fijo; CSV UTF-8 con BOM; sin fórmulas; booleanos como `sí/no`.

### 6.1 `resultados_BQ.csv/xlsx` (una fila por resultado exportable)

```
bq_id | query_text | sector | posicion_elegible | titulo_original | url | dominio |
snippet_original | fecha_captura_utc | proveedor | categoria_llm | senal_demanda |
relevancia | confianza | justificacion_llm | modelo_llm | prompt_version |
estado_revision | decision_humana | categoria_humana | es_duplicado | duplicado_de_url |
cross_duplicate | run_id | result_id | export_schema_v
```

Nota: `titulo_original`, `url`, `snippet_original` provienen **exclusivamente** de `raw_results`
(vía JOIN a `raw_id`), nunca de copias editables.

### 6.2 `hallazgos.csv/xlsx`

```
bq_id | query_text | sector | hallazgo | tema | fuerza | evidencias_result_ids |
origen | estado | modelo_llm | validado_por | fecha_validacion | export_schema_v
```

### 6.3 `revision_pendiente.csv` (ida y vuelta)

```
review_id | bq_id | result_id | url | titulo_original | motivo | categoria_llm |
confianza | decision_humana | categoria_humana | notas
```

Mapeo a propiedades de Notion: archivo `config/notion_mapping.yaml` (nombre columna ↔ propiedad)
para renombrar encabezados sin tocar el núcleo — pendiente de nombres reales de las bases (A-5).

---

## 7. Migraciones y versionado

- `PRAGMA user_version` como número de migración; scripts SQL incrementales en `src/dlm/db/migrations/`.
- Cambios de esquema solo aditivos en el MVP; ruptura exige bump de `schema_version` por fila y script de respaldo previo (`dlm admin backup`).
- **Nota para la implementación (aún no hay código):** las correcciones C-01/C-02/C-04 se incorporan
  en la migración inicial del Hito 2 (`snippet_raw TEXT NOT NULL` sin tope, columna derivada
  `url_norm`, UNIQUE natural sobre `url_norm`, índice parcial `ux_classifications_current`). Como el
  pipeline todavía no existe, no se requiere migración correctiva ni re-cálculo de hashes; si al
  implementarse ya hubiera bases de prueba con datos truncados, esas bases se descartan y se regeneran
  en modo mock (los fixtures son deterministas).

## 8. Ejemplo de fila trazable (anonimizado, tipo fixture)

```json
{
 "raw_id": 101, "bq_id": "BQ-007", "provider": "mock", "page": 1, "rank_position": 3,
 "title_raw": "Cursos de soldadura industrial en Monterrey 2026",
 "url_raw": "https://ejemplo-ficticio.mx/soldadura-monterrey?utm_source=x",
 "snippet_raw": "Capacitación certificada con bolsa de trabajo local.",
 "captured_at_utc": "2026-10-31T10:15:00Z", "content_hash": "sha256:…"
}
```

Clasificación asociada (tabla separada): `{llm_category:"demanda_de_formacion", demand_signal:"moderate", relevance:0.82, confidence:0.61}` → `needs_review` (confidence entre 0.35 y 0.75).
