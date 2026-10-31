# Directorio Laboral México — Sistema de investigación de demanda (BQ-001…BQ-024)

Repositorio reutilizado como sede del pipeline local de investigación de demanda.
Estado actual: **fase de diseño** (sin código de producto todavía).

## Documentos de diseño

| Documento | Contenido |
|---|---|
| [SPEC.md](SPEC.md) | Alcance, requisitos funcionales/no funcionales, elegibilidad, supuestos pendientes |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Componentes, flujo de datos, contratos, errores, idempotencia, dedup, seguridad |
| [DATA_MODEL.md](DATA_MODEL.md) | Tablas SQLite, máquinas de estado, inmutabilidad, esquemas de exportación |
| [DECISIONS.md](DECISIONS.md) | ADRs D-001…D-017 con estado (cerradas / propuestas) |
| [ACCEPTANCE_TESTS.md](ACCEPTANCE_TESTS.md) | AT-01…AT-15 ejecutables en PowerShell (mayoría en modo mock) |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Hitos 0–10, cada uno verificable |

## Reglas del proyecto

- Rama de trabajo: `feature/bq-research-pipeline` (nada se commitea directo a `main`).
- Repo **público**: prohibido subir claves, `.env` real, bases de datos operativas (`data/`),
  resultados crudos completos (`exports/`, `logs/`) o información personal. Solo `.env.example`.
- Los textos originales de búsqueda nunca se modifican; las clasificaciones del LLM viven aparte.
- Sin scraping de Google ni evasión de captchas/robots.txt: solo APIs oficiales vía adaptadores.

## Preparación (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt   # a partir del Hito 1
Copy-Item .env.example .env       # editar con claves propias; nunca commitear
```

El pipeline aún no está implementado; ver IMPLEMENTATION_PLAN.md para el orden de construcción.
