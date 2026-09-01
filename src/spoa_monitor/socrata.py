from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import requests

from .config import JAMUNDI_DANE, SOCRATA_DOMAIN, VALLE_NAME, DatasetSpec
from .utils import canonical_json, normalize_dane, normalize_text, sha256_bytes


LOGGER = logging.getLogger(__name__)


class DownloadError(RuntimeError):
    pass


class SchemaChangeError(RuntimeError):
    def __init__(self, dataset_id: str, missing: Iterable[str], extra: Iterable[str]):
        self.dataset_id = dataset_id
        self.missing = sorted(missing)
        self.extra = sorted(extra)
        super().__init__(
            f"Cambio de esquema en {dataset_id}; faltantes={self.missing}; nuevas={self.extra}"
        )


@dataclass(frozen=True)
class MetadataSnapshot:
    dataset_id: str
    title: str
    rows_updated_at: Optional[int]
    view_last_modified: Optional[int]
    publication_date: Optional[int]
    row_count: Optional[int]
    columns: tuple[str, ...]
    column_types: tuple[str, ...]
    metadata_sha256: str
    raw: Dict[str, Any]


def _metadata_row_count(metadata: Dict[str, Any]) -> Optional[int]:
    counts: List[int] = []
    for column in metadata.get("columns", []):
        cached = column.get("cachedContents") if isinstance(column, dict) else None
        try:
            if isinstance(cached, dict) and cached.get("count") is not None:
                counts.append(int(cached["count"]))
        except (TypeError, ValueError):
            pass
    return max(counts) if counts else None


class SocrataClient:
    def __init__(self, app_token: str = "", timeout: int = 60, page_size: int = 50000):
        self.timeout = timeout
        self.page_size = page_size
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "SISC-Jamundi-Fiscalia-SPOA-Monitor/1.0",
            }
        )
        if app_token:
            self.session.headers["X-App-Token"] = app_token

    def _get(self, url: str, *, params: Optional[dict] = None) -> requests.Response:
        last_error: Optional[Exception] = None
        for attempt in range(4):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
                return response
            except requests.RequestException as error:
                last_error = error
                if attempt < 3:
                    time.sleep(2 ** attempt)
        raise DownloadError(f"No fue posible consultar la fuente oficial: {last_error}") from last_error

    def metadata(self, spec: DatasetSpec) -> MetadataSnapshot:
        response = self._get(f"{SOCRATA_DOMAIN}/api/views/{spec.dataset_id}")
        try:
            raw = response.json()
        except ValueError as error:
            raise DownloadError(f"Metadatos JSON inválidos para {spec.dataset_id}") from error
        columns = tuple(str(item.get("fieldName") or "") for item in raw.get("columns", []))
        types = tuple(str(item.get("dataTypeName") or "") for item in raw.get("columns", []))
        signature = {
            "dataset_id": spec.dataset_id,
            "rows_updated_at": raw.get("rowsUpdatedAt"),
            "view_last_modified": raw.get("viewLastModified"),
            "publication_date": raw.get("publicationDate"),
            "row_count": _metadata_row_count(raw),
            "columns": list(zip(columns, types)),
            "description": raw.get("description"),
            "custom_fields": raw.get("metadata", {}).get("custom_fields", {}),
        }
        return MetadataSnapshot(
            dataset_id=spec.dataset_id,
            title=str(raw.get("name") or spec.title),
            rows_updated_at=raw.get("rowsUpdatedAt"),
            view_last_modified=raw.get("viewLastModified"),
            publication_date=raw.get("publicationDate"),
            row_count=_metadata_row_count(raw),
            columns=columns,
            column_types=types,
            metadata_sha256=sha256_bytes(canonical_json(signature)),
            raw=raw,
        )

    @staticmethod
    def validate_schema(spec: DatasetSpec, metadata: MetadataSnapshot) -> None:
        expected = set(spec.expected_columns)
        actual = set(metadata.columns)
        missing, extra = expected - actual, actual - expected
        if missing or extra:
            raise SchemaChangeError(spec.dataset_id, missing, extra)

    def iter_jamundi(self, spec: DatasetSpec, *, limit: Optional[int] = None) -> Iterable[dict]:
        endpoint = f"{SOCRATA_DOMAIN}/resource/{spec.dataset_id}.json"
        # El código DIVIPOLA es el criterio principal e indexable. Las variantes por nombre se
        # validan localmente; no se hace una consulta nacional ni se descargan filas de otros municipios.
        where = f"{spec.dane_field}='{JAMUNDI_DANE}'"
        offset = 0
        remaining = limit
        while remaining is None or remaining > 0:
            batch_size = self.page_size if remaining is None else min(self.page_size, remaining)
            response = self._get(
                endpoint,
                params={"$where": where, "$limit": batch_size, "$offset": offset, "$order": ":id"},
            )
            try:
                rows = response.json()
            except ValueError as error:
                raise DownloadError(f"Lote JSON inválido para {spec.dataset_id}") from error
            if not isinstance(rows, list):
                raise DownloadError(f"Respuesta inesperada para {spec.dataset_id}")
            for row in rows:
                if isinstance(row, dict):
                    yield row
            received = len(rows)
            offset += received
            if remaining is not None:
                remaining -= received
            LOGGER.info("%s: %s registros oficiales consultados", spec.dataset_id, offset)
            if received < batch_size:
                break


def territorial_validation(row: dict, spec: DatasetSpec) -> tuple[bool, str]:
    dane = normalize_dane(row.get(spec.dane_field))
    municipality = normalize_text(row.get(spec.municipality_field))
    department = normalize_text(row.get(spec.department_field))
    if department != VALLE_NAME:
        return False, "departamento_no_valle"
    if dane != JAMUNDI_DANE:
        return False, "codigo_dane_no_jamundi"
    if municipality != "JAMUNDI":
        return False, "nombre_municipio_no_jamundi"
    return True, "valido"


def write_raw_jsonl(path: Path, rows: Iterable[dict]) -> tuple[List[dict], str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    accepted: List[dict] = []
    digest_source: List[dict] = []
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            accepted.append(row)
            digest_source.append(row)
    return accepted, sha256_bytes(canonical_json(digest_source))

