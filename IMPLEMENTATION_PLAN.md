# IMPLEMENTATION_PLAN.md — Plan de implementación por hitos pequeños

Cada hito produce algo **usable y verificable**, commiteado en `feature/bq-research-pipeline`.
Tamaño objetivo: 1 sesión de trabajo (2–6 h). No se avanza de hito con su prueba en rojo.
Los comandos citados son PowerShell y corresponden a la arquitectura ya diseñada.

---

## Hito 0 · Preparación del repositorio (sin código de producto)
Objetivo: repo limpio para el nuevo proyecto, seguro para ser público.
- Borrar notebooks heredados (`Datos.ipynb`, `Mineria de Datos.ipynb`, `POO.ipynb`) — quedan en historial (D-017).
- Crear desde la raíz: `.gitignore` (`data/`, `exports/`, `logs/`, `.env`, `*.db*`, `.venv/`, `__pycache__/`), `.env.example` (variables ficticias: `BRAVE_API_KEY=reemplazar-en-local`, `GEMINI_API_KEY=reemplazar-en-local`), `README.md` mínimo, estructura vacía de `config/ src/dlm/ tests/fixtures/ docs/adr/`.
- Copiar los 6 documentos de diseño; ADRs espejo de decisiones cerradas.
Verificación: `git ls-files` no lista `.env` ni datos operativos; escáner regex de secretos limpio (base de AT-14); rama correcta; cero trabajo en `main`.
Commit sugerido: `chore: preparar repositorio para pipeline de investigación BQ`.

## Hito 1 · Esqueleto CLI + configuración + catálogo de BQ
Objetivo: `dlm` instalable y un catálogo validado; base del soporte al operador (C-05).
- `pyproject.toml` (entry point `dlm`), `src/dlm/cli.py` con subcomandos stub (`run search|classify|review|export|status|admin`), flags globales `--mock --dry-run --bq`.
- `config.py`: carga YAML + `.env` (dotenv), validación pydantic, objeto inmutable.
- `catalog.py` + `config/queries.yaml` con las 24 BQ (**placeholders legibles hasta recibir A-1**).
- Lockfile D-016 y logger NDJSON con sanitizer de secretos.
- **Esqueleto de accesibilidad (C-05):** comando stub `dlm menu` (menú numerado que delega a los subcomandos) y carpeta `scripts/` con `setup.ps1` funcional y los demás scripts como stubs que ya invocan los comandos correctos (se completan en el hito correspondiente).
Verificación: `python -m dlm status --mock` imprime tabla de 24 BQ en estado `pending`; unit tests de validación de ids/patrones; `--dry-run` no escribe; `dlm menu` lista opciones sin ejecutar nada destructivo.

## Hito 2 · Capa de persistencia (esquema + repository + triggers)
Objetivo: BD fiel a DATA_MODEL antes de cualquier integración.
- `schema.sql` completo + migraciones (`PRAGMA user_version`), funciones `repo.insert_ignore/get_state/set_derived_field/append_event`.
- Triggers de inmutabilidad (§5 DATA_MODEL) y CHECKs de enums/estados; transiciones de estado centralizadas en `state.py` con matriz legal.
- **Índices contractuales (C-02/C-04):** UNIQUE natural de `raw_results` sobre `(bq_id,provider,page,rank_position,url_norm)` e índice único parcial `ux_classifications_current WHERE is_current=1`, ambos creados en la migración inicial.
- Helpers `admin sql` (solo lectura), `admin hash-db`, `admin backup`.
Verificación: pytest de integridad: insert repetido = no-op; UPDATE prohibido lanza error; transición ilegal (`exported→pending`) rechazada; intento de segunda clasificación vigente falla por **error de BD** (chequeo C-04 de AT-04); `PRAGMA integrity_check` ok. Base de AT-03 preparada.

## Hito 3 · Contratos de adaptadores + modo mock end-to-end de búsqueda
Objetivo: flujo búsqueda→elegibilidad→dedup→persistencia funcionando offline.
- **Gate previo obligatorio (C-03):** el usuario debe haber elegido Alternativa A o B de D-009; el parámetro `eligible_counting_mode: rules|accepted_cap` en `settings.yaml` materializa la elección y la lógica de paginación/conteo se implementa conforme a ella. Sin esta respuesta, el hito no se cierra.
- `providers/base.py` (contrato §9.1 + errores tipificados), `providers/mock.py` leyendo fixtures anonimizados, `domain/eligibility.py` (función pura), `domain/dedup.py` (normalización + url_index + capas 1–3).
- `stages/search.py`: paginación con checkpoint `last_page_done`, cupo 20, `discards`, conteo honesto.
- Fixtures: catálogo normalizado ≥24 respuestas, incluyendo casos de AT-05, AT-07-4 (`tracking_variants`), AT-09 y **AT-03-C-01 (`long_snippet` >2000 chars)**.
Verificación: AT-02 parcial, AT-03 (chequeo C-01), AT-05, AT-07 (incluido chequeo C-02), AT-09 (y AT-09B si semántica B), AT-15 verdes. `dlm run search --mock` reproducible byte-a-byte dos veces sin duplicar (AT-07-1).

## Hito 4 · Clasificación LLM desacoplada + routing de confianza
Objetivo: clasificar en mock real y dejar listo el adaptador Gemini.
- `llm/base.py` (contrato §9.2), prompts versionados `llm/prompts/cls-v1.md`, salida JSON estricta; `llm/mock.py` determinista (umbrales controlables por fixture).
- `stages/classify.py`: anti-repetición `(result_id,prompt_version,model_id)`, reintentos LLMInvalidOutput, `llm_payloads`.
- `domain/routing.py`: thresholds accept/reject → estados y `review_items` automáticos.
- Evidencias derivadas con validación substring (`evidence.py`).
Verificación: AT-03, AT-04, AT-06(1–2), AT-13(parcial), AT-11(parcial) verdes; reclasificar no duplica; ninguna escritura toca columnas originales (hash-db idéntico).

## Hito 5 · Cola de revisión humana completa
Objetivo: cerrar el loop humano.
- `stages/review.py`: `list/export/import/resolve/skip`; import por `review_id` idempotente; `review_events` append-only; desbloqueo de BQ hacia `ready_for_export`.
Verificación: AT-06 completo (incluida reimportación no-op); bloqueo de export con ítems abiertos demostrado.

## Hito 6 · Hallazgos + exportación Notion-ready
Objetivo: primera salida útil para el proyecto.
- **Entrada requerida (C-07/A-5): nombres reales de las propiedades de las bases de Notion.** Si al iniciar el hito no se recibieron, se avanza con los encabezados contractuales internos (§6.1/6.2 DATA_MODEL) y `config/notion_mapping.yaml` queda como plantilla vacía documentada; el renombrado final a propiedades Notion es una tarea de configuración, no de código, y se aplica antes de la corrida real sin tocar AT-01…AT-11.
- `findings.py` + `synthesize()` en mock; hallazgo `draft` referenciando evidencias; validación humana mínima vía `review reason=finding_validation`.
- `stages/export.py`: CSV UTF-8-BOM + XLSX (openpyxl), encabezados contractuales golden-tested, `notion_mapping.yaml`, `exports_manifest`. El export **nunca trunca originales** por defecto; `--truncate-display` genera columna `export_truncated` (C-01).
Verificación: AT-01, AT-02 completos, AT-12 (con copia de prueba en Notion manual; si A-5 pendiente, solo checklist estructural), AT-13 completo.

## Hito 7 · Adaptador real Brave Search API
Objetivo: búsqueda live respetuosa de cuotas.
- **Pre-check obligatorio de cuotas (C-08/A-3):** antes de escribir el adaptador, confirmar en la cuenta real de Brave el free tier vigente (requests/mes, límites de página); ejecutar 1 BQ live piloto, registrar consumo en `quota_usage` y proyectar ×24 BQ en `docs/pilot-report.md`. Criterio de paso: la proyección cabe con ≥30 % de margen; si no, se activa/ajusta el plan alternativo (otro adaptador o presupuesto pagado) **antes** de comprometer el ciclo completo. Los defaults de `max_daily_provider_calls` quedan marcados como provisionales hasta este pre-check.
- `providers/brave.py` sobre REST directo; mapeo verbatim; manejo 429/backoff; QuotaGuard persistente (`quota_usage`); rate limiter 1.2 s.
- Pruebas de contrato con fixtures grabados-sanitizados (nunca tráfico crudo al repo).
Verificación: AT-08 (con simulación de agotamiento), AT-10 live smoke con una BQ real; diff documentado campo a campo; consumo medido vs presupuesto (criterio C-08).

## Hito 8 · Adaptador real Gemini Free + endurecimiento
Objetivo: clasificación live económica y auditable.
- **Pre-check de términos/cuota Gemini (C-08/A-4):** revisar los términos vigentes del free tier antes de codificar; el piloto de 2–3 BQ mide consumo real vs límites y alimenta `docs/pilot-report.md`.
- `llm/gemini.py` REST directo, temperature 0, JSON mode si disponible; límites diarios; fallback claro ante AuthError/cambio de términos (A-4).
Verificación: AT-10 extendido a classify; AT-11 completo alternando modelos en config; corrida real de 2–3 BQ como piloto; revisión humana del piloto documentada en `docs/pilot-report.md`; medición de duración de etapa conforme (insumo de AT-16).

## Hito 9 · Ciclo completo real + documentación operativa
Objetivo: ejecutar las 24 BQ reales y entregar operación estable.
- Corrida completa live por etapas; resolución de cola; hallazgos validados; export final.
- README definitivo (instalación Windows/PowerShell paso a paso, glosario de comandos, troubleshooting de cuotas), checklist pre-import a Notion.
- **Entregables de accesibilidad (C-05):** completar los scripts de `scripts/`, pulir `dlm menu` y producir `docs/GUIA_OPERACION.md` con capturas del flujo real; ejecutar la prueba de usabilidad con una persona externa.
Verificación: todas AT verdes en su modalidad —incluidas AT-16 (duración), AT-17 (privacidad) y AT-18 (usabilidad)—; las 24 BQ en `exported`; paquete de archivos finales entregado al usuario.

## Hito 10 · Cierre y fusión
Objetivo: integrar diseño+implementación a `main` mediante PR (nunca commit directo), con review del usuario.
- Etiquetar `v0.1.0-mvp`; registrar desviaciones del diseño detectadas durante pilotos como ADR nuevos (no editar los existentes).

---

## Backlog explícitamente fuera de este plan (para no arrastrar scope)
Escritura directa API Notion · UI web de revisión · agendador · crawl de páginas completas ·
multi-país · métricas/dashboards · paralelismo · embeddings/vector search.

## Riesgos del plan y mitigaciones
| Riesgo | Mitigación |
|---|---|
| A-1 (consultas reales) llega tarde | Hitos 1–6 no lo requieren (placeholders + mocks); solo Hito 7+ depende. |
| Cuota Brave/Gemini menor que la supuesta | Contadores persistentes + `partial` reanudable; piloto de 3 BQ antes de corrida completa; proveedor alternativo por contrato. |
| Cambios de API rompen adaptadores delgados | Tests de contrato con fixtures; separación estricta dominio/adaptador hace el daño local. |
| Deriva de calidad de clasificación LLM | Prompt versionado, umbrales en config, cola humana obligatoria en zona gris, piloto revisado. |
| Sesgo de normalización de URLs | Tabla golden de casos de dedup (AT-15/§7); near-dups van a humano, nunca descartes silenciosos. |
| Fricción de import en Notion | Mapping YAML externo + AT-12 temprano (Hito 6) para validar formato con base de prueba. |
| Secreto comiteado por accidente | `.gitignore` desde Hito 0 + secret-scan en pre-commit (AT-14). |
