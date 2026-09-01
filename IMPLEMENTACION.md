# Registro de implementación - Monitor Fiscalía SPOA V3

Fecha: 2026-08-28

## Hallazgos de arquitectura

- Los monitores MinDefensa, Policía/SIEDCO y Observatorio Valle viven en repositorios operativos separados del SISC.
- GitHub Actions realiza la programación, conserva estado/hash, genera artefactos y reporta heartbeat OIDC al Centro de fuentes.
- SISC-Jamundí-PRO es React/Vite + FastAPI + PostgreSQL/PostGIS y registra fuentes externas en `source_connector_states`.
- Los conjuntos oficiales V3 exponen 29 columnas (procesos), 43 (víctimas) y 41 (procesados). Jamundí usa `76364`, `Jamundí`, `Valle Del Cauca` y el corte consultado fue `2026-07-31`.

## Archivos creados en este monitor

- `.env.example`
- `.github/workflows/monitor_spoa_v3.yml`
- `.gitignore`
- `IMPLEMENTACION.md`
- `README.md`
- `main.py`
- `pytest.ini`
- `requirements.txt`
- `data/.gitkeep`
- `output/.gitkeep`
- `state/.gitkeep`
- `src/spoa_monitor/__init__.py`
- `src/spoa_monitor/config.py`
- `src/spoa_monitor/emailer.py`
- `src/spoa_monitor/pipeline.py`
- `src/spoa_monitor/processing.py`
- `src/spoa_monitor/reporting.py`
- `src/spoa_monitor/sisc.py`
- `src/spoa_monitor/socrata.py`
- `src/spoa_monitor/state.py`
- `src/spoa_monitor/utils.py`
- `src/spoa_monitor/templates/bulletin.html.j2`
- `src/spoa_monitor/templates/email.html.j2`
- `tests/__init__.py`
- `tests/conftest.py`
- `tests/test_pipeline.py`
- `tests/test_processing.py`
- `tests/test_reporting.py`
- `tests/test_socrata.py`
- `output/pdf/Boletin_Fiscalia_SPOA_V3_Jamundi_2026-07-31.pdf` (ejemplo real, `dry_run`)

## Archivos creados en SISC-Jamundí-PRO

- `backend/api/fiscalia_spoa.py`
- `backend/db/models_fiscalia_spoa.py`
- `backend/db/migrations/20260828_fiscalia_spoa_v3.sql`
- `backend/tests/test_fiscalia_spoa_contract.py`

## Archivos modificados en SISC-Jamundí-PRO

- `.gitignore` (excepción explícita para versionar la migración SPOA)
- `backend/api/source_center.py`
- `backend/db/models.py`
- `backend/main.py`
- `backend/services/source_center_service.py`
- `backend/tests/test_source_center_contract.py`
- `docs/CENTRO_DE_FUENTES.md`

No se modificaron ni descartaron los cambios locales preexistentes del usuario en otros archivos del SISC.

## Validación ejecutada

- `python -m pytest -q` en el monitor: 13 pruebas aprobadas.
- Pruebas contractuales SISC: 16 pruebas aprobadas.
- Importación completa de FastAPI: rutas de heartbeat, ingesta, cierre de ejecución y resumen registradas.
- Consulta real oficial: 63.926 filas de procesos, 60.994 de víctimas y 33.367 de procesados filtradas remotamente por `76364`.
- Render PDF: 5 páginas A4 revisadas visualmente con Poppler a 110 y 180 DPI.
- Correo y escritura SISC: omitidos durante la prueba mediante `--dry-run --no-sisc-sync`.

## Operación rápida

```powershell
# Revisión normal
python main.py

# Prueba segura
python main.py --force-run --dry-run --no-sisc-sync

# Reconstrucción y publicación autorizada
python main.py --force-run
```

Para desactivar o activar la programación, use la opción correspondiente del workflow **Monitor Fiscalía SPOA V3 - Jamundí** en GitHub Actions.
