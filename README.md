# Monitor Fiscalía SPOA V3 – Observatorio del Delito de Jamundí

Monitor automático de los conjuntos oficiales **Procesos** (`dbdv-iihs`), **Víctimas** (`hr73-zqjf`) y **Procesados** (`piva-db2c`) de la Fiscalía General de la Nación. Integra una capa judicial complementaria al SISC; no sustituye ni suma directamente las cifras de Policía/SIEDCO, Medicina Legal, Comisarías o Inspecciones.

## Flujo

`metadatos oficiales → detección de cambio → consulta Socrata filtrada → crudo trazable → validación territorial → deduplicación → indicadores → alertas → PDF/HTML → correo → ingesta y heartbeat SISC`

La consulta usa el código DIVIPOLA `76364` como filtro remoto principal. Cada fila se vuelve a validar localmente contra municipio normalizado `JAMUNDI` y departamento `VALLE DEL CAUCA`. Un cambio inesperado en cualquier columna bloquea cifras, boletín e ingesta, y deja un manifiesto `SCHEMA_ALERT`.

## Conteos

- Procesos: `COUNT DISTINCT proceso_anonimizado`.
- Víctimas: `COUNT DISTINCT id_victima_anonimizado`.
- Procesados: `COUNT DISTINCT id_procesado_anonimizado`.
- Las tablas por delito cuentan cada identificador una vez por delito; no equiparan filas a personas/procesos.
- `proceso_anonimizado` se conserva como clave opaca para cruces entre conjuntos. Nunca se decodifica ni se usa para reidentificar.

Los comparativos usan el año más reciente disponible hasta su último mes publicado contra exactamente los mismos meses del año anterior. Los umbrales están centralizados en `src/spoa_monitor/config.py` (`ALERT_THRESHOLDS`).

## Instalación local

```powershell
cd C:\Proyectos\monitor-fiscalia-spoa-v3
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Complete `.env` sin registrar secretos en Git. Para mayor cuota de API configure `SOCRATA_APP_TOKEN`.

## Operación

Ejecución normal (sólo genera/envía cuando cambia realmente algún lote):

```powershell
python main.py
```

Prueba completa sin enviar correo ni escribir en SISC:

```powershell
python main.py --force-run --dry-run --no-sisc-sync
```

Reconstrucción manual aun sin actualización (reutiliza el snapshot validado; no redescarga si sigue vigente):

```powershell
python main.py --force-run
```

`--sample-limit N` existe únicamente para pruebas de conectividad; un producto muestral no debe publicarse como boletín institucional.

## Programación

`.github/workflows/monitor_spoa_v3.yml` revisa de lunes a viernes a las **07:17 (America/Bogota)**. En ejecuciones manuales `dry_run` viene activado por seguridad. Desactive el monitor desde GitHub Actions deshabilitando el workflow; para reactivarlo, habilítelo nuevamente. `force_run` reconstruye el boletín.

Secretos de GitHub:

- `SOCRATA_APP_TOKEN` (opcional, recomendado).
- `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`.
- `SPOA_RECIPIENTS` (opcional; por defecto usa los dos destinatarios institucionales solicitados).
- `SISC_API_URL` (opcional si se usa la URL predeterminada).
- `SISC_SOURCE_MONITOR_KEY` como respaldo fuera de OIDC.

GitHub Actions usa OIDC para el heartbeat/ingesta cuando SISC reconoce el repositorio y workflow. Nunca se imprimen credenciales.

## Persistencia y recuperación

- `state/monitor_state.json`: firma de metadatos, hash SHA-256 de lote y snapshot vigente por conjunto.
- `state/runs/<run_id>.json`: manifiesto inmutable de cada ejecución, incluso sin cambios o con error.
- `data/raw/<run_id>/`: respuesta oficial filtrada tal como llegó de Socrata.
- `data/normalized/`: filas validadas usadas para análisis.
- `output/`: PDF, HTML y previsualización del correo.

El workflow conserva snapshots en cache y persiste el estado de detección. Si se pierde el cache, el monitor recupera los lotes requeridos, compara su SHA-256 y evita enviar un boletín si el contenido es idéntico. Para una reconstrucción deliberada use `--force-run`.

Ante `SCHEMA_ALERT`, revise los metadatos oficiales, actualice los campos esperados y sus transformaciones, agregue pruebas y sólo entonces fuerce una reconstrucción. Ante error SMTP, el manifiesto queda `FAILED`, el estado anterior no se reemplaza y puede repetirse en `dry_run`.

## Integración SISC

SISC incorpora:

- conector `FISCALIA_SPOA_V3` en el Centro de fuentes;
- tablas `fiscalia_spoa_runs`, `fiscalia_spoa_snapshots` y `fiscalia_spoa_records`;
- endpoint autenticado `POST /api/fiscalia-spoa/ingest` por lotes;
- resumen institucional `GET /api/fiscalia-spoa/summary`.

La clave única `(dataset_id, cutoff_date, payload_sha256)` hace idempotente cada snapshot, y `(snapshot_id, record_key)` impide duplicar filas normalizadas. La migración SQL está en `backend/db/migrations/20260828_fiscalia_spoa_v3.sql`; `create_tables()` también crea las tablas en despliegues nuevos.

## Pruebas

```powershell
pytest -q
```

Las pruebas no envían correo, no escriben en SISC y no requieren credenciales.
