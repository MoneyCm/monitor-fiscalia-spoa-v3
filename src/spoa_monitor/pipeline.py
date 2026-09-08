from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .config import DATASETS, Settings
from .emailer import configured_recipients, send_report
from .processing import build_indicators, validate_and_deduplicate
from .reporting import generate_pdf, render_email_html, render_report_html
from .sisc import SiscClient
from .socrata import SchemaChangeError, SocrataClient, write_raw_jsonl
from .state import MonitorState
from .utils import atomic_json, canonical_json, parse_date, read_json, sha256_bytes


LOGGER = logging.getLogger(__name__)


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
    return rows


class MonitorPipeline:
    def __init__(self, settings: Settings, client: Optional[SocrataClient] = None):
        self.settings = settings
        if client is not None:
            self.client = client
        else:
            self.client = SocrataClient(
                settings.socrata_app_token,
                settings.timeout_seconds,
                settings.page_size,
                getattr(settings, "socrata_max_retries", 6),
                getattr(settings, "socrata_backoff_seconds", 2.0),
            )
        self.state = MonitorState(settings.state_dir)

    def _fallback_from_snapshot(
        self, key: str, spec, previous: Dict[str, Any], reason: str
    ) -> tuple[list[dict], Dict[str, Any], dict]:
        """Reutiliza el snapshot validado cuando la fuente oficial no responde.

        Retorna (rows, item_manifest_parcial, pending_state). Lanza
        DownloadError si no existe snapshot utilizable.
        """
        from .socrata import DownloadError as _DownloadError

        snapshot_path = Path(previous.get("snapshot_path", "")) if previous.get("snapshot_path") else None
        if not snapshot_path or not snapshot_path.exists():
            raise _DownloadError(
                f"Fuente oficial no disponible para {key} y sin snapshot local: {reason}"
            )
        rows = read_json(snapshot_path, [])
        # Revalidar localmente por seguridad; las estadísticas se heredan del
        # último cambio real para no distorsionar conteos históricos.
        validate_and_deduplicate(rows, spec)
        prior_stats = self.state.latest_changed_dataset(key, previous.get("payload_sha256"))
        valid_count = int(prior_stats.get("valid_count", len(rows)))
        filtered_count = int(prior_stats.get("filtered_count", len(rows)))
        discarded_count = int(prior_stats.get("discarded_count", 0))
        discard_reasons = dict(prior_stats.get("discard_reasons", {}))
        duplicate_count = int(prior_stats.get("duplicate_rows", 0))
        # Buscar el manifiesto previo para conservar columnas/versión de esquema.
        prev_manifest = self._latest_dataset_manifest(key)
        if prev_manifest:
            columns = list(prev_manifest.get("columns", list(spec.expected_columns)))
            schema_version = str(prev_manifest.get("schema_version", ""))
            source_row_count = prev_manifest.get("source_row_count")
            official_updated_at = prev_manifest.get("official_updated_at")
        else:
            columns = list(spec.expected_columns)
            schema_version = sha256_bytes(canonical_json(list(spec.expected_columns)))[:16]
            source_row_count = None
            official_updated_at = None
        cutoffs = [parse_date(row.get(spec.cutoff_field)) for row in rows]
        cutoff = max(item for item in cutoffs if item).isoformat() if any(cutoffs) else None
        item_manifest = {
            "dataset_id": spec.dataset_id,
            "official_url": spec.about_url,
            "official_updated_at": official_updated_at,
            "source_row_count": source_row_count,
            "column_count": len(columns),
            "columns": columns,
            "schema_version": schema_version,
            "metadata_sha256": previous.get("metadata_sha256"),
            "payload_sha256": previous.get("payload_sha256"),
            "filtered_count": filtered_count,
            "valid_count": valid_count,
            "discarded_count": discarded_count,
            "discard_reasons": discard_reasons,
            "duplicate_rows": duplicate_count,
            "cutoff_date": cutoff,
            "snapshot_path": str(snapshot_path),
            "metadata_changed": False,
            "real_change": False,
            "degraded": True,
            "degraded_reason": str(reason)[:300],
        }
        pending = {
            "dataset_id": spec.dataset_id,
            "metadata_sha256": previous.get("metadata_sha256"),
            "payload_sha256": previous.get("payload_sha256"),
            "cutoff_date": cutoff,
            "schema_version": schema_version,
            "snapshot_path": str(snapshot_path),
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
            "filtered_count": filtered_count,
            "valid_count": valid_count,
            "discarded_count": discarded_count,
            "discard_reasons": discard_reasons,
            "duplicate_rows": duplicate_count,
        }
        return rows, item_manifest, pending

    def _latest_dataset_manifest(self, key: str) -> Dict[str, Any]:
        runs_dir = self.settings.state_dir / "runs"
        if not runs_dir.exists():
            return {}
        for path in sorted(runs_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            run = read_json(path, {})
            item = run.get("datasets", {}).get(key, {})
            if item:
                return dict(item)
        return {}

    def run(
        self,
        *,
        force_run: bool = False,
        dry_run: bool = False,
        sample_limit: Optional[int] = None,
        sync_sisc: Optional[bool] = None,
    ) -> Dict[str, Any]:
        started = datetime.now(timezone.utc)
        run_id = f"spoa-{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        run_data_dir = self.settings.data_dir / "raw" / run_id
        manifest: Dict[str, Any] = {
            "run_id": run_id,
            "status": "RUNNING",
            "started_at": started.isoformat(),
            "force_run": force_run,
            "dry_run": dry_run,
            "sample_limit": sample_limit,
            "datasets": {},
            "updated_datasets": [],
            "degraded_datasets": [],
            "degraded": False,
            "warnings": [],
        }
        rows_by_dataset: Dict[str, list[dict]] = {}
        pending_state: Dict[str, dict] = {}

        try:
            from .socrata import DownloadError as _DownloadError

            for key, spec in DATASETS.items():
                previous = self.state.dataset(key)
                # 1) Metadatos con tolerancia a fallos transitorios: si la
                # fuente no responde pero existe snapshot, se degrada en vez
                # de marcar todo el run como FAILED.
                try:
                    metadata = self.client.metadata(spec)
                    self.client.validate_schema(spec, metadata)
                except _DownloadError as error:
                    LOGGER.warning("Fuente oficial no disponible para %s: %s", key, error)
                    rows, item_manifest, pending = self._fallback_from_snapshot(
                        key, spec, previous, str(error)
                    )
                    manifest["datasets"][key] = item_manifest
                    rows_by_dataset[key] = rows
                    pending_state[key] = pending
                    manifest["degraded_datasets"].append(key)
                    manifest["warnings"].append(
                        f"{key}: fuente oficial no disponible; snapshot reutilizado ({type(error).__name__})"
                    )
                    continue
                metadata_changed = metadata.metadata_sha256 != previous.get("metadata_sha256")
                snapshot_path = Path(previous.get("snapshot_path", "")) if previous.get("snapshot_path") else None
                # force_run reconstruye productos desde el snapshot validado; sólo vuelve a consultar
                # filas cuando cambió la fuente o falta el snapshot local.
                must_fetch = metadata_changed or not snapshot_path or not snapshot_path.exists()

                try:
                    if must_fetch:
                        raw_path = run_data_dir / f"{key}.jsonl"
                        raw_rows, payload_sha256 = write_raw_jsonl(
                            raw_path, self.client.iter_jamundi(spec, limit=sample_limit)
                        )
                        validation = validate_and_deduplicate(raw_rows, spec)
                        filtered_count = len(raw_rows)
                        valid_count = len(validation.valid)
                        discarded_count = len(validation.discarded)
                        discard_reasons = validation.reasons
                        duplicate_count = validation.duplicates
                        normalized_path = self.settings.data_dir / "normalized" / f"{key}-{payload_sha256[:16]}.json"
                        atomic_json(normalized_path, validation.valid)
                        rows = validation.valid
                        real_change = payload_sha256 != previous.get("payload_sha256")
                    else:
                        normalized_path = snapshot_path
                        rows = read_json(normalized_path, [])
                        payload_sha256 = previous.get("payload_sha256")
                        validation = validate_and_deduplicate(rows, spec)
                        prior_stats = self.state.latest_changed_dataset(key, payload_sha256)
                        filtered_count = int(prior_stats.get("filtered_count", len(rows)))
                        valid_count = int(prior_stats.get("valid_count", len(rows)))
                        discarded_count = int(prior_stats.get("discarded_count", 0))
                        discard_reasons = dict(prior_stats.get("discard_reasons", {}))
                        duplicate_count = int(prior_stats.get("duplicate_rows", 0))
                        filtered_count = max(
                            filtered_count,
                            valid_count + discarded_count + duplicate_count,
                        )
                        real_change = False
                except _DownloadError as error:
                    LOGGER.warning("Descarga oficial falló para %s: %s", key, error)
                    rows, item_manifest, pending = self._fallback_from_snapshot(
                        key, spec, previous, str(error)
                    )
                    # Si ya teníamos metadatos frescos, conservarlos en el manifiesto degradado.
                    item_manifest["official_updated_at"] = metadata.rows_updated_at
                    item_manifest["source_row_count"] = metadata.row_count
                    item_manifest["metadata_sha256"] = metadata.metadata_sha256
                    pending["metadata_sha256"] = metadata.metadata_sha256
                    manifest["datasets"][key] = item_manifest
                    rows_by_dataset[key] = rows
                    pending_state[key] = pending
                    manifest["degraded_datasets"].append(key)
                    manifest["warnings"].append(
                        f"{key}: descarga oficial falló; snapshot reutilizado ({type(error).__name__})"
                    )
                    continue

                cutoffs = [parse_date(row.get(spec.cutoff_field)) for row in rows]
                cutoff = max(item for item in cutoffs if item).isoformat() if any(cutoffs) else None
                item_manifest = {
                    "dataset_id": spec.dataset_id,
                    "official_url": spec.about_url,
                    "official_updated_at": metadata.rows_updated_at,
                    "source_row_count": metadata.row_count,
                    "column_count": len(metadata.columns),
                    "columns": list(metadata.columns),
                    "schema_version": sha256_bytes(canonical_json(metadata.columns))[:16],
                    "metadata_sha256": metadata.metadata_sha256,
                    "payload_sha256": payload_sha256,
                    "filtered_count": filtered_count,
                    "valid_count": valid_count,
                    "discarded_count": discarded_count,
                    "discard_reasons": discard_reasons,
                    "duplicate_rows": duplicate_count,
                    "cutoff_date": cutoff,
                    "snapshot_path": str(normalized_path),
                    "metadata_changed": metadata_changed,
                    "real_change": real_change,
                }
                manifest["datasets"][key] = item_manifest
                rows_by_dataset[key] = rows
                pending_state[key] = {
                    "dataset_id": spec.dataset_id,
                    "metadata_sha256": metadata.metadata_sha256,
                    "payload_sha256": payload_sha256,
                    "cutoff_date": cutoff,
                    "schema_version": item_manifest["schema_version"],
                    "snapshot_path": str(normalized_path),
                    "last_checked_at": datetime.now(timezone.utc).isoformat(),
                    "filtered_count": filtered_count,
                    "valid_count": valid_count,
                    "discarded_count": discarded_count,
                    "discard_reasons": discard_reasons,
                    "duplicate_rows": duplicate_count,
                }
                if real_change:
                    manifest["updated_datasets"].append(key)
            manifest["degraded"] = bool(manifest["degraded_datasets"])

            indicators = build_indicators(rows_by_dataset, DATASETS)
            manifest["indicators"] = indicators
            manifest["cutoff_date"] = indicators["cutoff_date"]
            produce = force_run or bool(manifest["updated_datasets"])
            if produce:
                context = {
                    "run_id": run_id,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "datasets": manifest["datasets"],
                    "indicators": indicators,
                    "updated_datasets": manifest["updated_datasets"] or ["Reconstrucción forzada"],
                    "dashboard_url": self.settings.dashboard_url,
                    "degraded": manifest.get("degraded", False),
                    "degraded_datasets": manifest.get("degraded_datasets", []),
                    "warnings": manifest.get("warnings", []),
                }
                html = render_report_html(context)
                cutoff_label = indicators["cutoff_date"] or started.date().isoformat()
                pdf_path = self.settings.output_dir / "pdf" / f"Boletin_Fiscalia_SPOA_V3_Jamundi_{cutoff_label}.pdf"
                html_path = self.settings.output_dir / "html" / f"Boletin_Fiscalia_SPOA_V3_Jamundi_{cutoff_label}.html"
                html_path.parent.mkdir(parents=True, exist_ok=True)
                html_path.write_text(html, encoding="utf-8")
                pdf_sha256 = generate_pdf(html, pdf_path)
                manifest.update(
                    {"bulletin_path": str(pdf_path), "html_path": str(html_path), "pdf_sha256": pdf_sha256}
                )
                email_context = {**context, "pdf_sha256": pdf_sha256}
                email_html = render_email_html(email_context)
                email_preview_path = self.settings.output_dir / "html" / "correo_fiscalia_spoa_v3.html"
                email_preview_path.write_text(email_html, encoding="utf-8")
                manifest["email_preview_path"] = str(email_preview_path)
                if not dry_run:
                    subject = (
                        "Boletín Fiscalía SPOA V3 – Observatorio del Delito de Jamundí – "
                        f"Corte {cutoff_label}"
                    )
                    # El correo es notificación secundaria: si falla (p. ej. SMTP
                    # sin configurar en el runner), se registra y el run sigue
                    # COMPLETED con el boletín ya generado.
                    try:
                        send_report(subject, email_html, pdf_path, configured_recipients())
                        manifest["email_sent"] = True
                    except Exception as error:
                        manifest["email_sent"] = False
                        manifest["warnings"].append(
                            f"email_no_enviado: {type(error).__name__}: {str(error)[:300]}"
                        )
                        LOGGER.warning("No se pudo enviar el boletín por correo: %s", error)
                else:
                    manifest["email_sent"] = False
            else:
                manifest["email_sent"] = False
                manifest["no_change"] = True

            do_sync = self.settings.sisc_sync_enabled if sync_sisc is None else sync_sisc
            if do_sync and not dry_run:
                # La sincronización SISC tampoco invalida el boletín ya generado
                # (el backend en Render puede estar en cold start y dar timeout).
                try:
                    sisc = SiscClient(self.settings.sisc_api_url, self.settings.sisc_monitor_key)
                    if manifest["updated_datasets"]:
                        for key in manifest["updated_datasets"]:
                            sisc.ingest(run_id, key, manifest["datasets"][key], rows_by_dataset[key])
                        sisc.complete_run(run_id, {**manifest, "status": "COMPLETED"})
                        manifest["sisc_synced"] = True
                    else:
                        manifest["sisc_synced"] = False
                except Exception as error:
                    manifest["sisc_synced"] = False
                    manifest["warnings"].append(
                        f"sisc_no_sincronizado: {type(error).__name__}: {str(error)[:300]}"
                    )
                    LOGGER.warning("No se pudo sincronizar con SISC: %s", error)

            manifest["status"] = "COMPLETED"
            for key, value in pending_state.items():
                self.state.update_dataset(key, value)
            self.state.save(run_id)
        except SchemaChangeError as error:
            manifest["status"] = "SCHEMA_ALERT"
            manifest["warnings"].append(str(error))
            LOGGER.exception("Ejecución bloqueada por cambio de esquema")
        except Exception as error:
            manifest["status"] = "FAILED"
            manifest["warnings"].append(f"{type(error).__name__}: {str(error)[:400]}")
            LOGGER.exception("Fallo del monitor SPOA")
        finally:
            manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
            self.state.write_run(run_id, manifest)
            if (self.settings.sisc_sync_enabled if sync_sisc is None else sync_sisc) and not dry_run:
                try:
                    sisc = SiscClient(self.settings.sisc_api_url, self.settings.sisc_monitor_key)
                    status = "ERROR" if manifest["status"] in {"FAILED", "SCHEMA_ALERT"} else "UPDATED" if manifest.get("updated_datasets") else "CURRENT"
                    quality = "ERROR" if manifest["status"] in {"FAILED", "SCHEMA_ALERT"} else "VALIDATED"
                    sisc.heartbeat(manifest, status, quality)
                except Exception as error:
                    # No bloquea el run: la fuente oficial ya quedó registrada
                    # en state/runs. Se incluye la URL base (sin clave) para
                    # diagnosticar 404/credenciales sin exponer secretos.
                    LOGGER.warning(
                        "No se pudo enviar heartbeat SISC a %s: %s",
                        self.settings.sisc_api_url,
                        error,
                    )
        return manifest
