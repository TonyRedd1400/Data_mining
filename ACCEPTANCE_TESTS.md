# ACCEPTANCE_TESTS.md — Pruebas de aceptación verificables

Nivel: extremo a extremo, ejecutables por el operador. Salvo AT-10/AT-11 (live), **todas corren en
modo `--mock`** sin red ni claves. Cada prueba indica: propósito · precondiciones · pasos
(PowerShell) · criterio de éxito objetivo · cómo se verifica (comando/métrica).

Convenciones:
- `DB` = `data/pipeline.db`. Verificaciones SQL con `python -m dlm admin sql "<consulta>"` (helper de solo lectura) o `sqlite3` si está disponible.
- Todas las pruebas empiezan desde base limpia salvo donde se indique otra cosa: `Remove-Item data\pipeline.db* -ErrorAction SilentlyContinue`.

---

## AT-01 · Ciclo completo mock produce exportaciones consistentes
Propósito: el pipeline busca→clasifica→exporta de punta a punta sin credenciales.
Pasos:
```powershell
python -m dlm run all --mock
python -m dlm export all --format csv
```
Éxito:
1. `exports/resultados_BQ.csv` y `hallazgos.csv` existen y no están vacíos.
2. Encabezados idénticos al contrato DATA_MODEL §6.1/§6.2 (test golden diff).
3. Toda fila tiene `bq_id` válido BQ-001…BQ-024, `url` no vacía y `fecha_captura_utc` ISO-8601 Z.
4. Conteo por BQ: `posicion_elegible ≤ 20` siempre; ningún `bq_id` excede 20 filas con `es_duplicado=no`.
Verificación: `pytest tests/acceptance/test_at01.py` (incluye los checks 1–4).

## AT-02 · Catálogo completo: las 24 BQ quedan en estado terminal coherente
Propósito: RF-01/RF-02 sobre el catálogo íntegro.
Pasos: tras AT-01, ejecutar `python -m dlm status --json`.
Éxito:
1. 24 entradas; cada `status ∈ {ready_for_export, exported, review_open, partial}`.
2. Cada BQ reporta `eligible_count` igual al número real de `results` no duplicados (SQL: `SELECT q.bq_id,q.eligible_count,COUNT(r.result_id) FROM queries q LEFT JOIN results r ON r.bq_id=q.bq_id AND r.is_duplicate=0 GROUP BY q.bq_id HAVING eligible_count<>COUNT(...)` devuelve 0 filas).
3. Ninguna BQ tiene `eligible_count > 20`.

## AT-03 · Inmutabilidad del texto original
Propósito: RF-03/D-005 — ni clasificar ni revisar altera originales.
Pasos:
```powershell
python -m dlm run all --mock
python -m dlm admin hash-db            # imprime sha256 agregado de raw_results
python -m dlm run classify --mock      # segunda pasada
python -m dlm admin hash-db
```
Éxito: ambos hashes idénticos. Además intentar UPDATE prohibido falla:
`python -m dlm admin sql "UPDATE raw_results SET title_raw='X' WHERE raw_id=1"` → error `raw_results es inmutable`.

## AT-04 · Separación original vs LLM en la exportación
Propósito: RF-04 — columnas originales provienen del crudo, las de clasificación son independientes.
Pasos: fixture `tests/fixtures/mock_llm_conflict`: mismo resultado con dos clasificaciones (modelos A/B). Ejecutar classify + export.
Éxito: en `resultados_BQ.csv`, `titulo_original`/`snippet_original` coinciden byte a byte (JOIN vía `raw_id`) con `raw_results` pese a cualquier salida LLM; existe exactamente una fila vigente por resultado (`is_current=1`) y ambas clasificaciones permanecen en BD.

## AT-05 · Detección de duplicados intra-BQ e inter-BQ
Propósito: RF-05/D-006.
Entrada: fixture con (a) misma URL con y sin `?utm_source=` dentro de una BQ, (b) misma URL en BQ-003 y BQ-011, (c) títulos casi idénticos mismo dominio.
Éxito:
1. (a) segunda aparición: `is_duplicate=1, dup_method=exact_url, duplicate_of=<primer result_id>`; no consume cupo (eligible_count no aumenta).
2. (b) ambas filas conservadas, `cross_duplicate=1` en la segunda; export lo refleja.
3. (c) marcado `near_title` y genera `review_items.reason=near_duplicate`.
4. Ninguna fila eliminada: conteo total de `raw_results` estable.

## AT-06 · Cola de revisión humana (ida y vuelta por CSV)
Propósito: RF-06/RF-07.
Pasos:
```powershell
python -m dlm run all --mock                       # fixtures fuerzan confidence=0.6 → needs_review
python -m dlm review list                          # muestra los items abiertos
python -m dlm review export --out exports\revision_pendiente.csv
# humano edita: decision_humana=aceptar|rechazar en todas las filas
python -m dlm review import --in exports\revision_pendiente.csv
python -m dlm status --json                        # BQ pasan a ready_for_export
```
Éxito:
1. Items con confianza entre reject y accept thresholds aparecen en la cola automáticamente.
2. Tras importar, `review_items.status` termina en `resolved_*`; `results.status` pasa a `reviewed_accepted/reviewed_rejected`; existe fila en `review_events` por decisión.
3. Reimportar el mismo CSV es no-op (idempotencia por `review_id` + hash de decisión).
4. Con `open>0`, el export de resultados excluye esos `bq_id` hasta resolver/skip (o los marca explícitamente según flag `--include-open-review`).

## AT-07 · Detener y reanudar sin duplicar (interrupción real)
Propósito: RF-08/D-002.
Pasos:
```powershell
python -m dlm run search --mock --limit-bqs 12   # simula corrida parcial (o Ctrl+C manual durante run all)
# registrar contajes:
python -m dlm admin sql "SELECT COUNT(*) FROM raw_results"
python -m dlm run all --mock                      # reanudación
python -m dlm admin sql "SELECT COUNT(*) FROM raw_results"
```
Éxito:
1. Tras reanudar, no hay violaciones de UNIQUE ni filas duplicadas por `(bq_id,url_norm)`; el conteo final equals una corrida completa de referencia (fixture determinista: `expected_counts.json`).
2. `queries.last_page_done` nunca retrocede; BQ ya `searching_done` no generan llamadas nuevas (verificable en `quota_usage.calls` == calls de una corrida completa).
3. Interrupción con Ctrl+C deja `runs.status='aborted'` y transacción íntegra (PRAGMA integrity_check ok).

## AT-08 · Cuota insuficiente degrada ordenadamente
Propósito: §8 errores, D-013.
Pasos: fixture `provider_quota_exhausted` (adaptador mock lanza `QuotaExceeded` en BQ-015); `python -m dlm run search --mock`.
Éxito: BQ-001…014 completas; BQ-015+ en `partial`; exit code ≠ 0 con mensaje claro; reintento posterior (`run search --mock`) continúa desde BQ-015 sin tocar las primeras (mismo chequeo de AT-07-2). No se lanzan más de `max_daily_provider_calls` llamadas (tabla `quota_usage`).

## AT-09 · Menos de 20 elegibles reales se registra honestamente
Propósito: RF-02 "si existen menos, se registra el total real".
Entrada: fixture BQ-021 devuelve 12 crudos, 9 elegibles.
Éxito: `eligible_count=9`; export contiene 9 filas para BQ-021; no se rellena ni repite nada; `total_returned=12` y `discards` explica las 3 no elegibles (motivo enum válido).

## AT-10 · Live smoke con Brave (manual, con claves locales)
Precondición: `.env` con claves válidas (no commiteadas).
Pasos: `python -m dlm run search --bq BQ-001` ; `python -m dlm run classify --bq BQ-001` ; `python -m dlm export resultados`.
Éxito: 1 sola BQ real fluye end-to-end; HTTP 200 mapeado a contrato canónico; campos originales verbatim vs respuesta JSON del proveedor (diff manual documentado en checklist); cero secretos en logs (`Select-String -Path logs\*.ndjson -Pattern "Bearer|API_KEY"` sin resultados).
Frecuencia: una vez por hito M4 y ante cambio de adaptador.

## AT-11 · Intercambiabilidad de proveedor y modelo (contratos)
Propósito: RF-10/RF-11 — probar que el núcleo no está acoplado.
Pasos: en modo mock, correr `run all` con `settings.provider=mock_a` y `llm.model=mock_x`; luego con `mock_b`/`mock_y` sobre BD nueva.
Éxito: ambos ciclos completan sin cambios de código; las salidas difieren solo en campos derivados y en `provider`/`modelo_llm`; esquema de exportación idéntico. Un tercer check: sustituir el adaptador requiere únicamente un nuevo módulo que implemente la interfaz + registro en config (problema de compilación/prueba unitaria `test_provider_registry`).

## AT-12 · Compatibilidad Notion del CSV/XLSX
Pasos: abrir `resultados_BQ.csv` en Excel (o `Import-Csv` PS) y validar: decodificación UTF-8 correcta (acentos ñ), sin comas embebidas rotas, booleanos `sí/no`, fechas legibles. Generar XLSX: `python -m dlm export all --format xlsx` abre sin reparación. Checklist manual de import en Notion con una copia de prueba de la base real (solo con datos mock).
Éxito: 0 celdas corruptas; tipos inferidos aceptables; mapeo `notion_mapping.yaml` aplicado renombra encabezados según propiedades confirmadas (A-5).

## AT-13 · Trazabilidad inversa desde cualquier fila exportada
Pasos: tomar 5 filas al azar del export (`Get-Random`); para cada `result_id`, reconstruir cadena `findings.evidence_ids_json → evidence.result_id → results.raw_id → raw_results.*` con `dlm trace <result_id>`.
Éxito: la traza reproduce bq_id, query_text exacto, url_raw verbatim, posición, captured_at, run_id y clasificaciones vigentes; cada `quote_text` de evidencia es substring verificado de su campo original (`dlm verify` devuelve OK para toda la BD mock).

## AT-14 · Seguridad del repositorio público
Pasos (pre-commit): escaneo `python -m dlm admin secret-scan` + revisión manual de `git ls-files`.
Éxito: no hay `.env` rastreado (solo `.env.example` con valores ficticios); no hay `*.db`, `exports/*`, `logs/*` rastreados; no hay fixtures con URLs/datos personales reales (todos los dominios fixture usan `ejemplo-ficticio.*` u `example.com/.mx`); el escáner regex (sk-, AIza, Bearer, pk/live keys) sale limpio.

## AT-15 · Regresión de elegibilidad determinista (unit-level acceptance)
Casos tabla-driven (golden): URL vacía/nula → discard `invalid_fields`; dominio bloqueado → `blocked_domain`; patrón exclude de la BQ → `pattern_excluded`; página agotada antes del cupo → cierre con total real; tope 20 → `beyond_cap` desde la 21ª elegible.
Éxito: `pytest tests/acceptance/test_eligibility_table.py` verde; las decisiones quedan registradas en `discards` con `rule_ref` apuntando la regla.

---

### Matriz requisito → prueba

| Requisito | Pruebas |
|---|---|
| RF-01/02 | AT-01, AT-02, AT-09, AT-10 |
| RF-03/04 | AT-03, AT-04, AT-13 |
| RF-05 | AT-05 |
| RF-06/07 | AT-06 |
| RF-08 | AT-07, AT-08 |
| RF-09 | AT-01, AT-12 |
| RF-10/11 | AT-11 |
| RF-12 | AT-08 |
| RF-13 | todas (salvo AT-10/11-live) |
| RF-14 | AT-08, AT-15, unit tests de esquemas |
| RF-15/16 | AT-09, AT-13, AT-15 |
| RF-17/18 | AT-06, AT-07 |
| RNF-06 / seguridad | AT-14 |
