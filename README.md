# Data Mining · Investigación de demanda

Pipeline local mínimo para ejecutar las 24 búsquedas BQ del proyecto Directorio Laboral México, guardar resultados y recuperar contenido público con trazabilidad.

## Estado verificado · 3 de noviembre de 2026

- Rama actual: `feat/curl-egress`.
- Pruebas: `12/12` con `unittest`.
- Implementado: configuración de 24 BQ, SQLite, búsqueda Brave, deduplicación por URL, recuperación HTML y reanudación sin duplicados.
- No implementado: elegibilidad definitiva, extracción/clasificación de evidencias, revisión humana y exportación CSV.
- Piloto real BQ-002: 18 resultados, 8 páginas recuperadas, 10 limitadas y 0 evidencias laborales aprovechables.
- La clave `BRAVE_SEARCH_API_KEY` está configurada localmente y nunca debe subirse al repositorio.

## Flujo actual

```text
queries.csv
  -> collect (Brave Search API)
  -> runs + search_results (SQLite)
  -> retrieve (curl con IP pública fijada)
  -> contenido HTML o motivo de limitación
  -> evidencia y revisión humana (pendientes)
```

Un título o snippet de buscador es un resultado, no evidencia. La evidencia futura requerirá contenido recuperado y un fragmento exacto.

## Uso

```powershell
python pipeline.py validate-config
python pipeline.py init-db --db investigacion.sqlite
python pipeline.py collect --bq BQ-002 --db investigacion.sqlite
python pipeline.py retrieve --bq BQ-002 --db investigacion.sqlite
python -m unittest discover -s tests -v
```

## Seguridad de recuperación

Antes de cada descarga se valida que el destino resuelva únicamente a direcciones públicas. `curl` fija la conexión a la IP validada mediante `--resolve`, ignora proxies externos, no sigue redirecciones y aplica límites de 10 segundos y 1 MiB. Solo se acepta `text/html`.

La recolección Brave todavía no registra los encabezados de cuota. Antes de ampliar la corrida debe añadirse lectura de `X-RateLimit-Remaining`, `X-RateLimit-Reset` y manejo explícito de `429` sin reintentos automáticos.

## Archivos principales

| Archivo | Función actual |
|---|---|
| `pipeline.py` | CLI, SQLite, búsqueda Brave y recuperación HTML segura |
| `queries.csv` | Las 24 consultas BQ vigentes |
| `MVP_CONTRACT.md` | Límite funcional del MVP actual |
| `fixtures/brave_bq002.json` | Respuesta ficticia para pruebas sin red |
| `tests/test_pipeline.py` | 12 pruebas del flujo implementado |
| `DECISIONS.md` | Decisiones vigentes y diseño histórico |
| `IMPLEMENTATION_PLAN.md` | Estado real, siguiente hito y plan histórico |

## Commits del checkpoint

| Entrega | Commit |
|---|---|
| Contrato mínimo | `d2fca6c` |
| Recolector Brave + SQLite | `84e3b3c` |
| Recuperación sin red real | `a5546e0` |
| Transporte curl con IP fijada | `c7d5fce` |

No se ha hecho push ni PR de estas ramas. `pilot_bq002.sqlite` es un dato operativo local y no debe incluirse en commits.
