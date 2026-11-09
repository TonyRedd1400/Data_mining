# Organización del repositorio

## Decisión

Usar la separación superior de `claveteo-api`: `src`, `tests`, `config` y documentación. No copiar sus capas de API, nube o servicios porque este proyecto es un pipeline local pequeño.

```text
src/data_mining/
  cli.py
  db.py
  collect.py
  retrieval.py
tests/
  fixtures/
  test_cli.py
  test_db.py
  test_collect.py
  test_retrieval.py
config/
  queries.csv
docs/
pipeline.py
pyproject.toml
README.md
```

## Razón

- `cli.py`: conserva comandos, mensajes y códigos de salida.
- `db.py`: protege esquema y migraciones SQLite.
- `collect.py`: reúne catálogo, normalización y Brave.
- `retrieval.py`: mantiene juntos los controles de red, curl y HTML.
- `tests/`: refleja esas cuatro fronteras y funciona sin internet.
- `config/`: separa datos operativos del código.
- `docs/`: evita saturar la raíz.
- `pipeline.py`: mantiene compatible `python pipeline.py`.

## Límites

No crear `v1`, `common`, `services`, `schemas`, `providers`, `domain` ni `stages` hasta que exista una segunda implementación o responsabilidad real. No añadir dependencias para esta reorganización.
