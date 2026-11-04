# Data Mining · Investigación de demanda

Pipeline local para ejecutar las 24 búsquedas BQ, guardar resultados en SQLite y recuperar HTML público con trazabilidad.

## Estado

- Implementado: configuración BQ, SQLite, Brave, deduplicación, recuperación HTML y reanudación.
- Pendiente: elegibilidad, evidencias, clasificación, revisión humana y exportación.
- Sin dependencias Python de ejecución.

## Estructura

```text
src/data_mining/   código del pipeline
tests/             pruebas offline y fixtures
config/            consultas operativas
docs/              contrato, decisiones y diseño
pipeline.py        entrada compatible
pyproject.toml     metadatos del paquete
```

La decisión y sus límites están en [docs/ORGANIZATION.md](docs/ORGANIZATION.md).

## Uso

```powershell
python pipeline.py validate-config
python pipeline.py init-db --db investigacion.sqlite
python pipeline.py collect --bq BQ-002 --db investigacion.sqlite
python pipeline.py retrieve --bq BQ-002 --db investigacion.sqlite
python -m unittest discover -s tests -v
```

Para pruebas sin red:

```powershell
python pipeline.py collect --bq BQ-002 --db investigacion.sqlite --fixture tests\fixtures\brave_bq002.json
```

## Documentación

- [Contrato MVP](docs/MVP_CONTRACT.md)
- [Arquitectura](docs/ARCHITECTURE.md)
- [Modelo de datos](docs/DATA_MODEL.md)
- [Decisiones](docs/DECISIONS.md)
- [Plan](docs/IMPLEMENTATION_PLAN.md)
- [Pruebas de aceptación](docs/ACCEPTANCE_TESTS.md)
