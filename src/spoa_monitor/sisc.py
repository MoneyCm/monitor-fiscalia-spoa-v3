from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _request_github_oidc_token(audience: str = "sisc-source-center") -> Optional[str]:
    request_url = os.getenv("ACTIONS_ID_TOKEN_REQUEST_URL", "").strip()
    request_token = os.getenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "").strip()
    if not request_url or not request_token:
        return None
    separator = "&" if "?" in request_url else "?"
    request = Request(
        f"{request_url}{separator}{urlencode({'audience': audience})}",
        headers={"Authorization": f"Bearer {request_token}"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
        return str(result.get("value")) if isinstance(result, dict) and result.get("value") else None
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


class SiscClient:
    def __init__(self, api_url: str, service_key: str = "", oidc_token: Optional[str] = None):
        self.api_url = api_url.rstrip("/")
        self.service_key = service_key
        self.oidc_token = _request_github_oidc_token() if oidc_token is None else oidc_token

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "User-Agent": "monitor-fiscalia-spoa-v3/1.0"}
        if self.oidc_token:
            headers["Authorization"] = f"Bearer {self.oidc_token}"
        elif self.service_key:
            headers["X-SISC-SOURCE-KEY"] = self.service_key
        return headers

    def post(self, path: str, payload: Dict[str, Any], timeout: int = 45) -> Dict[str, Any]:
        if not self.oidc_token and not self.service_key:
            raise RuntimeError("Integración SISC sin credencial OIDC ni clave de servicio")
        request = Request(
            f"{self.api_url}/{path.lstrip('/')}",
            data=json.dumps(payload, ensure_ascii=True, default=str).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def heartbeat(self, manifest: Dict[str, Any], status: str, quality: str) -> Dict[str, Any]:
        datasets = manifest.get("datasets", {})
        cutoff = manifest.get("cutoff_date")
        record_count = sum(int(item.get("valid_count") or 0) for item in datasets.values())
        payload = {
            "connector_code": "FISCALIA_SPOA_V3",
            "status": status,
            "quality_status": quality,
            "period_label": f"Corte al {cutoff} - 3 conjuntos SPOA V3" if cutoff else "Corte no disponible",
            "source_cutoff_date": cutoff,
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
            "record_count": record_count,
            "indicator_count": len(manifest.get("indicators", {}).get("alerts", [])) + 3,
            "warnings": manifest.get("warnings", [])[:30],
            "details": {
                "run_id": manifest.get("run_id"),
                "outcome": manifest.get("status"),
                "updated_datasets": manifest.get("updated_datasets", []),
                "force_run": manifest.get("force_run", False),
                "dry_run": manifest.get("dry_run", False),
                "pdf_sha256": manifest.get("pdf_sha256"),
            },
        }
        if status != "ERROR":
            payload["last_success_at"] = datetime.now(timezone.utc).isoformat()
        if manifest.get("updated_datasets"):
            payload["last_change_detected_at"] = datetime.now(timezone.utc).isoformat()
        return self.post("source-center/heartbeat", payload)

    def ingest(
        self,
        run_id: str,
        dataset_key: str,
        dataset_manifest: Dict[str, Any],
        rows: Iterable[dict],
        batch_size: int = 500,
    ) -> None:
        batch: list[dict] = []
        for row in rows:
            batch.append(row)
            if len(batch) == batch_size:
                self._ingest_batch(run_id, dataset_key, dataset_manifest, batch)
                batch = []
        if batch:
            self._ingest_batch(run_id, dataset_key, dataset_manifest, batch)

    def _ingest_batch(self, run_id: str, dataset_key: str, manifest: Dict[str, Any], rows: list[dict]) -> None:
        self.post(
            "fiscalia-spoa/ingest",
            {
                "run_id": run_id,
                "dataset_key": dataset_key,
                "dataset_id": manifest["dataset_id"],
                "cutoff_date": manifest.get("cutoff_date"),
                "metadata_sha256": manifest["metadata_sha256"],
                "payload_sha256": manifest["payload_sha256"],
                "schema_version": manifest["schema_version"],
                "source_row_count": manifest.get("source_row_count"),
                "filtered_count": manifest.get("filtered_count"),
                "valid_count": manifest.get("valid_count"),
                "discarded_count": manifest.get("discarded_count"),
                "discard_reasons": manifest.get("discard_reasons", {}),
                "rows": rows,
            },
        )

    def complete_run(self, run_id: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
        return self.post(
            f"fiscalia-spoa/runs/{run_id}/complete",
            {
                "status": manifest.get("status", "COMPLETED"),
                "source_cutoff_date": manifest.get("cutoff_date"),
                "datasets": manifest.get("datasets", {}),
                "bulletin_path": manifest.get("bulletin_path"),
                "bulletin_sha256": manifest.get("pdf_sha256"),
            },
        )
