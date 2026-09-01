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
        self.client = client or SocrataClient(
            settings.socrata_app_token, settings.timeout_seconds, settings.page_size
        )
        self.state = MonitorState(settings.state_dir)

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
            "warnings": [],
        }
        rows_by_dataset: Dict[str, list[dict]] = {}
        pending_state: Dict[str, dict] = {}

        try:
            for key, spec in DATASETS.items():
                metadata = self.client.metadata(spec)
                self.client.validate_schema(spec, metadata)
                previous = self.state.dataset(key)
                metadata_changed = metadata.metadata_sha256 != previous.get("metadata_sha256")
                snapshot_path = Path(previous.get("snapshot_path", "")) if previous.get("snapshot_path") else None
                # force_run reconstruye productos desde el snapshot validado; sólo vuelve a consultar
                # filas cuando cambió la fuente o falta el snapshot local.
                must_fetch = metadata_changed or not snapshot_path or not snapshot_path.exists()

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
                    send_report(subject, email_html, pdf_path, configured_recipients())
                    manifest["email_sent"] = True
                else:
                    manifest["email_sent"] = False
            else:
                manifest["email_sent"] = False
                manifest["no_change"] = True

            do_sync = self.settings.sisc_sync_enabled if sync_sisc is None else sync_sisc
            if do_sync and not dry_run:
                sisc = SiscClient(self.settings.sisc_api_url, self.settings.sisc_monitor_key)
                if manifest["updated_datasets"]:
                    for key in manifest["updated_datasets"]:
                        sisc.ingest(run_id, key, manifest["datasets"][key], rows_by_dataset[key])
                    sisc.complete_run(run_id, {**manifest, "status": "COMPLETED"})
                    manifest["sisc_synced"] = True
                else:
                    manifest["sisc_synced"] = False

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
                    LOGGER.warning("No se pudo enviar heartbeat SISC: %s", error)
        return manifest
