# DECISIONS.md — Decisiones de arquitectura (ADR)

Formato: ID · Título · Estado (`Cerrada` / `Propuesta` / `Abierta`) · Contexto · Decisión ·
Alternativas descartadas · Consecuencias. Al implementarse, cada Cerrada se refleja como ADR corto
en `docs/adr/`.

---

## D-001 · Monolito CLI local en Python sobre SQLite
**Estado: Cerrada**
- Contexto: operador único, Windows, bajo costo, pocas dependencias, trazabilidad; no se requiere concurrencia ni servicio.
- Decisión: aplicación CLI por etapas con base SQLite local (WAL). Sin servidor web, sin Docker, sin BD cliente-servidor.
- Alternativas: DuckDB (mejor analítica, peor disponibilidad/stdlib), JSONL-only (sin consultas relacionales para dedup/trazas), PostgreSQL (operación innecesaria), app web Flask (complejidad sin beneficio MVP).
- Consecuencias: una sola persona puede auditar todo el sistema; migración futura a Postgres es posible porque el acceso está aislado en `Repository`. Riesgo: concurrencia nula (aceptable: un proceso a la vez, enforced con lockfile).

## D-002 · Etapas separadas como comandos (`search`, `classify`, `review`, `export`) en lugar de un comando "todo en uno" atómico
**Estado: Cerrada**
- Contexto: requisito de detener/reanudar sin duplicar; cuotas distintas por API; fallos independientes.
- Decisión: orquestador delgado + 4 etapas idempotentes invocables individualmente; además un wrapper `dlm run all --mock` que las encadena para comodidad (cada etapa sigue siendo reentrante).
- Alternativas: un solo job transaccional gigante (imposible con cuota externa), colas/celery (excesivo).
- Consecuencias: el estado entre etapas vive en la BD; se debe mantener la máquina de estados estricta (§3 DATA_MODEL).

## D-003 · Proveedor de búsqueda detrás de interfaz `SearchProvider`; Brave Search API como primer adaptador
**Estado: Cerrada**
- Contexto: prohibido scraping de Google/robots-evasion; se necesita API oficial, barata y paginable; evitar acoplamiento.
- Decisión: contrato canónico (§9.1 ARCHITECTURE) con adaptadores `brave` y `mock` iniciales. El núcleo usa solo el contrato; errores tipificados normalizados en el adaptador.
- Alternativas: Serper.dev (paga, más parecida a Google), SearxNG autohospedado (gratis pero dependencia de infra), Bing API (retirada para nuevos registros), DuckDuckGo unofficial (no oficial → riesgo de cumplimiento).
- Consecuencias: cambiar de proveedor = nuevo archivo de adaptador + config. Los datos crudos registran `provider` para que ninguna comparación entre épocas mezcle semánticas sin saberlo. **Confirmación pendiente de cuota real vigente de Brave (A-3).**

## D-004 · LLM detrás de interfaz `Classifier`; Gemini API Free como adaptador inicial, salida estructurada JSON validada
**Estado: Cerrada**
- Contexto: clasificador intercambiable; free tier como punto de partida; necesidad de auditoría de prompt/modelo.
- Decisión: `classify()` y `synthesize()` sobre contrato único; temperature 0; respuesta JSON validada contra enum cerrado; toda llamada deja payload truncado en `llm_payloads`; `prompt_version` y `model_id` obligatorios en cada clasificación.
- Alternativas: OpenAI (costo), Ollama local (hardware extra, calidad dudosa en español de México), SDK google-genai pesado (se prefiere REST directo, envuelto para poder cambiar).
- Consecuencias: si el free tier cambia de límites o términos, se sustituye el adaptador sin tocar dominio. **Depende de A-4 (términos de uso del free tier).**

## D-005 · Separación física original vs derivado + triggers de inmutabilidad en SQLite
**Estado: Cerrada**
- Contexto: "un LLM puede clasificar pero nunca alterar datos originales".
- Decisión: originales verbatim en `raw_results` (UPDATE/DELETE bloqueados por trigger); todo juicio (LLM o humano) en tablas/columnas derivadas con prefijo `llm_`/`human_`; evidencia validada como substring exacto del original.
- Alternativas: columnas duales en la misma tabla (riesgo de UPDATE accidental), versionado completo tipo event-sourcing (excesivo para MVP).
- Consecuencias: la exportación siempre hace JOIN al original; imposible "contaminar" texto fuente. Costo: complejidad de esquema algo mayor.

## D-006 · Deduplicación determinista primero, LLM nunca decide duplicados
**Estado: Cerrada**
- Contexto: requisito de detectar duplicados sin falsos negativos silenciosos.
- Decisión: 3 capas: (1) URL normalizada sha256 exacta, (2) near-dup título+dominio Jaccard≥0.9 intra-BQ → revisión humana si afecta cupo, (3) cruce inter-BQ marcado `cross_duplicate` sin eliminar. Marcar, jamás borrar.
- Alternativas: MinHash/fuzzy global (infra innecesaria), dedup por LLM (no determinista, cuesta cuota, audita mal).
- Consecuencias: lista de parámetros de tracking a limpiar es mantenible en YAML; sesgos de normalización quedan documentados en tests golden.

## D-007 · Revisión humana vía cola en BD + CSV de ida y vuelta + CLI (`review list/export/import/resolve`)
**Estado: Cerrada**
- Contexto: "cola de revisión humana" sin UI; operador único; trazabilidad de decisiones.
- Decisión: `review_items` + `review_events` append-only; el humano puede resolver en CLI o editar el CSV exportado y reimportarlo (por `review_id`).
- Alternativas: UI web (fuera de alcance), editar Notion directamente (rompe el pipeline de fuente única).
- Consecuencias: dos canales de resolución deben converger al mismo repositorio de eventos; riesgo de importación duplicada mitigado por `review_id` + hash de decisión.

## D-008 · HTTP REST directo con `requests` en vez de SDKs oficiales (Brave/Gemini)
**Estado: Cerrada (reversible)**
- Contexto: "pocas dependencias"; los SDKs de Gemini traen acoplamiento y superficie grande.
- Decisión: adaptadores delgados sobre `requests`; esquemas de respuesta validados con pydantic; timeouts explícitos.
- Alternativas: `google-generativeai`/`genai` (más dependencias), httpx (similar, menos ubicuo).
- Consecuencias: mantenimiento manual del mapeo si cambian las APIs; contenido en tests de contrato con fixtures grabados. Si pesa, se cambia solo el adaptador.

## D-009 · Cuenteo de "20 elegibles" sobre elegibilidad determinista; LLM califica después
**Estado: Propuesta (requiere aprobación del usuario — ligado a A-2, revisión C-03)**
- Contexto: ambigüedad: ¿20 resultados devueltos, 20 relevantes según LLM, o 20 elegibles por reglas?
- Decisión propuesta: elegibilidad por reglas (URL válida, no dup, no bloqueado, patrones) define el cupo; la irrelevancia LLM se registra como `rejected` posterior y `eligible_count` queda como dato operativo + `accepted_count` como dato analítico. **Consecuencia explícita:** una BQ puede exportar menos de 20 filas *aceptadas* si el LLM rechaza; eso no se considera incumplimiento con esta semántica, sino señal de demanda escasa.
- Alternativa B (si el usuario la elige): seguir paginando hasta reunir 20 **aceptados** por el LLM (o agotar tope duro de páginas/crudos). Consecuencias: más llamadas de búsqueda y clasificación por BQ, cupo dependiente del modelo, resultados menos reproducibles entre corridas; requiere subir `max_raw_pages_per_bq` y redefinir AT-09/AT-01-4.
- Alternativas descartadas: contar los 20 crudos devueltos sin filtro (mezcla basura en el cupo).
- Consecuencias: dos contadores conviven; la exportación muestra ambos. **El usuario debe elegir A (propuesta) o B antes del Hito 3**, donde se implementa la lógica de paginación/conteo.

## D-010 · Hallazgo = síntesis por BQ generada por LLM, estado `draft` hasta validación humana
**Estado: Propuesta (ligado a A-6)**
- Decisión: tras clasificación completa, `synthesize()` produce 1..n borradores por BQ referenciando evidencias; nada se exporta como hallazgo `validated` sin evento humano.
- Alternativas: hallazgo = cada resultado aceptado (demasiado granular, lo llamativo lo decide el humano); sin hallazgos automáticos (más trabajo manual).
- Consecuencias: consume ~24 llamadas extra de LLM por ciclo; exige campo `evidence_ids_json` íntegro.

## D-011 · Configuración de BQ en `config/queries.yaml` versionado; secretos solo en `.env`
**Estado: Cerrada**
- Decisión: catálogo de búsquedas es código-configurable (revisable en PR); claves únicamente por entorno (`python-dotenv`); `.env.example` con valores ficticios; `.gitignore` excluye `data/ exports/ logs/ .env *.db`.
- Alternativas: BD como única fuente de BQ (opaca el diff), guardar claves en settings.yaml (prohibido por ser repo público).
- Consecuencias: cambios de consulta quedan auditados en Git; imposibilidad estructural de comitear secretos salvo error humano → mitigado con pre-commit regex scan.

## D-012 · Exportación a archivos planos (CSV UTF-8-BOM + XLSX) con mapa a Notion por YAML, sin API de Notion en MVP
**Estado: Cerrada (mapeo de nombres Abierta — A-5)**
- Decisión: Notion soporta import CSV/XLSX nativo; el MVP termina en archivos con encabezados contractuales y `export_schema_v`; `notion_mapping.yaml` renombra columnas a las propiedades reales.
- Alternativas: integración Notion API (tokens adicionales, acoplamiento, fuera de "bajo costo/pocas dependencias").
- Consecuencias: carga semi-manual aceptada; si Notion exige tipos especiales (selects/multi-select), se resuelve en el mapping, no en el núcleo.

## D-013 · Rate limiting conservador con tope diario persistente y pausa cooperativa
**Estado: Cerrada**
- Decisión: intervalo mínimo 1.2 s, ≤100 llamadas/proveedor/día, ≤500 LLM/día (defaults ajustables); contador en BD sobrevive reinicios; al agotarse, run termina ordenadamente en `partial`.
- Alternativas: correr hasta 429 remoto (quema reputación de IP/key), paralelismo (complica idempotencia y cuotas).
- Consecuencias: ciclo completo cabe en ~minutos; respeta spirit de "no presión sobre proveedores".

## D-014 · Modo `--mock` de primera clase con fixtures anonimizados versionados
**Estado: Cerrada**
- Decisión: adaptadores `mock` devuelven respuestas deterministas desde `tests/fixtures/`; todo el pipeline (incl. routing y export) corre offline; los fixtures son ficticios (<20 filas/BQ, dominios `ejemplo-ficticio.*`).
- Alternativas: solo unit tests sin pipeline end-to-end (deja la reanudación sin probar), grabar tráfico real (datos de terceros en repo público → riesgo).
- Consecuencias: AT-01…AT-08 son reproducibles sin claves; contratos verificables en CI.

## D-015 · Python empaquetado con `pyproject.toml`, entry point `dlm`, src-layout
**Estado: Cerrada**
- Decisión: instalación editable `pip install -e .` en venv; comandos `python -m dlm …` equivalentes.
- Consecuencias: rutas estables en Windows; evita sys.path hacks.

## D-016 · Lockfile de ejecución única
**Estado: Cerrada**
- Decisión: `data/.lock` con PID; segunda instancia aborta con mensaje claro.
- Consecuencias: previene escritura concurrente de SQLite y dobles llamadas a APIs.

## D-017 · Eliminar notebooks heredados del repo
**Estado: Cerrada (acción de preparación)**
- Decisión: `Datos.ipynb`, `Mineria de Datos.ipynb`, `POO.ipynb` se eliminan en el commit de preparación; quedan en el historial Git.
- Consecuencias: raíz limpia para la nueva estructura; cero riesgo de confusión de alcance.

---

# Registro de correcciones de diseño (revisión externa 2026-10-31)

| ID | Severidad | Problema detectado | Corrección aplicada | Documentos tocados | Prueba de resolución |
|---|---|---|---|---|---|
| C-01 | Bloqueante | Truncamiento de `snippet_raw` a 2000 chars perdía el texto original y rompía la promesa de verbatim/hash íntegro. | Se elimina todo tope de snippet en persistencia: columna NOT NULL completa, hash sobre texto íntegro, truncamiento solo posible en export con flag; triggers ampliados. | SPEC RF-03; DATA_MODEL §2.2/§5; ARCHITECTURE §8 límites | AT-03 ampliado (fixture >2000 chars) |
| C-02 | Alta | Inconsistencia UNIQUE `raw_results`: DATA_MODEL usaba `url_raw`, ARCHITECTURE `url_norm`; permitía crudos duplicados de la misma URL lógica. | Clave natural unificada en `(bq_id, provider, page, rank_position, url_norm)` con índice único explícito; `url_raw` verbatim se conserva como columna aparte. | DATA_MODEL §2.2/§5; ARCHITECTURE §6; SPEC RF-18 | AT-07 ampliado (misma URL, distintos parámetros de tracking) |
| C-03 | Alta | Semántica de "20 elegibles" ambigua sin consecuencias documentadas. | A-2/D-009 explicitan las dos alternativas (A: elegibles por reglas, aceptados pueden ser <20; B: paginar hasta 20 aceptados) y su impacto en cuota/reproducción; se marca punto de decisión obligatorio antes del Hito 3. | SPEC §10 A-2; DECISIONS D-009 + pendientes | AT-09 versión A + AT-09B condicional |
| C-04 | Media | Faltaba restricción de BD que garantizara un solo `is_current=1` por `(result_id, prompt_version)`. | Añadido índice único parcial `ux_classifications_current ... WHERE is_current = 1`. | DATA_MODEL §2.6/§5 | AT-04 ampliado (segunda vigente → error de BD esperado) |
| C-05 | Alta | Requisito de uso por persona no técnica sin mecanismo concreto (solo CLI/edición de archivos). | Nuevos mecanismos dentro de alcance: scripts PowerShell de un paso, menú de consola `dlm menu`, guía con capturas; RF-19; hitos de plan actualizados. | SPEC §3.1/RF-19; IMPLEMENTATION_PLAN Hitos 1/9; README | AT-18 (prueba de usabilidad) |
| C-06 | Media | RNF-07 (duración) y RNF-08 (privacidad) sin prueba asociada. | Añadidos AT-16 (medición de duración reportada por el propio run) y AT-17 (auditoría de fixtures/privacidad); matriz requisito→prueba actualizada. | ACCEPTANCE_TESTS | AT-16, AT-17 |
| C-07 | Media | Nombres de propiedades Notion sin confirmar bloqueaba formato final del export. | El mapping se declara entrada obligatoria del Hito 6 con fallback explícito (encabezados contractuales internos + renombrado por `notion_mapping.yaml` al confirmar); gate añadido al plan. | DECISIONS D-012 + pendientes; IMPLEMENTATION_PLAN Hito 6; SPEC A-5 | AT-12 con copia real de base Notion (datos mock) |
| C-08 | Media | Cuotas gratuitas Brave/Gemini sin validar podían romper el ciclo completo. | Gate de validación de cuotas antes del Hito 7 con medición real por BQ (`quota_usage`), proyección 24 BQ y criterio de parada/rediseño; defaults marcados provisionales. | DECISIONS D-003/D-004/D-013 + pendientes; IMPLEMENTATION_PLAN Hito 7 pre-check | Medición live 1 BQ vs presupuesto; registro en `docs/pilot-report.md` |

Nota de proceso: estas correcciones editan el diseño (fase pre-código); cuando exista `docs/adr/`,
cada corrección con cambio de decisión se reflejará como ADR complementario, sin reescribir los
ADRs originales.

---

## Registro de pendientes ligadas a decisiones

| ID | Pendiente | Bloquea | Gate / fecha límite |
|---|---|---|---|
| A-1 | Recibir las 24 consultas literales | queries.yaml real, AT-02 live | Antes del Hito 7 (corrida real) |
| A-2 | Semántica exacta de "20 elegibles" — elegir Alternativa A o B (ver D-009/C-03) | D-009; lógica de paginación y conteo | **Antes del Hito 3** (implementación de elegibilidad/conteo) |
| A-3 | Cuota vigente Brave free (confirmar en la cuenta real) | D-013 defaults (`max_daily_provider_calls` provisional) | Pre-check obligatorio del Hito 7: corrida live de 1 BQ + proyección 24 BQ documentada en `docs/pilot-report.md`; si no cabe, activar adaptador alternativo antes de continuar |
| A-4 | Términos free tier Gemini para este uso | D-004 | Pre-check del Hito 8: lectura de términos vigente + piloto 2–3 BQ; fallback a otro adaptador documentado |
| A-5 | Nombres reales de propiedades de las bases Notion | D-012 mapping final | **Entrada requerida del Hito 6**; mientras tanto el export usa encabezados contractuales internos y `notion_mapping.yaml` queda pendiente (no bloquea AT-01…AT-11) |
| A-6 | Definición canónica de "hallazgo" | D-010 | Antes del Hito 6 |
